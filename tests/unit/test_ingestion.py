"""
Regression tests. Each one reproduces a bug documented in FINDINGS.md and
asserts the fix. No network -- fixtures are synthetic XBRL-shaped payloads.
"""
from __future__ import annotations

import pytest

from ledgerline import derive, edgar, signals

# --------------------------------------------------------------- fixtures


def ytd_facts(concept: str, start_year: int, years: int, quarterly: list[float],
              fy_start_month: int = 1) -> list[dict]:
    """Build cash-flow-style facts the way most filers actually tag them:
    cumulative year-to-date in 10-Qs, full year in the 10-K. Only fiscal Q1 has
    a ~90-day span -- which is exactly what the old 80-100 day filter kept."""
    out = []
    for y in range(start_year, start_year + years):
        fy_start = f"{y}-{fy_start_month:02d}-01"
        ends = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"]
        filed = [f"{y}-05-10", f"{y}-08-10", f"{y}-11-10", f"{y + 1}-02-20"]
        cum = 0.0
        for i, (end, fdate) in enumerate(zip(ends, filed, strict=True)):
            cum += quarterly[(y - start_year) * 4 + i]
            out.append({
                "start": fy_start, "end": end, "val": cum,
                "form": "10-K" if i == 3 else "10-Q",
                "filed": fdate, "fy": y, "fp": f"Q{i + 1}",
                "accn": f"{concept}-{y}-Q{i + 1}",
            })
    return out


def discrete_facts(concept: str, start_year: int, years: int, quarterly: list[float]) -> list[dict]:
    """Filers that tag genuine 3-month durations (typical for revenue)."""
    out = []
    starts = ["01-01", "04-01", "07-01", "10-01"]
    ends = ["03-31", "06-30", "09-30", "12-31"]
    filed = ["05-10", "08-10", "11-10", "02-20"]
    for y in range(start_year, start_year + years):
        for i in range(4):
            fy = y + 1 if i == 3 else y
            out.append({
                "start": f"{y}-{starts[i]}", "end": f"{y}-{ends[i]}",
                "val": quarterly[(y - start_year) * 4 + i],
                "form": "10-K" if i == 3 else "10-Q",
                "filed": f"{fy}-{filed[i]}", "fy": y, "fp": f"Q{i + 1}",
                "accn": f"{concept}-{y}-Q{i + 1}",
            })
    return out


def pit_facts(concept: str, start_year: int, years: int, values: list[float]) -> list[dict]:
    out = []
    ends = ["03-31", "06-30", "09-30", "12-31"]
    filed = ["05-10", "08-10", "11-10", "02-20"]
    for y in range(start_year, start_year + years):
        for i in range(4):
            fy = y + 1 if i == 3 else y
            out.append({
                "end": f"{y}-{ends[i]}", "val": values[(y - start_year) * 4 + i],
                "form": "10-K" if i == 3 else "10-Q",
                "filed": f"{fy}-{filed[i]}", "fy": y, "fp": f"Q{i + 1}",
                "accn": f"{concept}-{y}-Q{i + 1}",
            })
    return out


def facts_doc(mapping: dict[str, list[dict]], unit: str = "USD") -> dict:
    return {c: {"units": {unit: rows}} for c, rows in mapping.items()}


# ------------------------------------------------- FINDINGS §2: YTD derivation


def test_ytd_cumulatives_are_differenced_not_discarded():
    """The original bug: 8 years of YTD-tagged OCF produced 8 quarters (fiscal
    Q1 only) instead of 32. PTON's shipped state.db showed exactly this --
    17 OCF points against 37 revenue points."""
    q = [100.0 + i for i in range(32)]
    facts = facts_doc({"NetCashProvidedByUsedInOperatingActivities":
                       ytd_facts("ocf", 2018, 8, q)})
    norm = edgar.normalize("0000000001", facts)
    rows = norm["operating_cash_flow"]

    assert len(rows) == 32, "YTD cumulatives must be differenced into quarters"
    assert [round(r["value"], 6) for r in rows] == q
    assert derive.is_contiguous(rows)
    assert {r["origin"] for r in rows} == {"reported", "derived"}


def test_derived_row_filed_date_is_the_later_input():
    """Post-derivation truncation by `filed` must equal pre-derivation
    truncation. That equivalence is what lets backtest and production share one
    code path."""
    facts = facts_doc({"NetCashProvidedByUsedInOperatingActivities":
                       ytd_facts("ocf", 2020, 2, [10.0] * 8)})
    norm = edgar.normalize("0000000001", facts)
    q2 = next(r for r in norm["operating_cash_flow"] if r["end"] == "2020-06-30")
    assert q2["origin"] == "derived"
    assert q2["filed"] == "2020-08-10"  # the H1 filing, not the Q1 filing
    assert len(q2["sources"]) == 2


