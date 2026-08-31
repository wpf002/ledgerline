"""
The numbers themselves: what the arithmetic adds up, and what it cites.

Six defects, all reproduced against the live cache before they were fixed and
each pinned here by the reproduction that found it.

  1. total_debt double-counted current maturities whenever a filer tagged the
     all-in us-gaap:LongTermDebt but not LongTermDebtNoncurrent. Jefferies at
     2012-12-31 read 1,799,264,000 against a true 1,358,695,000.
  2. restate.diff called a component being tagged for the first time a
     material revision, and took the event's `form` from a filing that did not
     create the vintage.
  3. deferred_vs_revenue_gap and net_debt_to_ttm_ocf paired a stale balance
     sheet with a current flow, which is the defect the dso/dio alignment
     guard already existed to refuse.
  4. derived_fraction shipped as 0.0 -- the positive claim that nothing was
     differenced -- on the coverage-gate path, which returns before diagnose()
     ever measures it.
  5. The accession trace keyed on the coverage-gate table, so six of thirteen
     diagnostics published a strict subset of the filings their arithmetic
     read and the reading was still labelled TRACED.
  6. The coverage record's fiscal profile was computed from the untruncated
     norm while the record was stamped with the cutoff, so a point-in-time
     record carried period ends that had not happened yet.
"""
from __future__ import annotations

import sqlite3

from ledgerline import coverage, edgar, fiscal, provenance, restate, signals, signals_v3
from ledgerline.signals import series
from tests.unit.test_ingestion import discrete_facts, facts_doc, ytd_facts

# --------------------------------------------- 1. the total_debt double-count


