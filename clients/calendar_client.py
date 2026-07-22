#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from watcher.schemas import Meeting, Vacation

logger = logging.getLogger(__name__)

# IANA timezone -> short label shown in same-day absence statuses
# (e.g. "back 2pm MUC"). Unknown zones fall back to their city name.
TIMEZONE_LABELS = {
    "Europe/Berlin": "MUC",
    "Europe/Munich": "MUC",
    "Europe/Kyiv": "UA",
    "Europe/Kiev": "UA",
}


def _tz_label(tz_name: Optional[str]) -> str:
    """Short location label for a timezone, e.g. 'Europe/Berlin' -> 'MUC'."""
    if not tz_name:
        return ""
    if tz_name in TIMEZONE_LABELS:
        return TIMEZONE_LABELS[tz_name]
    return tz_name.split("/")[-1].replace("_", " ")


def _format_clock(dt: datetime) -> str:
    """Format a time as a compact 12-hour clock, e.g. '2pm' or '2:30pm'."""
    fmt = "%-I:%M%p" if dt.minute else "%-I%p"
    return dt.strftime(fmt).lower()


def _format_back_date(d: date) -> str:
    """Format a return date compactly, e.g. 'Jun 16'."""
    return f"{d.strftime('%b')} {d.day}"


# How far ahead to look for follow-up absence events when computing the
# real return date of a running absence.
ABSENCE_HORIZON_DAYS = 60


def _is_absence_event(event: Dict[str, Any]) -> bool:
    """Whether a calendar event marks the person as absent."""
    if event.get("eventType") == "outOfOffice":
        return True
    summary = event.get("summary", "").lower()
    return (
        summary.startswith("vacation")
        or summary.startswith("out of office")
        or summary.startswith("day off")
        or summary.startswith("workation")
        or summary.startswith("sick")
    )


