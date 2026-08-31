"""
Track-record tests: the forward-scoring loop, the abstention rules, the
append-only resolution ledger, and the one-definition guarantee.

Each test pins a specific decision or defect, matching the register in
test_validation_integrity.py. Same isolation idiom as test_signal_store.py:
edgar.DATA and edgar.DB_PATH are redirected to tmp_path so the live state.db
is never touched; norms come from the synthetic filers test_decisions.build()
constructs, and resolve() takes an injected normalizer. No network.
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest
from typer.testing import CliRunner

from ledgerline import cli, edgar, emit, label, signals_v3, status, track
from ledgerline.validate import harness
from tests.unit.test_decisions import build


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def healthy():
    return build(quarters=32, start=2016)


def sick():
    """Deteriorates at 2023-12-31: revenue decel + margin collapse."""
    return build(quarters=32, start=2016, rev_mult=0.55, cor_mult=0.95)


def put_signals(specs: list[tuple], source: str = "scan") -> str:
    """Persist hand-shaped verdicts. specs: (cik, ticker, as_of, score).
    score=None means unscoreable. Returns the gate version written."""
    verdicts = []
    for cik, ticker, as_of, score in specs:
        v = {
            "cik": cik, "ticker": ticker, "as_of": as_of, "score": score,
            "gated_in": score is not None and score >= signals_v3.THRESHOLD,
            "scoreable": score is not None,
            "reason": None if score is not None else "too little history",
            "reason_code": None if score is not None else "INSUFFICIENT_HISTORY",
            "flags": [], "z": {},
            "accessions": ["acc-1"] if score is not None else [],
        }
        verdicts.append(status.stamp(v))
    out = emit.emit_run(verdicts, source=source, run_date="2024-01-01")
    return out["gate_version"]


SIG_ROW = {"signal_id": "x", "cik": "0000000001", "ticker": "T",
           "as_of": "2023-03-01"}


# ------------------------------------------------------- the label horizon


def test_horizon_default_is_byte_identical_for_existing_callers():
    """The horizon kwarg must not move the validated label by a byte:
    label.py is the outcome side of the one clean measurement the project
    has, and the Phase 0 label set must stay reproducible from the code."""
    norm = sick()
    a = label.label("T", "1", norm, as_of="2022-06-01")
    b = label.label("T", "1", norm, as_of="2022-06-01",
                    horizon=label.HORIZON_QUARTERS)
    assert a.as_dict() == b.as_dict()


def test_min_criteria_is_not_relaxed_at_short_horizons():
    """Relaxing the 2-of-5 requirement for short windows would make the +1q
    number a different experiment wearing the same name."""
    assert "MIN_CRITERIA" not in inspect.signature(label.label).parameters
    assert label.MIN_CRITERIA == 2


def test_short_horizon_watches_a_shorter_window_of_the_same_criteria():
    """At horizon 1 the deterioration four quarters out is out of scope --
    CLEAN -- while horizon 4 sees it. Same five criteria, different window."""
    norm = sick()
    h1 = track.resolve_signal(SIG_ROW, 1, "2026-08-30", norm)
    h4 = track.resolve_signal(SIG_ROW, 4, "2026-08-30", norm)
    assert h1["outcome"] == "CLEAN"
    assert h4["outcome"] == "DETERIORATED"
    assert h4["event_period"] == "2023-12-31"


def test_short_horizon_rows_carry_a_distinct_label_rule():
    """A +1q recall silently compared to the +4q reference is two different
    experiments wearing one name. Only horizon 4 is prereg.json's rule."""
    assert track.label_rule(4) == harness.PREREG["label"]
    assert track.label_rule(1) != harness.PREREG["label"]
    assert track.label_rule(2) != harness.PREREG["label"]
    assert "1q" in track.label_rule(1)


# --------------------------------------------------- pending is not clean


def test_unresolved_horizon_is_pending_not_clean(isolated_db):
    """A signal whose forward window holds two quarters resolves PENDING at
    horizon 4 and writes NO row. Counting immature signals as CLEAN would
    manufacture precision out of the calendar -- the same shape of defect as
    FINDINGS 2's sum(series[-4:])."""
    norm = healthy()
    gate = put_signals([("0000000001", "T", "2023-09-01", 10.0)])
    out = track.resolve(as_of="2026-08-30", gate_version=gate,
                        normalizer=lambda cik: norm)
    assert out["pending"] == 1          # horizon 4: only 2 quarters exist
    assert out["resolved"] == 2         # horizons 1 and 2 settle CLEAN
    conn = edgar.db()
    try:
        h4 = conn.execute("SELECT COUNT(*) FROM signal_scores "
                          "WHERE horizon_q = 4").fetchone()[0]
    finally:
        conn.close()
    assert h4 == 0, "PENDING must be the absence of a row, never a stored one"


