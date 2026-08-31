"""
Metric-layer tests: the abstention taxonomy, the accounting invariant that
makes the coverage dashboard countable, the structural-abstention fix, the
peer ladder, and the reason segment revenue is out of scope.

The defect this layer answers (Phase 2 measurement, 250 filers at 2024-05-15):
of 169 filers marked scoreable, exactly ONE had all 13 diagnostics evaluated
-- median 10, minimum 2 -- and nothing in the emitted Verdict said so. One
filer could not have reached THRESHOLD at any z and was reported as score 0.0,
gated_in False, scoreable True: a structural abstention wearing the costume of
a clean assessment.
"""
from __future__ import annotations

import json

import pytest

from ledgerline import coverage, edgar, peers, reasons, render, signals, signals_v3, status
from tests.unit.test_gate import build_filer
from tests.unit.test_ingestion import discrete_facts, facts_doc, pit_facts

# ------------------------------------------------------------------- taxonomy


def test_every_abstention_code_has_human_text():
    """The taxonomy is closed and complete: every code carries the sentence a
    reader is owed, and the three tiers partition it with no overlap."""
    assert set(reasons.ALL) == set(reasons.TEXT)
    tiers = (set(reasons.FILER_LEVEL), set(reasons.ADMISSION_LEVEL),
             set(reasons.DIAGNOSTIC_LEVEL))
    assert tiers[0] | tiers[1] | tiers[2] == set(reasons.ALL)
    assert not (tiers[0] & tiers[1] or tiers[0] & tiers[2] or tiers[1] & tiers[2])
    assert reasons.is_valid(reasons.BASELINE_TOO_THIN)
    assert not reasons.is_valid("SOME_MADE_UP_CODE")


# ----------------------------------------------------- the accounting invariant


def test_evaluate_accounts_for_every_tracked_diagnostic():
    """THE LOAD-BEARING ONE: on a scoreable verdict every tracked diagnostic
    is either evaluated (in z) or accounted for (in abstentions), and no
    abstention is UNEXPLAINED on a fixture where diagnose() knows every cause.
    Without this the dashboard's per-diagnostic histogram silently
    under-counts and nobody notices."""
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    assert res["scoreable"]
    assert set(res["z"]) | set(res["abstentions"]) == set(signals_v3.TRACKED)
    assert not set(res["z"]) & set(res["abstentions"])
    assert reasons.UNEXPLAINED not in res["abstentions"].values()
    for code in res["abstentions"].values():
        assert reasons.is_valid(code)


def test_verdict_reports_how_much_weight_it_actually_evaluated():
    """The score is a weighted hinge sum over a FIXED divisor, so missing
    diagnostics compress the scale. evaluated_weight beside weight_total is
    what lets a reader see the compression."""
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    expect = round(sum(signals_v3.TRACKED[n][1] for n in res["z"]), 4)
    assert res["evaluated_weight"] == expect
    assert res["weight_total"] == pytest.approx(1.9923)
    assert res["evaluated_weight"] < res["weight_total"]  # this fixture has gaps


def test_verdict_carries_the_gate_version():
    """The span guard changed which quarters evaluate, so scores from this
    arithmetic must never pool with Phase 0's. Pinned: a silent version bump
    (or a silent failure to bump on the next change) fails here. 3.2.0 is the
    metric-arithmetic pass -- the total_debt double-count, the two stale-balance
    abstentions, the provenance input table and the derived_fraction default."""
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    assert res["gate_version"] == signals_v3.GATE_VERSION == "3.2.0"


# --------------------------------------------------- per-diagnostic attribution


def test_missing_metric_is_attributed_to_the_metric_not_the_diagnostic():
    """A filer with no inventory facts is told 'no inventory figures', not a
    vague failure on the diagnostic that needed them."""
    d = signals.diagnose("TEST", "0000000001", build_filer())
    assert d.reasons["dio"] == reasons.INPUT_METRIC_ABSENT
    assert "inventory" in d.reason_detail["dio"]
    assert d.reasons["inventory_vs_revenue"] == reasons.INPUT_METRIC_ABSENT
    assert "inventory" in d.reason_detail["inventory_vs_revenue"]


