"""
Tests for the three decisions: XBRL-era admission, fundamental labeling,
sector exclusion.
"""
from __future__ import annotations

from ledgerline import edgar, label, universe
from ledgerline.validate import harness
from tests.unit.test_ingestion import discrete_facts, facts_doc, pit_facts, ytd_facts


def build(quarters=32, start=2016, rev_mult=1.0, ocf_mult=1.0, cor_mult=1.0,
          impair=None, assets=8000.0):
    """A steadily growing filer. Multipliers apply to the FINAL quarter."""
    rev = [1000.0 * (1.02**i) for i in range(quarters)]
    ocf = [200.0 * (1.02**i) for i in range(quarters)]
    ni = [150.0 * (1.02**i) for i in range(quarters)]
    cor = [600.0 * (1.02**i) for i in range(quarters)]
    rev[-1] *= rev_mult
    ocf[-1] *= ocf_mult
    cor[-1] *= cor_mult

    years = quarters // 4
    mapping = {
        "Revenues": discrete_facts("rev", start, years, rev),
        "CostOfRevenue": discrete_facts("cor", start, years, cor),
        "NetIncomeLoss": discrete_facts("ni", start, years, ni),
        "NetCashProvidedByUsedInOperatingActivities": ytd_facts("ocf", start, years, ocf),
        "Assets": pit_facts("as", start, years, [assets] * quarters),
    }
    if impair is not None:
        charges = [0.0] * quarters
        charges[-1] = impair
        mapping["AssetImpairmentCharges"] = discrete_facts("imp", start, years, charges)
    return edgar.normalize("0000000001", facts_doc(mapping))


# ------------------------------------------- decision 1: XBRL-era admission


def test_pre_mandate_history_does_not_count_toward_scoreability():
    """companyfacts returns pre-2011 comparatives, but they carry the LATER
    filing's `filed` date. The system must not treat them as contemporaneous."""
    norm = build(quarters=32, start=2004)  # all filed 2004-2012
    start = universe.scoreable_from(norm)
    assert start is None or start >= universe.XBRL_FLOOR


def test_post_mandate_filer_becomes_scoreable():
    norm = build(quarters=32, start=2016)
    start = universe.scoreable_from(norm)
    assert start is not None
    assert start >= universe.EARLIEST_CUTOFF


def test_cutoffs_start_at_the_filers_own_scoreable_date():
    """Censoring only means something if the loop starts where the filer could
    first be assessed, rather than at a fixed calendar year."""
    norm = build(quarters=32, start=2016)
    cuts = universe.cutoffs_for(norm, end="2024-12-31")
    assert cuts
    assert cuts[0] >= universe.scoreable_from(norm)
    assert cuts == sorted(cuts)


def test_case_rejected_when_break_precedes_first_scoreable_cutoff():
    """A gate cannot be blamed for missing something it could not have seen."""
    norm = build(quarters=32, start=2016)
    ok, reason = universe.admit("1", "T", norm, sic="3711", broke="2012-01")
    assert not ok
    assert "precedes first scoreable cutoff" in reason


def test_case_rejected_outside_defined_regimes():
    norm = build(quarters=32, start=2016)
    ok, reason = universe.admit("1", "T", norm, sic="3711", broke="2026-06")
    assert not ok
    assert "regime" in reason


def test_regime_lookup():
    assert universe.regime_for("2021-11") == "2021-22-growth-unwind"
    assert universe.regime_for("2015-03") in ("2014-16-energy", "2015-18-retail")
    assert universe.regime_for("2020-06") == "2020-covid"
    assert universe.regime_for("2003-01") is None


def test_regime_set_meets_the_prereg_minimum():
    assert len(universe.REGIMES) >= harness.PREREG["min_regimes"]
    assert "2017-19-idiosyncratic" in universe.REGIMES


# ------------------------------------------ decision 1b: sector exclusion


def test_financials_and_reits_are_excluded():
    """DSO, DIO, inventory, gross margin and deferred revenue assume an
    operating company. A random Russell 3000 draw is ~20% financials."""
    assert universe.sic_excluded("6021")   # national commercial bank
    assert universe.sic_excluded("6311")   # life insurance
    assert universe.sic_excluded("6798")   # REIT
    assert universe.sic_excluded("6770")   # blank check
    assert not universe.sic_excluded("3711")  # motor vehicles
    assert not universe.sic_excluded("7372")  # prepackaged software


def test_unknown_sector_is_not_admissible():
    assert universe.sic_excluded(None)
    assert universe.sic_excluded("")


def test_admit_rejects_excluded_sector_before_anything_else():
    norm = build(quarters=32, start=2016)
    ok, reason = universe.admit("1", "BANK", norm, sic="6021")
    assert not ok
    assert "sector" in reason


# --------------------------------------- decision 2: fundamental labeling


def test_healthy_filer_is_not_labeled():
    norm = build(quarters=32, start=2016)
    assert label.first_deterioration("T", "1", norm) is None


def test_two_criteria_trip_a_deterioration_event():
    """Revenue collapses and margin collapses in the same quarter."""
    norm = build(quarters=32, start=2016, rev_mult=0.55, cor_mult=0.95)
    period = label.first_deterioration("T", "1", norm)
    assert period is not None


