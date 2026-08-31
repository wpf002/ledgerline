"""
Signal-store tests: the persistence contract a future track record depends on.

Each test pins one decision: full denominator (quiet and unscoreable rows are
rows), append-only (triggers, not prose), content-addressed idempotency,
gate-version coexistence, provenance enforcement, and the KILL stamp on every
surface. Same isolation idiom as test_bootstrap_fixes.py: edgar.DATA and
edgar.DB_PATH are redirected to tmp_path, so the live state.db is never
touched. No network.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from ledgerline import backtest, cli, edgar, emit, ingest, signals_v3, status
from tests.unit.test_gate import build_filer


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def quiet_verdict() -> dict:
    """A scoreable, unflagged quarter -- the row a fires-only store drops."""
    norm = build_filer(quarters=32)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2023-12-01",
                              norm=norm)
    assert res["scoreable"] and not res["gated_in"]
    return res


def fired_verdict() -> dict:
    norm = build_filer(quarters=32, shock={"ocf": 0.35})
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2024-03-01",
                              norm=norm)
    assert res["scoreable"] and res["flags"]
    return res


def unscoreable_verdict() -> dict:
    norm = build_filer(quarters=16)
    res = signals_v3.evaluate("TEST", "0000000001", as_of="2017-06-01",
                              norm=norm)
    assert not res["scoreable"]
    return res


def rows(where: str = "", args: tuple = ()) -> list[tuple]:
    conn = edgar.db()
    try:
        return conn.execute(
            "SELECT signal_id, seq, scoreable, gated_in, score, reason, "
            "gate_version, validation_status, record FROM signals "
            + where, args).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------- the denominator


def test_every_scoreable_evaluation_is_persisted_not_only_fires(isolated_db):
    """A quiet scoreable quarter writes a row with gated_in=0. Without it the
    store has no denominator and can compute neither a false-positive rate
    nor any future comparison of firing volume between gates."""
    emit.emit(quiet_verdict(), source="score")
    got = rows()
    assert len(got) == 1
    assert got[0][2] == 1 and got[0][3] == 0
    assert got[0][4] is not None


def test_unscoreable_evaluation_is_persisted_with_its_reason(isolated_db):
    """Abstention is a record, not a silence: scoreable=0, score NULL, reason
    non-empty. A filer that silently vanishes from the store is survivorship
    bias with extra steps."""
    emit.emit(unscoreable_verdict(), source="score")
    got = rows("WHERE scoreable = 0")
    assert len(got) == 1
    assert got[0][4] is None, "score must be NULL for unscoreable, never 0.0"
    assert got[0][5], "the reason must travel with the abstention"


def test_run_denominator_travels_with_every_record(isolated_db):
    """The run block is embedded in EVERY record, built after all evaluations
    finished (two-pass): a fired record read in isolation cannot be seen
    without also seeing that N of M filers were unassessable that day."""
    emit.emit_run([quiet_verdict(), unscoreable_verdict()], source="scan",
                  run_id=7, run_date="2026-08-30")
    for r in rows():
        run = json.loads(r[8])["run"]
        assert run["evaluated"] == 2
        assert run["scoreable"] == 1
        assert run["unscoreable"] == 1
        assert run["unscoreable_reasons"] == {"SHORT_HISTORY": 1}


# ------------------------------------------------- idempotency + coexistence


def test_reemitting_an_identical_evaluation_is_idempotent(isolated_db):
    """Content-addressed ids make replay safely re-runnable: emit twice, one
    row, and the second call says so instead of writing."""
    v = quiet_verdict()
    first = emit.emit_run([v], source="replay")
    second = emit.emit_run([v], source="replay")
    assert first["written"] == 1 and second["written"] == 0
    assert second["already"] == 1
    assert len(rows()) == 1


def test_a_gate_change_writes_a_new_row_rather_than_overwriting(
        isolated_db, monkeypatch):
    """The same (cik, as_of) under a changed gate constant coexists with the
    old row under a new gate_version. That coexistence is the entire
    mechanism by which a revised gate is ever compared to the one that
    returned KILL."""
    v = quiet_verdict()
    emit.emit(v, source="score")
    monkeypatch.setattr(signals_v3, "THRESHOLD", 50.0)
    emit.emit(v, source="score")
    got = rows()
    assert len(got) == 2
    assert len({r[6] for r in got}) == 2, "two gate versions must both be present"


def test_seq_is_a_dense_monotonic_cursor(isolated_db):
    """seq exists so a future export can resume from a cursor; a duplicate
    emit consumes no seq."""
    emit.emit_run([quiet_verdict(), unscoreable_verdict()], source="scan")
    emit.emit(quiet_verdict(), source="score")  # duplicate, ignored
    assert sorted(r[1] for r in rows()) == [1, 2]


# ------------------------------------------------------------- append-only


def test_signals_table_is_append_only(isolated_db):
    """Two pins: the writer's source contains no UPDATE or DELETE against
    signals (same technique as the price-never-enters-a-criterion test), and
    the schema triggers RAISE(ABORT) on both -- prose is not an invariant."""
    with open(emit.__file__) as fh:
        src = fh.read()
    assert "UPDATE signals" not in src
    assert "DELETE FROM signals" not in src

    emit.emit(quiet_verdict(), source="score")
    conn = edgar.db()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE signals SET score = 99.0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM signals")
    conn.close()
    assert len(rows()) == 1


# ---------------------------------------------------------------- payloads


def test_persisted_flag_payload_round_trips_verbatim(isolated_db):
    """Every ZFlag field survives dataclass -> json -> sqlite -> json
    unchanged. A future refit consumes exactly these."""
    v = fired_verdict()
    emit.emit(v, source="score")
    stored = emit.load_signals(cik="0000000001")[0]
    assert stored["flags"] == v["flags"]
    for f in stored["flags"]:
        for key in ("code", "label", "weight", "z", "value", "baseline_median",
                    "baseline_scale", "baseline_n", "floored", "detail",
                    "sources", "filed"):
            assert key in f


def test_full_z_vector_is_persisted_not_only_fired_diagnostics(isolated_db):
    """Verdict.z exists so calibration fits the numbers production computes;
    a store that dropped sub-trigger z values would break any refit."""
    v = quiet_verdict()
    assert v["z"], "fixture must have computable diagnostics"
    assert not v["flags"], "fixture must be sub-trigger everywhere"
    emit.emit(v, source="score")
    stored = emit.load_signals(cik="0000000001")[0]
    assert stored["z"] == v["z"]


# ------------------------------------------------------------- enforcement


def test_a_scoreable_row_with_no_accessions_is_refused(isolated_db):
    """'A score traces back to accessions or it does not ship', enforced at
    the last place it can be -- the stored row outlives the fact cache."""
    v = quiet_verdict()
    v["accessions"] = []
    with pytest.raises(RuntimeError, match="accession"):
        emit.emit(v, source="score")
    assert rows() == []


def test_an_unstamped_verdict_is_refused(isolated_db):
    """No path from a verdict to the store that skips the frozen Phase 0
    stamp. status.assert_stamped is the single enforcement point."""
    v = quiet_verdict()
    del v["gate_status"]
    with pytest.raises(RuntimeError, match="unstamped"):
        emit.emit(v, source="score")
    assert rows() == []


def test_every_persisted_row_carries_the_kill_status(isolated_db):
    """validation_status is non-empty and names the KILL on every row --
    quiet and unscoreable included. A row read out of this table five years
    from now still says the gate failed its own test, with the frozen
    numbers inside the record."""
    emit.emit_run([quiet_verdict(), unscoreable_verdict()], source="scan")
    for r in rows():
        assert "KILL" in r[7]
        record = json.loads(r[8])
        status.assert_stamped(record["verdict"])


def test_verdict_accessions_are_populated_even_on_abstention():
    """evidence_accessions rides every path where a snapshot exists --
    'why was this filer not scoreable' also has to trace to filings."""
    assert quiet_verdict()["accessions"]
    assert unscoreable_verdict()["accessions"]


def test_gate_fingerprint_names_every_scoring_constant():
    """The fingerprint hashes into gate_version; a constant that can change
    a score but is missing here would let a retune silently blend two gates
    into one track record."""
    fp = signals_v3.gate_fingerprint()
    assert set(fp) == {"tracked", "z_trigger", "threshold", "score_divisor",
                       "z_cap", "min_flags", "min_history", "min_baseline_n",
                       "max_baseline", "required_coverage", "coverage_min"}
    assert set(fp["tracked"]) == set(signals_v3.TRACKED)


# ------------------------------------------------------------ CLI surfaces


runner = CliRunner()


def test_replay_refuses_the_holdout_outright(isolated_db):
    """The holdout was scored once, 2026-08-30. A queryable table of holdout
    scores is a re-scoring surface, so replay refuses -- no confirm flag, no
    override -- and writes nothing."""
    result = runner.invoke(cli.app, ["replay", "--split", "holdout"])
    assert result.exit_code != 0
    assert "refuses" in result.output
    assert "2026-08-30" in result.output
    assert rows() == []


def test_signals_cli_prints_the_banner_before_the_first_entry(isolated_db):
    """No surface shows a score without the fact that the detector failed its
    own test, and the banner leads -- a feed that leads with entries and
    buries the failed test is an alert with a disclaimer."""
    emit.emit(quiet_verdict(), source="score")
    result = runner.invoke(cli.app, ["signals"])
    assert result.exit_code == 0
    assert "NOT VALIDATED" in result.output
    assert result.output.index("NOT VALIDATED") < result.output.index("TEST")


def test_signals_cli_shows_abstentions_with_their_reason(isolated_db):
    """The read surface keeps the denominator visible: a company that could
    not be assessed appears as a sentence, not a dropped row."""
    emit.emit_run([quiet_verdict(), unscoreable_verdict()], source="scan")
    result = runner.invoke(cli.app, ["signals"])
    assert "cannot assess" in result.output
    assert "could not be assessed" in result.output


# ----------------------------------------------------------- the scan hook


def test_scan_score_persists_every_evaluation_with_the_full_denominator(
        isolated_db, monkeypatch):
    """scan --score emits AFTER its loop finishes, so every stored record's
    run block carries the completed day's denominator -- including the filer
    that could not be assessed."""
    monkeypatch.setattr(
        edgar, "universe",
        lambda: {c: {"cik": c, "ticker": f"T{i}", "name": f"T{i} Inc"}
                 for i, c in enumerate(["1", "2"])})
    monkeypatch.setattr(
        edgar, "daily_index",
        lambda d, refresh=False: [
            {"form": "10-Q", "name": "X", "cik": c, "filing_date": "2026-08-28",
             "file": "edgar/data/x", "accession": f"acc-{c}"}
            for c in ("1", "2")])
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False:
            {"cik": cik, "status": "ok", "restatements": 0})

    def fake_evaluate(ticker, cik, as_of=None, norm=None):
        if cik == "1":
            return status.stamp({"ticker": ticker, "cik": cik, "as_of": as_of,
                                 "score": 10.0, "gated_in": False,
                                 "scoreable": True, "reason": None,
                                 "reason_code": None, "flags": [], "z": {},
                                 "accessions": ["acc-1"]})
        return status.stamp({"ticker": ticker, "cik": cik, "as_of": as_of,
                             "score": None, "gated_in": False,
                             "scoreable": False,
                             "reason": "insufficient own-history (3q of 12)",
                             "reason_code": "SHORT_HISTORY", "flags": [],
                             "z": {}, "accessions": []})

    monkeypatch.setattr(ingest.signals_v3, "evaluate", fake_evaluate)
    ingest.scan(days_back=1, refresh=False, score=True)

    stored = emit.load_signals(source="scan")
    assert len(stored) == 2
    for row in stored:
        run = row["record"]["run"]
        assert run["evaluated"] == 2
        assert run["unscoreable_reasons"] == {"SHORT_HISTORY": 1}
    assert {r["scoreable"] for r in stored} == {0, 1}


def test_run_test_refuses_the_spent_holdout(isolated_db, monkeypatch):
    """`run-test --split holdout` had no guard at all. replay refused the
    sealed half and calibrate refused it, but the one command whose job is to
    score a split would rescore it -- overwriting reports/backtest_holdout.json,
    the only full record of the 2026-08-30 failure -- and print a verdict sheet.
    It refuses now: non-zero exit, retests.json named as the legitimate
    alternative, no override flag, and the scorer is never reached.

    The scorer is stubbed rather than trusted: a test that let the old code
    through would itself be a second scoring of the sealed half.
    """
    reached = []
    monkeypatch.setattr(backtest, "run",
                        lambda **kw: reached.append(kw) or {"outcomes": []})
    result = runner.invoke(cli.app, ["run-test", "--split", "holdout"])
    assert result.exit_code != 0
    assert not reached, "the sealed half reached the scorer"
    assert "refuses" in result.output
    assert "no override flag" in result.output
    assert "retests.json" in result.output
    assert "2026-08-30" in result.output


def test_run_test_prints_the_verdict_before_any_result(isolated_db, monkeypatch):
    """A sheet of PASS rows with the failed test nowhere on it is the loudest
    way this repo can imply a working gate, and run-test was the one
    score-showing command with no banner on either branch."""
    monkeypatch.setattr(backtest, "run", lambda **kw: {
        "outcomes": [{"fired": True}, {"fired": False}]})
    result = runner.invoke(cli.app, ["run-test"])
    assert result.exit_code == 0
    assert result.output.index("NOT VALIDATED") < result.output.index("practice half")


def test_narrations_prints_the_banner_before_the_first_summary(isolated_db):
    """Only flagged assessments are narrated, so every line this command
    prints is confident model prose about a company the failed gate fired on.
    The single-narration view already led with the verdict; this listing -- the
    most product-like output the tool produces -- carried none at all, on
    either stream."""
    conn = edgar.db()
    with conn:
        conn.execute(
            "INSERT INTO narrations (cik, ticker, as_of, period, payload_sha, "
            "status, attempts, headline, input_tokens, output_tokens, "
            "created_at) VALUES ('0000000001','TEST','2026-08-31','2026-06-30',"
            "'sha1','narrated',1,'TEST margins and cash conversion both broke "
            "from its own pattern.',900,120,'2026-08-31T00:00:00Z')")
    conn.close()
    result = runner.invoke(cli.app, ["narrations"])
    assert result.exit_code == 0
    assert "NOT VALIDATED" in result.output
    assert result.output.index("NOT VALIDATED") < result.output.index("TEST")
