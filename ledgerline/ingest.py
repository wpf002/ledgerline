"""
Run lifecycle and per-filer ingestion: the job bodies behind scan and fetch.

A new module rather than more code in edgar.py: edgar.py is the SEC client,
and run bookkeeping is a different concern. Three defects motivated it:

  * The runs table was keyed on run_date with INSERT OR REPLACE, so a retry
    after a transient failure silently overwrote the record of the failure it
    was retrying -- and a crash left no row at all. run() is a contextmanager
    so a crash writes status='failed' with the error, and every execution is
    its own job_runs row.

  * The old scan returned before writing anything on a quiet day, and wrote
    len(hits) into both the `scanned` and `changed` columns -- so the
    market-wide index count, the number that demonstrates the Tier 0 cost
    claim, was never stored. Here the run row opens BEFORE detection, and
    index_rows / universe_hits are separate counters.

  * The scheduled job is an INGESTION job, not a scoring job. The gate failed
    its pre-registered test on 2026-08-30 (28.7% caught vs a 60% floor; false
    alarms did not beat the naive baseline), so a daily job that scores and
    emits would distribute an invalidated claim on a schedule. scan() detects,
    refetches only the filers that actually filed a periodic form, normalizes,
    diffs vintages, emits restatement events, and books the run. score=False
    is the default; a caller that opts in gets payloads stamped with the
    frozen verdict by signals_v3.evaluate itself.

Resumability rule: a filing is only recorded as known AFTER its filer's
ingest succeeds (edgar.record_filings per filer), and backfill checkpoints to
the ingest_state table rather than a JSON file outside the database -- so a
crash mid-run leaves work retryable and `check` can tell a filer never pulled
from one pulled and holding no XBRL.
"""
from __future__ import annotations

import json
import traceback
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from . import edgar, emit, restate, signals_v3


@dataclass
class RunCounters:
    requests: int = 0
    cache_hits: int = 0
    bytes_fetched: int = 0
    index_rows: int = 0
    universe_hits: int = 0
    filers_done: int = 0
    filers_failed: int = 0
    restatements: int = 0
    scored: int = 0
    gated_in: int = 0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def open_run(job: str, as_of: str | None = None) -> int:
    """Open the run row FIRST, before any detection: a quiet day must still
    leave a row, because the day nothing filed is the day the one-request cost
    claim is demonstrated."""
    conn = edgar.db()
    with conn:
        cur = conn.execute(
            "INSERT INTO job_runs (job, as_of, status, started_at) "
            "VALUES (?,?,?,?)",
            (job, as_of, "running", _now()),
        )
    run_id = cur.lastrowid
    conn.close()
    assert run_id is not None
    return run_id


def finish_run(run_id: int, counters: RunCounters, *, status: str = "ok",
               error: str | None = None) -> None:
    stats = edgar.stats()
    counters.requests = stats["requests"]
    counters.cache_hits = stats["cache_hits"]
    counters.bytes_fetched = stats["bytes_fetched"]
    conn = edgar.db()
    with conn:
        conn.execute(
            "UPDATE job_runs SET status=?, finished_at=?, requests=?, "
            "cache_hits=?, bytes_fetched=?, index_rows=?, universe_hits=?, "
            "filers_done=?, filers_failed=?, restatements=?, scored=?, "
            "gated_in=?, error=? WHERE run_id=?",
            (status, _now(), counters.requests, counters.cache_hits,
             counters.bytes_fetched, counters.index_rows,
             counters.universe_hits, counters.filers_done,
             counters.filers_failed, counters.restatements, counters.scored,
             counters.gated_in, error, run_id),
        )
    conn.close()


@contextmanager
def run(job: str, as_of: str | None = None) -> Iterator[tuple[int, RunCounters]]:
    """The run lifecycle. A crash writes status='failed' with the error text
    and re-raises -- never a row stuck at 'running', never a silent overwrite
    of the failure by the retry (each execution is its own row)."""
    edgar.stats_reset()
    run_id = open_run(job, as_of)
    counters = RunCounters()
    try:
        yield run_id, counters
    except BaseException as exc:
        finish_run(run_id, counters, status="failed",
                   error=f"{type(exc).__name__}: {exc}\n"
                         f"{traceback.format_exc(limit=3)}"[:2000])
        raise
    finish_run(run_id, counters)


def _facts_filed_max(norm: dict) -> str | None:
    """Newest `filed` across every vintage -- the staleness key ingest_state
    carries so scan can refresh only filers whose cache is behind a filing."""
    dates = [
        v.get("filed") or ""
        for rows in norm.values()
        for r in rows
        for v in (r.get("vintages") or [r])
    ]
    best = max(dates, default="")
    return best or None