def _absence_day_interval(event: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    """Day-granular [first absent day, first free day) interval of an absence.

    Only fully covered days count: a timed absence ending at 2pm does not
    block its last day. Google's out-of-office events are timed events
    running midnight to midnight in the event's own timezone, so they cover
    their days fully. Returns None when no full day is covered.
    """
    start_str = event["start"].get("dateTime", event["start"].get("date"))
    end_str = event["end"].get("dateTime", event["end"].get("date"))

    if "T" not in start_str:
        # All-day event; the end date is already exclusive.
        return date.fromisoformat(start_str), date.fromisoformat(end_str)

    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    first_day = start_dt.date() if start_dt.time() == time(0) else start_dt.date() + timedelta(days=1)
    first_free = end_dt.date()
    if first_free <= first_day:
        return None
    return first_day, first_free


def _first_free_workday(first_free: date, intervals: List[Tuple[date, date]]) -> date:
    """Walk forward from first_free until a working day with no absence.

    People often add multi-day vacations as one event per day (and skip the
    weekends), so the running event's end date alone understates the absence.
    Weekends never count as the day someone is "back".
    """
    horizon = first_free + timedelta(days=ABSENCE_HORIZON_DAYS)
    while first_free < horizon:
        if first_free.weekday() >= 5:  # Saturday / Sunday
            first_free += timedelta(days=1)
            continue
        covering_end = max(
            (e for (s, e) in intervals if s <= first_free < e), default=None
        )
        if covering_end is None:
            return first_free
        first_free = covering_end
    return first_free


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

        return build("calendar", "v3", credentials=creds, cache_discovery=False)

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

            # Manually filter events to only include events active today
            filtered_events = []
            for event in events:
                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))

                # Parse the start and end times
                if "T" in start_str:
                    # DateTime event - check if currently active
                    start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if start_time <= now <= end_time:
                        filtered_events.append(event)
                else:
                    # All-day event - check if today falls within the event range
                    start_date = datetime.fromisoformat(start_str).date()
                    end_date = datetime.fromisoformat(end_str).date()
                    if start_date <= now.date() < end_date:
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
                # Only true calendar events count as meetings. Tasks,
                # birthdays, focus time, out-of-office, working locations,
                # and Gmail-derived events are all non-meeting eventTypes.
                event_type = event.get("eventType")
                if event_type and event_type != "default":
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

        When multiple working location events overlap, prefers the most recently updated one.
        Timed events take priority over whole-day events.

        Returns:
            Tuple of (location_type, end_time_str) where end_time_str is HH:MM for timed events or None for whole-day.
        """
        try:
            events = self.get_events_list(max_results=10)
            current_time = datetime.now(timezone.utc)

            # Collect all matching working location events
            timed_matches = []  # List of (updated_timestamp, location_type, end_time_str)
            allday_matches = []  # List of (updated_timestamp, location_type)

            for event in events:
                if not event.get("workingLocationProperties"):
                    continue

                wl_props = event.get('workingLocationProperties', {})
                logger.debug(f"Working location event: {event.get('summary', 'No summary')} - type: {wl_props.get('type')} - updated: {event.get('updated')}")
                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))
                updated_str = event.get("updated", "1970-01-01T00:00:00.000Z")
                updated_time = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))

                if "T" in start_str:
                    # Timed working location event
                    start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if start_time <= current_time <= end_time:
                        location_type = wl_props.get("type", None)
                        end_time_local = end_time.strftime("%H:%M")
                        timed_matches.append((updated_time, location_type, end_time_local))
                else:
                    # Whole-day working location event
                    start_date = datetime.fromisoformat(start_str).date()
                    end_date = datetime.fromisoformat(end_str).date()
                    today = current_time.date()
                    if start_date <= today < end_date:
                        location_type = wl_props.get("type", None)
                        allday_matches.append((updated_time, location_type))

            # Timed events take priority, then pick most recently updated
            if timed_matches:
                timed_matches.sort(key=lambda x: x[0], reverse=True)  # Sort by updated time, newest first
                if len(timed_matches) > 1:
                    logger.warning(f"Multiple timed working location events found, using most recent: {timed_matches[0][1]}")
                return (timed_matches[0][1], timed_matches[0][2])

            if allday_matches:
                allday_matches.sort(key=lambda x: x[0], reverse=True)  # Sort by updated time, newest first
                if len(allday_matches) > 1:
                    logger.warning(f"Multiple all-day working location events found, using most recent: {allday_matches[0][1]}")
                return (allday_matches[0][1], None)

            return (None, None)
        except Exception as e:
            logger.error(f"Error fetching working location: {e}")
            return (None, None)

    def _fetch_absence_events(self) -> List[Dict[str, Any]]:
        """Fetch absence events for the next ABSENCE_HORIZON_DAYS days.

        Uses its own, longer window than get_events_list so that follow-up
        absence events (e.g. vacation days added one per day) are visible
        when computing the real return date.
        """
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=ABSENCE_HORIZON_DAYS)
        events_result = (
            self.service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=100,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return [e for e in events_result.get("items", []) if _is_absence_event(e)]

    def check_vacation(self) -> Optional[Vacation]:
        """Check for vacation/sick/day off events (both whole-day and timed).

        Whole-day has priority. Returns a Vacation carrying the event summary
        and a short "back" label (the first day/time the person is available
        again), or None when no absence is active.

        The back label looks past the running event: adjacent absence events
        and weekends are skipped, so a vacation entered as one event per day
        still reports the first actual working day back.
        """
        try:
            events = self._fetch_absence_events()
            current_time = datetime.now(timezone.utc)
            today = current_time.date()

            intervals = [
                iv for iv in (_absence_day_interval(e) for e in events) if iv
            ]

            timed_match = None  # Optional[Vacation]

            for event in events:
                is_ooo_event_type = event.get("eventType") == "outOfOffice"
                # Google Calendar OOO event type always maps to "Out of office"
                event_summary = "Out of office" if is_ooo_event_type else event.get("summary", "")

                start_str = event["start"].get("dateTime", event["start"].get("date"))
                end_str = event["end"].get("dateTime", event["end"].get("date"))

                if "T" in start_str:
                    # Timed event - save for later if no whole-day match
                    if timed_match is None:
                        start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if start_time <= current_time <= end_time:
                            back_label = self._timed_back_label(
                                end_time, event["end"].get("timeZone"), current_time, intervals
                            )
                            timed_match = Vacation(summary=event_summary, back_label=back_label)
                else:
                    # Whole-day event - return immediately (has priority).
                    start_date = datetime.fromisoformat(start_str).date()
                    end_date = datetime.fromisoformat(end_str).date()
                    if start_date <= today < end_date:
                        # All-day end date is exclusive, so it is already the
                        # first day back — before skipping follow-up absences.
                        back_label = _format_back_date(
                            _first_free_workday(end_date, intervals)
                        )
                        logger.info(
                            f"Whole-day vacation/OOO event found: {event_summary} (back {back_label})"
                        )
                        return Vacation(summary=event_summary, back_label=back_label)

            if timed_match:
                logger.info(
                    f"Timed vacation/OOO event found: {timed_match.summary} (back {timed_match.back_label})"
                )
                return timed_match

            return None
        except Exception as e:
            logger.error(f"Error checking vacation: {e}")
            return None

    @staticmethod
    def _timed_back_label(
        end_time: datetime,
        tz_name: Optional[str],
        now: datetime,
        intervals: Optional[List[Tuple[date, date]]] = None,
    ) -> str:
        """Build the "back" label for a timed absence.

        Same-day return -> clock time with a timezone label (e.g. "2pm MUC");
        a return on a later day -> a date (e.g. "Jun 16"), pushed past any
        follow-up absence events and weekends. All times are shown in the
        event's own timezone so a Munich doctor's appointment reads in Munich
        time and a Ukrainian one in Kyiv time.
        """
        zone = None
        if tz_name:
            try:
                zone = ZoneInfo(tz_name)
            except Exception:
                logger.warning(f"Unknown event timezone '{tz_name}', falling back to event offset")

        # When the zone is unknown, the parsed datetime still carries the
        # original UTC offset, so its wall-clock time stays correct.
        end_local = end_time.astimezone(zone) if zone else end_time
        now_local = now.astimezone(zone) if zone else now.astimezone()

        if end_local.date() > now_local.date():
            return _format_back_date(
                _first_free_workday(end_local.date(), intervals or [])
            )

        return f"{_format_clock(end_local)} {_tz_label(tz_name)}".strip()