def test_pending_signals_are_absent_from_every_denominator(isolated_db):
    """Half-unresolved ledger: the pending signal appears in no numerator and
    no denominator, rather than being treated as a quiet negative."""
    norm = healthy()
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0),
                        ("0000000001", "T", "2023-09-01", 10.0)])
    track.resolve(as_of="2026-08-30", gate_version=gate,
                  normalizer=lambda cik: norm)
    rows = track.resolutions(gate, 4)
    assert len(rows) == 1
    stats = track.quarter_stats(rows)
    assert stats["n_resolved"] == 1
    outs = track.case_outcomes(gate, 4)
    assert len(outs) == 1
    assert outs[0].scoreable_quarters == 1
    assert [p["horizon_q"] for p in track.pending(gate)] == [4]


# --------------------------------------------- resolution is a vintage


def test_resolve_is_idempotent(isolated_db):
    """A re-run over unchanged facts writes nothing new -- the daily cron
    must not restate its own ignorance into rows."""
    norm = healthy()
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    first = track.resolve(as_of="2026-08-30", gate_version=gate,
                          normalizer=lambda cik: norm)
    again = track.resolve(as_of="2026-08-30", gate_version=gate,
                          normalizer=lambda cik: norm)
    assert first["resolved"] == 3
    assert again["resolved"] == 0 and again["revised"] == 0
    assert again["unchanged"] == 3


def test_resolution_is_reproducible_after_a_later_restatement():
    """Resolve at D; append a restatement vintage filed after D; resolve at D
    again: identical outcome, criteria and quarter count. Pins the vintage
    truncation -- and it caught a real defect on first run: edgar.as_of()
    keeps the full vintage list on every row, so label._restatement saw the
    post-D amendment and tripped RESTATEMENT in a resolution dated before
    the amendment existed. track._truncate_vintages() is the fix."""
    norm = sick()
    before = track.resolve_signal(SIG_ROW, 4, "2025-01-01", norm)
    restated = copy.deepcopy(norm)
    row = next(r for r in restated["revenue"] if r["end"] == "2023-12-31")
    row["vintages"].append({**row["vintages"][-1], "filed": "2025-09-01",
                            "form": "10-K/A", "value": 999999.0})
    after = track.resolve_signal(SIG_ROW, 4, "2025-01-01", restated)
    assert before["outcome"] == after["outcome"]
    assert before["criteria"] == after["criteria"]
    assert before["n_quarters_observed"] == after["n_quarters_observed"]


def _restated_after(norm: dict, period: str, filed: str,
                    factor: float = 0.4) -> dict:
    """`norm` with a 10-K/A vintage filed at `filed` that cuts one period's
    revenue. The shape edgar.normalize actually produces, and the shape that
    separates 'what was knowable when the window closed' from 'what is known
    today' -- the whole difference this module claims to preserve."""
    out = copy.deepcopy(norm)
    row = next(r for r in out["revenue"] if r["end"] == period)
    row["vintages"].append({**row["vintages"][-1], "filed": filed,
                            "form": "10-K/A",
                            "value": row["vintages"][-1]["value"] * factor})
    return out


