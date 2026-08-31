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


def _stored_figures(conn: sqlite3.Connection) -> dict[str, int]:
    """How many quarterly figures are stored, per company.

    The honest reading of "has anything been downloaded for this one" on THIS
    machine. `ingest_state` records a fetch, but the live database was filled
    by scripts/backfill.py before that table carried rows -- 1,496 companies
    with a full figure history have no ingest row at all -- so a page that
    called them un-fetched would be reporting its own blind spot. Counting
    what is stored cannot be wrong in that direction.
    """
    return {r[0]: r[1] for r in conn.execute(
        "SELECT cik, COUNT(*) FROM metrics GROUP BY cik").fetchall()}


def _fetch_state(conn: sqlite3.Connection) -> dict[str, tuple]:
    """Per company: how the last download ended, and why if it failed."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT cik, status, error FROM ingest_state").fetchall()}


def _quality(assessable: bool | None, reason: str | None, figures: int,
             fetch: tuple | None, avail: int | None, total: int | None,
             latest: dict | None) -> list[dict]:
    """Why a row is not what a blank cell would make it look like.

    A watchlist is mostly rows with no number in them, and the four reasons a
    row is empty are four different pieces of news: nothing downloaded yet,
    the SEC has nothing to download, the company cannot be assessed at all,
    and it can be assessed on fewer than the thirteen measures. Collapsing
    them into one blank cell -- or into the word "no" -- is what makes a
    watchlist read as "1,498 companies, nothing to worry about".

    Written here rather than in the page for the reason api/__init__ states:
    the sentences are part of the answer, and two copies of them can disagree.
    """
    chips: list[dict] = []
    status = fetch[0] if fetch else None
    if status == "no_facts":
        chips.append({"label": "no filings at the SEC", "detail":
                      "The SEC holds no machine-readable filings for this "
                      "company, so there is nothing to read. Nothing is wrong "
                      "with your setup."})
    elif status == "error":
        chips.append({"label": "last download failed", "detail":
                      "The last attempt to download this company's filings "
                      f"failed ({fetch[1] if fetch else 'no reason recorded'})."
                      " Run `ledgerline fetch` to try again."})
    elif figures == 0:
        chips.append({"label": "never fetched", "detail":
                      "No filing figures are stored for this company yet. "
                      "Run `ledgerline fetch` to download its filing history."})
    if assessable is False:
        chips.append({"label": "cannot assess",
                      "detail": reason or "No reason recorded."})
    elif assessable is None:
        chips.append({"label": "not checked yet",
                      "detail": reason or "No reason recorded."})
    if avail is not None and total is not None and avail < total:
        chips.append({"label": f"{total - avail} of {total} measures "
                               "unavailable", "detail":
                      f"Only {avail} of the {total} measures can be computed "
                      "from what this company filed. That is a gap in the "
                      "filings, not something a re-fetch fixes."})
    if latest is None:
        chips.append({"label": "no assessment saved", "detail":
                      "Nothing has been assessed for this company yet. "
                      "`ledgerline scan --score` saves assessments as it runs."})
    return chips


def _measures(verdict: dict) -> list[dict]:
    """The thirteen measures for one company, in the order render.PLAIN lists
    them, each with what it read, how far that is from this company's own
    past, and -- when it could not be computed -- why not.

    Every field a page shows is read from the saved verdict; nothing here
    recomputes a diagnostic. A measure with no reading is a row that says so,
    never an omitted row: the ones a company cannot supply are the reason a
    score means less than it looks like it means, and dropping them from the
    table would make every company look fully measured.
    """
    z = verdict.get("z") or {}
    values = verdict.get("diagnostics") or {}
    fired = {(f.get("code") or "").lower(): f for f in verdict.get("flags") or []}
    detail = verdict.get("abstention_detail") or {}
    out = []
    for key, (short, bad_direction) in render.PLAIN.items():
        flag = fired.get(key)
        row = {
            "measure": short,
            "technical": key,
            "breaks_when": bad_direction,
            "value": values.get(key),
            "z": z.get(key),
            "out_of_line": flag is not None,
            "baseline_median": flag["baseline_median"] if flag else None,
            "baseline_scale": flag["baseline_scale"] if flag else None,
            "baseline_n": flag["baseline_n"] if flag else None,
            "floored": bool(flag.get("floored")) if flag else False,
            "filed": flag.get("filed") if flag else None,
            "sources": list(flag.get("sources") or []) if flag else [],
            "unavailable_reason": None,
        }
        if key not in z:
            row["unavailable_reason"] = detail.get(key) or (
                "not computable from what this company filed")
        out.append(row)
    return out


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
        figures = _stored_figures(conn)
        fetched = _fetch_state(conn)
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
        n_figures = figures.get(cik, 0)
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
            "figures_stored": n_figures,
            "fetch_status": (fetched[cik][0] if cik in fetched else None),
            "quality": _quality(assessable, reason, n_figures,
                                fetched.get(cik), avail, total,
                                latest.get(cik)),
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


def _run_outcomes(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Per run: how many companies it assessed, and how many it could not.

    The job log counts what a run cost and what it flagged; it has never
    counted what it walked away from. That number is the denominator -- a run
    that flagged 6 of 471 assessed and abstained on 18 more is a different
    piece of news from one that flagged 6 of 489 -- and the append-only signal
    store has it, one row per evaluation, abstentions included.
    """
    return {
        str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in conn.execute(
            "SELECT run_id, SUM(scoreable), SUM(1 - scoreable) FROM signals "
            "WHERE run_id IS NOT NULL GROUP BY run_id").fetchall()
    }


