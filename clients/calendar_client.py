#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from watcher.schemas import Meeting

logger = logging.getLogger(__name__)


class CalendarClient:
    def __init__(
        self, credentials_file: str = "credentials.json", token_file: str = "token.json",
        subject: str = None
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.subject = subject
        self.scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
        self.service = self._authenticate()

    def _authenticate(self):
        """Authenticate and return the Google Calendar service."""
        if not os.path.exists(self.credentials_file):
            logger.error(f"Credential file not found: {self.credentials_file}")
            raise FileNotFoundError(
                f"Credential file not found: {self.credentials_file}"
            )

        creds = ServiceAccountCredentials.from_service_account_file(
            self.credentials_file, scopes=self.scopes
        )

        if self.subject:
            creds = creds.with_subject(self.subject)

        return build("calendar", "v3", credentials=creds)

    def get_events_list(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Fetch today's events from the primary calendar."""
        now = datetime.now(timezone.utc)
        try:
            events_result = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])

            # Manually filter events to only include today's events
            filtered_events = []
            for event in events:
                start_str = event["start"].get("dateTime", event["start"].get("date"))

                # Parse the start time
                if "T" in start_str:
                    # DateTime event
                    start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                else:
                    # All-day event (date only)
                    start_time = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)

                # Check if event starts today
                if start_time.date() == now.date():
                    filtered_events.append(event)

            return filtered_events
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []

    def get_current_meeting(self) -> Optional[Meeting]:
        """Fetch the current ongoing meeting, if any."""
        try:
            events = self.get_events_list(max_results=10)
            current_time = datetime.now(timezone.utc)

            for event in events:
                # Skip working location events - they're not meetings
                if event.get("workingLocationProperties"):
                    continue

                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))

                if "T" in start_str:
                    start_time = datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    )
                    end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

                    if start_time <= current_time <= end_time:
                        meeting_url = None
                        if "conferenceData" in event:
                            entry_points = event["conferenceData"].get(
                                "entryPoints", []
                            )
                            for entry in entry_points:
                                if entry["entryPointType"] == "video":
                                    meeting_url = entry["uri"]
                                    break
                        status = ""
                        for user in event.get("attendees", []):
                            if user.get("self") and user.get("responseStatus"):
                                status = user.get("responseStatus")

                        visibility = event.get("visibility", "default")
                        if visibility in ["private", "confidential"]:
                            title = "Busy"
                        else:
                            title = event.get("summary", "Untitled Meeting")

                        return Meeting(
                            title=title,
                            start_time=start_time,
                            end_time=end_time,
                            meeting_url=meeting_url,
                            status=status if status else "accepted",
                        )

            return None
        except Exception as e:
            logger.error(f"Error fetching current meeting: {e}")
            return None

    def get_working_location(self) -> tuple[Optional[str], Optional[str]]:
        """Fetch the working location from events (both whole-day and timed).

        Returns:
            Tuple of (location_type, end_time_str) where end_time_str is HH:MM for timed events or None for whole-day.
        """
        try:
            events = self.get_events_list(max_results=10)
            current_time = datetime.now(timezone.utc)

            for event in events:
                if not event.get("workingLocationProperties"):
                    continue

                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))

                if "T" in start_str:
                    # Timed working location event
                    start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if start_time <= current_time <= end_time:
                        location_type = event["workingLocationProperties"].get("type", None)
                        end_time_local = end_time.strftime("%H:%M")
                        return (location_type, end_time_local)
                else:
                    # Whole-day working location event
                    start_date = datetime.fromisoformat(start_str).date()
                    end_date = datetime.fromisoformat(end_str).date()
                    today = current_time.date()
                    if start_date <= today < end_date:
                        location_type = event["workingLocationProperties"].get("type", None)
                        return (location_type, None)

            return (None, None)
        except Exception as e:
            logger.error(f"Error fetching working location: {e}")
            return (None, None)

    def check_vacation(self):
        """Check for vacation/sick/day off events (both whole-day and timed). Whole-day has priority."""
        try:
            events = self.get_events_list(max_results=10)
            current_time = datetime.now(timezone.utc)
            today = current_time.date()

            timed_match = None

            for event in events:
                summary = event.get("summary", "").lower()
                if not (
                    summary.startswith("vacation")
                    or summary.startswith("out of office")
                    or summary.startswith("day off")
                    or summary.startswith("workation")
                    or summary.startswith("sick")
                ):
                    continue

                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))

                if "T" in start_str:
                    # Timed event - save for later if no whole-day match
                    if timed_match is None:
                        start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if start_time <= current_time <= end_time:
                            timed_match = event.get("summary", "")
                else:
                    # Whole-day event - return immediately (has priority)
                    start_date = datetime.fromisoformat(start_str).date()
                    end_date = datetime.fromisoformat(end_str).date()
                    if start_date <= today < end_date:
                        logger.info(f"Whole-day vacation event found: {event.get('summary', '')}")
                        return event.get("summary", "")

            if timed_match:
                logger.info(f"Timed vacation event found: {timed_match}")
                return timed_match

            return None
        except Exception as e:
            logger.error(f"Error checking vacation: {e}")
            return None