def test_the_first_resolution_is_dated_when_the_window_closed(isolated_db):
    """resolve() stamped every row with the RUN date and used
    earliest_resolvable() only as a skip test, so the first row a backlogged
    signal ever got carried every filing made since -- up to twenty years of
    it for the replayed evaluations dated 2005 onward. The row that
    resolutions(revisions='first') documents as 'the outcome as it was FIRST
    known' was today's answer, and 'first' and 'latest' were identical for
    every backfilled signal."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    track.resolve(as_of="2026-08-31", gate_version=gate, horizons=(4,),
                  normalizer=lambda cik: healthy())
    rows = track.resolutions(gate, 4, revisions="all")
    assert [r["resolved_at"] for r in rows] == [
        track.earliest_resolvable("2023-03-01", 4)]


def test_a_restatement_filed_after_the_window_does_not_grade_history(
        isolated_db):
    """The consequence, end to end. A 10-K/A filed 19 months after the window
    closed flips the label; resolved at the run date it flipped the FIRST
    row, so the hindsight-free view returned DETERIORATED for a quarter that
    read CLEAN on every day a reader could have looked. One run now writes
    both rows: the window-close answer, and the restated answer beside it."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    norm = _restated_after(healthy(), "2023-12-31", "2026-01-01")
    counts = track.resolve(as_of="2026-08-31", gate_version=gate,
                           horizons=(4,), normalizer=lambda cik: norm)
    assert (counts["resolved"], counts["revised"]) == (1, 1)
    first = track.resolutions(gate, 4, revisions="first")
    latest = track.resolutions(gate, 4, revisions="latest")
    assert [r["outcome"] for r in first] == ["CLEAN"]
    assert [r["outcome"] for r in latest] == ["DETERIORATED"]
    assert first[0]["resolved_at"] == track.earliest_resolvable("2023-03-01", 4)
    assert latest[0]["resolved_at"] == "2026-08-31"
    assert first != latest, (
        "'first' and 'latest' being identical is the defect, not a "
        "coincidence of the fixture")


def test_a_same_day_flip_is_never_reported_as_recorded(isolated_db):
    """signal_scores is keyed on (signal_id, horizon_q, resolved_at) and
    written with INSERT OR IGNORE, so a second resolution on the same
    calendar day collides and is swallowed. counts['revised'] was incremented
    without ever reading cur.rowcount: the run printed a revision that was
    never written, and every reader kept the stale label. The append-only
    rule stands -- the flip is not forced over the day's record -- but the
    run says it is held over instead of claiming it landed, and the next
    day's run appends it."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    day = "2026-08-31"
    track.resolve(as_of=day, gate_version=gate, horizons=(4,),
                  normalizer=lambda cik: healthy())
    flip = track.resolve(as_of=day, gate_version=gate, horizons=(4,),
                         normalizer=lambda cik: sick())
    assert flip["revised"] == 1
    stored = [(r["outcome"], r["resolved_at"])
              for r in track.resolutions(gate, 4, revisions="all")]
    assert (flip["revised"] == sum(1 for o, d in stored if d == day)), \
        "a reported revision must correspond to a row that exists"

    # A THIRD distinct outcome on the same day has nowhere to go: the day's
    # row already exists and is never overwritten.
    held = track.resolve(as_of=day, gate_version=gate, horizons=(4,),
                         normalizer=lambda cik: healthy())
    assert held["revised"] == 0
    assert held["same_day_conflict"] == 1
    assert [(r["outcome"], r["resolved_at"])
            for r in track.resolutions(gate, 4, revisions="all")] == stored

    # Not lost, deferred: the next run's date differs and appends it.
    later = track.resolve(as_of="2026-09-01", gate_version=gate, horizons=(4,),
                          normalizer=lambda cik: healthy())
    assert later["revised"] == 1
    assert track.resolutions(gate, 4, revisions="latest")[0]["outcome"] \
        == "CLEAN"


def test_a_flipped_label_appends_rather_than_overwrites(isolated_db):
    """A restatement that changes the outcome writes a SECOND signal_scores
    row. revisions='first' still returns the original; 'all' shows both.
    Overwriting would rewrite history in the one table whose job is
    remembering what was known when."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    track.resolve(as_of="2025-01-01", gate_version=gate, horizons=(4,),
                  normalizer=lambda cik: healthy())
    track.resolve(as_of="2025-06-01", gate_version=gate, horizons=(4,),
                  normalizer=lambda cik: sick())
    first = track.resolutions(gate, 4, revisions="first")
    latest = track.resolutions(gate, 4, revisions="latest")
    everything = track.resolutions(gate, 4, revisions="all")
    assert [r["outcome"] for r in first] == ["CLEAN"]
    assert [r["outcome"] for r in latest] == ["DETERIORATED"]
    assert [r["revision"] for r in everything] == [0, 1]


# ------------------------------------------------ the one-definition pin


def _outcome_set() -> list[harness.Outcome]:
    """Positives with a censored case and an over-cap lead, controls with
    fires -- the corners where the two arithmetics could quietly diverge."""
    out = harness.Outcome
    return [
        out("P1", True, True, False, "2020-05-15", 7, 0.2, 10, n_fires=2,
          regime="2020-covid"),
        out("P2", True, True, True, "2019-02-15", None, 0.1, 10, n_fires=1,
          regime="2020-covid"),                       # censored
        out("P3", True, False, False, None, None, 0.0, 8, n_fires=0,
          regime="2014-16-energy"),                   # miss
        out("P4", True, True, False, "2015-05-15", None, 0.1, 12, n_fires=1,
          regime="2014-16-energy"),                   # lead past the cap
        out("C1", False, False, False, None, None, 0.0, 40, n_fires=0),
        out("C2", False, True, False, "2021-08-15", None, 0.05, 40, n_fires=2),
    ]


