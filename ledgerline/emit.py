"""
The signal store: append-only, content-addressed persistence of every gate
evaluation. This is the substrate any future track record stands on.

Why every EVALUATION and not only fires: a store containing only fires has no
denominator -- it can measure precision and can never measure recall, and
recall (28.7% caught against a required 60%) is the criterion the gate failed
on 2026-08-30. Quiet scoreable quarters and unscoreable filers are rows too,
each with its reason; abstention volume is a product metric, and a filer that
silently vanishes from the store is survivorship bias with extra steps (the
same register as harness.build_cases).

Why two passes: the run block embedded in every record says how many filers
were evaluated that run and how many could not be assessed, and those
denominators are not complete until every filer HAS been evaluated. A one-pass
streaming emit would ship its first records with a denominator still counting.
So emit_run() takes the finished list of verdicts, builds the run block once,
and only then writes -- a fired record read in isolation cannot be seen
without also seeing that N of M filers were unassessable that day.

Why content-addressed: signal_id is a sha256 over the fields that constitute
the evaluation (cik, as_of, gate_version, score, gated_in, scoreable, reason,
flags, z), so re-emitting an identical evaluation is a no-op (INSERT OR
IGNORE) and a replay is idempotent -- while the same (cik, as_of) under a
CHANGED gate writes a second row beside the old one. That coexistence is the
only mechanism by which a revised gate is ever compared to the one that
returned KILL.

Why gate_version is a fingerprint and not just a label: GATE_VERSION is bumped
by hand and hands forget. The hash over signals_v3.gate_fingerprint() -- every
constant that can change a score -- changes when the arithmetic changes,
whether or not anyone remembered to bump the label. Both travel together in
one column.

This module contains no UPDATE and no DELETE against `signals` -- pinned by a
source-level test, and enforced independently by the RAISE(ABORT) triggers in
edgar.SCHEMA.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from . import edgar, signals_v3, status

SCHEMA_VERSION = "1"

# When the record column keeps the full diagnostics and coverage payload.
# Quiet scoreable rows drop them: on those rows the blobs are near-identical
# boilerplate, and a full-universe replay is ~64k rows against a state.db
# already at 196MB. The rule is stated here rather than reverse-engineered.
FULL_DIAGNOSTICS_WHEN = "gated_in or not scoreable"


def gate_version() -> str:
    """Human label + fingerprint hash, e.g. '3.1.0+a1b2c3d4e5f6'.

    The label says what an engineer meant; the hash says what the constants
    were. A retune that forgets to bump GATE_VERSION still gets a new hash,
    so two gates can never silently pool into one track record.
    """
    digest = hashlib.sha256(
        json.dumps(signals_v3.gate_fingerprint(), sort_keys=True).encode()
    ).hexdigest()[:12]
    return f"{signals_v3.GATE_VERSION}+{digest}"


def signal_id(verdict: dict, gate: str) -> str:
    """Content address of one evaluation. Deliberately excludes source,
    run_id, and emitted_at: the same evaluation reached by scan and again by
    replay is one fact, not two rows."""
    core = {
        "cik": verdict.get("cik"),
        "as_of": verdict.get("as_of"),
        "gate_version": gate,
        "score": verdict.get("score"),
        "gated_in": bool(verdict.get("gated_in")),
        "scoreable": bool(verdict.get("scoreable")),
        "reason": verdict.get("reason"),
        "flags": verdict.get("flags", []),
        "z": verdict.get("z", {}),
    }
    return hashlib.sha256(
        json.dumps(core, sort_keys=True).encode()
    ).hexdigest()[:32]


def _coverage_failed(verdict: dict) -> list[str]:
    """The metrics that failed their scoreable check -- the informative slice
    of the 8-metric coverage dict, whose passing entries are near-identical
    on every row."""
    cov = verdict.get("coverage") or {}
    return sorted(
        m for m, c in cov.items()
        if isinstance(c, dict) and c.get("n") and not c.get("scoreable")
    )


def _record(verdict: dict, run_block: dict) -> dict:
    """The full stored payload: the stamped verdict plus the run it belongs
    to. diagnostics/coverage are kept only per FULL_DIAGNOSTICS_WHEN."""
    v = dict(verdict)
    if v.get("scoreable") and not v.get("gated_in"):
        v.pop("diagnostics", None)
        v.pop("coverage", None)
    return {"schema_version": SCHEMA_VERSION, "run": run_block, "verdict": v}


def _run_block(verdicts: list[dict], *, source: str, run_id: str | None,
               run_date: str | None, split: str | None) -> dict:
    """The denominator that travels with every numerator: of everything this
    run evaluated, how much was scoreable, how much fired, and why the rest
    could not be assessed."""
    unscoreable: dict[str, int] = {}
    for v in verdicts:
        if not v.get("scoreable"):
            code = v.get("reason_code") or "UNEXPLAINED"
            unscoreable[code] = unscoreable.get(code, 0) + 1
    return {
        "source": source,
        "run_id": run_id,
        "run_date": run_date,
        "split": split,
        "evaluated": len(verdicts),
        "scoreable": sum(1 for v in verdicts if v.get("scoreable")),
        "gated_in": sum(1 for v in verdicts if v.get("gated_in")),
        "unscoreable": sum(1 for v in verdicts if not v.get("scoreable")),
        "unscoreable_reasons": dict(sorted(unscoreable.items())),
    }


def emit_run(verdicts: list[dict], *, source: str, run_id: str | int | None = None,
             run_date: str | None = None, split: str | None = None,
             conn: sqlite3.Connection | None = None,
             now: str | None = None) -> dict:
    """Persist a completed run's evaluations. Returns
    {"written": n, "already": m, "signal_ids": [...]}.

    Pass 1 happened at the caller: every filer in scope has been evaluated
    and `verdicts` is the complete list. This function builds the run block
    from that complete list, then appends -- so no record ever carries a
    denominator that was still counting when it was written.

    Refusals, both RuntimeError:
      * a verdict without the status.stamp() verdict block -- a persisted
        score without the fact that the gate failed its own test is a claim
        the project cannot support;
      * a scoreable verdict whose accessions list is empty -- "a score traces
        back to accessions or it does not ship", enforced at the last point
        it can be, because the stored row outlives the fact cache.
    """
    for v in verdicts:
        try:
            status.assert_stamped(v)
        except AssertionError as exc:
            raise RuntimeError(
                f"refusing to persist an unstamped verdict for "
                f"{v.get('ticker') or v.get('cik')}: {exc}"
            ) from exc
        if v.get("scoreable") and not v.get("accessions"):
            raise RuntimeError(
                f"refusing to persist a scoreable verdict for "
                f"{v.get('ticker') or v.get('cik')} at {v.get('as_of')} with no "
                "accession trace -- a score traces back to filings or it does "
                "not ship, and the stored row outlives the fact cache"
            )

    gate = gate_version()
    run_id_s = str(run_id) if run_id is not None else None
    block = _run_block(verdicts, source=source, run_id=run_id_s,
                       run_date=run_date, split=split)
    stamp_time = now or datetime.now(UTC).isoformat()

    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    written = 0
    already = 0
    ids: list[str] = []
    try:
        with conn:
            for v in verdicts:
                sid = signal_id(v, gate)
                ids.append(sid)
                # seq comes from a subselect inside the same statement; with
                # OR IGNORE a duplicate consumes no seq, so the cursor stays
                # dense enough to resume from.
                cur = conn.execute(
                    "INSERT OR IGNORE INTO signals (signal_id, seq, "
                    "schema_version, cik, ticker, as_of, period, emitted_at, "
                    "run_id, source, split, score, gated_in, scoreable, "
                    "reason, reason_code, n_flags, flags, z, abstentions, "
                    "evaluated_weight, weight_total, accessions, "
                    "coverage_failed, derived_fraction, gate_version, "
                    "validation_status, record) VALUES "
                    "(?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM signals), "
                    "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid, SCHEMA_VERSION, v.get("cik"), v.get("ticker"),
                        v.get("as_of"), v.get("period"), stamp_time, run_id_s,
                        source, split,
                        # score is NULL for unscoreable, never 0.0 -- the
                        # "assessed, looks clean" costume, refused again at
                        # the delivery boundary.
                        v.get("score") if v.get("scoreable") else None,
                        int(bool(v.get("gated_in"))),
                        int(bool(v.get("scoreable"))),
                        v.get("reason"), v.get("reason_code"),
                        len(v.get("flags") or []),
                        json.dumps(v.get("flags") or []),
                        json.dumps(v.get("z") or {}),
                        json.dumps(v.get("abstentions") or {}),
                        v.get("evaluated_weight"), v.get("weight_total"),
                        json.dumps(v.get("accessions") or []),
                        json.dumps(_coverage_failed(v)),
                        v.get("derived_fraction"), gate,
                        # The stamp's machine-readable verdict; the full
                        # frozen numbers ride inside `record` via the stamp.
                        v.get("gate_status") or "",
                        json.dumps(_record(v, block)),
                    ),
                )
                if cur.rowcount:
                    written += 1
                else:
                    already += 1
    finally:
        if own:
            conn.close()
    return {"written": written, "already": already, "signal_ids": ids,
            "run": block, "gate_version": gate}


def emit(verdict: dict, *, source: str, run_id: str | int | None = None,
         run_date: str | None = None, split: str | None = None,
         conn: sqlite3.Connection | None = None) -> str:
    """Persist one evaluation. A single emit is a run of one, and its record
    says so -- the denominator of a hand-picked score is 1 by construction,
    which a later reader should see rather than infer."""
    out = emit_run([verdict], source=source, run_id=run_id,
                   run_date=run_date, split=split, conn=conn)
    return out["signal_ids"][0]


_COLS = ("signal_id", "seq", "schema_version", "cik", "ticker", "as_of",
         "period", "emitted_at", "run_id", "source", "split", "score",
         "gated_in", "scoreable", "reason", "reason_code", "n_flags", "flags",
         "z", "abstentions", "evaluated_weight", "weight_total", "accessions",
         "coverage_failed", "derived_fraction", "gate_version",
         "validation_status", "record")

_JSON_COLS = ("flags", "z", "abstentions", "accessions", "coverage_failed",
              "record")


def load_signals(*, ticker: str | None = None, cik: str | None = None,
                 since: str | None = None, until: str | None = None,
                 gated_in: bool | None = None, gate_version: str | None = None,
                 source: str | None = None, limit: int = 500,
                 conn: sqlite3.Connection | None = None) -> list[dict]:
    """Read the store, filters ANDed, newest first. JSON columns come back
    decoded so a caller sees the shapes emit() was given."""
    clauses: list[str] = []
    args: list[object] = []
    if ticker:
        clauses.append("ticker = ?")
        args.append(ticker.upper())
    if cik:
        clauses.append("cik = ?")
        args.append(cik)
    if since:
        clauses.append("as_of >= ?")
        args.append(since)
    if until:
        clauses.append("as_of <= ?")
        args.append(until)
    if gated_in is not None:
        clauses.append("gated_in = ?")
        args.append(int(gated_in))
    if gate_version:
        clauses.append("gate_version = ?")
        args.append(gate_version)
    if source:
        clauses.append("source = ?")
        args.append(source)
    q = f"SELECT {', '.join(_COLS)} FROM signals"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY as_of DESC, seq DESC LIMIT ?"
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        rows = conn.execute(q, (*args, limit)).fetchall()
    finally:
        if own:
            conn.close()
    out = []
    for r in rows:
        d = dict(zip(_COLS, r, strict=True))
        for c in _JSON_COLS:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        out.append(d)
    return out


def signal_counts(*, gate_version: str | None = None,
                  conn: sqlite3.Connection | None = None) -> dict:
    """The store's denominators at a glance: evaluations, scoreable, fires,
    and abstentions, optionally for one gate revision."""
    where = ""
    args: tuple = ()
    if gate_version:
        where = " WHERE gate_version = ?"
        args = (gate_version,)
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(scoreable), 0), "
            "COALESCE(SUM(gated_in), 0), "
            "COUNT(*) - COALESCE(SUM(scoreable), 0), "
            "COUNT(DISTINCT gate_version) FROM signals" + where, args
        ).fetchone()
    finally:
        if own:
            conn.close()
    return {"evaluations": row[0], "scoreable": row[1], "gated_in": row[2],
            "unscoreable": row[3], "gate_versions": row[4]}
