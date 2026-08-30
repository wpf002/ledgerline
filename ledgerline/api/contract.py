"""
The versioned JSON envelope every published assessment travels in, and the
JSONL export other programs read.

Why this boundary exists: inside the repo, every surface prints the Phase 0
KILL because status.stamp() is enforced at the single scoring path. The
moment a record leaves the repo -- a file, a pipe, another program -- that
discipline stops travelling with it. So the delivery envelope makes the
verdict structural: `validation` sits at the same depth as `assessment`, the
schema marks it required with additionalProperties false, and a consumer
cannot deserialise a score without deserialising the fact that the detector
failed its own pre-registered test on 2026-08-30.

Two refusals, both deliberate:

  * validation_block() RAISES when ledgerline/data/phase0.json is missing,
    because it reads it through status.load(), which has no default. A
    fallback "UNVALIDATED" here would produce the right label on a machine
    holding no evidence -- and would silently mislabel a future gate that
    actually passed a new pre-registration. There is no path from a missing
    evidence file to an exported record.
  * export_jsonl() validates every envelope against api.schema before a
    single byte is written, and builds the validation block before reading
    any row. A feed with one malformed line is worse than no feed, because
    a consumer that skips bad lines silently loses the denominator.

The statement inside the block is COMPUTED from the frozen numbers, never a
string literal -- if the committed record's numbers changed, the sentence
would change with them, and a hand-typed sentence is a second copy of the
result that can drift from the committed one.
"""
from __future__ import annotations

import json
import os
import sqlite3

from .. import edgar, emit, status
from . import schema

SCHEMA_VERSION = "1.0.0"
MEDIA_TYPE = "application/vnd.ledgerline.signal+json; version=1"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FEED_PATH = os.path.join(ROOT, "reports", "feed", "signals.jsonl")

# The frozen keys the statement and the measured block are built from --
# the same set status.PHASE0 pins, so the drift check covers them.
_MEASURED_KEYS = ("positive_hit_rate", "positive_hit_rate_floor",
                  "fpr_per_control_quarter", "fpr_ceiling",
                  "naive_baseline_fpr", "fpr_per_filer")


def statement(ph: dict) -> str:
    """The failure as one computed paragraph, from the frozen summary block.

    Takes the numbers as an argument (rather than loading them itself) so a
    test can prove the sentence tracks its inputs: change a number, the text
    changes with it. Plain words per docs/VOICE.md -- every figure carries
    its unit and the bar it is judged against.
    """
    ratio = ph["fpr_per_control_quarter"] / ph["naive_baseline_fpr"]
    return (
        f"This detector failed its own pre-registered test, scored once on "
        f"{ph['scored_on']}: it caught {ph['positive_hit_rate']:.1%} of the "
        f"deteriorations it was built to find (needed at least "
        f"{ph['positive_hit_rate_floor']:.0%}), and its false-alarm rate of "
        f"{ph['fpr_per_control_quarter']:.2%} per quiet company-quarter was "
        f"{ratio:.1f}x the crude two-line rule it had to beat. "
        f"{ph['fpr_per_filer']:.1%} of companies that stayed fine were "
        f"flagged at least once. A flag from this detector is not evidence "
        f"of deterioration, and a quiet result is not a clean bill of health."
    )


def validation_block() -> dict:
    """The required validation field, built from the committed evidence.

    Goes through status.stamp() -- the one enforcement point every scored
    surface already uses -- so this module can never carry a paraphrase of
    the verdict that the stamp does not. Raises (inside status.load) when
    ledgerline/data/phase0.json is absent; deliberately, there is no default.
    """
    stamped = status.stamp({})
    ph = stamped["phase0"]
    return {
        "status": stamped["gate_status"],
        "verdict": ph["verdict"],
        "scored_on": ph["scored_on"],
        "measured": {k: ph[k] for k in _MEASURED_KEYS},
        "statement": statement(ph),
        "writeup": ph["writeup"],
    }