def test_live_and_backtest_are_one_definition():
    """live_stats() reproduces harness.verdict()'s arithmetic number for
    number on identical Outcome sets. Without this, ROADMAP 10's 'measured on
    one definition' is a docstring claim, and a live per-quarter rate could
    drift from the definition the frozen reference numbers carry."""
    outs = _outcome_set()
    v = harness.verdict(outs, baseline_fpr=0.0051)
    ls = track.live_stats(outs, baseline_fpr=0.0051)
    assert ls["positive_hit_rate"] == v["checks"]["positive_hit_rate"]["value"]
    assert ls["median_lead_months"] == v["checks"]["median_lead_months"]["value"]
    assert (ls["false_positive_rate_per_quarter"]
            == v["checks"]["false_positive_rate_per_quarter"]["value"])
    assert (ls["false_positive_rate_per_filer"]
            == v["false_positive_rate_per_filer"])
    assert ls["regimes_detected"] == v["regimes_detected"]
    assert ls["n_censored_positives"] == v["n_censored_positives"]
    assert ls["n_assessable_positives"] == v["n_assessable_positives"]
    assert ls["control_filer_quarters"] == v["control_filer_quarters"]


def test_a_hit_rate_with_no_assessable_case_is_none_not_zero():
    """`hit_rate = ... if assessable else 0.0` was the one rate in the block
    that did not degrade to None on an empty denominator. record_payload()
    prints it under level 'per-case' directly beneath the frozen holdout
    0.287, also per-case: a published 0.0 there reads as 'live performance
    has collapsed' when the truth is 'nothing has been measured'. This is
    FINDINGS 6a -- a score of 0.0 beside scoreable=false -- one level up."""
    out = harness.Outcome
    # Five positives the detector fired on, every one censored, so not one is
    # assessable for lead time. The detector hit 5 of 5; the report said 0.0.
    censored = [out(f"P{i}", True, True, True, "2020-05-15", None, 0.2, 10,
                    n_fires=2) for i in range(5)]
    ls = track.live_stats(censored)
    assert ls["positive_hit_rate"] is None
    assert ls["n_positive"] == 5 and ls["n_assessable_positives"] == 0
    assert "not zero" in ls["positive_hit_rate_undefined"]

    empty = track.live_stats([])
    assert empty["positive_hit_rate"] is None
    assert "not zero" in empty["positive_hit_rate_undefined"]

    # And when it IS measurable the number is a number, with no excuse text.
    real = track.live_stats(_outcome_set())
    assert real["positive_hit_rate"] is not None
    assert real["positive_hit_rate_undefined"] is None


def test_the_gate_still_fails_closed_where_a_verdict_is_owed():
    """harness.verdict() keeps its 0.0 and must: there the hit rate feeds a
    pre-registered pass/fail, and an unmeasurable rate has to fail the gate,
    not skip it. live_stats has no pass/fail, which is why the same
    expression is wrong there and right here."""
    out = harness.Outcome
    censored = [out("P1", True, True, True, "2020-05-15", None, 0.2, 10,
                    n_fires=2)]
    v = harness.verdict(censored, baseline_fpr=0.0051)
    assert v["checks"]["positive_hit_rate"]["value"] == 0.0
    assert v["checks"]["positive_hit_rate"]["pass"] is False


def test_live_stats_never_returns_a_verdict():
    """A live surface that prints SHIP re-scores a pre-registered rule that
    has already been spent. Status is MONITORING and no verdict verb exists
    anywhere in the output."""
    ls = track.live_stats(_outcome_set())
    assert ls["status"] == "MONITORING"
    assert "verdict" not in ls
    dumped = json.dumps(ls)
    assert "SHIP" not in dumped
    assert '"KILL"' not in dumped


# ----------------------------------------------- denominators kept apart


