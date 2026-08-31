"""
Restatement events and provenance.

The vintage work (FINDINGS §5) put the full revision history on every
normalized row; persist_metrics threw it away. These tests pin the persistence
of that history, the event stream built from vintage GROWTH (not /A forms --
measured, 0.96% of revisions arrive on one), the materiality flag, and the
accession trace the README promised and ZFlag never carried. The headline
fixture is the same real revision the vintage work was built for: ABT Q1 2012,
$9.457B filed 2012-05-08, restated to $5.284B in the 2013-05-08 10-Q.
"""
from __future__ import annotations

import sqlite3

import pytest

from ledgerline import derive, edgar, ingest, provenance, restate, signals_v3, status
from tests.unit.test_gate import build_filer
from tests.unit.test_ingestion import ytd_facts

CIK = "0000000001"


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    return edgar.db()


def _abt(restated: bool) -> dict:
    rows = [
        {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31",
         "value": 9_456_633_000.0, "filed": "2012-05-08", "form": "10-Q",
         "accession": "orig"},
    ]
    if restated:
        rows.append(
            {"metric": "revenue", "start": "2012-01-01", "end": "2012-03-31",
             "value": 5_283_685_000.0, "filed": "2013-05-08", "form": "10-Q",
             "accession": "rs1"})
    return {"revenue": derive.derive_quarterly(rows)}


# ----------------------------------------------------------- vintage storage


def test_vintages_survive_persistence(tmp_path, monkeypatch):
    """persist_metrics keeps exactly one row per period -- the latest view,
    which is its job -- and has therefore been silently discarding the
    revision history since FINDINGS §5. The vintages table is where the
    history survives: every vintage lands, `metrics` still holds one row."""
    conn = _db(tmp_path, monkeypatch)
    norm = _abt(restated=True)
    n = restate.persist_vintages(conn, CIK, norm)
    conn.close()
    assert n == 2
    edgar.persist_metrics(CIK, norm)

    conn = edgar.db()
    vints = conn.execute("SELECT filed, value FROM vintages "
                         "ORDER BY filed").fetchall()
    metrics = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    conn.close()
    assert vints == [("2012-05-08", 9_456_633_000.0),
                     ("2013-05-08", 5_283_685_000.0)]
    assert metrics == 1


# -------------------------------------------------------------- the detector


def test_restatement_detected_from_vintage_growth(tmp_path, monkeypatch):
    """ABT Q1 2012: ingest the original, then the filing that restated it.
    One event, with both vintages and both filing dates intact."""
    conn = _db(tmp_path, monkeypatch)
    restate.persist_vintages(conn, CIK, _abt(restated=False))

    events = restate.diff(conn, CIK, _abt(restated=True))
    conn.close()
    assert len(events) == 1
    e = events[0]
    assert e.prior_value == 9_456_633_000.0
    assert e.value == 5_283_685_000.0
    assert e.prior_filed == "2012-05-08"
    assert e.filed == "2013-05-08"
    assert e.material


def test_restatement_detected_without_an_amended_form(tmp_path, monkeypatch):
    """The ABT revision arrived in an ordinary 10-Q, like 99% of measured
    revisions (6 of 624 were on /A forms). label.py's _restatement keys on the
    form and catches ~1%; the detector here keys on vintage growth and flags
    the form instead."""
    conn = _db(tmp_path, monkeypatch)
    restate.persist_vintages(conn, CIK, _abt(restated=False))
    events = restate.diff(conn, CIK, _abt(restated=True))
    conn.close()
    assert events[0].on_amendment is False


def test_amendment_flag_travels_on_the_event(tmp_path, monkeypatch):
    """The ROADMAP's 10-K/A / 10-Q/A case, as a labeled subset rather than
    the trigger."""
    conn = _db(tmp_path, monkeypatch)
    restate.persist_vintages(conn, CIK, _abt(restated=False))
    amended = _abt(restated=True)
    amended["revenue"][0]["vintages"][1]["form"] = "10-Q/A"
    events = restate.diff(conn, CIK, amended)
    conn.close()
    assert events[0].on_amendment is True


