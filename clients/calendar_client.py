#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from watcher.schemas import Meeting

logger = logging.getLogger(__name__)


class CalendarClient:
    def __init__(
        self, credentials_file: str = "credentials.json", token_file: str = "token.json"
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
        self.service = self._authenticate()

    def _authenticate(self):
        """Authenticate and return the Google Calendar service."""
        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    logger.error(f"Credential file not found: {self.credentials_file}")
                    raise FileNotFoundError(
                        f"Credential file not found: {self.credentials_file}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.scopes
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_file, "w") as token:
                token.write(creds.to_json())

        return build("calendar", "v3", credentials=creds)

    def get_events_list(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Fetch today's events from the primary calendar."""
        now = datetime.now(timezone.utc)
        print(f"{now=}")
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        print(f"{end_of_day=}")
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

            print(f"{filtered_events=}")
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
                        return Meeting(
                            title=event.get("summary", "Untitled Meeting"),
                            start_time=start_time,
                            end_time=end_time,
                            meeting_url=meeting_url,
                            status=status if status else "accepted",
                        )

            return None
        except Exception as e:
            logger.error(f"Error fetching current meeting: {e}")
            return None

    def get_working_location(self) -> Optional[str]:
        """Fetch the working location from upcoming events."""
        try:
            events = self.get_events_list(max_results=10)

            for event in events:
                start_str = event["start"].get("dateTime", event["start"].get("date"))
                if "T" not in start_str:
                    if event.get("workingLocationProperties"):
                        return event["workingLocationProperties"].get("type", None)
            return None
        except Exception as e:
            logger.error(f"Error fetching working location: {e}")
            return None

    def check_vacation(self):
        """Fetch the working location from upcoming events."""
        try:
            events = self.get_events_list(max_results=10)

            for event in events:
                start_str = event["start"].get("dateTime", event["start"].get("date"))
                if "T" in start_str:
                    if (
                        event.get("summary", "").lower().startswith("vacation")
                        or event.get("summary", "").lower().startswith("out of office")
                        or event.get("summary", "").lower().startswith("day off")
                        or event.get("summary", "").lower().startswith("workation")
                    ):
                        logger.info(f"Vacation event found: {event.get('summary', '')}")
                        return event.get("summary", "")
            return None
        except Exception as e:
            logger.error(f"Error fetching working location: {e}")
            return None