def test_ttm_refuses_a_gappy_series():
    """The old ttm() summed series[-4:] unconditionally. On the real PTON OCF
    series that meant four non-adjacent quarters spanning two years, silently
    corrupting accrual_ratio, ocf_to_revenue and net_debt_to_ttm_ocf."""
    gappy = [
        {"end": "2022-06-30", "start": "2022-04-01", "value": 10.0},
        {"end": "2022-09-30", "start": "2022-07-01", "value": 10.0},
        {"end": "2023-06-30", "start": "2023-04-01", "value": 10.0},
        {"end": "2023-09-30", "start": "2023-07-01", "value": 10.0},
    ]
    assert derive.ttm(gappy) is None
    assert not derive.is_contiguous(gappy)

    clean = [
        {"end": "2023-03-31", "start": "2023-01-01", "value": 10.0},
        {"end": "2023-06-30", "start": "2023-04-01", "value": 10.0},
        {"end": "2023-09-30", "start": "2023-07-01", "value": 10.0},
        {"end": "2023-12-31", "start": "2023-10-01", "value": 10.0},
    ]
    assert derive.ttm(clean) == 40.0


def test_reported_quarter_beats_derived_for_same_period():
    ytd = ytd_facts("ocf", 2021, 1, [10.0, 20.0, 30.0, 40.0])
    standalone = [{
        "start": "2021-04-01", "end": "2021-06-30", "val": 999.0,
        "form": "10-Q", "filed": "2021-08-10", "accn": "standalone-q2",
    }]
    facts = facts_doc({"NetCashProvidedByUsedInOperatingActivities": ytd + standalone})
    norm = edgar.normalize("0000000001", facts)
    q2 = next(r for r in norm["operating_cash_flow"] if r["end"] == "2021-06-30")
    assert q2["origin"] == "reported"
    assert q2["value"] == 999.0


# ------------------------------------------------- FINDINGS §3: summed metrics


def test_total_debt_includes_current_maturities():
    """LongTermDebt alone understated net debt for exactly the leveraged names
    the LEVERAGE flag exists to catch."""
    facts = facts_doc({
        "LongTermDebtNoncurrent": pit_facts("ltd", 2023, 1, [1000.0] * 4),
        "LongTermDebtCurrent": pit_facts("ltdc", 2023, 1, [200.0] * 4),
        "ShortTermBorrowings": pit_facts("stb", 2023, 1, [50.0] * 4),
    })
    norm = edgar.normalize("0000000001", facts)
    assert norm["total_debt"][0]["value"] == 1250.0
    assert norm["total_debt"][0]["origin"] == "summed"


def test_total_debt_tolerates_missing_optional_components():
    facts = facts_doc({"LongTermDebtNoncurrent": pit_facts("ltd", 2023, 1, [1000.0] * 4)})
    norm = edgar.normalize("0000000001", facts)
    assert norm["total_debt"][0]["value"] == 1000.0


def test_deferred_revenue_includes_noncurrent():
    """A reclass between current and noncurrent contract liability previously
    read as a demand break."""
    facts = facts_doc({
        "ContractWithCustomerLiabilityCurrent": pit_facts("dc", 2023, 1, [800.0] * 4),
        "ContractWithCustomerLiabilityNoncurrent": pit_facts("dn", 2023, 1, [200.0] * 4),
    })
    norm = edgar.normalize("0000000001", facts)
    assert norm["deferred_revenue"][0]["value"] == 1000.0


# --------------------------------------------- FINDINGS §3: corporate actions


def test_corporate_action_does_not_read_as_dilution():
    """eval.json flagged BYND for DILUTION on +673.8% YoY diluted shares. PTON
    shows the same shape across its IPO: 22.9M -> 279.9M."""
    shares = [20e6] * 4 + [280e6] * 4  # IPO between year 1 and year 2
    facts = facts_doc(
        {"WeightedAverageNumberOfDilutedSharesOutstanding":
         discrete_facts("sh", 2019, 2, shares)},
        unit="shares",
    )
    norm = edgar.normalize("0000000001", facts)
    d = signals.diagnose("TEST", "0000000001", norm)
    assert d.dilution_yoy is None, "a 13x share-count jump is a corporate action"