def test_event_is_emitted_not_applied_over_the_prior_vintage(
        tmp_path, monkeypatch):
    """Emit, do not overwrite: after the restated ingest, the superseded
    vintage is still in `vintages` at its original value and filed date."""
    conn = _db(tmp_path, monkeypatch)
    restate.persist_vintages(conn, CIK, _abt(restated=False))
    norm = _abt(restated=True)
    events = restate.diff(conn, CIK, norm)
    restate.persist_vintages(conn, CIK, norm)
    restate.record(conn, events, run_id=1)

    original = conn.execute(
        "SELECT value FROM vintages WHERE filed='2012-05-08'").fetchone()
    conn.close()
    assert original == (9_456_633_000.0,)


def test_diff_runs_before_the_write_so_a_revision_is_not_self_erased(
        tmp_path, monkeypatch):
    """The ordering IS the correctness of the module: persist first and the
    stored list already contains the new vintage, so the revision looks
    already-known and no event is ever emitted. ingest_filer diffs first."""
    conn = _db(tmp_path, monkeypatch)
    restate.persist_vintages(conn, CIK, _abt(restated=False))
    norm = _abt(restated=True)
    restate.persist_vintages(conn, CIK, norm)  # the WRONG order
    assert restate.diff(conn, CIK, norm) == [], (
        "diff after the write found nothing -- which is why it must run first")
    conn.close()


def test_reingesting_unchanged_facts_emits_no_events(tmp_path, monkeypatch):
    """Idempotence: a cron job that re-emits every restatement daily is a
    broken feed. The PK on the superseding vintage guards record() too."""
    conn = _db(tmp_path, monkeypatch)
    norm = _abt(restated=True)
    events = restate.diff(conn, CIK, norm)
    restate.persist_vintages(conn, CIK, norm)
    restate.record(conn, events, run_id=1)

    assert restate.diff(conn, CIK, norm) == []
    restate.record(conn, events, run_id=2)  # a re-record must not duplicate
    n = conn.execute("SELECT COUNT(*) FROM restatements").fetchone()[0]
    conn.close()
    assert n == 1


# --------------------------------------------------------------- materiality


def test_immaterial_revision_is_recorded_but_not_flagged_material(
        tmp_path, monkeypatch):
    """42.5% of measured revisions are under 1% relative -- rounding and
    reclass. They are written with material=0, never dropped: filtering at
    write time destroys the denominator needed to say what fraction of
    restatements matter."""
    conn = _db(tmp_path, monkeypatch)
    rows = [
        {"metric": "revenue", "start": "2023-01-01", "end": "2023-03-31",
         "value": 1000.0, "filed": "2023-05-10", "form": "10-Q",
         "accession": "a"},
        {"metric": "revenue", "start": "2023-01-01", "end": "2023-03-31",
         "value": 1003.0, "filed": "2024-02-20", "form": "10-K",
         "accession": "b"},
    ]
    norm = {"revenue": derive.derive_quarterly(rows)}
    events = restate.diff(conn, CIK, norm)
    restate.persist_vintages(conn, CIK, norm)
    restate.record(conn, events, run_id=1)
    conn.close()

    assert len(events) == 1
    assert events[0].material is False
    assert restate.events(cik=CIK, material_only=True) == []
    all_rows = restate.events(cik=CIK, material_only=False)
    assert len(all_rows) == 1
    assert all_rows[0]["material"] == 0


def test_sign_flip_cannot_produce_an_unbounded_rel_change():
    """Against |prior| alone the measured p99 was 57.8: sign flips and
    near-zero priors make the ratio meaningless. max(|prior|, |new|, 1.0)
    bounds it -- a -5 to +5 flip is 2.0, not a 2-billion-percent headline."""
    assert restate.rel_change(-5.0, 5.0) == 2.0
    assert restate.rel_change(0.001, 1.0) < 1.0  # the 1.0 floor absorbs it
    assert restate.rel_change(100.0, 101.0) == 1.0 / 101.0


# ------------------------------------------------- derived quarters and events