def test_fpr_denominators_are_distinct_keys():
    """Phase 0's 0.0383 counts fires among quarters of filers that NEVER
    deteriorated. The clean-quarter analogue includes the quiet quarters of
    filers that broke later -- a different denominator. Reporting one as the
    other is the exact defect class FINDINGS documents."""
    rows = [
        {"cik": "A", "score": 50.0, "outcome": "CLEAN"},
        {"cik": "A", "score": 50.0, "outcome": "DETERIORATED"},
        {"cik": "B", "score": 10.0, "outcome": "CLEAN"},
        {"cik": "B", "score": 50.0, "outcome": "CLEAN"},
    ]
    qs = track.quarter_stats(rows)
    clean = qs["fpr_per_quarter_clean"]
    ctrl = qs["fpr_per_quarter_control_filer"]
    assert clean["quarters"] == 3 and ctrl["quarters"] == 2
    assert clean["value"] != ctrl["value"]
    assert ctrl["comparable_to_reference"] is True
    assert clean["comparable_to_reference"] is False
    assert "biased DOWNWARD" in ctrl["note"]


def test_live_per_quarter_recall_is_never_compared_to_the_case_rate():
    """The holdout 0.287 is per-CASE, with censoring and a lead cap; the only
    per-quarter reference that exists is the tuning 0.1396. A per-quarter
    block carrying 0.287 would be comparing two different experiments."""
    rows = [{"cik": "A", "score": 50.0, "outcome": "DETERIORATED"},
            {"cik": "B", "score": 10.0, "outcome": "CLEAN"}]
    qs = track.quarter_stats(rows)
    recall = qs["recall_per_quarter"]
    assert recall["level"] == "per-quarter"
    assert recall["floor"]["value"] == 0.1396
    assert recall["floor"]["split"] == "tuning"
    assert "0.287" not in json.dumps(qs)


def test_above_reference_wording_is_banned():
    """The reference is a failed test, not a target: 'above reference' reads
    as 'performing better than expected' when it means 'exceeding a number
    that already failed'. The floor framing is the only one allowed."""
    for mod in (track, cli):
        assert "ABOVE_REFERENCE" not in inspect.getsource(mod)


# ---------------------------------------------------------- the monitor


def _resolve_det_rows(gate: str, n: int, fires: int) -> None:
    """Insert n resolved deteriorating quarters, `fires` of them fired.
    Direct inserts: signal_scores has no triggers, and the join needs real
    signals rows, which put_signals wrote."""
    conn = edgar.db()
    try:
        with conn:
            for sid, in conn.execute(
                    "SELECT signal_id FROM signals WHERE gate_version = ? "
                    "ORDER BY seq LIMIT ?", (gate, n)).fetchall():
                conn.execute(
                    "INSERT INTO signal_scores (signal_id, horizon_q, "
                    "resolved_at, outcome, event_period, "
                    "n_quarters_observed, criteria, label_rule) "
                    "VALUES (?,4,'2026-08-30','DETERIORATED','2024-12-31',"
                    "4,'[]',?)", (sid, track.label_rule(4)))
    finally:
        conn.close()


def test_monitor_requires_a_minimum_before_it_will_speak(isolated_db):
    """Ten resolved deteriorating quarters with zero fires is INSUFFICIENT,
    not BELOW_FLOOR. A point comparison against the floor on ten cases is
    noise, and saying so is the honest output."""
    gate = put_signals([(f"00000000{i:02d}", f"T{i}", "2023-03-01", 10.0)
                        for i in range(10)])
    _resolve_det_rows(gate, 10, 0)
    mon = track.monitor(gate, 4)
    assert mon["status"] == "INSUFFICIENT"
    assert mon["recall_per_quarter"] is None
    assert mon["n_required"] == track.MIN_RESOLVED_DETERIORATING


def test_monitor_speaks_with_an_interval_once_the_minimum_is_met(isolated_db):
    """Sixty resolved deteriorating quarters, half of them fired: the status
    is a floor comparison with a Wilson interval, and beating the floor is
    named as improvement over a failure, never success."""
    gate = put_signals([(f"000000{i:04d}", f"T{i}", "2023-03-01",
                         50.0 if i < 30 else 10.0) for i in range(60)])
    _resolve_det_rows(gate, 60, 30)
    mon = track.monitor(gate, 4)
    assert mon["status"] == "ABOVE_FLOOR"
    assert mon["recall_per_quarter"] == 0.5
    assert mon["wilson"][0] > mon["floor"]["value"] == 0.1396
    assert "never a grade" in mon["floor"]["meaning"]


