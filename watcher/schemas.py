#  (C) Copyright
#  Logivations GmbH, Munich 2025
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class ZulipStatus:
    status_text: str
    emoji_name: str
    emoji_code: str = ""
    reaction_type: str = "unicode_emoji"


class AvailableStatuses(Enum):
    IN_OFFICE = ZulipStatus(status_text="In office", emoji_name="office")
    OUT_OF_OFFICE = ZulipStatus(status_text="Out of office", emoji_name="door")
    WORKING_REMOTELY = ZulipStatus(status_text="Working remotely", emoji_name="house")
    MEETING = ZulipStatus(status_text="meet: {meet_name}", emoji_name="calendar")
    LUNCH_BREAK = ZulipStatus(status_text="On a lunch break", emoji_name="salad")
    VACATION = ZulipStatus(status_text="On vacation", emoji_name="palm_tree")
    WORKATION = ZulipStatus(
        status_text="On a workation",
        emoji_name="workation_new",
        reaction_type="realm_emoji",
    )
    DAY_OFF = ZulipStatus(status_text="Day off", emoji_name="palm_tree")
    SICK_LEAVE = ZulipStatus(status_text="Sick leave", emoji_name="face_with_thermometer")
    # W2MO check-in derived statuses
    AVAILABLE = ZulipStatus(status_text="Available", emoji_name="check")
    UNAVAILABLE = ZulipStatus(status_text="Unavailable", emoji_name="red_circle")


class W2moPresence(Enum):
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    UNKNOWN = "unknown"


class W2moWorkLocation(Enum):
    """Mirrors the W2MO 'work-location' enum ordinals stored on lv_workday_recorded."""

    MUC_OFFICE = 0
    LVIV_OFFICE = 1
    HOME = 2
    CUSTOMER = 3
    OTHERS = 4

    @classmethod
    def from_code(cls, code: Optional[int]) -> Optional["W2moWorkLocation"]:
        if code is None:
            return None
        try:
            return cls(code)
        except ValueError:
            return None

    @property
    def is_office(self) -> bool:
        return self in (W2moWorkLocation.MUC_OFFICE, W2moWorkLocation.LVIV_OFFICE)

    @property
    def is_remote(self) -> bool:
        return self is W2moWorkLocation.HOME


class WorkingLocations(Enum):
    OFFICE = "officeLocation"
    HOME = "homeLocation"
    HOME_OFFICE = "homeOffice"
    OTHER = "otherLocation"


@dataclass
class Meeting:
    title: str
    start_time: datetime
    end_time: datetime
    meeting_url: Optional[str] = None
    status: str = "accepted"


@dataclass
class Vacation:
    """An active absence (vacation/workation/day off/sick/out-of-office).

    back_label is a short, human-readable hint of when the person is
    available again, e.g. "Jun 16" for a multi-day absence or "2pm MUC"
    for a same-day one. None when it can't be determined.
    """
    summary: str
    back_label: Optional[str] = None