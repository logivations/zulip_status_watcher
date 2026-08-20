#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
from typing import Optional

import requests

from watcher.schemas import W2moPresence, W2moWorkLocation

logger = logging.getLogger(__name__)

# W2MO accounts live on these domains; Google workspace emails (lvairo.com)
# usually share the local part. Tried in order after the literal email.
W2MO_EMAIL_DOMAINS = ("logivations.com", "pixel-robotics.eu")


class W2moClient:
    """Reads colleagues' current W2MO workday check-in state.

    Backs onto GET /api/workday/currentWorkDayRecordedByEmail, which returns the
    WorkDay record for today (or an empty body if the user has no record yet).
    Any authenticated W2MO user may query any other user's state, so a single
    long-lived service token is enough for the whole team.

    One instance is shared across all users and update cycles; per-user W2MO
    email resolution is cached after the first successful lookup.
    """

    def __init__(self, server_url: str, warehouse_id: int, auth_token: str, timeout: int = 10):
        self.server_url = server_url.rstrip("/")
        self.warehouse_id = warehouse_id
        self.timeout = timeout
        self.session = requests.Session()
        # W2MO issues a long-lived JWT; sent as a Bearer token (see SecurityConfiguration.isBearer).
        self.session.headers.update({"Authorization": f"Bearer {auth_token}"})
        self.w2mo_email_cache: dict[str, str] = {}
        self.missing_users: set[str] = set()

    def get_presence(self, email: str) -> tuple[W2moPresence, Optional[W2moWorkLocation]]:
        """Return (check-in state, work location of the open session) for a user.

        The location is only meaningful while checked in; None otherwise.
        """
        workday = self._fetch_workday(email)
        if workday is None:
            return W2moPresence.UNKNOWN, None
        presence = self._resolve_presence(workday)
        location = self._resolve_location(workday) if presence is W2moPresence.CHECKED_IN else None
        return presence, location

    def clear_missing_users(self) -> None:
        """Forget negative lookups so new W2MO accounts get retried."""
        self.missing_users.clear()

    def _candidate_emails(self, email: str) -> list[str]:
        cached = self.w2mo_email_cache.get(email)
        if cached:
            return [cached]
        candidates = [email]
        local_part = email.split("@")[0]
        for domain in W2MO_EMAIL_DOMAINS:
            alt = f"{local_part}@{domain}"
            if alt not in candidates:
                candidates.append(alt)
        return candidates

    def _fetch_workday(self, email: str) -> Optional[dict]:
        if email in self.missing_users:
            return None
        for candidate in self._candidate_emails(email):
            workday, user_exists = self._request_workday(candidate)
            if user_exists:
                self.w2mo_email_cache[email] = candidate
                if candidate != email:
                    logger.debug(f"Resolved W2MO user {candidate} for {email}")
                return workday
        logger.info(f"No W2MO user found for {email}")
        self.missing_users.add(email)
        return None

    def _request_workday(self, email: str) -> tuple[Optional[dict], bool]:
        """Fetch today's workday record; returns (record, user_exists).

        (None, True) means the user exists but has no record today.
        """
        url = f"{self.server_url}/api/workday/currentWorkDayRecordedByEmail"
        try:
            response = self.session.get(
                url,
                params={"warehouseId": self.warehouse_id, "email": email},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            logger.error(f"W2MO request failed for {email}: {e}")
            return None, False

        if response.status_code in (401, 403):
            # Expired/invalid token affects every user — make it loud.
            logger.error(f"W2MO auth failed ({response.status_code}) — check w2mo_auth_token")
            return None, False
        if response.status_code != 200:
            # Unknown user surfaces as an error response (USER_NOT_FOUND_$).
            logger.debug(
                f"W2MO returned {response.status_code} for {email}: {response.text[:200]}"
            )
            return None, False

        # User exists; body may be empty when there is no record for today.
        if not response.text or not response.text.strip():
            return None, True
        try:
            body = response.json()
        except ValueError:
            logger.warning(f"W2MO returned non-JSON body for {email}: {response.text[:200]}")
            return None, True
        return body or None, True

    @staticmethod
    def _resolve_presence(workday: dict) -> W2moPresence:
        """Derive check-in state from the morning/afternoon start/end timestamps.

        A session is open (checked in) when its start is set but its end is not.
        The afternoon session supersedes the morning one once it begins.
        """
        morning_start = workday.get("morningStartTime")
        morning_end = workday.get("morningEndTime")
        afternoon_start = workday.get("afternoonStartTime")
        afternoon_end = workday.get("afternoonEndTime")

        if afternoon_start:
            return W2moPresence.CHECKED_IN if not afternoon_end else W2moPresence.CHECKED_OUT
        if morning_start:
            return W2moPresence.CHECKED_IN if not morning_end else W2moPresence.CHECKED_OUT
        # Record exists (e.g. only a correction) but no session was ever started.
        return W2moPresence.UNKNOWN

    @staticmethod
    def _resolve_location(workday: dict) -> Optional[W2moWorkLocation]:
        # Prefer the afternoon location once the afternoon session has started.
        raw = workday.get("workLocationAfternoon")
        if raw is None:
            raw = workday.get("workLocationMorning")
        return W2moWorkLocation.from_code(raw)
