"""
52/53-week fiscal calendar tests, in both directions: the 14-week-vs-13-week
comparison artifact is refused, AND the four things that already handled these
calendars correctly are pinned so nobody "fixes" them. FINDINGS §5's standing
lesson applies: fixture realism is load-bearing -- a fixture whose quarters
all end on month-end cannot detect a 52/53-week defect, which is why
week_53_facts() does not.
"""
from __future__ import annotations

from datetime import date, timedelta

from ledgerline import derive, edgar, fiscal, reasons, signals
from tests.unit.test_ingestion import facts_doc


def week_53_facts(concept: str, first_start: date, n_quarters: int,
                  values: list[float], long_at: tuple[int, ...] = ()) -> list[dict]:
    """Quarterly duration facts on an AAPL/NVDA-shaped 52/53-week calendar:
    period ends drift off month-end, and the quarters at indexes in `long_at`
    carry a 14th week (98-day span instead of 91)."""
    rows = []
    qstart = first_start
    for i in range(n_quarters):
        span = 98 if i in long_at else 91
        qend = qstart + timedelta(days=span)
        rows.append({
            "start": qstart.isoformat(), "end": qend.isoformat(),
            "val": values[i], "form": "10-K" if i % 4 == 3 else "10-Q",
            "filed": (qend + timedelta(days=40)).isoformat(),
            "fy": qend.year, "fp": f"Q{i % 4 + 1}", "accn": f"{concept}-{i}",
        })
        qstart = qend + timedelta(days=1)
    return rows


def week_53_norm(n_quarters: int = 24, long_at: tuple[int, ...] = ()) -> dict:
    rev = [1000.0 * (1.02 ** i) for i in range(n_quarters)]
    cor = [600.0 * (1.02 ** i) for i in range(n_quarters)]
    facts = facts_doc({
        "Revenues": week_53_facts("rev", date(2015, 3, 29), n_quarters, rev, long_at),
        "CostOfRevenue": week_53_facts("cor", date(2015, 3, 29), n_quarters, cor, long_at),
    })
    return edgar.normalize("0000000001", facts)


# ------------------------------------------------------------------ detection


def test_calendar_filer_is_classified_as_calendar():
    """Month-end period ends with 89-92 day spans are a calendar filer."""
    rows = [
        {"start": f"{y}-{s}", "end": f"{y}-{e}"}
        for y in (2020, 2021, 2022)
        for s, e in (("01-01", "03-31"), ("04-01", "06-30"),
                     ("07-01", "09-30"), ("10-01", "12-31"))
    ]
    assert fiscal.profile(rows).calendar == fiscal.CALENDAR


def test_52_53_week_filer_is_detected_from_non_month_end_period_ends():
    """AAPL-shaped ends (like 2015-06-28, 2015-09-27) classify as 52/53-week
    with no filer flag, no network call and no hardcoded ticker list."""
    norm = week_53_norm(16)
    prof = fiscal.profile(norm["revenue"])
    assert prof.calendar == fiscal.WEEK_52_53
    assert prof.n_quarters == 16


def test_the_53rd_week_quarter_is_identified_by_its_span():
    """A 98-day quarter is long; a 92-day one is not. Pins
    LONG_QUARTER_MIN_DAYS=95 in the gap between calendar drift (at most ~3
    days) and a 7-day extra week."""
    assert fiscal.is_long_quarter({"start": "2023-07-02", "end": "2023-10-08"})
    assert not fiscal.is_long_quarter({"start": "2023-01-01", "end": "2023-04-03"})
    assert fiscal.LONG_QUARTER_MIN_DAYS == 95


def test_calendar_quarter_length_variation_is_not_a_span_mismatch():
    """An 89-day Q1 and a 92-day Q3 are comparable. Pins
    SPAN_TOLERANCE_DAYS=4: widening it to 7 or more would silently readmit
    the 14-week artifact the guard exists to refuse."""
    q1 = {"start": "2023-01-01", "end": "2023-03-31"}   # 89 days
    q3 = {"start": "2022-07-01", "end": "2022-10-01"}   # 92 days
    assert fiscal.comparable(q1, q3)
    assert fiscal.SPAN_TOLERANCE_DAYS == 4
    assert fiscal.SPAN_TOLERANCE_DAYS < 7
    long_q = {"start": "2023-07-02", "end": "2023-10-08"}  # 98 days
    assert not fiscal.comparable(q1, long_q)