def test_low_coverage_input_is_attributed_as_input_coverage_low():
    """The FTI case from signals_v3's own docstring: sparse cost_of_revenue
    suppresses dio -- now with a code instead of silence."""
    quarters = 32
    rev = [1000.0] * quarters
    cor_recent = [600.0] * 12  # only the last 3 years -- ratio 12/32 = 0.375
    norm = edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 8, rev),
        "NetIncomeLoss": discrete_facts("ni", 2016, 8, [150.0] * quarters),
        "NetCashProvidedByUsedInOperatingActivities":
            discrete_facts("ocf", 2016, 8, [200.0] * quarters),
        "CostOfRevenue": discrete_facts("cor", 2021, 3, cor_recent),
        "InventoryNet": pit_facts("inv", 2016, 8, [700.0] * quarters),
    }))
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2024-03-01", norm=norm)
    assert res["abstentions"]["dio"] == reasons.INPUT_COVERAGE_LOW
    assert "cost of revenue" in res["abstention_detail"]["dio"]


def test_noncontiguous_ttm_is_attributed():
    """A gap inside the trailing-year window gives ocf_to_revenue a
    TTM_NONCONTIGUOUS code -- distinct from the metric being absent."""
    quarters = 32
    ocf = discrete_facts("ocf", 2016, 8, [200.0] * quarters)
    del ocf[30]  # one missing quarter inside the final TTM window
    norm = edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 8, [1000.0] * quarters),
        "NetCashProvidedByUsedInOperatingActivities": ocf,
    }))
    d = signals.diagnose("TEST", "0000000001", norm)
    assert d.ocf_to_revenue is None
    assert d.reasons["ocf_to_revenue"] == reasons.TTM_NONCONTIGUOUS


def test_stale_balance_sheet_is_attributed_as_period_misaligned():
    """The FTI defect shape: a fresh inventory balance over a cost-of-revenue
    window that ends years earlier. signals._aligned already refuses it; this
    pins that the refusal is now NAMED."""
    norm = edgar.normalize("0000000001", facts_doc({
        "Revenues": discrete_facts("rev", 2016, 8, [1000.0] * 32),
        "CostOfRevenue": discrete_facts("cor", 2016, 3, [600.0] * 12),
        "InventoryNet": pit_facts("inv", 2016, 8, [700.0] * 32),
    }))
    d = signals.diagnose("TEST", "0000000001", norm)
    assert d.dio is None
    assert d.reasons["dio"] == reasons.PERIOD_MISALIGNED


def test_thin_baseline_is_attributed_with_its_n():
    """BASELINE_TOO_THIN says how many readings existed and how many are
    needed, so 'too thin' is checkable rather than asserted."""
    quarters = 32
    norm = build_filer(quarters)
    # Receivables only for the last 2 years: dso and receivables_vs_revenue
    # become computable too recently to build an 8-observation baseline.
    short_ar = edgar.normalize("0000000001", facts_doc({
        "AccountsReceivableNetCurrent": pit_facts("ar", 2022, 2, [500.0] * 8),
    }))
    norm["receivables"] = short_ar["receivables"]
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01", norm=norm)
    assert res["abstentions"]["receivables_vs_revenue"] == reasons.BASELINE_TOO_THIN
    detail = res["abstention_detail"]["receivables_vs_revenue"]
    assert str(signals_v3.MIN_BASELINE_N) in detail and "past readings" in detail


def test_corporate_action_share_move_is_attributed():
    """FIX §3's guard (a >50% share-count move is a split or listing, not
    dilution) now records CORPORATE_ACTION instead of a bare None."""
    shares = [100.0] * 31 + [220.0]
    norm = build_filer()
    dil = edgar.normalize("0000000001", {
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            {"units": {"shares": discrete_facts("dil", 2016, 8, shares)}},
    })
    norm["diluted_shares"] = dil["diluted_shares"]
    d = signals.diagnose("TEST", "0000000001", norm)
    assert d.dilution_yoy is None
    assert d.reasons["dilution_yoy"] == reasons.CORPORATE_ACTION


# ------------------------------------------------------ structural abstention


def test_a_filer_that_cannot_reach_the_threshold_is_marked_not_silently_scored_zero(
        monkeypatch):
    """A filer whose computable diagnostics cannot reach THRESHOLD at any z is
    scoreable=False with a written reason -- never score 0.0, which is
    indistinguishable from 'assessed, looks clean'. One such filer existed in
    the 250-filer 2024-05-15 sample. Reproduced here by narrowing TRACKED to
    low-weight diagnostics, which is exactly the situation such a filer is in."""
    # The exact bound, derived not hardcoded: ceiling = weight * Z_CAP /
    # SCORE_DIVISOR * 100 must reach THRESHOLD.
    assert pytest.approx(0.399204, abs=1e-6) == signals_v3.MIN_SCOREABLE_WEIGHT
    slim = {k: v for k, v in signals_v3.TRACKED.items()
            if k in ("ocf_to_revenue", "dso")}  # weight 0.1323 < 0.3992
    monkeypatch.setattr(signals_v3, "TRACKED", slim)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    assert res["scoreable"] is False
    assert res["score"] is None
    assert res["gated_in"] is False
    assert res["reason_code"] == reasons.CANNOT_REACH_THRESHOLD
    assert "cannot reach the flag threshold" in res["reason"]
    # The plain-language surface translates it rather than echoing machine text.
    assert render.plain_reason(res["reason"]).startswith("Too few")
    # The accounting invariant holds here too, so the dashboard can count it.
    assert set(res["z"]) | set(res["abstentions"]) == set(slim)