def _set_state(cik: str, run_id: int | None, **fields: object) -> None:
    conn = edgar.db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO ingest_state "
            "(cik, status, last_run_id, facts_filed_max, rows, metrics, "
            " low_coverage, error, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cik, fields.get("status"), run_id, fields.get("facts_filed_max"),
             fields.get("rows"), fields.get("metrics"),
             json.dumps(fields.get("low_coverage", [])), fields.get("error"),
             _now()),
        )
    conn.close()


def ingest_filer(cik: str, run_id: int, counters: RunCounters, *,
                 refresh: bool = False) -> dict:
    """Fetch, normalize, diff vintages, persist -- one filer, fully booked.

    Catches RuntimeError and JSONDecodeError as well as HTTPError, records the
    error in ingest_state, and lets the caller continue -- scripts/backfill.py
    did this for years without a test; the behaviour now lives here and has
    one. The vintage diff runs BEFORE the vintages are written, or every
    revision would look already-known (pinned by a test in
    test_restatement.py), and diff/record/write share ONE transaction --
    restate.ingest_revisions -- because a crash between the vintage write and
    the event write used to destroy that filer's revision history for good.
    """
    try:
        doc = edgar.companyfacts(cik, refresh=refresh)
        facts = doc.get("facts", {}).get("us-gaap", {})
    except urllib.error.HTTPError:
        facts = {}  # the SEC has no facts file for this filer
    except (RuntimeError, json.JSONDecodeError) as exc:
        counters.filers_failed += 1
        err = f"{type(exc).__name__}: {exc}"[:200]
        _set_state(cik, run_id, status="error", error=err)
        return {"cik": cik, "status": "error", "error": err}

    norm = edgar.normalize(cik, facts) if facts else {}
    if not norm:
        _set_state(cik, run_id, status="no_facts", rows=0, metrics=0)
        return {"cik": cik, "status": "no_facts", "rows": 0}

    conn = edgar.db()
    try:
        events = restate.ingest_revisions(conn, cik, norm, run_id)
    finally:
        conn.close()
    rows = edgar.persist_metrics(cik, norm)
    cov = edgar.coverage_report(norm)
    low = sorted(m for m, c in cov.items() if c["n"] and not c["scoreable"])
    _set_state(cik, run_id, status="ok", rows=rows, metrics=len(norm),
               low_coverage=low, facts_filed_max=_facts_filed_max(norm))
    counters.filers_done += 1
    counters.restatements += len(events)
    return {"cik": cik, "status": "ok", "rows": rows, "metrics": len(norm),
            "restatements": len(events), "low_coverage": low}


def scan(days_back: int = 1, as_of: str | None = None, score: bool = False,
         refresh: bool = True, ciks: set[str] | None = None) -> dict:
    """The scheduled job body: detect, ingest the filers that filed, book it.

    score=False by default -- justified by the Phase 0 numbers in the module
    docstring, not by preference. With score=True every payload comes back
    stamped with the frozen verdict (signals_v3.evaluate stamps its own
    output), and the CLI prints the banner before the first result line.

    `ciks` narrows which watched companies are considered -- one named group,
    say. It does NOT narrow the daily-index request, which is a single
    market-wide read whatever the filter says: the Tier 0 cost is flat in
    universe size, and a per-group scan is cheaper only in the per-filer work
    that follows, never in the one request that starts it. index_rows in the
    run row stays the market-wide count for exactly that reason.
    """
    with run("scan", as_of) as (run_id, c):
        uni = edgar.universe()
        if ciks is not None:
            uni = {k: v for k, v in uni.items() if k in ciks}
        known = edgar.known_accessions() if uni else set()
        today = date.today()
        anchor = date.fromisoformat(as_of) if as_of else today
        cutoff = anchor.isoformat()

        hits: list[dict] = []
        for i in range(days_back):
            d = anchor - timedelta(days=i)
            # Today's index is still being appended to; past days are final.
            rows = edgar.daily_index(d, refresh=refresh and d >= today)
            c.index_rows += len(rows)
            hits += edgar.match_universe(rows, uni, known)
        c.universe_hits = len(hits)

        by_cik: dict[str, list[dict]] = {}
        for h in hits:
            by_cik.setdefault(h["cik"], []).append(h)

        filers: list[dict] = []
        results: list[dict] = []
        for cik, cik_hits in sorted(by_cik.items()):
            ticker = cik_hits[0].get("ticker") or ""
            periodic = any(h["form"] in edgar.PERIODIC_FORMS for h in cik_hits)
            if periodic:
                # Refetch companyfacts for exactly the filers the index says
                # filed -- one index request plus one request per filer that
                # filed is what Tier 1 costs by design. An 8-K-only filer gets
                # its filing recorded and no ~3.4MB refetch, because an 8-K
                # carries no XBRL fundamentals and cannot move a diagnostic.
                summary = ingest_filer(cik, run_id, c, refresh=refresh)
            else:
                summary = {"cik": cik, "status": "recorded"}
            summary["ticker"] = ticker
            summary["forms"] = [h["form"] for h in cik_hits]
            filers.append(summary)
            if summary["status"] == "error":
                continue  # not recorded: the rerun must retry these accessions
            edgar.record_filings(cik_hits, run_id=run_id)
            if score and periodic and summary["status"] == "ok":
                res = signals_v3.evaluate(ticker, cik, as_of=cutoff)
                results.append(res)
                if res["scoreable"]:
                    c.scored += 1
                    c.gated_in += int(res["gated_in"])

        # Persist AFTER the loop, never inside it: the run block embedded in
        # every stored record carries the day's denominators (how many filers
        # were evaluated, how many could not be assessed and why), and those
        # are not complete until every filer has been evaluated. Unscoreable
        # verdicts are persisted too -- the denominator travels with the
        # numerator.
        if score and results:
            emit.emit_run(results, source="scan", run_id=run_id,
                          run_date=cutoff)

        return {"run_id": run_id, "as_of": cutoff, "hits": len(hits),
                "index_rows": c.index_rows, "filers": filers,
                "results": results, "counters": asdict(c)}