def test_ordinary_dilution_still_registers():
    shares = [100e6, 101e6, 102e6, 103e6, 106e6, 107e6, 108e6, 109e6]
    facts = facts_doc(
        {"WeightedAverageNumberOfDilutedSharesOutstanding":
         discrete_facts("sh", 2019, 2, shares)},
        unit="shares",
    )
    norm = edgar.normalize("0000000001", facts)
    d = signals.diagnose("TEST", "0000000001", norm)
    assert d.dilution_yoy is not None
    assert 0.05 < d.dilution_yoy < 0.07


def test_basic_shares_are_not_substituted_for_diluted():
    """Mixing basic and diluted across periods manufactures dilution."""
    facts = facts_doc(
        {"WeightedAverageNumberOfSharesOutstandingBasic":
         discrete_facts("sh", 2019, 2, [100e6] * 8)},
        unit="shares",
    )
    norm = edgar.normalize("0000000001", facts)
    assert "diluted_shares" not in norm


# ------------------------------------------------ FINDINGS §3: point-in-time


def test_as_of_truncates_on_filed_not_period_end():
    """A quarter ending 3/31 filed 5/10 must be invisible on 4/30."""
    facts = facts_doc({"Revenues": discrete_facts("rev", 2023, 1, [100.0] * 4)})
    norm = edgar.normalize("0000000001", facts)
    assert any(r["end"] == "2023-03-31" for r in norm["revenue"])

    snap = edgar.as_of(norm, "2023-04-30")
    assert not any(r["end"] == "2023-03-31" for r in snap.get("revenue", []))

    snap = edgar.as_of(norm, "2023-05-15")
    assert any(r["end"] == "2023-03-31" for r in snap["revenue"])


def test_as_of_is_monotone():
    facts = facts_doc({"Revenues": discrete_facts("rev", 2020, 4, [100.0] * 16)})
    norm = edgar.normalize("0000000001", facts)
    sizes = [len(edgar.as_of(norm, c).get("revenue", [])) for c in
             ("2020-06-01", "2021-06-01", "2022-06-01", "2024-06-01")]
    assert sizes == sorted(sizes)


# ---------------------------------------------------- FINDINGS §3: persistence


def test_persist_does_not_duplicate_a_quarter(tmp_path, monkeypatch):
    """Old PK was (cik, metric, period, form), so the same quarter landed twice
    -- once from the 10-Q, once from the 10-K. That is why the shipped state.db
    held 37 revenue rows for ~33 quarters."""
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))

    rows = discrete_facts("rev", 2023, 1, [100.0] * 4)
    dupe = dict(rows[3])
    dupe["form"] = "10-Q"
    dupe["accn"] = "rev-2023-Q4-amended"
    norm = edgar.normalize("0000000001", facts_doc({"Revenues": rows + [dupe]}))

    edgar.persist_metrics("0000000001", norm)
    edgar.persist_metrics("0000000001", norm)  # idempotent

    conn = edgar.db()
    n = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE metric='revenue' AND kind='Q'"
    ).fetchone()[0]
    conn.close()
    assert n == 4


# ------------------------------------------------------------ coverage gate


def test_coverage_gate_flags_a_gappy_filer():
    """A filer whose OCF only tags fiscal Q1 must be excluded with a reason,
    not scored on partial data."""
    q1_only = [f for f in ytd_facts("ocf", 2018, 6, [10.0] * 24)
               if f["end"].endswith("03-31")]
    facts = facts_doc({
        "Revenues": discrete_facts("rev", 2018, 6, [100.0] * 24),
        "NetCashProvidedByUsedInOperatingActivities": q1_only,
    })
    norm = edgar.normalize("0000000001", facts)
    report = edgar.coverage_report(norm)
    assert report["operating_cash_flow"]["ratio"] < derive.COVERAGE_MIN
    assert not report["operating_cash_flow"]["scoreable"]
    assert report["revenue"]["scoreable"]


def test_full_coverage_passes():
    facts = facts_doc({
        "Revenues": discrete_facts("rev", 2018, 6, [100.0] * 24),
        "NetCashProvidedByUsedInOperatingActivities": ytd_facts("ocf", 2018, 6, [10.0] * 24),
    })
    norm = edgar.normalize("0000000001", facts)
    report = edgar.coverage_report(norm)
    assert report["operating_cash_flow"]["scoreable"]
    assert report["operating_cash_flow"]["ratio"] == 1.0


# -------------------------------------------------------------- fair access


def test_fetch_refuses_without_a_contact_user_agent(monkeypatch):
    monkeypatch.setattr(edgar, "USER_AGENT", "")
    with pytest.raises(RuntimeError, match="LEDGERLINE_UA"):
        edgar.fetch("https://example.com")
