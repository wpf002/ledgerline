"""
52/53-week fiscal calendars: detection and span comparison.

NOT a redo of the yoy elapsed-days fix -- signals.yoy_at already finds the
year-ago quarter across a 371-day fiscal year. This module handles the one
thing that fix did not: a 14-week quarter being COMPARED against a 13-week
quarter as if the two measured the same span of business. Measured on the
cache: 17-20% of filers run a 52/53-week calendar, the 45 detected long
quarters carry a median +7.4% revenue lift over their neighbours, and median
|revenue_accel| is 62% higher in quarters whose YoY chain touches a 14-week
quarter -- against a scale floor of 0.02, so the artifact reads as signal.

NOTHING IS RESCALED. Multiplying a 14-week quarter by 13/14 would invent a
number the filer never reported, which the house rule forbids. The options are
report-as-is or abstain, and the repo's standing answer for a diagnostic input
is abstain (the caller decides; see signals.yoy_at's require_comparable_span).

Detection is on period ends, not a filer flag: a 52/53-week filer's quarters
do not end on the last day of a month. Deterministic, no network, and it
agreed with the span test on 14 of 16 detected filers in a 91-filer sample.
PROVISIONAL: the detection constants below were set from that 91-filer sample,
not the universe; the first full-universe dashboard run replaces the numbers.
"""
from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date

NOMINAL_QUARTER_DAYS = 91

# A 14th week is 7 days; ordinary calendar-quarter variation is at most ~3 days
# (89 to 92 -- February, leap years). LONG_QUARTER_MIN_DAYS = 95 sits in the
# gap and SPAN_TOLERANCE_DAYS = 4 separates the two cleanly. Both are pinned by
# tests so nobody widens the tolerance past 7 and silently readmits the
# artifact.
LONG_QUARTER_MIN_DAYS = 95
SPAN_TOLERANCE_DAYS = 4

# Fraction of period ends that must fall on a month-end for the filer to be
# called a calendar filer. 0.5 rather than 1.0 because one 52/53-week transition
# year in an otherwise calendar history should not reclassify the filer.
MONTH_END_FRACTION = 0.5

CALENDAR = "calendar"
WEEK_52_53 = "52-53-week"
UNKNOWN = "unknown"

# Deliberately the same numbers as signals.YOY_MIN_DAYS / YOY_MAX_DAYS. A test
# pins the equality so the two search windows cannot drift apart -- if yoy_at's
# window is ever retuned, that test fails and this file is retuned with it.
YEAR_AGO_MIN_DAYS = 330
YEAR_AGO_MAX_DAYS = 400


def span_days(row: dict) -> int | None:
    """Days a duration fact covers, or None when the row carries no start
    (point-in-time series). None is the honest answer -- a span that cannot be
    computed must not be guessed at."""
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def is_long_quarter(row: dict) -> bool:
    """True when this quarter demonstrably carries a 14th week."""
    span = span_days(row)
    return span is not None and span >= LONG_QUARTER_MIN_DAYS


def comparable(a: dict, b: dict, tolerance_days: int = SPAN_TOLERANCE_DAYS) -> bool:
    """True when two duration rows cover close enough spans to be compared.

    An UNKNOWN span passes: refusing every comparison whose span cannot be
    measured would blank most PIT-derived series for a defect that cannot be
    demonstrated. The guard refuses only a mismatch it can prove.
    """
    sa, sb = span_days(a), span_days(b)
    if sa is None or sb is None:
        return True
    return abs(sa - sb) <= tolerance_days


def _is_month_end(iso: str) -> bool:
    d = date.fromisoformat(iso)
    return d.day == calendar.monthrange(d.year, d.month)[1]


@dataclass(frozen=True)
class FiscalProfile:
    calendar: str                 # CALENDAR | WEEK_52_53 | UNKNOWN
    fye_month: int | None         # month of the latest fiscal year end seen
    n_quarters: int
    month_end_fraction: float
    long_quarters: tuple[str, ...]  # period ends of detected 14-week quarters

    def as_dict(self) -> dict:
        return asdict(self)


def profile(rows: list[dict]) -> FiscalProfile:
    """Classify a filer's quarterly series. Returns UNKNOWN rather than
    guessing when the series is too short to judge -- same register as every
    other refusal in the codebase."""
    ends = [r["end"] for r in rows if r.get("end")]
    if len(ends) < 4:
        return FiscalProfile(UNKNOWN, None, len(ends), 0.0, ())
    on_month_end = sum(1 for e in ends if _is_month_end(e))
    frac = on_month_end / len(ends)
    longs = tuple(r["end"] for r in rows if is_long_quarter(r))
    kind = CALENDAR if frac >= MONTH_END_FRACTION else WEEK_52_53
    fye = date.fromisoformat(max(ends)).month
    return FiscalProfile(kind, fye, len(ends), round(frac, 3), longs)


def year_ago(rows: list[dict], idx: int) -> tuple[int | None, bool]:
    """(index of the year-ago row, spans comparable) -- or (None, False).

    Deliberately mirrors signals.yoy_at's backward elapsed-days search and
    returns the INDEX rather than a ratio, so the caller decides what to do
    about an incomparable span instead of this module deciding for it.
    """
    if not 0 <= idx < len(rows):
        return None, False
    cur = rows[idx]
    cur_d = date.fromisoformat(cur["end"])
    for i in range(idx - 1, -1, -1):
        gap = (cur_d - date.fromisoformat(rows[i]["end"])).days
        if YEAR_AGO_MIN_DAYS <= gap <= YEAR_AGO_MAX_DAYS:
            return i, comparable(cur, rows[i])
    return None, False