def _debt_facts(rows: dict[str, list[tuple[str, float, str, str, str]]]) -> dict:
    """concept -> [(end, value, filed, form, accession)] as a facts document."""
    return facts_doc({
        concept: [{"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
                  for end, val, filed, form, accn in items]
        for concept, items in rows.items()
    })


def test_all_in_long_term_debt_is_not_summed_with_its_own_components():
    """Jefferies (CIK 0000096223) at period end 2012-12-31, verbatim.

    The 10-K of 2013-02-25 tags noncurrent 918,126,000 plus current maturities
    440,569,000. The 10-Q of 2013-05-09 tags only LongTermDebt 1,358,695,000 --
    the same total, all in one concept -- and the old resolution treated it as
    the NONCURRENT component and added the current maturities on top, giving
    1,799,264,000 for a figure the filer never revised.
    """
    facts = _debt_facts({
        "LongTermDebtNoncurrent": [
            ("2012-12-31", 918_126_000.0, "2013-02-25", "10-K", "10k"),
        ],
        "LongTermDebt": [
            ("2012-12-31", 1_358_695_000.0, "2013-02-25", "10-K", "10k"),
            ("2012-12-31", 1_358_695_000.0, "2013-05-09", "10-Q", "10q"),
        ],
        "LongTermDebtCurrent": [
            ("2012-12-31", 440_569_000.0, "2013-02-25", "10-K", "10k"),
            ("2012-12-31", 440_569_000.0, "2013-05-09", "10-Q", "10q"),
        ],
    })
    (row,) = edgar.normalize("0000096223", facts)["total_debt"]
    assert [v["value"] for v in row["vintages"]] == [1_358_695_000.0, 1_358_695_000.0]
    assert row["vintages"][1]["concept"] == "LongTermDebt"

    snap = edgar.as_of({"total_debt": [row]}, "2013-06-30")
    assert snap["total_debt"][0]["value"] == 1_358_695_000.0


def test_double_count_needs_no_restatement_to_happen():
    """WBD (CIK 0001437107) at 2024-06-30: ONE accession tags LongTermDebt
    40,958,000,000 and LongTermDebtCurrent 3,669,000,000 and never tags the
    noncurrent split. One vintage, one filing, and the old code still read
    44,627,000,000."""
    facts = _debt_facts({
        "LongTermDebt": [
            ("2024-06-30", 40_958_000_000.0, "2024-08-07", "10-Q", "wbd-q2"),
        ],
        "LongTermDebtCurrent": [
            ("2024-06-30", 3_669_000_000.0, "2024-08-07", "10-Q", "wbd-q2"),
        ],
    })
    (row,) = edgar.normalize("0001437107", facts)["total_debt"]
    assert row["value"] == 40_958_000_000.0


def test_short_term_borrowings_are_still_added_to_the_all_in_tag():
    """The guard is narrow on purpose. Commercial paper and revolver draws are
    not maturities of long-term debt, so ShortTermBorrowings stays in the sum
    even when LongTermDebt is the all-in figure."""
    facts = _debt_facts({
        "LongTermDebt": [("2019-12-31", 900.0, "2020-02-20", "10-K", "a")],
        "LongTermDebtCurrent": [("2019-12-31", 100.0, "2020-02-20", "10-K", "a")],
        "ShortTermBorrowings": [("2019-12-31", 50.0, "2020-02-20", "10-K", "a")],
    })
    (row,) = edgar.normalize("0000000001", facts)["total_debt"]
    assert row["value"] == 950.0
    assert row["concept"] == "LongTermDebt+ShortTermBorrowings"


# ------------------------------------ 2. component arrivals are not revisions


def _hancock() -> dict:
    """Hancock Whitney (CIK 0000750577), total_debt at 2010-12-31.

    LongTermDebt is 376,000 in the 10-K of 2011-02-28 and unchanged in every
    filing after it. The 10-K/A of 2012-02-29 tags ShortTermBorrowings
    364,676,000 for the same period end for the first time.
    """
    facts = _debt_facts({
        "LongTermDebt": [
            ("2010-12-31", 376_000.0, "2011-02-28", "10-K", "orig"),
            ("2010-12-31", 376_000.0, "2012-02-29", "10-K/A", "amend"),
        ],
        "ShortTermBorrowings": [
            ("2010-12-31", 364_676_000.0, "2012-02-29", "10-K/A", "amend"),
        ],
    })
    return edgar.normalize("0000750577", facts)


def test_a_newly_tagged_component_is_not_a_restatement():
    """diff() reported prior_value 376,000 -> value 365,052,000, rel_change
    0.99897, material=True: a 971x revision of a figure nobody revised."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(edgar.SCHEMA)
    assert restate.diff(conn, "0000750577", _hancock()) == []


def test_a_real_revision_alongside_a_new_component_still_reports():
    """The suppression is not a blanket one: it fires only when every concept
    present in both vintages held its value."""
    facts = _debt_facts({
        "LongTermDebt": [
            ("2010-12-31", 376_000.0, "2011-02-28", "10-K", "orig"),
            ("2010-12-31", 500_000.0, "2012-02-29", "10-K/A", "amend"),
        ],
        "ShortTermBorrowings": [
            ("2010-12-31", 364_676_000.0, "2012-02-29", "10-K/A", "amend"),
        ],
    })
    conn = sqlite3.connect(":memory:")
    conn.executescript(edgar.SCHEMA)
    (event,) = restate.diff(conn, "0000750577",
                            edgar.normalize("0000750577", facts))
    assert event.prior_value == 376_000.0


def test_a_summed_vintage_takes_its_form_from_the_filing_that_created_it():
    """The 2012-02-29 vintage exists because a 10-K/A landed that day, and the
    event reported form='10-K', on_amendment=False -- the primary component's
    newest vintage was a year old and belonged to a different filing. Measured
    over a 300-filer sample, 372 of 17,027 summed vintages (2.2%) carried a
    form from a filing that did not create them."""
    (row,) = _hancock()["total_debt"]
    assert [v["filed"] for v in row["vintages"]] == ["2011-02-28", "2012-02-29"]
    assert [v["form"] for v in row["vintages"]] == ["10-K", "10-K/A"]


# --------------------------------- 3. a stale balance against a current flow


def _q(end: str, start: str, value: float, accn: str = "acc") -> dict:
    return {"metric": "m", "kind": "Q", "start": start, "end": end, "value": value,
            "origin": "reported", "form": "10-Q", "filed": end, "sources": [accn]}


def _bal(end: str, value: float, accn: str = "acc") -> dict:
    return {"metric": "m", "kind": "PIT", "start": None, "end": end, "value": value,
            "origin": "reported", "form": "10-Q", "filed": end, "sources": [accn]}


def _wbd_norm(deferred_ends: tuple[str, str]) -> dict:
    return {
        "revenue": [_q("2012-06-30", "2012-04-01", 1_126_000_000.0),
                    _q("2013-06-30", "2013-04-01", 1_467_000_000.0)],
        "deferred_revenue": [_bal(deferred_ends[0], 93_000_000.0, "10k-2009"),
                             _bal(deferred_ends[1], 91_000_000.0, "10k-2009")],
    }


def test_deferred_gap_abstains_when_the_balance_is_years_from_the_quarter():
    """WBD at cutoff 2013-08-15: the newest deferred-revenue balances in the
    snapshot were 2008-12-31 and 2009-12-31, so a 2009-vs-2008 change of -2.2%
    was set against revenue growth for the quarter ending 2013-06-30 and
    published as a 4.26-sigma 'Forward bookings breaking from trend'."""
    d = signals.diagnose("WBD", "0001437107", _wbd_norm(("2008-12-31", "2009-12-31")))
    assert d.period == "2013-06-30"
    assert d.deferred_rev_yoy is not None  # the yoy itself is computable
    assert d.deferred_vs_revenue_gap is None
    assert d.reasons["deferred_vs_revenue_gap"] == "PERIOD_MISALIGNED"


def test_deferred_gap_still_computes_when_the_balance_is_current():
    d = signals.diagnose("WBD", "0001437107", _wbd_norm(("2012-06-30", "2013-06-30")))
    assert d.deferred_vs_revenue_gap is not None


def _chh_norm(debt_end: str) -> dict:
    ocf = [_q("2012-09-30", "2012-07-01", 40_000_000.0),
           _q("2012-12-31", "2012-10-01", 40_000_000.0),
           _q("2013-03-31", "2013-01-01", 40_000_000.0),
           _q("2013-06-30", "2013-04-01", 32_181_000.0)]
    return {
        "revenue": [_q("2012-06-30", "2012-04-01", 180_000_000.0),
                    _q("2013-06-30", "2013-04-01", 190_000_000.0)],
        "operating_cash_flow": ocf,
        "cash": [_bal("2013-06-30", 143_790_000.0)],
        "total_debt": [_bal(debt_end, 652_400_000.0, "10q-2012")],
    }


def test_net_debt_abstains_when_the_debt_balance_is_a_year_stale():
    """CHH at cutoff 2013-08-15 divided a 2012-06-30 total_debt balance -- 365
    days older than the assessed quarter -- by a cash-flow year ending
    2013-06-30, and published NET_DEBT_TO_TTM_OCF at 3.34. pit() returns the
    newest balance, which is not the same as a recent one."""
    d = signals.diagnose("CHH", "0001046311", _chh_norm("2012-06-30"))
    assert d.net_debt is None, "a 2012 debt minus 2013 cash is in no filing"
    assert d.net_debt_to_ttm_ocf is None
    assert d.reasons["net_debt_to_ttm_ocf"] == "PERIOD_MISALIGNED"


def test_net_debt_still_computes_when_the_balances_are_current():
    d = signals.diagnose("CHH", "0001046311", _chh_norm("2013-06-30"))
    assert d.net_debt == 652_400_000.0 - 143_790_000.0
    assert d.net_debt_to_ttm_ocf is not None


# ------------------------- 4. derived_fraction is None when it was not measured


def _coverage_gated_filer() -> dict:
    """Revenue over four years, operating cash flow over the first two: OCF
    coverage 50%, well under derive.COVERAGE_MIN, so evaluate() returns on the
    coverage gate -- before diagnose() runs."""
    rev = [1000.0 + i for i in range(16)]
    ocf = [200.0 + i for i in range(8)]
    return edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 4, rev),
        "NetIncomeLoss": discrete_facts("ni", 2016, 4, rev),
        "NetCashProvidedByUsedInOperatingActivities": ytd_facts("ocf", 2016, 2, ocf),
    }))


def test_coverage_gated_filer_reports_no_derived_fraction_rather_than_zero():
    """0.0 is not a neutral placeholder: it is the claim that none of this
    filer's quarterly figures were worked out by differencing YTD reports.
    Measured at as_of 2026-08-31, all 104 REQUIRED_COVERAGE_LOW filers shipped
    0.0 while diagnose() could compute a value for every one of them, 96 of
    them nonzero and ACT at 0.491 -- just under the DERIVED_FRACTION_HIGH
    tripwire it would have tripped."""
    norm = _coverage_gated_filer()
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2021-06-01", norm=norm)
    assert res["reason_code"] == "REQUIRED_COVERAGE_LOW"
    assert res["derived_fraction"] is None

    measured = signals.diagnose("TEST", "0000000001",
                                edgar.as_of(norm, "2021-06-01")).derived_fraction
    assert measured > 0.0, "the value was computable all along"


def test_coverage_record_carries_the_missing_derived_fraction_through():
    fc = coverage.filer_coverage("0000000001", "TEST", _coverage_gated_filer(),
                                 "2021-06-01")
    assert fc.derived_fraction is None


# --------------------------------- 5. the trace covers what the arithmetic read


def test_every_diagnostic_declares_all_of_its_inputs_for_provenance():
    """PROVENANCE_INPUTS is a superset of the coverage gate's table and covers
    every tracked diagnostic. The two were one table, and the trace keyed on
    the gate's."""
    assert set(signals_v3.PROVENANCE_INPUTS) == set(signals_v3.TRACKED)
    for name, gated in signals_v3.DIAGNOSTIC_INPUTS.items():
        assert set(gated) <= set(signals_v3.PROVENANCE_INPUTS[name]), name
    assert "deferred_revenue" in signals_v3.PROVENANCE_INPUTS["deferred_vs_revenue_gap"]
    assert "receivables" in signals_v3.PROVENANCE_INPUTS["dso"]
    assert "inventory" in signals_v3.PROVENANCE_INPUTS["dio"]
    assert "total_assets" in signals_v3.PROVENANCE_INPUTS["accrual_ratio"]
    for metric in ("cash", "total_debt"):
        assert metric in signals_v3.PROVENANCE_INPUTS["net_debt_to_ttm_ocf"]


def test_the_trace_cites_the_balance_sheet_filing_not_only_the_flow_one():
    """WBD at 2013-08-15 published sources ['0001437107-13-000031'] for
    DEFERRED_VS_REVENUE_GAP; the deferred-revenue balances behind half of that
    number came from accession 0001193125-10-035850, which appeared nowhere in
    the flag's trace. Over a 300-filer sample the completed table traces 1,171
    metrics across 546 fired flags where the gate's table traced 837."""
    snap = {
        "revenue": [_q("2013-06-30", "2013-04-01", 1_467_000_000.0, "0001437107-13-000031")],
        "deferred_revenue": [_bal("2013-06-30", 91_000_000.0, "0001193125-10-035850")],
    }
    reading = provenance.reading_trace(snap, "2013-06-30",
                                       [{"code": "DEFERRED_VS_REVENUE_GAP"}])
    traced = reading["flags"]["deferred_vs_revenue_gap"]
    assert set(traced) == {"revenue", "deferred_revenue"}
    assert traced["deferred_revenue"]["sources"] == ["0001193125-10-035850"]


def test_an_untraceable_balance_sheet_input_can_now_be_seen():
    """The UNTRACED abstention was structurally blind to the omitted half: the
    input with no accession was not in the list label() was handed, so the
    reading came back TRACED however untraceable that half was."""
    snap = {
        "revenue": [_q("2013-06-30", "2013-04-01", 1.0, "0001437107-13-000031")],
        "deferred_revenue": [{**_bal("2013-06-30", 91.0), "sources": []}],
    }
    reading = provenance.reading_trace(snap, "2013-06-30",
                                       [{"code": "DEFERRED_VS_REVENUE_GAP"}])
    assert provenance.label(reading, 0.0)[0] == "PARTIAL"


# ---------------------------- 6. a point-in-time record holds no future quarter


def test_the_fiscal_profile_comes_from_the_same_truncation_as_everything_else():
    """BKE's record stamped 2014-05-15 listed long quarters ending 2018-02-03
    and 2024-02-03 -- two period ends that had not happened at the stamped
    date. Across the first 80 watched filers, 78 stored profiles differed from
    the point-in-time one and 26 differed in the calendar label itself, which
    is the 52/53-week census the dashboard reports per date."""
    norm = edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 4, [1000.0 + i for i in range(16)]),
        "NetIncomeLoss": discrete_facts("ni", 2016, 4, [100.0 + i for i in range(16)]),
    }))
    cutoff = "2018-01-01"
    fc = coverage.filer_coverage("0000000001", "TEST", norm, cutoff)

    pit = fiscal.profile(series(edgar.as_of(norm, cutoff), "revenue", "Q")).as_dict()
    assert fc.fiscal == pit
    assert fc.fiscal["n_quarters"] < fiscal.profile(
        series(norm, "revenue", "Q")).n_quarters
    assert all(end <= cutoff for end in fc.fiscal["long_quarters"])
