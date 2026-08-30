"""
Tier 3 gate tests. Asserts the v2 -> v3 fixes from FINDINGS.md §3.
"""
from __future__ import annotations

import json

import pytest

from ledgerline import edgar, signals_v3
from ledgerline.validate import harness
from tests.unit.test_ingestion import discrete_facts, facts_doc, pit_facts, ytd_facts


def build_filer(quarters: int = 32, shock: dict | None = None) -> dict:
    """A steadily growing, boringly healthy filer. `shock` overrides the final
    quarter's values to inject a break."""
    rev = [1000.0 * (1.02**i) for i in range(quarters)]
    ocf = [200.0 * (1.02**i) for i in range(quarters)]
    ni = [150.0 * (1.02**i) for i in range(quarters)]
    cor = [600.0 * (1.02**i) for i in range(quarters)]
    ar = [500.0 * (1.02**i) for i in range(quarters)]

    if shock:
        for name, mult in shock.items():
            {"rev": rev, "ocf": ocf, "ni": ni, "cor": cor, "ar": ar}[name][-1] *= mult

    years = quarters // 4
    facts = facts_doc({
        "Revenues": discrete_facts("rev", 2016, years, rev),
        "CostOfRevenue": discrete_facts("cor", 2016, years, cor),
        "NetIncomeLoss": discrete_facts("ni", 2016, years, ni),
        "NetCashProvidedByUsedInOperatingActivities": ytd_facts("ocf", 2016, years, ocf),
        "Assets": pit_facts("as", 2016, years, [8000.0] * quarters),
        "AccountsReceivableNetCurrent": pit_facts("ar", 2016, years, ar),
    })
    return edgar.normalize("0000000001", facts)


# ------------------------------------------------------ robust scale + floor


def test_mad_scale_ignores_a_single_outlier():
    """pstdev on this baseline is ~30x the MAD estimate. That is how one bad
    quarter blinded the v2 detector for the following three years."""
    calm = [1.0] * 11 + [100.0]
    mu, sd = signals_v3.mad_scale(calm)
    assert mu == 1.0
    assert sd < 1.0


def test_scale_floor_prevents_a_flat_baseline_from_manufacturing_sigma():
    """v2 had no floor: sd -> 0 on a quiet stretch made any move a 5-sigma
    event. This is a direct false-positive generator."""
    flat = [0.10] * 12
    res = signals_v3.robust_z(0.11, flat, floor=0.05)
    assert res is not None
    z, _, scale, floored = res
    assert floored
    assert scale == 0.05
    assert abs(z) < 1.0  # a 1pp move on a 5pp floor is not a signal


def test_thin_baseline_returns_none_rather_than_a_number():
    assert signals_v3.robust_z(1.0, [0.1] * 4, floor=0.01) is None
    assert signals_v3.robust_z(1.0, [0.1] * 8, floor=0.01) is not None


# ------------------------------------------------------------- history gate


def test_short_history_is_unscoreable_not_zero():
    """v2 returned score 0.0 with gated_in False for a filer it could not
    assess -- indistinguishable from 'assessed, looks clean'. The verdict field
    fixed that; the JSON then still carried "score": 0.0 beside
    "scoreable": false, which read as a clean bill of health to anyone scanning
    for the number. An unscored filer has NO score."""
    norm = build_filer(quarters=16)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2017-06-01", norm=norm)
    assert res["scoreable"] is False
    assert "own-history" in res["reason"]
    assert res["score"] is None


def test_long_history_is_scoreable():
    norm = build_filer(quarters=32)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    assert res["scoreable"] is True, res.get("reason")
    assert res["period"] is not None


# ------------------------------------------------------------ signal itself


def test_steady_filer_stays_quiet():
    """The v1 failure mode: absolute thresholds fired on 60-90% of quarters
    because they measure a business model, not a change in one."""
    norm = build_filer(quarters=32)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    assert res["scoreable"]
    assert not res["gated_in"], f"clean filer fired: {[f['code'] for f in res['flags']]}"


def test_cash_conversion_break_fires():
    """Revenue holds, operating cash flow halves. That is the shape the product
    exists to catch."""
    norm = build_filer(quarters=32, shock={"ocf": 0.35})
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2024-03-01", norm=norm)
    assert res["scoreable"], res.get("reason")
    codes = {f["code"] for f in res["flags"]}
    assert codes, "an OCF collapse produced no flags at all"
    assert "CASH_CONVERSION_GAP" in codes or "OCF_TO_REVENUE" in codes


