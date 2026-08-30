"""Point-in-time vintage handling.

The bug: normalize() collapsed each period to a single fact, keeping the most
recently filed one ("restatements supersede originals"). Combined with
truncation on `filed`, that inverted the point-in-time guarantee -- the
original disclosure disappeared and its period end inherited the restatement's
filing date.

Real case, ABT Q1 2012: filed 2012-05-08 at $9.457B, restated to $5.284B in the
2013-05-08 10-Q after the AbbVie spin-off, restated again in the 2014-02-21
10-K. The surviving row carried filed=2014-02-21, so as_of("2012-08-15") hid a
quarter that had been public for three months, and any baseline that did reach
it used a figure nobody could have seen. Across 150 filers this delayed first
scoreability by a median of 56 months.
"""
from __future__ import annotations

from ledgerline import derive, edgar, signals_v3, universe


def _abt_q1_2012() -> list[dict]:
    return [
        {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31",
         "value": 9_456_633_000.0, "filed": "2012-05-08", "form": "10-Q", "accession": "orig"},
        {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31",
         "value": 5_283_685_000.0, "filed": "2013-05-08", "form": "10-Q", "accession": "rs1"},
        {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31",
         "value": 5_284_000_000.0, "filed": "2014-02-21", "form": "10-K", "accession": "rs2"},
    ]


def test_every_vintage_is_retained():
    (row,) = derive.derive_quarterly(_abt_q1_2012())
    assert [v["filed"] for v in row["vintages"]] == ["2012-05-08", "2013-05-08", "2014-02-21"]


def test_top_level_row_is_the_latest_vintage():
    """Labels are allowed to look forward, so the default view is the restated
    one -- restatement is itself a deterioration criterion."""
    (row,) = derive.derive_quarterly(_abt_q1_2012())
    assert row["value"] == 5_284_000_000.0
    assert row["filed"] == "2014-02-21"


def test_as_of_returns_what_was_public_then_not_the_restatement():
    norm = {"revenue": derive.derive_quarterly(_abt_q1_2012())}
    snap = edgar.as_of(norm, "2012-08-15")
    assert snap["revenue"][0]["value"] == 9_456_633_000.0, "restated value leaked backwards"

    snap = edgar.as_of(norm, "2013-08-15")
    assert snap["revenue"][0]["value"] == 5_283_685_000.0

    snap = edgar.as_of(norm, "2014-06-01")
    assert snap["revenue"][0]["value"] == 5_284_000_000.0


def test_as_of_before_first_publication_hides_the_quarter_entirely():
    """The other direction still has to hold: no lookahead."""
    norm = {"revenue": derive.derive_quarterly(_abt_q1_2012())}
    assert edgar.as_of(norm, "2012-04-01") == {}


def test_quarter_is_visible_immediately_after_its_original_filing():
    """The regression that delayed scoreability: this quarter was public on
    2012-05-08 and must not wait for the 2014 10-K to appear."""
    norm = {"revenue": derive.derive_quarterly(_abt_q1_2012())}
    assert edgar.as_of(norm, "2012-05-08")["revenue"]


def test_unrevised_facts_collapse_to_one_vintage():
    """A quarter repeated unchanged as a comparative in four later filings
    stores one vintage, not four."""
    rows = [
        {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31", "value": 100.0,
         "filed": f, "form": "10-K", "accession": f}
        for f in ("2012-05-08", "2013-02-15", "2014-02-21", "2015-02-20")
    ]
    (row,) = derive.derive_quarterly(rows)
    assert len(row["vintages"]) == 1
    assert row["filed"] == "2012-05-08"


def test_derived_quarter_gets_a_vintage_when_both_inputs_are_restated_together():
    """Q2 = 6M - 3M. When a filer restates the whole year onto a new basis,
    both cumulatives move in the same filing and the difference is still
    arithmetic."""
    rows = [
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-03-31", "value": 100.0,
         "filed": "2012-05-08", "accession": "q1"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-06-30", "value": 250.0,
         "filed": "2012-08-08", "accession": "h1"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-03-31", "value": 90.0,
         "filed": "2013-08-08", "accession": "q1r"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-06-30", "value": 220.0,
         "filed": "2013-08-08", "accession": "h1r"},
    ]
    q2 = next(r for r in derive.derive_quarterly(rows) if r["end"] == "2012-06-30")
    assert q2["origin"] == "derived"
    assert derive.newest_at(q2["vintages"], "2012-09-01")["value"] == 150.0
    assert derive.newest_at(q2["vintages"], "2013-09-01")["value"] == 130.0


def test_one_sided_restatement_does_not_produce_a_derived_vintage():
    """Only one cumulative restated means the two are on different bases, and
    differencing them subtracts two different companies. Refuse."""
    rows = [
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-03-31", "value": 100.0,
         "filed": "2012-05-08", "accession": "q1"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-06-30", "value": 250.0,
         "filed": "2012-08-08", "accession": "h1"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-06-30", "value": 220.0,
         "filed": "2013-08-08", "accession": "h1r"},
    ]
    q2 = next(r for r in derive.derive_quarterly(rows) if r["end"] == "2012-06-30")
    assert [v["value"] for v in q2["vintages"]] == [150.0], "one-sided pair was differenced"
    assert derive.newest_at(q2["vintages"], "2013-09-01")["value"] == 150.0