def test_restating_a_cumulative_emits_events_on_both_adjacent_quarters(
        tmp_path, monkeypatch):
    """A restated 9M cumulative changes the derived quarter on BOTH sides of
    it (Q3 = 9M - 6M, Q4 = FY - 9M). derive_quarterly already produces a
    vintage at every date either input moved, so both period ends get events
    -- provided the neighbours were restated on the same basis, which is what
    a real comparative restatement does."""
    conn = _db(tmp_path, monkeypatch)
    base = ytd_facts("ocf", 2020, 1, [10.0, 20.0, 30.0, 40.0])
    norm1 = {"operating_cash_flow": derive.derive_quarterly([
        {"metric": "operating_cash_flow", "start": r["start"], "end": r["end"],
         "value": r["val"], "filed": r["filed"], "form": r["form"],
         "accession": r["accn"]} for r in base])}
    restate.persist_vintages(conn, CIK, norm1)

    # A later 10-K restates the comparative cumulatives: H1, 9M and FY all
    # move (28/55/90 against 30/60/100), so Q3 becomes 27 and Q4 becomes 35.
    restated = [
        {"start": "2020-01-01", "end": e, "val": v, "form": "10-K",
         "filed": "2021-05-10", "accn": f"restated-{e}"}
        for e, v in (("2020-06-30", 28.0), ("2020-09-30", 55.0),
                     ("2020-12-31", 90.0))
    ]
    norm2 = {"operating_cash_flow": derive.derive_quarterly([
        {"metric": "operating_cash_flow", "start": r["start"], "end": r["end"],
         "value": r["val"], "filed": r["filed"], "form": r["form"],
         "accession": r["accn"]} for r in base + restated])}
    events = restate.diff(conn, CIK, norm2)
    conn.close()
    touched = {e.end_date for e in events}
    assert "2020-09-30" in touched, "Q3 (9M - 6M) did not get an event"
    assert "2020-12-31" in touched, "Q4 (FY - 9M) did not get an event"


def test_one_sided_restatement_emits_no_event(tmp_path, monkeypatch):
    """The DLTR discontinued-ops case: when only the FY cumulative is restated,
    derive.same_basis refuses the derived quarter, so there is no new vintage
    -- and a quarter that was never published cannot have been restated."""
    conn = _db(tmp_path, monkeypatch)
    base = [
        {"metric": "revenue", "start": "2022-01-30", "end": "2022-10-29",
         "value": 20_602.0, "filed": "2022-11-22", "form": "10-Q",
         "accession": "9m"},
        {"metric": "revenue", "start": "2022-01-30", "end": "2023-01-28",
         "value": 28_318.0, "filed": "2023-03-10", "form": "10-K",
         "accession": "fy"},
    ]
    norm1 = {"revenue": derive.derive_quarterly(base)}
    restate.persist_vintages(conn, CIK, norm1)

    restated_fy = base + [
        {"metric": "revenue", "start": "2022-01-30", "end": "2023-01-28",
         "value": 15_406.0, "filed": "2025-03-26", "form": "10-K",
         "accession": "fy-restated"},
    ]
    norm2 = {"revenue": derive.derive_quarterly(restated_fy)}
    events = restate.diff(conn, CIK, norm2)
    conn.close()
    assert events == [], "a refused derivation must not manufacture an event"


# ------------------------------------------------------------------ provenance


def test_every_fired_flag_traces_to_at_least_one_accession():
    """The README promises 'a score traces back to accessions or it does not
    ship'; ZFlag published z and its baseline statistics with no accession.
    Every fired flag now carries the filings behind its current-quarter
    inputs."""
    norm = build_filer(quarters=32, shock={"ocf": 0.35})
    res = signals_v3.evaluate("T", CIK, as_of="2024-03-01", norm=norm)
    assert res["flags"], "the fixture is supposed to fire"
    for f in res["flags"]:
        assert f["sources"], f"{f['code']} fired without citing a filing"
        assert f["filed"] is not None
    assert res["provenance_label"] == "TRACED"
    assert res["provenance"]["flags"]