def test_a_fully_weighted_filer_is_not_structurally_abstained():
    """The fix must not blind ordinary scoring: build_filer carries weight
    well above the bound and stays scoreable."""
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    assert res["scoreable"] is True
    assert res["evaluated_weight"] > signals_v3.MIN_SCOREABLE_WEIGHT


# ------------------------------------------------- coverage report + ceiling


def test_coverage_report_carries_a_code_alongside_the_sentence():
    """The sentence stays exactly as it was (existing readers, logs and tests
    parse it); the code is new and countable."""
    rep = edgar.coverage_report(build_filer())
    ok = rep["revenue"]
    assert ok["scoreable"] and ok["code"] is None and ok["reason"] is None
    gap = rep["diluted_shares"]  # absent from this fixture
    assert not gap["scoreable"]
    assert gap["code"] == reasons.INPUT_COVERAGE_LOW
    assert gap["reason"].startswith("coverage ")


def test_diluted_shares_ceiling_is_reported_not_acted_on():
    """The cut, pinned from both sides. A filer tagging 3 of 4 quarters
    (ratio 0.75 -- the structural ceiling of a 10-K that carries only an
    annual share count) is STILL not scoreable on diluted_shares: acting on
    the ceiling would unsuppress dilution_yoy in ~92% of the universe under a
    weight fitted on the ~8% where it existed. The dashboard reports ratio,
    expected and achieved side by side instead."""
    rev = [{"end": e, "kind": "Q"} for e in
           (f"{y}-{q}" for y in range(2020, 2024)
            for q in ("03-31", "06-30", "09-30", "12-31"))]
    dil = [r for r in rev if not r["end"].endswith("12-31")]  # 3 of 4
    rep = edgar.coverage_report({"revenue": rev, "diluted_shares": dil})
    entry = rep["diluted_shares"]
    assert entry["ratio"] == 0.75
    assert entry["scoreable"] is False  # the global COVERAGE_MIN still governs
    assert coverage.expected_for("diluted_shares") == 0.75
    enriched = coverage._metric_entries(rep)["diluted_shares"]
    assert enriched["expected"] == 0.75
    assert enriched["achieved"] == 1.0  # at its ceiling -- visibly, not silently


# ------------------------------------------------------------------ dashboard


def _toy_dashboard():
    healthy = build_filer()
    norms = {"0000000001": healthy, "0000000002": {}}
    return coverage.build(
        as_of="2023-12-01",
        tickers={"0000000001": "AAA", "0000000002": "BBB"},
        normalizer=lambda cik: norms[cik],
        sic_map={"0000000001": "3674", "0000000002": "3674"},
    )


def test_dashboard_counts_every_filer_exactly_once():
    """An aggregate that drops filers is how a coverage number flatters
    itself: every filer lands in exactly one reason bucket."""
    dash = _toy_dashboard()
    assert sum(dash.reasons.values()) == dash.n_filers == 2
    assert dash.reasons["SCOREABLE"] == dash.n_scoreable == 1
    assert dash.reasons[reasons.NO_XBRL_FACTS] == 1


def test_dashboard_aggregates_codes_not_prose():
    """Histogram keys are taxonomy codes only; a detail sentence can never
    become a key, so counts stay groupable across runs."""
    dash = _toy_dashboard()
    for key in dash.reasons:
        assert key == "SCOREABLE" or reasons.is_valid(key)
    for per_code in dash.abstentions.values():
        for code in per_code:
            assert reasons.is_valid(code)
    assert dash.n_unexplained == 0


def test_dashboard_and_verdict_both_carry_the_phase_0_kill():
    """Every surface that could be read as the tool working carries the
    frozen verdict -- the dashboard's whole subject is what the gate cannot
    say, so it is not exempt."""
    dash = _toy_dashboard()
    assert dash.gate_status == status.GATE_STATUS
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=build_filer())
    status.assert_stamped(res)  # raises if the stamp or its numbers are absent


# --------------------------------------------------------------- persistence