def test_flags_carry_full_provenance():
    norm = build_filer(quarters=32, shock={"ocf": 0.35})
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2024-03-01", norm=norm)
    for f in res["flags"]:
        assert f["baseline_n"] >= signals_v3.MIN_BASELINE_N
        assert f["baseline_scale"] > 0
        assert isinstance(f["floored"], bool)
    assert "derived_fraction" in res
    assert res["coverage"]["operating_cash_flow"]["scoreable"]


# ------------------------------------------------------- single code path


def test_baselines_are_point_in_time():
    """v2's _history() cut on period `end`, so baselines absorbed restatements
    that were not public yet. Production and backtest computed different
    functions. Evaluating at cutoff D must use only facts filed by D."""
    norm = build_filer(quarters=32)
    early = signals_v3.evaluate("TEST", "0000000001", as_of="2021-06-01", norm=norm)
    late = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    assert early["period"] < late["period"]

    # scoring at a past cutoff must not see any period filed after it
    snap = edgar.as_of(norm, "2021-06-01")
    assert all(r["filed"] <= "2021-06-01" for rows in snap.values() for r in rows)


def test_evaluate_is_deterministic():
    norm = build_filer(quarters=32)
    a = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    b = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    assert a == b


# ----------------------------------------------------------------- harness


def test_censored_first_fire_is_not_credited_as_lead():
    """WBD and LUMN 'fired' at the first cutoff in the window and that was
    reported as +42mo and +45mo of lead. The true first fire is outside the
    data."""
    case = harness.Case(ticker="X", cik="1", label="t", is_positive=True,
                        regime="r", broke="2022-08")
    cutoffs = ["2019-02-15", "2019-05-15", "2019-08-15"]

    def always_fires(ticker, cik, as_of):
        return {"scoreable": True, "score": 99.0, "flags": []}

    out = harness.evaluate_case(case, cutoffs, always_fires, threshold=45.0)
    assert out.censored
    assert out.lead_months is None
    assert out.fire_rate == 1.0


def test_genuine_lead_is_measured():
    case = harness.Case(ticker="X", cik="1", label="t", is_positive=True,
                        regime="r", broke="2022-08")
    cutoffs = ["2021-02-15", "2021-05-15", "2021-08-15", "2022-02-15"]

    def fires_second(ticker, cik, as_of):
        return {"scoreable": True, "score": 99.0 if as_of >= "2021-05-15" else 1.0, "flags": []}

    out = harness.evaluate_case(case, cutoffs, fires_second, threshold=45.0)
    assert not out.censored
    assert out.lead_months == 15


REGIMES = ["2014-16-energy", "2017-19-idiosyncratic", "2020-covid",
           "2021-22-growth-unwind"]


QUARTERS = 20


@pytest.fixture(autouse=True)
def _committed_prereg(tmp_path, monkeypatch):
    """verdict() reads prereg.json, not the module dict -- editing the constant
    in place used to turn a KILL into a SHIP on identical outcomes. Every
    verdict test therefore needs a committed rule on disk."""
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps({"committed": "2026-08-30", "rule": harness.PREREG}))
    monkeypatch.setattr(harness, "PREREG_PATH", str(path))


def _positives(n, lead=12, regimes=REGIMES):
    return [harness.Outcome(f"P{i}", True, True, False, "2021-02-15", lead, 0.1, QUARTERS, [],
                            regimes[i % len(regimes)], 2) for i in range(n)]


def _controls(n, n_firing, fires_each=1):
    """`n_firing` controls fire `fires_each` times out of QUARTERS scoreable
    quarters. The per-quarter FPR is what the rule gates on."""
    return [harness.Outcome(f"C{i}", False, i < n_firing, False, None, None,
                            (fires_each / QUARTERS) if i < n_firing else 0.0, QUARTERS, [],
                            "control", fires_each if i < n_firing else 0) for i in range(n)]


def test_verdict_kills_on_false_positive_rate():
    # 60 of 200 controls firing 5 quarters each -> 300/4000 = 7.5% per quarter
    v = harness.verdict(_positives(40) + _controls(200, 60, fires_each=5),
                        baseline_fpr=0.30)
    assert v["verdict"] == "KILL"
    assert not v["checks"]["false_positive_rate_per_quarter"]["pass"]
    assert v["checks"]["median_lead_months"]["pass"]