def test_reading_with_untraceable_inputs_abstains():
    """Measured: 0 of 21,032 rows lack an accession, so this fires on nothing
    today -- it is a regression guard on the invariant, and the reading
    ABSTAINS (not filters) through the existing unscoreable channel."""
    norm = build_filer(quarters=32, shock={"ocf": 0.35})
    for rows in norm.values():
        for r in rows:
            r["sources"] = []
            for v in r.get("vintages", []):
                v["sources"] = []
    res = signals_v3.evaluate("T", CIK, as_of="2024-03-01", norm=norm)
    assert res["provenance_label"] == "UNTRACED"
    assert res["scoreable"] is False
    assert res["score"] is None
    assert res["abstain_reason"]
    status.assert_stamped(res)


def test_high_derived_fraction_is_labeled_not_suppressed(monkeypatch):
    """Derivation is the normal path -- observed median 0.294, max 0.457
    across 34 filers -- so a high fraction is surfaced as a tripwire label,
    never an abstention: suppressing it would discard exactly the data the
    FINDINGS §2 fix recovered."""
    monkeypatch.setattr(provenance, "DERIVED_FRACTION_HIGH", 0.01)
    norm = build_filer(quarters=32)
    res = signals_v3.evaluate("T", CIK, as_of="2023-12-01", norm=norm)
    assert res["scoreable"] is True
    assert res["provenance"]["derived_fraction_high"] is True
    assert res["provenance"]["derived_fraction_observed"]["max"] == 0.457


# ------------------------------------------------- the two writes are one


def _revised_facts() -> dict:
    """One period filed twice: $9.457B in 2012, restated to $5.284B in 2013 --
    the ABT revision above, in the raw shape companyfacts serves."""
    def fact(val: float, filed: str, accn: str) -> dict:
        return {"start": "2012-01-01", "end": "2012-03-31", "val": val,
                "form": "10-Q", "filed": filed, "fy": 2012, "fp": "Q1",
                "accn": accn}

    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fact(9_456_633_000.0, "2012-05-08", "orig"),
        fact(5_283_685_000.0, "2013-05-08", "rs1"),
    ]}}}}}


def _break_the_event_write(on: bool) -> None:
    """Make the write to `restatements` fail, the way a Ctrl-C or any raised
    exception between the two writes did. A trigger and not a monkeypatch,
    because ingest_filer opens its own connection."""
    conn = edgar.db()
    with conn:
        if on:
            conn.execute(
                "CREATE TRIGGER events_fail BEFORE INSERT ON restatements "
                "BEGIN SELECT RAISE(ABORT, 'interrupted'); END")
        else:
            conn.execute("DROP TRIGGER IF EXISTS events_fail")
    conn.close()


def test_an_interrupted_ingest_does_not_destroy_the_revision_history(
        tmp_path, monkeypatch):
    """The defect: ingest_filer ran diff, persist_vintages and record as three
    separate autocommitted transactions. persist_vintages is what makes a
    revision already-known -- diff keys on the stored `filed` dates -- so a
    crash after it and before record left the baseline advanced and the events
    gone, permanently and silently: the rerun re-downloads, re-normalizes and
    finds nothing new. Measured on the live cache, CIK 0000001750 lost all 125
    of its events that way. One transaction, so the rerun still sees them."""
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(edgar, "universe",
                        lambda: {CIK: {"cik": CIK, "ticker": "T", "name": "T"}})
    monkeypatch.setattr(edgar, "companyfacts",
                        lambda cik, refresh=False: _revised_facts())

    _break_the_event_write(True)
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        ingest.ingest_filer(CIK, run_id=1, counters=ingest.RunCounters())

    conn = edgar.db()
    counts = (conn.execute("SELECT COUNT(*) FROM vintages").fetchone()[0],
              conn.execute("SELECT COUNT(*) FROM restatements").fetchone()[0])
    conn.close()
    assert counts == (0, 0), "the baseline moved without its events"

    _break_the_event_write(False)
    out = ingest.ingest_filer(CIK, run_id=2, counters=ingest.RunCounters())
    assert out["restatements"] == 1

    conn = edgar.db()
    stored = conn.execute(
        "SELECT prior_value, value FROM restatements").fetchall()
    conn.close()
    assert stored == [(9_456_633_000.0, 5_283_685_000.0)]