def test_one_criterion_alone_is_not_enough():
    """MIN_CRITERIA = 2. A single soft quarter is noise, not deterioration."""
    norm = build(quarters=32, start=2016, ocf_mult=0.30)
    hits = [c for c in (fn(norm, "2023-12-31") for fn in label.CRITERIA) if c]
    if len(hits) < label.MIN_CRITERIA:
        assert label.first_deterioration("T", "1", norm) is None


def test_impairment_criterion_fires_above_threshold():
    norm = build(quarters=32, start=2016, impair=900.0, assets=8000.0)
    c = label._impairment(norm, "2023-12-31")
    assert c is not None
    assert c.code == "IMPAIRMENT"
    assert c.value >= label.IMPAIRMENT_OF_ASSETS


def test_small_impairment_does_not_fire():
    norm = build(quarters=32, start=2016, impair=100.0, assets=8000.0)
    assert label._impairment(norm, "2023-12-31") is None


def test_ocf_break_requires_a_real_ttm():
    """Contiguity gating carries through to labeling: a gappy OCF series must
    not produce a fabricated year-over-year comparison."""
    q1_only = [f for f in ytd_facts("ocf", 2016, 8, [200.0] * 32)
               if f["end"].endswith("03-31")]
    norm = edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 8, [1000.0] * 32),
        "NetCashProvidedByUsedInOperatingActivities": q1_only,
    }))
    assert label._ocf_break(norm, "2023-03-31") is None


def test_label_horizon_only_looks_at_filings_after_the_cutoff():
    """Labels may look forward -- that is the outcome side. But the horizon must
    start after the cutoff, or the label leaks into its own scoring window."""
    norm = build(quarters=32, start=2016)
    lab = label.label("T", "1", norm, as_of="2020-06-01")
    assert lab.n_quarters_observed <= label.HORIZON_QUARTERS
    rows = {r["end"]: r["filed"] for r in norm["revenue"]}
    for period in [c["period"] for c in lab.criteria] or []:
        assert rows[period] > "2020-06-01"


def test_broke_date_is_the_filing_date_not_the_period_end():
    """Lead must be measured to when deterioration became PUBLIC. A quarter
    ending 3/31 is not public until it is filed."""
    norm = build(quarters=32, start=2016)
    filed = label.broke_date_filed(norm, "2023-03-31")
    assert filed is not None
    assert filed > "2023-03-31"


def test_price_is_not_part_of_the_label():
    """Price drawdown is reported alongside, never gated on."""
    import inspect

    src = inspect.getsource(label.label)
    assert "price" not in src.lower()
    # price may be mentioned in the prereg NOTES (explaining that it is
    # excluded), but must not appear in any criterion key or threshold
    criteria_keys = {k: v for k, v in harness.PREREG.items() if k != "notes"}
    assert "price" not in str(criteria_keys).lower()
    assert "drawdown" not in str(criteria_keys).lower()


# -------------------------------------- generated cases replace curation


def test_build_cases_splits_positives_from_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "CASES_PATH", str(tmp_path / "cases.json"))
    monkeypatch.setattr(harness, "DATA", str(tmp_path))

    healthy = build(quarters=32, start=2016)
    broken = build(quarters=32, start=2016, rev_mult=0.55, cor_mult=0.95)
    norms = {"0000000001": healthy, "0000000002": broken}

    payload = harness.build_cases(
        {"GOOD": "0000000001", "BAD": "0000000002"},
        sic_lookup=lambda cik: "3711",
        normalizer=lambda cik: norms[cik],
    )
    kinds = {c["ticker"]: c["is_positive"] for c in payload["cases"]}
    assert kinds.get("GOOD") is False
    assert kinds.get("BAD") is True
    assert payload["n_positive"] == 1
    assert payload["n_control"] == 1


def test_build_cases_records_every_rejection(tmp_path, monkeypatch):
    """A silently dropped filer is a survivorship bias with extra steps."""
    monkeypatch.setattr(harness, "CASES_PATH", str(tmp_path / "cases.json"))
    monkeypatch.setattr(harness, "DATA", str(tmp_path))

    norm = build(quarters=32, start=2016)
    payload = harness.build_cases(
        {"BANK": "0000000001"},
        sic_lookup=lambda cik: "6021",
        normalizer=lambda cik: norm,
    )
    assert payload["cases"] == []
    assert len(payload["rejected"]) == 1
    assert "sector" in payload["rejected"][0]["reason"]


def test_readiness_blocks_a_split_on_the_original_eight(tmp_path, monkeypatch):
    """The original case set cannot support a split, and readiness says so
    before any threshold gets fit to it."""
    monkeypatch.setattr(harness, "CASES_PATH", str(tmp_path / "cases.json"))
    payload = {
        "cases": [
            {"ticker": f"T{i}", "cik": str(i), "label": "x", "is_positive": True,
             "broke": "2021-11", "regime": "2021-22-growth-unwind"}
            for i in range(8)
        ]
    }
    r = harness.readiness(payload)
    assert not r["ready"]
    assert not r["checks"]["positives"]["pass"]
    assert not r["checks"]["controls"]["pass"]
    assert not r["checks"]["regimes"]["pass"]
