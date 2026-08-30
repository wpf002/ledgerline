"""
The run digest: one scan's results as a short text report, written to a file.

This is the honest replacement for the roadmap's "daily digest email". There
is NO send step in this module -- no recipients, no SMTP, no webhook -- and
the absence is the design: at the measured 28.7% hit rate and 51.2% per-filer
false-alarm rate, a scheduled message naming companies is more often wrong
than right, and mail is the surface where a caveat is most reliably skimmed
past. This writes a file; a person decides what to do with it.

The ordering of the text is load-bearing and pinned by a byte-offset test:

  1. the failed-test banner (status.banner(), generated from the frozen file);
  2. the run's coverage -- evaluated, assessed, could-not-assess with the
     reasons counted;
  3. the expectation line: how many of the companies assessed TODAY would be
     expected to be flagged even if nothing were wrong, COMPUTED from the
     frozen false-alarm rate (never hardcoded -- if the frozen number
     changed, this line would change with it);
  4. only then the first company name.

A digest that leads with flags and buries the expectation is an alert with a
disclaimer; on the current numbers the expectation line is the most useful
sentence this product can print, and it costs one multiplication.
"""
from __future__ import annotations

import sqlite3

from .. import edgar, reasons, render, status
from . import contract


def expected_false_positives(scored: int, fpr_per_quarter: float) -> float:
    """How many flags pure chance would produce among `scored` companies, at
    the frozen per-quiet-company-quarter false-alarm rate. One multiplication,
    kept as a function so a test can pin that the printed number tracks the
    inputs rather than matching a string literal."""
    return scored * fpr_per_quarter


def expectation_line(scored: int, fpr_per_quarter: float) -> str:
    exp = expected_false_positives(scored, fpr_per_quarter)
    return (
        f"Even if nothing were wrong at any of them, about {exp:.1f} of the "
        f"{scored} compan{'y' if scored == 1 else 'ies'} assessed would be "
        f"expected to be flagged anyway, because the detector's measured "
        f"false-alarm rate is {fpr_per_quarter:.2%} per quiet "
        f"company-quarter."
    )


def _run_rows(conn: sqlite3.Connection, run_id: str | None) -> list[dict]:
    """The rows of one emit run, oldest first.

    Without a run_id, the most recent run: rows written by one emit_run()
    call share run_id when the caller set one, and share their emitted_at
    timestamp always -- so the fallback key is the newest row's emitted_at,
    which also covers single `score --emit` entries (a run of one).
    """
    if run_id is not None:
        rows = contract.feed_rows(conn, 0)
        return [r for r in rows if r["run_id"] == run_id]
    newest = conn.execute(
        "SELECT run_id, emitted_at FROM signals ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if newest is None:
        return []
    rid, stamped_at = newest
    rows = contract.feed_rows(conn, 0)
    if rid is not None:
        return [r for r in rows if r["run_id"] == rid]
    return [r for r in rows if r["emitted_at"] == stamped_at]


def build(run_id: str | None = None, *,
          conn: sqlite3.Connection | None = None) -> dict:
    """Everything the digest says, as data. Numbers come from the run block
    embedded in the stored records -- the denominator that was complete
    before any record was written -- never recomputed here."""
    validation = contract.validation_block()
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        rows = _run_rows(conn, run_id)
    finally:
        if own:
            conn.close()
    if not rows:
        raise RuntimeError(
            "no saved assessments" + (f" for run {run_id}" if run_id else "")
            + " -- they are added by `ledgerline scan --score`, `ledgerline "
            "score TICKER --emit`, or `ledgerline replay`"
        )
    run = (rows[0].get("record") or {}).get("run") or {}
    fires = [r for r in rows if r["gated_in"]]
    unscoreable = [r for r in rows if not r["scoreable"]]
    scored = int(run.get("scoreable") or 0)
    fpr = validation["measured"]["fpr_per_control_quarter"]
    return {
        "run": run,
        "run_id": rows[0]["run_id"],
        "date": run.get("run_date") or rows[0]["as_of"],
        "banner": status.banner(),
        "expected_false_positives": expected_false_positives(scored, fpr),
        "expectation_line": expectation_line(scored, fpr),
        "fires": [{
            "ticker": r["ticker"] or r["cik"],
            "score": r["score"],
            "flags": [f["code"] for f in (r["flags"] or [])],
        } for r in fires],
        "unscoreable": [{
            "ticker": r["ticker"] or r["cik"],
            "reason": r["reason"],
            "reason_code": r["reason_code"],
        } for r in unscoreable],
        "validation": validation,
    }


def render_text(d: dict) -> str:
    """The digest as plain text, in the pinned order: banner, coverage,
    expectation, and only then a company name. No section before the
    expectation line may contain a ticker."""
    run = d["run"]
    evaluated = run.get("evaluated", 0)
    scored = run.get("scoreable", 0)
    n_un = run.get("unscoreable", 0)
    out: list[str] = []
    out.append(f"Ledgerline digest for {d['date']}")
    out.append("=" * len(out[-1]))
    out.append("")
    out.append(d["banner"])
    out.append("")
    out.append(f"This run looked at {evaluated} "
               f"compan{'y' if evaluated == 1 else 'ies'}: {scored} could be "
               f"assessed, {len(d['fires'])} "
               f"{'was' if len(d['fires']) == 1 else 'were'} flagged, and "
               f"{n_un} could not be assessed.")
    reason_counts = run.get("unscoreable_reasons") or {}
    for code, n in sorted(reason_counts.items()):
        sentence = reasons.TEXT.get(code, code)
        out.append(f"  {n} not assessable: {sentence}")
    out.append("")
    out.append(d["expectation_line"])
    out.append("")
    if d["fires"]:
        out.append("Flagged:")
        for f in d["fires"]:
            names = ", ".join(render.PLAIN.get(c.lower(), (c,))[0]
                              for c in f["flags"])
            out.append(f"  {f['ticker']:6} scored {f['score']:5.1f} of 100"
                       + (f"  ({names})" if names else ""))
    else:
        out.append("No companies were flagged in this run.")
    if d["unscoreable"]:
        out.append("")
        out.append("Could not be assessed:")
        for u in d["unscoreable"]:
            out.append(f"  {u['ticker']:6} "
                       f"{render.plain_reason(u['reason'])}")
    out.append("")
    out.append("This digest was written to a file and sent nowhere. That is "
               "deliberate: on the measured numbers above, a flag is more "
               "often wrong than right.")
    return "\n".join(out) + "\n"