def backfill(only: list[str] | None = None, refresh: bool = False,
             limit: int | None = None, resume: bool = True) -> dict:
    """Full-universe (or named-filer) pull, resuming from ingest_state.

    Over an already-cached universe this issues zero HTTP requests -- the
    cache-honoring claim, demonstrated by the run row's requests=0 rather than
    asserted. --refresh bypasses the permanent companyfacts cache and is the
    supported way to pull filings newer than the cached document.
    """
    with run("backfill") as (run_id, c):
        uni = edgar.universe()
        todo = sorted(uni.items())
        if only:
            wanted = {t.upper() for t in only}
            todo = [(k, m) for k, m in todo if m["ticker"] in wanted]
        if resume and not refresh:
            conn = edgar.db()
            done = {
                r[0] for r in conn.execute(
                    "SELECT cik FROM ingest_state WHERE status IN ('ok', 'no_facts')"
                )
            }
            conn.close()
            todo = [(k, m) for k, m in todo if k not in done]
        if limit is not None:
            todo = todo[:limit]

        filers = []
        for cik, meta in todo:
            summary = ingest_filer(cik, run_id, c, refresh=refresh)
            summary["ticker"] = meta["ticker"]
            filers.append(summary)

        return {"run_id": run_id, "filers": filers, "counters": asdict(c)}


def ingest_state(cik: str | None = None) -> dict[str, dict]:
    """What has been pulled, per filer. 'no_facts' and an absent row mean
    different things: pulled-and-empty versus never pulled."""
    q = ("SELECT cik, status, last_run_id, facts_filed_max, rows, metrics, "
         "low_coverage, error, updated_at FROM ingest_state")
    args: tuple = ()
    if cik:
        q += " WHERE cik = ?"
        args = (cik,)
    conn = edgar.db()
    rows = conn.execute(q, args).fetchall()
    conn.close()
    cols = ("cik", "status", "last_run_id", "facts_filed_max", "rows",
            "metrics", "low_coverage", "error", "updated_at")
    return {r[0]: dict(zip(cols, r, strict=True)) for r in rows}


def run_log(job: str | None = None, limit: int = 20) -> list[dict]:
    q = ("SELECT run_id, job, as_of, status, started_at, finished_at, "
         "requests, cache_hits, bytes_fetched, index_rows, universe_hits, "
         "filers_done, filers_failed, restatements, scored, gated_in, error "
         "FROM job_runs")
    args: tuple = ()
    if job:
        q += " WHERE job = ?"
        args = (job,)
    q += " ORDER BY run_id DESC LIMIT ?"
    conn = edgar.db()
    rows = conn.execute(q, (*args, limit)).fetchall()
    conn.close()
    cols = ("run_id", "job", "as_of", "status", "started_at", "finished_at",
            "requests", "cache_hits", "bytes_fetched", "index_rows",
            "universe_hits", "filers_done", "filers_failed", "restatements",
            "scored", "gated_in", "error")
    return [dict(zip(cols, r, strict=True)) for r in rows]
