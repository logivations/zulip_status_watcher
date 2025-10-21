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
    OUT_OF_OFFICE = ZulipStatus(status_text="Out of office", emoji_name="palm_tree")
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