def test_monitor_never_retunes_or_pulls(isolated_db):
    """monitor() reads and reports. It must not touch a gate constant, write
    a snapshot row, or modify any file -- auto-retuning against live outcomes
    would overfit the only unspent data that exists."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    fingerprint_before = signals_v3.gate_fingerprint()
    track.monitor(gate, 4)
    assert signals_v3.gate_fingerprint() == fingerprint_before
    conn = edgar.db()
    try:
        rows = conn.execute("SELECT COUNT(*) FROM track_record").fetchone()[0]
    finally:
        conn.close()
    assert rows == 0


# ---------------------------------------------------------- the payload


def test_track_payload_refuses_to_build_without_the_kill_record(
        isolated_db, tmp_path, monkeypatch):
    """No KILL record, no track record. A payload built from a default would
    label the gate correctly today for the wrong reason, and mislabel a
    future gate that actually passed."""
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(status, "_cache", {})
    with pytest.raises(RuntimeError, match="phase0.json is missing"):
        track.record_payload(gate_version="v-any")


def test_track_payload_is_stamped_and_leads_with_the_banner(isolated_db):
    """Every emitted score carries the frozen verdict; the payload is a score
    surface, so it does too, and its banner states the failure before any
    live number appears."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    track.resolve(as_of="2026-08-30", gate_version=gate,
                  normalizer=lambda cik: healthy())
    payload = track.record_payload(gate_version=gate)
    status.assert_stamped(payload)
    assert payload["banner"].startswith("NOT VALIDATED")
    assert payload["reference"]["holdout_per_case"]["level"] == "per-case"
    assert "0.287" not in json.dumps(payload["horizons"])
    assert payload["horizons"]["4"]["comparable_to_reference"] is True
    assert payload["horizons"]["1"]["comparable_to_reference"] is False


def test_replay_rows_and_live_rows_are_never_pooled(isolated_db):
    """Replayed tuning quarters are already spent and prove nothing about
    unseen data. The payload reports them under a separate key with their own
    counts, never averaged into the live record."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)],
                       source="scan")
    put_signals([("0000000002", "U", "2023-03-01", 10.0)], source="replay")
    track.resolve(as_of="2026-08-30", gate_version=gate,
                  normalizer=lambda cik: healthy())
    block = track.record_payload(gate_version=gate)["horizons"]["4"]
    assert block["live"]["n_resolved"] == 1
    assert block["replay_backfill"]["n_resolved"] == 1
    assert "never pooled" in block["note"]


def test_snapshot_writes_dated_rows_once_per_day(isolated_db):
    """The decay time series: one dated row per horizon, and a same-day
    re-run writes nothing new."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    track.resolve(as_of="2026-08-30", gate_version=gate,
                  normalizer=lambda cik: healthy())
    payload = track.record_payload(gate_version=gate)
    assert track.snapshot(payload) == len(track.HORIZONS)
    assert track.snapshot(payload) == 0


# --------------------------------------------------------------- the CLI


def test_track_cli_prints_the_banner_before_any_number(isolated_db):
    """The failed-test banner leads and there is no flag that suppresses it.
    A track record that opens with rates is an alert with a disclaimer."""
    put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    result = CliRunner().invoke(cli.app, ["track"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("NOT VALIDATED")
    assert "floor" in result.output


def test_track_cli_exits_nonzero_without_the_kill_record(
        isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(status, "_cache", {})
    result = CliRunner().invoke(cli.app, ["track"])
    assert result.exit_code == 2


def test_resolve_cli_reports_in_plain_words(isolated_db, monkeypatch):
    """The daily command speaks sentences, not counters -- and routes the
    reader to `ledgerline track` next."""
    gate = put_signals([("0000000001", "T", "2023-03-01", 10.0)])
    norm = healthy()
    monkeypatch.setattr(edgar, "normalize", lambda cik: norm)
    result = CliRunner().invoke(
        cli.app, ["resolve", "--as-of", "2026-08-30",
                  "--gate-version", gate])
    assert result.exit_code == 0, result.output
    assert "Newly judged: 3" in result.output
    assert "ledgerline track" in result.output


def test_pending_cli_names_the_earliest_decision_date(isolated_db):
    """A thin track record must read as young, not bad: every waiting
    assessment shows when its answer first becomes possible."""
    gate = put_signals([("0000000001", "T", "2023-09-01", 10.0)])
    result = CliRunner().invoke(cli.app, ["pending", "--gate-version", gate])
    assert result.exit_code == 0, result.output
    assert "answer possible from" in result.output
    assert track.earliest_resolvable("2023-09-01", 1) in result.output
