"""
The read side of the delivery boundary: the JSON files a viewer reads.

Why these files exist rather than a browser asking questions of the database:
api/__init__ states the rule -- Python computes, anything that serves reads
and re-serves. A page that joined the watchlist to the signal store itself, or
re-derived "assessable" from coverage numbers, would be a second implementation
of predicates that already ship, and the moment the two disagreed the page
would be the more visible one. So every join, every plain-language sentence,
and the explain text itself are produced here, by the same functions the CLI
prints, and written to disk beside signals.jsonl.

Three files, matching the three questions a person asks in order:

  watchlist.json        who is being watched, can they be assessed, and what
                        did the last saved assessment say
  runs.json             what has run, when, what it cost, what it found
  companies/<T>.json    one company: the latest assessment in full, the
                        filings its numbers came from, the figures it later
                        revised, and the plain-language reading of all of it

Every one of them carries the same validation block the signal feed carries,
built through contract.validation_block() -> status.stamp(). A page cannot
render a company without also holding the fact that the detector failed its
own pre-registered test, and on a machine missing ledgerline/data/phase0.json
this raises before a byte is written. There is deliberately no default.

Nothing here evaluates a company. Publishing reads what has been saved: a
`publish` that quietly re-scored 1,498 filers would be the most expensive
command in the tool wearing the costume of a file write, and -- worse -- would
put scores on disk that were never persisted to the append-only store, so the
page and the record could disagree about what was assessed and when.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date

from .. import edgar, groups, ingest, render
from . import contract

FEED_DIR = os.path.dirname(contract.FEED_PATH)

# How many runs the published log carries. The job log grows one row per scan
# forever, and a page showing the last few months answers "is this thing
# running and what does it cost"; the full history stays in the database and
# in `ledgerline export runs`.
RUNS_LIMIT = 500

# Cap on one company's filing timeline. A filer with fifteen years of history
# has a few hundred filings; the cap is a page-size guard, and the file says
# when it truncated rather than silently showing a partial record as complete.
TIMELINE_LIMIT = 400


def _plain_flags(flags: list[dict] | None) -> list[str]:
    return [render.PLAIN.get((f.get("code") or "").lower(),
                             (f.get("code") or "",))[0]
            for f in flags or []]


def _latest_assessments(conn: sqlite3.Connection) -> dict[str, dict]:
    """Each company's newest saved assessment, summarised. One query.

    Newest is by as_of and then by position, not by emission time: a replay
    run today can write an assessment made as of 2019, and the page must show
    the most recent thing assessed, not the most recently typed command.
    """
    rows = conn.execute(
        "SELECT cik, as_of, period, score, gated_in, scoreable, reason, flags "
        "FROM signals s WHERE seq = (SELECT seq FROM signals x "
        "WHERE x.cik = s.cik ORDER BY as_of DESC, seq DESC LIMIT 1)"
    ).fetchall()
    out = {}
    for cik, as_of, period, score, gated, scoreable, reason, flags in rows:
        parsed = json.loads(flags) if flags else []
        out[cik] = {
            "as_of": as_of,
            "period": period,
            "score": score,
            "flagged": bool(gated),
            "scoreable": bool(scoreable),
            "reason": render.plain_reason(reason) if reason else None,
            "flags": _plain_flags(parsed),
        }
    return out


def _scoreability(conn: sqlite3.Connection) -> dict[str, tuple]:
    return {
        r[0]: r[1:] for r in conn.execute(
            "SELECT s.cik, s.as_of, s.scoreable, s.detail, s.n_evaluated, "
            "s.n_tracked FROM scoreability s JOIN "
            "(SELECT cik, MAX(as_of) AS m FROM scoreability GROUP BY cik) t "
            "ON t.cik = s.cik AND t.m = s.as_of").fetchall()
    }


def watchlist(conn: sqlite3.Connection | None = None) -> dict:
    """Every watched company, joined to what has been recorded about it."""
    own = conn is None
    conn = conn or edgar.db()
    try:
        latest = _latest_assessments(conn)
        scored = _scoreability(conn)
        member = groups.memberships(conn=conn)
        group_list = groups.listing(conn=conn)
        rows = conn.execute(
            "SELECT cik, ticker, name, sic FROM universe "
            "ORDER BY ticker IS NULL, ticker").fetchall()
    finally:
        if own:
            conn.close()

    companies = []
    for cik, ticker, name, sic in rows:
        hit = scored.get(cik)
        assessable: bool | None
        reason: str | None
        if hit is None:
            assessable = None
            reason = ("This company has not been checked yet. Run "
                      "`ledgerline check` to find out whether it can be "
                      "assessed.")
            checked = avail = total = None
        else:
            checked, ok, detail, avail, total = hit
            assessable = bool(ok)
            reason = None if ok else render.plain_reason(detail)
        companies.append({
            "ticker": ticker,
            "name": name,
            "cik": cik,
            "sic": sic,
            "groups": member.get(cik, []),
            "assessable": assessable,
            "assessable_reason": reason,
            "checked_on": checked,
            "measures_available": avail,
            "measures_total": total,
            "latest": latest.get(cik),
        })
    return {
        "generated": date.today().isoformat(),
        "n_companies": len(companies),
        "n_assessable": sum(1 for c in companies if c["assessable"]),
        "n_flagged": sum(1 for c in companies
                         if (c["latest"] or {}).get("flagged")),
        "groups": group_list,
        "companies": companies,
        "validation": contract.validation_block(),
    }


def runs(limit: int = RUNS_LIMIT) -> dict:
    """The job log as the page shows it, newest first."""
    return {
        "generated": date.today().isoformat(),
        "runs": ingest.run_log(limit=limit),
        "validation": contract.validation_block(),
    }


def filing_timeline(cik: str, conn: sqlite3.Connection,
                    limit: int = TIMELINE_LIMIT) -> list[dict]:
    """The filings this company's numbers actually came from, newest first.

    Built from the provenance the stored figures already carry -- every row in
    `metrics` and `vintages` names its form, its filing date and the accession
    numbers it came from, which is the same trace an accession-traced score is
    assembled from. Both tables, not one: `metrics` holds the newest figure per
    period and `vintages` holds the superseded ones, so a filing whose numbers
    were later revised away still appears on the timeline of what this company
    filed. On a database backfilled before vintages existed, `vintages` is
    simply empty and `metrics` carries the whole timeline.

    The `filings` table is folded in last and cannot be the only source: it is
    a live-run log holding whatever a scan happened to see since this machine
    started scanning, and it records no period. What it does carry, and the
    figures cannot, is a filing with no XBRL fundamentals at all -- an 8-K,
    which moves no diagnostic and is still something the company filed.
    """
    by_acc: dict[str, dict] = {}
    for table in ("metrics", "vintages"):
        for filed, form, end, sources in conn.execute(
                f"SELECT filed, form, end_date, sources FROM {table} "
                "WHERE cik = ?", (cik,)).fetchall():
            for acc in (json.loads(sources) if sources else []):
                if not acc:
                    continue
                hit = by_acc.setdefault(
                    acc, {"accession": acc, "form": form, "filed": filed,
                          "periods": set()})
                # One accession, one filing date: keep the earliest seen,
                # since a later vintage of the same filing is the same
                # document filed once.
                if filed and (hit["filed"] is None or filed < hit["filed"]):
                    hit["filed"] = filed
                if end:
                    hit["periods"].add(end)
    for acc, form, filing_date, period in conn.execute(
            "SELECT accession, form, filing_date, period FROM filings "
            "WHERE cik = ?", (cik,)).fetchall():
        hit = by_acc.setdefault(
            acc, {"accession": acc, "form": form, "filed": filing_date,
                  "periods": set()})
        if period:
            hit["periods"].add(period)

    out = []
    for hit in by_acc.values():
        periods = sorted(hit["periods"])
        # A filing is cited by more periods than it reported: a quarter worked
        # out by subtracting one year-to-date report from another cites BOTH
        # filings, so the newest period citing a filing can end months after
        # that filing was submitted. Taking the newest period that had already
        # ended when the filing went in gives the filing its own period back;
        # n_periods keeps the citation count, which is a different number and
        # says so.
        own = [p for p in periods if hit["filed"] and p <= hit["filed"]]
        out.append({
            "accession": hit["accession"],
            "form": hit["form"],
            "filed": hit["filed"],
            "period": (own or periods)[-1] if periods else None,
            "n_periods": len(periods),
        })
    out.sort(key=lambda r: (r["filed"] or "", r["accession"]), reverse=True)
    return out[:limit]


def _revisions(cik: str, conn: sqlite3.Connection) -> list[dict]:
    """Figures this company later revised, oldest revision first.

    The same rows `ledgerline restatements` reads, sub-1% ones included: 42.5%
    of measured revisions fall under 1%, and a page that hid them would report
    a revision rate it had already filtered. `material` travels as a field so
    the page can offer the filter without inventing the number.
    """
    cols = ("metric", "end_date", "kind", "filed", "prior_filed",
            "prior_value", "value", "rel_change", "form", "on_amendment",
            "material")
    out = []
    for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM restatements WHERE cik = ? "
            "ORDER BY filed, metric, end_date", (cik,)).fetchall():
        row = dict(zip(cols, r, strict=True))
        row["metric_plain"] = render.plain_metric(row["metric"])
        row["direction"] = ("up" if (row["value"] or 0) > (row["prior_value"] or 0)
                            else "down")
        row["material"] = bool(row["material"])
        row["on_amendment"] = bool(row["on_amendment"])
        out.append(row)
    return out


def company(ticker: str, conn: sqlite3.Connection | None = None,
            timeline_limit: int = TIMELINE_LIMIT) -> dict:
    """One company's detail page, as data. Raises if it is not watched.

    `explain` is the exact text `ledgerline explain` prints, produced here by
    render.explain from the SAVED verdict -- not recomputed, and not
    reimplemented in the browser. Two renderings of one assessment is two
    things that can disagree about a company in public.
    """
    ticker = ticker.upper()
    own = conn is None
    conn = conn or edgar.db()
    try:
        row = conn.execute(
            "SELECT cik, ticker, name, sic FROM universe WHERE ticker = ?",
            (ticker,)).fetchone()
        if row is None:
            raise RuntimeError(
                f"{ticker} is not on your watchlist, so there is nothing to "
                f"publish about it. Add it first:\n"
                f"  ledgerline watch --add {ticker}"
            )
        cik, tick, name, sic = row
        stored = conn.execute(
            "SELECT as_of, period, score, gated_in, scoreable, reason, "
            "gate_version, emitted_at, record FROM signals WHERE cik = ? "
            "ORDER BY as_of DESC, seq DESC LIMIT 1", (cik,)).fetchone()
        history = [
            {"as_of": r[0], "period": r[1], "score": r[2],
             "flagged": bool(r[3]), "scoreable": bool(r[4]),
             "reason": render.plain_reason(r[5]) if r[5] else None,
             "flags": _plain_flags(json.loads(r[6]) if r[6] else [])}
            for r in conn.execute(
                "SELECT as_of, period, score, gated_in, scoreable, reason, "
                "flags FROM signals WHERE cik = ? "
                "ORDER BY as_of DESC, seq DESC LIMIT 40", (cik,)).fetchall()
        ]
        member = [r[0] for r in conn.execute(
            "SELECT name FROM group_members WHERE cik = ? "
            "ORDER BY name COLLATE NOCASE", (cik,)).fetchall()]
        timeline = filing_timeline(cik, conn, limit=timeline_limit)
        revisions = _revisions(cik, conn)
    finally:
        if own:
            conn.close()

    latest: dict | None = None
    explain: str | None = None
    if stored is not None:
        record = json.loads(stored[8]) if stored[8] else {}
        verdict = record.get("verdict") or {}
        latest = {
            "as_of": stored[0],
            "period": stored[1],
            "score": stored[2],
            "flagged": bool(stored[3]),
            "scoreable": bool(stored[4]),
            "reason": render.plain_reason(stored[5]) if stored[5] else None,
            "gate_version": stored[6],
            "emitted_at": stored[7],
            "verdict": verdict,
            "run": record.get("run") or {},
        }
        if verdict:
            explain = render.explain(verdict, name=name)

    return {
        "generated": date.today().isoformat(),
        "ticker": tick,
        "name": name,
        "cik": cik,
        "sic": sic,
        "groups": member,
        "latest": latest,
        "explain": explain or (
            "No assessment has been saved for this company yet. Assessments "
            "are saved by `ledgerline scan --score` and by `ledgerline score "
            f"{tick} --emit`."),
        "history": history,
        "filings": timeline,
        "filings_truncated": len(timeline) >= timeline_limit,
        "restatements": revisions,
        "validation": contract.validation_block(),
    }


# --------------------------------------------------------------- writing out


def _write(path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def write_all(out_dir: str | None = None, companies: bool = True) -> dict:
    """Write watchlist.json, runs.json and companies/<TICKER>.json.

    One connection for the whole job: the per-company files are ~1,500 small
    reads against the same database, and reopening it per company is the shape
    of loop that turns a fast publish into a slow one.
    """
    out_dir = out_dir or FEED_DIR
    conn = edgar.db()
    try:
        wl = watchlist(conn=conn)
        _write(os.path.join(out_dir, "watchlist.json"), wl)
        job_log = runs()
        _write(os.path.join(out_dir, "runs.json"), job_log)
        written = 0
        if companies:
            for c in wl["companies"]:
                if not c["ticker"]:
                    continue
                _write(os.path.join(out_dir, "companies", f"{c['ticker']}.json"),
                       company(c["ticker"], conn=conn))
                written += 1
    finally:
        conn.close()
    return {"dir": out_dir, "companies": written,
            "watched": wl["n_companies"], "runs": len(job_log["runs"])}