def _state(row: dict) -> str:
    if not row["scoreable"]:
        return "unscoreable"
    return "fired" if row["gated_in"] else "quiet"


def envelope(row: dict, validation: dict | None = None) -> dict:
    """One stored signal row (as emit.load_signals decodes it) as a contract
    record. Pure reshaping: nothing here computes a number, because the
    delivery layer recomputing arithmetic is how two surfaces disagree."""
    if validation is None:
        validation = validation_block()
    state = _state(row)
    score = row["score"]
    if state == "unscoreable" and score is not None:
        # The store's own rule, restated at the boundary the record crosses:
        # a number on an unassessed company reads as "assessed, looks clean".
        raise ValueError(
            f"signal {row['signal_id']} is unscoreable but carries score "
            f"{score!r} -- refusing to publish an assessment that did not "
            "happen"
        )
    record = row.get("record") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "media_type": MEDIA_TYPE,
        "signal_id": row["signal_id"],
        "seq": row["seq"],
        "emitted_at": row["emitted_at"],
        "source": row["source"],
        "as_of": row["as_of"],
        "period": row["period"],
        "filer": {"cik": row["cik"], "ticker": row["ticker"]},
        "assessment": {
            "state": state,
            "score": score,
            "reason": row["reason"],
            "reason_code": row["reason_code"],
            "n_flags": row["n_flags"],
        },
        "flags": row["flags"] or [],
        "gate": {
            "version": row["gate_version"],
            "validation_status": row["validation_status"],
        },
        "provenance": {
            "accessions": row["accessions"] or [],
            "derived_fraction": row["derived_fraction"],
        },
        # The run block embedded at emit time travels verbatim: a record read
        # in isolation still says how many companies could not be assessed
        # that day, and why.
        "run": record.get("run") or {},
        "validation": validation,
    }


def feed_rows(conn: sqlite3.Connection, since_seq: int = 0,
              limit: int | None = None) -> list[dict]:
    """Stored rows strictly after a cursor, oldest first, JSON decoded.

    seq ascending is the feed's ordering contract: two exports windowed at
    any cursor concatenate into exactly the full feed, which is what makes
    the export resumable instead of merely repeatable.
    """
    q = (f"SELECT {', '.join(emit._COLS)} FROM signals "
         "WHERE seq > ? ORDER BY seq ASC")
    args: tuple = (since_seq,)
    if limit is not None:
        q += " LIMIT ?"
        args = (since_seq, limit)
    out = []
    for r in conn.execute(q, args).fetchall():
        d = dict(zip(emit._COLS, r, strict=True))
        for c in emit._JSON_COLS:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        out.append(d)
    return out


def export_jsonl(path: str = FEED_PATH, *, since_seq: int = 0,
                 conn: sqlite3.Connection | None = None) -> tuple[int, int]:
    """Write the log to JSONL, one validated envelope per line.

    Returns (n_written, max_seq); the caller resumes from max_seq. since_seq=0
    rewrites the file from the start; a positive cursor appends, so the file
    stays a faithful projection of the append-only table it came from.

    The validation block is built FIRST -- on a machine without the frozen
    evidence this raises before any byte is written -- and every envelope is
    schema-checked before any line is written, so a partial or malformed feed
    cannot be produced by this function.
    """
    validation = validation_block()
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        rows = feed_rows(conn, since_seq)
    finally:
        if own:
            conn.close()
    lines: list[str] = []
    max_seq = since_seq
    for row in rows:
        env = envelope(row, validation)
        errors = schema.validate(env)
        if errors:
            raise RuntimeError(
                f"signal {row['signal_id']} does not conform to the contract "
                f"and the export is refused whole: {'; '.join(errors)}"
            )
        lines.append(json.dumps(env, sort_keys=True))
        max_seq = row["seq"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    mode = "a" if since_seq > 0 else "w"
    with open(path, mode) as fh:
        for line in lines:
            fh.write(line + "\n")
    return len(lines), max_seq
