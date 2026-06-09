#  (C) Copyright
#  Logivations GmbH, Munich 2025
import configparser
import logging
import threading
import time
from typing import List, Optional

import schedule

from tools.utils import get_expanded_appconfig
from watcher.schemas import (
    AvailableStatuses,
    Meeting,
    Vacation,
    WorkingLocations,
    ZulipStatus,
)
from clients.calendar_client import CalendarClient
from clients.zulip_client import ZulipClient
from clients.groups_client import GroupsClient
from tools.utils import setup_logging

# Setup logging
setup_logging("zulip_status_controller.log")
logger = logging.getLogger(__name__)

config_parser = configparser.RawConfigParser()
config_parser.read("/data/zulip_status_watcher/zulip.properties")

ZULIP_BOT_API_TOKEN = config_parser.get("zulip_status_watcher", "zulip_user_api_token")
ZULIP_SERVER_URL = config_parser.get("zulip_status_watcher", "zulip_server_url")
ZULIP_BOT_EMAIL = config_parser.get("zulip_status_watcher", "zulip_user_email")
GOOGLE_CREDS = config_parser.get("zulip_status_watcher", "google_creds")
GOOGLE_TOKEN_FILE = config_parser.get("zulip_status_watcher", "google_token_file")
BETA_GROUP_EMAIL = "zulip_status_beta@lvairo.com"
ADMIN_EMAIL = "johannes.plapp@lvairo.com"
APPLY_TO_ALL_USERS = True
STATUS_UPDATE_INTERVAL_SECONDS = 20
USER_DISCOVERY_INTERVAL_SECONDS = 300


