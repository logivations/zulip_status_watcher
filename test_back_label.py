"""Quick tests for the multi-event back-label logic (no Google deps needed)."""
import sys
import types
from datetime import date, datetime, timedelta, timezone, time as dtime

# Stub the Google client libraries so clients.calendar_client imports cleanly.
def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_stub("google")
_stub("google.auth")
_stub("google.auth.transport")
_stub("google.auth.transport.requests", Request=object)
_stub("google.oauth2")
_stub("google.oauth2.credentials", Credentials=object)
class _SA:
    @classmethod
    def from_service_account_file(cls, *a, **k):
        raise RuntimeError("not used in tests")
_stub("google.oauth2.service_account", Credentials=_SA)
_stub("google_auth_oauthlib")
_stub("google_auth_oauthlib.flow", InstalledAppFlow=object)
_stub("googleapiclient")
_stub("googleapiclient.discovery", build=lambda *a, **k: None)

from clients.calendar_client import (
    CalendarClient,
    _absence_day_interval,
    _first_free_workday,
)

FAILURES = []

def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILURES.append(name)


# --- _absence_day_interval ---------------------------------------------
allday = {"start": {"date": "2026-07-20"}, "end": {"date": "2026-07-25"}}
check("all-day interval", _absence_day_interval(allday), (date(2026, 7, 20), date(2026, 7, 25)))

# Google OOO style: timed, midnight->midnight in event tz
ooo = {
    "start": {"dateTime": "2026-07-22T00:00:00+03:00"},
    "end": {"dateTime": "2026-07-23T00:00:00+03:00"},
}
check("midnight OOO interval", _absence_day_interval(ooo), (date(2026, 7, 22), date(2026, 7, 23)))

# Timed absence ending mid-day blocks no full day
doctor = {
    "start": {"dateTime": "2026-07-22T09:00:00+02:00"},
    "end": {"dateTime": "2026-07-22T14:00:00+02:00"},
}
check("partial day -> no interval", _absence_day_interval(doctor), None)

# --- _first_free_workday -------------------------------------------------
# 2026-07-22 is a Wednesday; 25/26 are Sat/Sun.
natalia = [
    (date(2026, 7, 22), date(2026, 7, 23)),
    (date(2026, 7, 23), date(2026, 7, 24)),
    (date(2026, 7, 24), date(2026, 7, 25)),
]
check("chain of daily events + weekend", _first_free_workday(date(2026, 7, 23), natalia), date(2026, 7, 27))

# Single event ending before the weekend: back Monday, not Saturday
check("weekend roll", _first_free_workday(date(2026, 7, 25), []), date(2026, 7, 27))

# Two weeks entered as two Mon-Fri events with the weekend gap in between
two_weeks = [
    (date(2026, 7, 20), date(2026, 7, 25)),
    (date(2026, 7, 27), date(2026, 7, 30)),  # Mon..Wed next week
]
check("weekend gap between events", _first_free_workday(date(2026, 7, 25), two_weeks), date(2026, 7, 30))

# Plain single-day absence mid-week stays unchanged
check("plain mid-week", _first_free_workday(date(2026, 7, 23), [(date(2026, 7, 22), date(2026, 7, 23))]), date(2026, 7, 23))

# --- check_vacation end-to-end (relative to real 'today') ----------------
today = datetime.now(timezone.utc).date()

def _ooo_event(day_offset_start, day_offset_end, tz="+03:00"):
    s = today + timedelta(days=day_offset_start)
    e = today + timedelta(days=day_offset_end)
    return {
        "eventType": "outOfOffice",
        "summary": "Vacation",
        "start": {"dateTime": f"{s}T00:00:00{tz}"},
        "end": {"dateTime": f"{e}T00:00:00{tz}", "timeZone": "Europe/Kyiv"},
    }

client = object.__new__(CalendarClient)
events = [_ooo_event(0, 1), _ooo_event(1, 2), _ooo_event(2, 3)]
client._fetch_absence_events = lambda: events

# expected: first free workday starting from today+3
expected = today + timedelta(days=3)
while expected.weekday() >= 5:
    expected += timedelta(days=1)
# NB: if today+... crosses no weekend this is just today+3
vac = client.check_vacation()
want_label = f"{expected.strftime('%b')} {expected.day}"
check("check_vacation chains 3 daily OOO events", (vac.summary, vac.back_label), ("Out of office", want_label))

# Same-day timed absence keeps the clock label
same_day = [{
    "eventType": "outOfOffice",
    "summary": "Out of office",
    "start": {"dateTime": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
    "end": {"dateTime": (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)).isoformat(), "timeZone": "Europe/Berlin"},
}]
client._fetch_absence_events = lambda: same_day
vac2 = client.check_vacation()
end_local = datetime.fromisoformat(same_day[0]["end"]["dateTime"]).astimezone(
    __import__("zoneinfo").ZoneInfo("Europe/Berlin")
)
if end_local.date() == datetime.now(__import__("zoneinfo").ZoneInfo("Europe/Berlin")).date():
    assert vac2.back_label.endswith("MUC"), vac2.back_label
    print(f"PASS: same-day timed keeps clock label: {vac2.back_label!r}")
else:
    print("SKIP: same-day test crosses midnight locally")

# No absence -> None
client._fetch_absence_events = lambda: []
check("no absence", client.check_vacation(), None)

print("\n%d failure(s)" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