def test_dltr_discontinued_operations_does_not_fabricate_negative_revenue():
    """The reproduced case. DLTR moved Family Dollar to discontinued operations
    and restated FY2022 revenue from 28,318M to 15,406M, without restating the
    9M cumulative. Differencing them gave Q4 = -5,196,300,000 -- negative
    revenue, carrying a legitimate filed date, which made DLTR a labeled
    positive in a quarter its revenue actually grew."""
    rows = [
        {"metric": "revenue", "start": "2022-01-30", "end": "2022-10-29",
         "value": 20_602_000_000.0, "filed": "2022-11-22", "accession": "9m"},
        {"metric": "revenue", "start": "2022-01-30", "end": "2023-01-28",
         "value": 28_318_200_000.0, "filed": "2023-03-10", "accession": "fy"},
        {"metric": "revenue", "start": "2022-01-30", "end": "2023-01-28",
         "value": 15_405_700_000.0, "filed": "2025-03-26", "accession": "fy-restated"},
    ]
    q4 = next(r for r in derive.derive_quarterly(rows, non_negative=True)
              if r["end"] == "2023-01-28")
    assert all(v["value"] > 0 for v in q4["vintages"]), "negative revenue emitted"
    assert q4["value"] == 7_716_200_000.0
    assert derive.newest_at(q4["vintages"], "2026-01-01")["value"] == 7_716_200_000.0


def test_derived_quarter_is_not_public_before_both_inputs_are():
    rows = [
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-03-31", "value": 100.0,
         "filed": "2012-05-08", "accession": "q1"},
        {"metric": "ocf", "start": "2012-01-01", "end": "2012-06-30", "value": 250.0,
         "filed": "2012-08-08", "accession": "q2"},
    ]
    norm = {"operating_cash_flow": derive.derive_quarterly(rows)}
    assert "2012-06-30" not in {r["end"] for r in edgar.as_of(norm, "2012-07-01")
                                .get("operating_cash_flow", [])}
    assert "2012-06-30" in {r["end"] for r in edgar.as_of(norm, "2012-08-08")
                            ["operating_cash_flow"]}


def test_newest_at_picks_the_latest_vintage_not_past_the_cutoff():
    v = [{"filed": "2012-05-08", "value": 1}, {"filed": "2013-05-08", "value": 2},
         {"filed": "2014-02-21", "value": 3}]
    assert derive.newest_at(v, "2012-01-01") is None
    assert derive.newest_at(v, "2012-05-08")["value"] == 1
    assert derive.newest_at(v, "2013-12-31")["value"] == 2
    assert derive.newest_at(v, "2099-01-01")["value"] == 3


# ------------------------------------------- consumers must read vintages


def test_history_counts_publication_events_not_collapsed_filed_dates():
    """signals_v3._history() built its baseline from distinct top-level `filed`
    values. Those collapse to the latest vintage, so a filer with 73 quarters
    of history reported 33 filing dates and then "6q of 12" at a 2017 cutoff."""
    rows = []
    for i, (start, end, filed) in enumerate([
        (f"20{y:02d}-01-01", f"20{y:02d}-03-31", f"20{y:02d}-05-08") for y in range(11, 25)
    ]):
        rows.append({"metric": "revenue", "start": start, "end": end,
                     "value": 100.0 + i, "filed": filed, "accession": f"a{i}"})
        # each quarter is repeated unchanged-in-date but restated in a later 10-K
        rows.append({"metric": "revenue", "start": start, "end": end,
                     "value": 90.0 + i, "filed": "2025-02-20", "accession": f"r{i}"})
    norm = {"revenue": derive.derive_quarterly(rows)}
    hist = signals_v3._history("T", "1", norm, "2024-12-31")
    assert len(hist) >= signals_v3.MIN_HISTORY, f"only {len(hist)} baseline quarters"


def test_scoreable_from_dates_a_quarter_to_its_first_publication():
    """universe.scoreable_from() read the top-level `filed`, dating every
    quarter to whichever later filing last restated it."""
    rows = []
    for i in range(20):
        y, q = 2012 + i // 4, i % 4
        start = f"{y}-{q * 3 + 1:02d}-01"
        end = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"][q]
        rows.append({"metric": "revenue", "start": start, "end": end, "value": 100.0 + i,
                     "filed": f"{y}-{q * 3 + 5:02d}-08", "accession": f"a{i}"})
        rows.append({"metric": "revenue", "start": start, "end": end, "value": 80.0 + i,
                     "filed": "2025-02-20", "accession": f"r{i}"})
    norm = {"revenue": derive.derive_quarterly(rows)}
    start = universe.scoreable_from(norm)
    assert start is not None and start < "2017-01-01", f"dated to {start}, not first publication"
