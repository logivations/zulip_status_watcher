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


class UserStatusController:
    """Controller for a single user's status."""

    def __init__(self, user_email: str, zulip_client: ZulipClient, google_creds: str):
        self.user_email = user_email
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

    def _get_vacation_status(self, vacation: str) -> ZulipStatus:
        """Determine status based on vacation event."""
        vacation_lower = vacation.lower()

        if "vacation" in vacation_lower:
            return AvailableStatuses.VACATION.value
        elif "workation" in vacation_lower:
            return AvailableStatuses.WORKATION.value
        elif "day off" in vacation_lower:
            return AvailableStatuses.DAY_OFF.value
        elif "sick" in vacation_lower:
            return AvailableStatuses.SICK_LEAVE.value
        elif "out of office" in vacation_lower:
            template = AvailableStatuses.OUT_OF_OFFICE.value
            return ZulipStatus(
                status_text=vacation,
                emoji_name=template.emoji_name,
                emoji_code=template.emoji_code,
                reaction_type=template.reaction_type,
            )
        else:
            return AvailableStatuses.OUT_OF_OFFICE.value

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
        try:
            new_status = self.check_status()
            current_status = self.zulip_client.get_user_status()

            if new_status is None:
                # Remove auto part if present, keep only user prefix
                current_text = current_status.status_text if current_status else ""
                user_prefix = self._get_user_prefix(current_text)
                if "|" in current_text:
                    # Need to update to remove the auto part
                    if self.zulip_client.update_user_status(ZulipStatus(
                        status_text=user_prefix,
                        emoji_name=current_status.emoji_name if current_status else "",
                        emoji_code=current_status.emoji_code if current_status else "",
                        reaction_type=current_status.reaction_type if current_status else "unicode_emoji",
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

            if current_text != final_status_text:
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

class MultiUserStatusController:
    """Controller that manages status updates for multiple users."""

    def __init__(self):
        self.groups_client = GroupsClient(GOOGLE_CREDS, ADMIN_EMAIL)
        self.user_controllers: dict[str, UserStatusController] = {}
        # Map of user emails to their Zulip API tokens
        # Using same admin token for all users (admin can update any user's status)
        self.user_tokens: dict[str, str] = {}
        self.admin_token = ZULIP_BOT_API_TOKEN
        self.admin_email = ZULIP_BOT_EMAIL
        self.running = False

    def _get_beta_users(self) -> List[str]:
        """Get list of users from the beta group, filtered for safety."""
        try:
            members = self.groups_client.get_group_members(BETA_GROUP_EMAIL)
            # Safety filter: only users with 'plapp' in email for now
            filtered = [m for m in members if "plapp" in m.lower()]
            if filtered:
                logger.info(f"Beta users (filtered): {filtered}")
                return filtered
        except Exception as e:
            logger.warning(f"Could not fetch group members: {e}")

        # Fallback to hardcoded list
        fallback = [ZULIP_BOT_EMAIL]
        logger.info(f"Using fallback beta users: {fallback}")
        return fallback

    def _find_zulip_user(self, zulip_client: ZulipClient, google_email: str) -> Optional[dict]:
        """Try to find Zulip user, trying alternative domains if needed."""
        # Try original email first
        user = zulip_client.get_user_by_email(google_email)
        if user and self._is_user_active(zulip_client, user):
            return user

        # Extract username part
        username = google_email.split("@")[0]

        # Try alternative domains
        alternative_domains = ["pixel-robotics.eu", "logivations.com"]
        for domain in alternative_domains:
            alt_email = f"{username}@{domain}"
            if alt_email == google_email:
                continue
            user = zulip_client.get_user_by_email(alt_email)
            if user and self._is_user_active(zulip_client, user):
                logger.info(f"Found Zulip user {alt_email} for Google user {google_email}")
                return user

        return None

    def _is_user_active(self, zulip_client: ZulipClient, user: dict) -> bool:
        """Check if a Zulip user is active by trying to get their status."""
        try:
            original_target = zulip_client.target_user
            zulip_client.target_user = user
            status = zulip_client.get_user_status()
            zulip_client.target_user = original_target
            return status is not None
        except:
            return False

    def _ensure_user_controller(self, user_email: str) -> Optional[UserStatusController]:
        """Get or create a controller for a user."""
        if user_email not in self.user_controllers:
            # Use admin token to update user's status
            zulip_client = ZulipClient(ZULIP_SERVER_URL, self.admin_email, self.admin_token)
            zulip_user = self._find_zulip_user(zulip_client, user_email)
            if not zulip_user:
                logger.warning(f"Could not find active Zulip user for {user_email}, skipping")
                return None
            zulip_client.target_user = zulip_user
            self.user_controllers[user_email] = UserStatusController(
                user_email, zulip_client, GOOGLE_CREDS
            )
        return self.user_controllers[user_email]

    def update_all_users(self) -> bool:
        """Update status for all beta users."""
        users = self._get_beta_users()
        for user_email in users:
            try:
                controller = self._ensure_user_controller(user_email)
                if controller:
                    logger.info(f"Updating status for {user_email}")
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

        # Schedule the update_all_users method to run every minute
        schedule.every(20).seconds.do(self.update_all_users)

        # Run once immediately
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