#  (C) Copyright
#  Logivations GmbH, Munich 2025
import configparser
import logging
import threading
import time
from typing import Optional

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
from tools.utils import setup_logging

# Setup logging
setup_logging("zulip_status_controller.log")
logger = logging.getLogger(__name__)

config_parser = configparser.RawConfigParser()
config_parser.read(get_expanded_appconfig("zulip/zulip.properties"))

ZULIP_USER_API_TOKEN = config_parser.get("zulip_status_watcher", "zulip_user_api_token")
ZULIP_SERVER_URL = config_parser.get("zulip_status_watcher", "zulip_server_url")
ZULIP_USER_EMAIL = config_parser.get("zulip_status_watcher", "zulip_user_email")
GOOGLE_CREDS = config_parser.get("zulip_status_watcher", "google_creds")
GOOGLE_TOKEN_FILE = config_parser.get("zulip_status_watcher", "google_token_file")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]



class ZulipStatusController:
    def __init__(self):
        self.calendar_client = CalendarClient(GOOGLE_CREDS, GOOGLE_TOKEN_FILE)
        self.zulip_client = ZulipClient(
            ZULIP_SERVER_URL, ZULIP_USER_EMAIL, ZULIP_USER_API_TOKEN
        )
        self.running = False

    def check_status(self) -> ZulipStatus:
        """Check calendar and determine the appropriate Zulip status."""
        meeting = self.calendar_client.get_current_meeting()
        logger.debug(f"Meeting: {meeting}")
        location = self.calendar_client.get_working_location()
        logger.debug(f"Location: {location}")
        vacation = self.calendar_client.check_vacation()
        logger.debug(f"Vacation: {vacation}")

        if vacation:
            return self._get_vacation_status(vacation)

        if meeting:
            return self._get_meeting_status(meeting)

        return self._get_location_status(location)

    def _get_vacation_status(self, vacation: str) -> ZulipStatus:
        """Determine status based on vacation event."""
        vacation_lower = vacation.lower()

        if "vacation" in vacation_lower:
            return AvailableStatuses.VACATION.value
        elif "workation" in vacation_lower:
            return AvailableStatuses.WORKATION.value
        elif "day off" in vacation_lower:
            return AvailableStatuses.DAY_OFF.value
        elif "out of office" in vacation_lower:
            status = AvailableStatuses.OUT_OF_OFFICE.value
            status.status_text = vacation
            return status
        else:
            return AvailableStatuses.OUT_OF_OFFICE.value

    def _get_meeting_status(self, meeting: Meeting) -> ZulipStatus:
        """Determine status based on current meeting."""
        if "lunch" in meeting.title.lower():
            return AvailableStatuses.LUNCH_BREAK.value

        if meeting.status not in ["accepted", "tentative", "needsAction"]:
            logger.info(
                f"Meeting found but status is {meeting.status}, not updating status."
            )
            return self._get_location_status(
                self.calendar_client.get_working_location()
            )

        status = AvailableStatuses.MEETING.value
        status.status_text = status.status_text.format(meet_name=meeting.title)
        return status

    def _get_location_status(self, location: Optional[str]) -> ZulipStatus:
        """Determine status based on working location."""
        logger.info(f"Working location from calendar: {location}")
        if location in [WorkingLocations.HOME.value, WorkingLocations.HOME_OFFICE.value]:
            return AvailableStatuses.WORKING_REMOTELY.value
        elif location == WorkingLocations.OFFICE.value:
            return AvailableStatuses.IN_OFFICE.value
        else:
            return AvailableStatuses.OUT_OF_OFFICE.value

    def update_status(self) -> bool:
        """Update the Zulip status based on calendar information."""
        try:
            new_status = self.check_status()
            current_status = self.zulip_client.get_user_status()
            if current_status.status_text != new_status.status_text:
                if self.zulip_client.update_user_status(new_status):
                    logger.info(f"Status updated to: {new_status.status_text}")
                else:
                    logger.error("Failed to update status.")
            else:
                logger.info("Status is already up-to-date.")
            return True
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            return False

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

        # Schedule the update_status method to run every minute
        schedule.every(1).minutes.do(self.update_status)

        # Run once immediately
        self.update_status()

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=self._scheduler_thread, daemon=True)
        scheduler_thread.start()

        logger.info("StatusController started. Will update status every minute.")
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


if __name__ == __name__:
    # python3 -m lv.tools.zulip.zulip_status_watcher
    controller = ZulipStatusController()
    controller.start()

    # Controlelr check
    # controller = StatusController()
    # controller.update_status()

    # Calendar usage
    # gclient = CalendarClient(GOOGLE_CREDS, GOOGLE_TOKEN_FILE)
    # meeting = gclient.get_current_meeting()
    # print("Current Meeting:", meeting)
    # location = gclient.get_working_location()
    # print("Working Location:", location)
    # vacation = gclient.check_vacation()
    # print("Vacation Check:", vacation)

    # Zulip usage
    # # client = ZulipClient(ZULIP_SERVER_URL, ZULIP_USER_EMAIL, ZULIP_USER_API_TOKEN)
    # user_status = client.get_user_status()
    # if user_status:
    #     print("Current User Status:", user_status)
    # if client.update_user_status(AvailableStatuses.LUNCH_BREAK.value):
    #     print("User status updated successfully.")
    # else:
    #     print("Failed to update user status.")