def test_coverage_and_scoreability_round_trip_through_sqlite(tmp_path, monkeypatch):
    """Persist the same filer at two different as_of dates and read both rows
    back. Catches a primary key that forgot as_of -- exactly the defect that
    made the superseded `coverage` table unable to hold what was computed."""
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    rep = {"revenue": {"ratio": 1.0, "expected": 1.0, "achieved": 1.0, "n": 32,
                       "scoreable": True, "code": None, "reason": None}}
    edgar.persist_coverage("0000000001", "2023-06-30", rep)
    edgar.persist_coverage("0000000001", "2023-12-31", rep)
    row = {"cik": "0000000001", "ticker": "AAA", "scoreable": True,
           "code": None, "detail": None, "n_evaluated": 7, "n_tracked": 13,
           "evaluated_weight": 1.54, "weight_total": 1.9923,
           "can_reach_threshold": True, "abstentions": {"dio": "INPUT_METRIC_ABSENT"},
           "derived_fraction": 0.2, "fiscal_calendar": "calendar",
           "peer_level": 4, "peer_n": 8}
    edgar.persist_scoreability([{**row, "as_of": "2023-06-30"}])
    edgar.persist_scoreability([{**row, "as_of": "2023-12-31"}])
    conn = edgar.db()
    assert conn.execute("SELECT COUNT(*) FROM coverage_pit").fetchone()[0] == 2
    got = conn.execute(
        "SELECT as_of, abstentions FROM scoreability ORDER BY as_of").fetchall()
    conn.close()
    assert [r[0] for r in got] == ["2023-06-30", "2023-12-31"]
    assert json.loads(got[0][1]) == {"dio": "INPUT_METRIC_ABSENT"}


# ------------------------------------------------------------------ peer sets


def _sic_map():
    # 4 filers in industry 3674 (too few for 6 peers), 4 more in 3671 --
    # together industry group 367 has 8.
    m = {f"367400000{i}": "3674" for i in range(4)}
    m |= {f"367100000{i}": "3671" for i in range(4)}
    return m


def test_peer_set_falls_back_from_industry_to_group_to_major_group():
    """A 4-digit group of 3 peers widens to 3-digit, and the level actually
    used travels on the set -- a 2-digit 'peer set' is a sector, not an
    industry, and a consumer is entitled to know which it got."""
    ps = peers.peer_set("3674000000", _sic_map())
    assert ps.level == 3 and ps.key == "367"
    assert ps.n() == 7
    assert ps.sic_is_current is True  # the named point-in-time exception


def test_peer_set_abstains_when_even_the_major_group_is_too_small():
    """An empty set with a reason, never a short list that peer_z would
    silently reject downstream."""
    ps = peers.peer_set("3674000000", {"3674000000": "3674", "9999000000": "9911"})
    assert ps.level is None and ps.members == ()
    assert ps.reason == reasons.NO_PEER_SET


def test_peer_set_excludes_filers_not_scoreable_at_the_cutoff():
    """Membership from filers that only became assessable later is
    survivorship selection, so the scoreable set gates membership."""
    m = _sic_map()
    scoreable = set(m) - {"3671000000"}
    ps = peers.peer_set("3674000000", m, scoreable=scoreable)
    assert "3671000000" not in ps.members
    assert ps.n() == 6


def test_peer_set_excludes_the_filer_itself_from_its_own_peers():
    ps = peers.peer_set("3674000000", _sic_map())
    assert "3674000000" not in ps.members


def test_min_peers_is_peer_z_own_guard():
    """peers.py imports the 6 from signals.MIN_PEERS rather than restating it,
    so the set builder and the statistic cannot drift on 'enough'."""
    assert peers.MIN_PEERS is signals.MIN_PEERS


# ------------------------------------------------------------ segment revenue


def test_companyfacts_carries_no_segment_dimensions():
    """Pins in code what was verified against the 4.7GB cache: a companyfacts
    fact carries exactly accn/end/filed/form/fp/fy/start/val -- the API strips
    dimensional facts entirely -- so no METRIC_MAP entry can ever address
    segment revenue. Reaching it would take per-filing XBRL instances (which
    destroys the one-request-a-day cost architecture) or a second ingestion
    path with different revision semantics. Recorded so nobody re-scopes
    segment work on a false assumption."""
    allowed = {"accn", "end", "filed", "form", "fp", "fy", "start", "val"}
    for fact in discrete_facts("rev", 2020, 2, [1.0] * 8):
        assert set(fact) <= allowed
        assert "segment" not in fact and "dimension" not in fact
    assert not any("segment" in m.lower() for m in edgar.METRIC_MAP)
    assert not any("Segment" in c for cs in edgar.METRIC_MAP.values() for c in cs)