def test_false_positive_rate_is_measured_per_quarter_not_per_filer():
    """"<=10% of controls ever fire" silently demanded a 0.24% per-quarter rate,
    because controls average ~43 scoreable cutoffs. The rule now gates on the
    per-quarter rate and reports the per-filer one alongside."""
    # every control fires exactly once in 20 quarters -> 5% per quarter, but
    # 100% of filers "ever fired"
    v = harness.verdict(_positives(40) + _controls(200, 200, fires_each=1),
                        baseline_fpr=0.30)
    assert v["checks"]["false_positive_rate_per_quarter"]["value"] == 0.05
    assert v["false_positive_rate_per_filer"] == 1.0
    assert v["control_filer_quarters"] == 4000


def test_verdict_fails_closed_when_the_naive_baseline_was_not_computed():
    """PREREG requires the gate to beat "TTM OCF negative and net debt
    positive". Nothing computed it, and the check was only ADDED when a value
    was passed -- so `all(...)` over the remaining checks could not distinguish
    a missing criterion from a passing one."""
    v = harness.verdict(_positives(40) + _controls(200, 0))
    assert v["checks"]["beats_naive_baseline"]["value"] == "NOT COMPUTED"
    assert not v["checks"]["beats_naive_baseline"]["pass"]
    assert v["verdict"] == "KILL"


def test_regime_coverage_counts_regimes_the_gate_DETECTED_in():
    """Presence is a property of the case set, so the old check could not fail
    -- which made it unable to catch the beta detector it exists for. A gate
    that fires only in one regime must fail even when all four are present."""
    pos = _positives(40)
    for o in pos:
        if o.regime != "2021-22-growth-unwind":
            o.fired, o.lead_months = False, None
    v = harness.verdict(pos + _controls(200, 0), baseline_fpr=0.30)
    assert len(v["regimes_present"]) == 4
    assert v["regimes_detected"] == ["2021-22-growth-unwind"]
    assert not v["checks"]["regime_coverage"]["pass"]


def test_censored_positives_leave_the_hit_rate_denominator():
    """They are excluded from the median lead, so counting them as misses in
    the hit rate punished the gate twice for a case it could never assess."""
    pos = _positives(40)
    for o in pos[:20]:
        o.censored, o.lead_months = True, None
    v = harness.verdict(pos + _controls(200, 0), baseline_fpr=0.30)
    assert v["n_censored_positives"] == 20
    assert v["n_assessable_positives"] == 20
    assert v["checks"]["positive_hit_rate"]["value"] == 1.0


def test_verdict_kills_on_insufficient_regime_coverage():
    """A gate validated only in 2021-22 is a beta detector, not an accounting
    detector. Breadth is a pass/fail criterion, not a footnote."""
    v = harness.verdict(_positives(40, regimes=["2021-22-growth-unwind"]) + _controls(200, 5),
                        baseline_fpr=0.30)
    assert v["verdict"] == "KILL"
    assert not v["checks"]["regime_coverage"]["pass"]
    assert v["checks"]["false_positive_rate_per_quarter"]["pass"]


def test_verdict_kills_on_sample_size():
    """The original eight-case set cannot pass regardless of how good it looks."""
    v = harness.verdict(_positives(8) + _controls(20, 0), baseline_fpr=0.30)
    assert v["verdict"] == "KILL"
    assert not v["checks"]["sample_size"]["pass"]


def test_verdict_ships_when_all_criteria_clear():
    v = harness.verdict(_positives(40) + _controls(200, 10), baseline_fpr=0.30)
    assert v["verdict"] == "SHIP", v["checks"]
    assert v["checks"]["regime_coverage"]["value"] == 4


def test_split_hash_detects_tampering(tmp_path, monkeypatch):
    """The holdout is only worth something if nobody edits it after commit."""
    import json

    monkeypatch.setattr(harness, "SPLIT_PATH", str(tmp_path / "split.json"))
    payload = {"seed": 1, "created": "2026-01-01", "tuning": ["A"], "holdout": ["B"]}
    payload["sha256"] = harness._hash(payload)
    (tmp_path / "split.json").write_text(json.dumps(payload))
    assert harness.verify_split()

    payload["holdout"] = ["B", "C"]
    (tmp_path / "split.json").write_text(json.dumps(payload))
    try:
        harness.verify_split()
        raise AssertionError("tampered split was accepted")
    except RuntimeError as exc:
        assert "burned" in str(exc)