class UserStatusController:
    """Controller for a single user's status.

    Transient — built once per update cycle per user, then discarded so the
    googleapiclient service objects don't accumulate in memory.
    """

    def __init__(self, user_email: str, zulip_client: ZulipClient, zulip_user: dict, google_creds: str):
        self.user_email = user_email
        self.zulip_user = zulip_user
        self.calendar_client = CalendarClient(google_creds, subject=user_email)
        self.zulip_client = zulip_client

    def check_status(self) -> Optional[ZulipStatus]:
        """Check calendar and determine the appropriate Zulip status."""
        meeting = self.calendar_client.get_current_meeting()
        logger.debug(f"Meeting: {meeting}")
        location, location_end_time = self.calendar_client.get_working_location()
        logger.debug(f"Location: {location}, until: {location_end_time}")
        vacation = self.calendar_client.check_vacation()
        logger.debug(f"Vacation: {vacation}")

        if vacation:
            return self._get_vacation_status(vacation)

        if meeting:
            return self._get_meeting_status(meeting, location, location_end_time)

        return self._get_location_status(location, location_end_time)

    def _get_vacation_status(self, vacation: Vacation) -> ZulipStatus:
        """Determine status based on vacation event.

        Appends a "back" hint (e.g. "On vacation (back Jun 16)" or
        "Out of office (back 2pm MUC)") when the calendar gives one.
        """
        summary_lower = vacation.summary.lower()

        if "vacation" in summary_lower:
            template = AvailableStatuses.VACATION.value
            base_text = template.status_text
        elif "workation" in summary_lower:
            template = AvailableStatuses.WORKATION.value
            base_text = template.status_text
        elif "day off" in summary_lower:
            template = AvailableStatuses.DAY_OFF.value
            base_text = template.status_text
        elif "sick" in summary_lower:
            template = AvailableStatuses.SICK_LEAVE.value
            base_text = template.status_text
        elif "out of office" in summary_lower:
            # Preserve any custom OOO event title as the base text.
            template = AvailableStatuses.OUT_OF_OFFICE.value
            base_text = vacation.summary
        else:
            template = AvailableStatuses.OUT_OF_OFFICE.value
            base_text = template.status_text

        status_text = base_text
        if vacation.back_label:
            status_text = f"{base_text} (back {vacation.back_label})"

        return ZulipStatus(
            status_text=status_text,
            emoji_name=template.emoji_name,
            emoji_code=template.emoji_code,
            reaction_type=template.reaction_type,
        )

    def _get_meeting_status(self, meeting: Meeting, location: Optional[str] = None, location_end_time: Optional[str] = None) -> ZulipStatus:
        """Determine status based on current meeting."""
        if "lunch" in meeting.title.lower():
            return AvailableStatuses.LUNCH_BREAK.value

        if meeting.status not in ["accepted", "tentative", "needsAction"]:
            logger.info(
                f"Meeting found but status is {meeting.status}, not updating status."
            )
            return self._get_location_status(location, location_end_time)

        template = AvailableStatuses.MEETING.value
        return ZulipStatus(
            status_text=f"meet: {meeting.title}",
            emoji_name=template.emoji_name,
            emoji_code=template.emoji_code,
            reaction_type=template.reaction_type,
        )

    def _get_location_status(self, location: Optional[str], end_time: Optional[str] = None) -> Optional[ZulipStatus]:
        """Determine status based on working location."""
        logger.info(f"Working location from calendar: {location}")
        if location in [WorkingLocations.HOME.value, WorkingLocations.HOME_OFFICE.value]:
            template = AvailableStatuses.WORKING_REMOTELY.value
            status_text = "Working remotely"
            if end_time:
                status_text = f"Working remotely (until {end_time})"
            return ZulipStatus(
                status_text=status_text,
                emoji_name=template.emoji_name,
                emoji_code=template.emoji_code,
                reaction_type=template.reaction_type,
            )
        elif location == WorkingLocations.OFFICE.value:
            template = AvailableStatuses.IN_OFFICE.value
            status_text = "In office"
            if end_time:
                status_text = f"In office (until {end_time})"
            return ZulipStatus(
                status_text=status_text,
                emoji_name=template.emoji_name,
                emoji_code=template.emoji_code,
                reaction_type=template.reaction_type,
            )
        else:
            return None

    def _get_auto_status_text(self, current_text: str) -> str:
        """Extract the auto-generated part after last '|' from current status."""
        if "|" in current_text:
            return current_text.rsplit("|", 1)[1].strip()
        return ""

    def _get_user_prefix(self, current_text: str) -> str:
        """Extract the user's custom prefix before first '|' from current status.

        If there's no '|', the entire text is considered user's custom status.
        """
        if "|" in current_text:
            return current_text.split("|", 1)[0].strip()
        return current_text.strip()  # Preserve user's custom status as prefix

    def _build_status_text(self, user_prefix: str, auto_text: str) -> str:
        """Build the full status text with user prefix and auto-generated text."""
        if user_prefix:
            return f"{user_prefix} | {auto_text}"
        return f"| {auto_text}"

    def update_status(self) -> bool:
        """Update the Zulip status based on calendar information."""
        original_target = self.zulip_client.target_user
        self.zulip_client.target_user = self.zulip_user
        try:
            new_status = self.check_status()
            current_status = self.zulip_client.get_user_status()

            if new_status is None:
                # Remove auto part if present, keep only user prefix
                current_text = current_status.status_text if current_status else ""
                user_prefix = self._get_user_prefix(current_text)
                if "|" in current_text:
                    # Need to update to remove the auto part
                    # If no user prefix remains, also clear the emoji
                    if user_prefix:
                        emoji_name = current_status.emoji_name if current_status else ""
                        emoji_code = current_status.emoji_code if current_status else ""
                        reaction_type = current_status.reaction_type if current_status else "unicode_emoji"
                    else:
                        emoji_name = ""
                        emoji_code = ""
                        reaction_type = "unicode_emoji"
                    if self.zulip_client.update_user_status(ZulipStatus(
                        status_text=user_prefix,
                        emoji_name=emoji_name,
                        emoji_code=emoji_code,
                        reaction_type=reaction_type,
                    )):
                        logger.info(f"Status updated to: {user_prefix}")
                    else:
                        logger.error("Failed to update status.")
                else:
                    logger.info("No calendar status to set, leaving status unchanged.")
                return True

            current_text = current_status.status_text if current_status else ""
            user_prefix = self._get_user_prefix(current_text)
            current_auto_text = self._get_auto_status_text(current_text)
            final_status_text = self._build_status_text(user_prefix, new_status.status_text)
            logger.debug(f"current_text='{current_text}', user_prefix='{user_prefix}', new_auto='{new_status.status_text}', final='{final_status_text}'")

            current_emoji = current_status.emoji_name if current_status else ""
            if current_text != final_status_text or current_emoji != new_status.emoji_name:
                status_to_update = ZulipStatus(
                    status_text=final_status_text,
                    emoji_name=new_status.emoji_name,
                    emoji_code=new_status.emoji_code,
                    reaction_type=new_status.reaction_type,
                )
                if self.zulip_client.update_user_status(status_to_update):
                    logger.info(f"Status updated to: {final_status_text}")
                else:
                    logger.error("Failed to update status.")
            else:
                logger.info("Status is already up-to-date.")
            return True
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            return False
        finally:
            self.zulip_client.target_user = original_target