def runs(limit: int = RUNS_LIMIT) -> dict:
    """The job log as the page shows it, newest first."""
    log = ingest.run_log(limit=limit)
    conn = edgar.db()
    try:
        outcomes = _run_outcomes(conn)
    finally:
        conn.close()
    for row in log:
        hit = outcomes.get(str(row["run_id"]))
        # None, not 0: a run that saved no assessments and a run that assessed
        # nothing look identical as zeros, and only one of them is news.
        row["assessed"] = hit[0] if hit else None
        row["could_not_assess"] = hit[1] if hit else None
    return {
        "generated": date.today().isoformat(),
        "runs": log,
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


def _trail(verdict: dict) -> dict:
    """Which filing each figure behind a broken measure came from.

    provenance.py resolves the trail and labels the reading TRACED / PARTIAL /
    UNTRACED; this reshapes it for a page and gives every name its plain
    reading. The reshaping is the point: the stored trail is keyed by
    diagnostic and metric identifiers, and a page that printed them would show
    a person `ocf_to_revenue` and `operating_cash_flow` -- the exact thing
    docs/VOICE.md forbids, in the one table whose job is to make a number
    checkable against the filing it came from.
    """
    prov = verdict.get("provenance") or {}
    measures = []
    for key, inputs in (prov.get("flags") or {}).items():
        measures.append({
            "measure": render.PLAIN.get(key, (key.replace("_", " "), ""))[0],
            "inputs": [
                {
                    "figure": render.plain_metric(name),
                    "concept": trace.get("concept"),
                    "period": trace.get("end"),
                    "origin": trace.get("origin"),
                    "form": trace.get("form"),
                    "filed": trace.get("filed"),
                    "sources": [s for s in (trace.get("sources") or []) if s],
                }
                for name, trace in (inputs or {}).items()
            ],
        })
    return {
        "label": verdict.get("provenance_label"),
        "derived_fraction": prov.get("derived_fraction"),
        "derived_fraction_high": bool(prov.get("derived_fraction_high")),
        "measures": measures,
    }


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
    measures: list[dict] = []
    trail: dict = {}
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
            measures = _measures(verdict)
            trail = _trail(verdict)

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
        "measures": measures,
        "provenance": trail,
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


def _sweep_company_files(dir_path: str, keep: set[str]) -> int:
    """Delete company files this directory holds for companies not in `keep`.

    Publishing rewrites a file per watched company, which leaves any file from
    an EARLIER publish in place -- and the service serves whatever it finds, so
    a company that has since changed ticker keeps answering under the old
    symbol with an assessment nothing will ever refresh. A stale page is worse
    than a missing one here: it carries the same "published on <date>" footer
    as the live ones and there is nothing on it to say otherwise.

    Deliberately narrow: only *.json directly inside the companies/ directory
    that publish itself writes, and only names shaped like the tickers it
    writes. Anything else a person put here is theirs.
    """
    if not os.path.isdir(dir_path):
        return 0
    removed = 0
    for name in os.listdir(dir_path):
        if not name.endswith(".json") or name[:-5] in keep:
            continue
        path = os.path.join(dir_path, name)
        if os.path.isfile(path):
            os.remove(path)
            removed += 1
    return removed


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
        dropped = 0
        if companies:
            for c in wl["companies"]:
                if not c["ticker"]:
                    continue
                _write(os.path.join(out_dir, "companies", f"{c['ticker']}.json"),
                       company(c["ticker"], conn=conn))
                written += 1
            dropped = _sweep_company_files(
                os.path.join(out_dir, "companies"),
                {c["ticker"] for c in wl["companies"] if c["ticker"]})
    finally:
        conn.close()
    return {"dir": out_dir, "companies": written, "dropped": dropped,
            "watched": wl["n_companies"], "runs": len(job_log["runs"])}