def test_fiscal_profile_returns_unknown_rather_than_guessing():
    """A PIT-only or too-short series yields UNKNOWN, not a fabricated
    calendar -- the same register as every other refusal in the codebase."""
    assert fiscal.profile([]).calendar == fiscal.UNKNOWN
    assert fiscal.profile([{"end": "2023-03-31"}] * 3).calendar == fiscal.UNKNOWN


def test_year_ago_search_window_mirrors_yoy_at():
    """fiscal.year_ago and signals.yoy_at must search the same elapsed-day
    window. If yoy_at's window is ever retuned, this fails and fiscal.py is
    retuned with it -- the two cannot drift silently."""
    assert fiscal.YEAR_AGO_MIN_DAYS == signals.YOY_MIN_DAYS
    assert fiscal.YEAR_AGO_MAX_DAYS == signals.YOY_MAX_DAYS


# ------------------------------------------------------------- the yoy guard


def test_yoy_at_refuses_a_14_week_versus_13_week_comparison():
    """With the guard on, the comparison that carries a ~7% calendar artifact
    returns None where it previously returned a number."""
    rows = week_53_norm(24, long_at=(23,))["revenue"]
    assert signals.yoy_at(rows, len(rows) - 1, require_comparable_span=True) is None


def test_yoy_at_default_keeps_the_as_reported_comparison():
    """The guard is opt-in. label.py calls yoy_at with the default, and the
    outcome label must stay byte-identical -- the 52/53-week label guard was
    measured at ~0.5% of trips and deliberately cut, because editing a
    labeling criterion after the holdout was scored breaks the one clean
    measurement the project has."""
    rows = week_53_norm(24, long_at=(23,))["revenue"]
    val = signals.yoy_at(rows, len(rows) - 1)
    assert val is not None  # as reported, artifact included


def test_yoy_at_still_matches_a_52_week_year_ago_quarter():
    """The 330-400 elapsed-day match shipped earlier must not regress: a
    year-ago gap stretched to ~375 days by an intervening 53rd week still
    resolves, because both compared quarters are 13 weeks."""
    rows = week_53_norm(24, long_at=(18,))["revenue"]
    val = signals.yoy_at(rows, len(rows) - 1, require_comparable_span=True)
    assert val is not None


def test_revenue_accel_abstains_on_a_span_mismatch():
    """Through diagnose(), a 14-week current quarter leaves revenue_accel
    absent WITH its reason recorded. Measured: median |revenue_accel| is
    0.0675 in quarters whose YoY chain touches a 14-week quarter versus
    0.0417 in clean ones, against a scale floor of 0.02 -- the artifact reads
    as signal, so it is refused."""
    norm = week_53_norm(24, long_at=(23,))
    d = signals.diagnose("TST", "0000000001", norm)
    assert d.revenue_accel is None
    assert d.reasons["revenue_accel"] == reasons.FISCAL_SPAN_MISMATCH
    assert "53rd fiscal week" in d.reason_detail["revenue_accel"]


def test_gross_margin_is_unaffected_by_the_53rd_week():
    """Gross margin is a ratio: the 14th week adds revenue and cost
    proportionally and cancels. Pinned so nobody adds a guard that is not
    needed."""
    norm = week_53_norm(24, long_at=(23,))
    d = signals.diagnose("TST", "0000000001", norm)
    assert d.gross_margin is not None
    assert "gross_margin" not in d.reasons


# --------------------------------------- things that already work, pinned


def test_ttm_spans_a_53_week_year_without_refusing():
    """A 371-day fiscal year sits inside derive's TTM_MIN/MAX window; derive.py
    needs no fiscal change and must not get one."""
    rows = week_53_norm(8, long_at=(7,))["revenue"]
    assert derive.ttm(rows) is not None


def test_is_contiguous_tolerates_a_98_day_quarter_gap():
    """derive's 91 +/- 20 day contiguity tolerance already admits a 14-week
    quarter; pinned as already sufficient."""
    rows = week_53_norm(8, long_at=(5,))["revenue"]
    assert derive.is_contiguous(rows[-4:])