class MultiUserStatusController:
    """Controller that manages status updates for multiple users.

    Holds exactly one admin ZulipClient shared across all users (via
    target_user swapping), and rebuilds UserStatusController / CalendarClient
    objects per update cycle so they don't accumulate in memory.
    """

    def __init__(self):
        self.groups_client = GroupsClient(GOOGLE_CREDS, ADMIN_EMAIL)
        self.zulip_client = ZulipClient(ZULIP_SERVER_URL, ZULIP_BOT_EMAIL, ZULIP_BOT_API_TOKEN)
        self.known_users: List[str] = []
        self.zulip_user_cache: dict[str, dict] = {}
        self.missing_zulip_users: set[str] = set()
        self.running = False

    def _fetch_user_list(self) -> List[str]:
        """Fetch the current list of users to manage."""
        if APPLY_TO_ALL_USERS:
            try:
                users = self.groups_client.get_all_domain_users()
                if users:
                    logger.info(f"Applying to all {len(users)} workspace users")
                    return users
            except Exception as e:
                logger.warning(f"Could not fetch all domain users: {e}")
        else:
            try:
                members = self.groups_client.get_group_members(BETA_GROUP_EMAIL)
                if members:
                    logger.info(f"Beta users: {members}")
                    return members
            except Exception as e:
                logger.warning(f"Could not fetch group members: {e}")

        fallback = [ZULIP_BOT_EMAIL]
        logger.info(f"Using fallback users: {fallback}")
        return fallback

    def refresh_user_list(self) -> None:
        """Re-fetch the user list and drop caches for users who left.

        Also clears the missing-user cache so that colleagues who were not yet
        in Zulip at the last discovery pass get retried.
        """
        users = self._fetch_user_list()
        current = set(users)
        previous = set(self.known_users)

        for email in current - previous:
            logger.info(f"New user discovered: {email}")
        for email in previous - current:
            logger.info(f"User removed: {email}")
            self.zulip_user_cache.pop(email, None)
            self.missing_zulip_users.discard(email)

        # Retry users previously not found — they may have a Zulip account now.
        self.missing_zulip_users.clear()
        self.known_users = users

    def _find_zulip_user(self, google_email: str) -> Optional[dict]:
        """Find the Zulip user for a Google email, with caching.

        Positive hits are cached until the user leaves the workspace; negative
        hits are cached until the next refresh_user_list() call.
        """
        cached = self.zulip_user_cache.get(google_email)
        if cached is not None:
            return cached
        if google_email in self.missing_zulip_users:
            return None

        candidates = [google_email]
        username = google_email.split("@")[0]
        for domain in ("pixel-robotics.eu", "logivations.com"):
            alt = f"{username}@{domain}"
            if alt != google_email:
                candidates.append(alt)

        for email in candidates:
            user = self.zulip_client.get_user_by_email(email)
            if user and self._is_user_active(user):
                if email != google_email:
                    logger.info(f"Found Zulip user {email} for Google user {google_email}")
                self.zulip_user_cache[google_email] = user
                return user

        self.missing_zulip_users.add(google_email)
        logger.info(f"No active Zulip user for {google_email}")
        return None

    def _is_user_active(self, user: dict) -> bool:
        """Check if a Zulip user is active by trying to get their status."""
        original_target = self.zulip_client.target_user
        try:
            self.zulip_client.target_user = user
            status = self.zulip_client.get_user_status()
            return status is not None
        except Exception:
            return False
        finally:
            self.zulip_client.target_user = original_target

    def update_all_users(self) -> bool:
        """Update status for all currently-known users."""
        for user_email in self.known_users:
            try:
                zulip_user = self._find_zulip_user(user_email)
                if not zulip_user:
                    continue
                logger.info(f"Updating status for {user_email}")
                controller = UserStatusController(
                    user_email, self.zulip_client, zulip_user, GOOGLE_CREDS
                )
                controller.update_status()
            except Exception as e:
                logger.error(f"Error updating status for {user_email}: {e}")
        return True

    def _scheduler_thread(self):
        """Thread to run the scheduler."""
        while self.running:
            schedule.run_pending()
            time.sleep(1)

    def start(self):
        """Start the status controller and scheduler."""
        if self.running:
            logger.warning("StatusController is already running.")
            return

        self.running = True

        schedule.every(STATUS_UPDATE_INTERVAL_SECONDS).seconds.do(self.update_all_users)
        schedule.every(USER_DISCOVERY_INTERVAL_SECONDS).seconds.do(self.refresh_user_list)

        # Discover users first, then run one update immediately.
        self.refresh_user_list()
        self.update_all_users()

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=self._scheduler_thread, daemon=True)
        scheduler_thread.start()

        logger.info("MultiUserStatusController started. Will update status every minute.")
        logger.info("Press Ctrl+C to stop.")

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop the status controller and scheduler."""
        if not self.running:
            logger.warning("StatusController is not running.")
            return

        self.running = False
        schedule.clear()
        logger.info("StatusController stopped.")


if __name__ == "__main__":
    controller = MultiUserStatusController()
    controller.start()