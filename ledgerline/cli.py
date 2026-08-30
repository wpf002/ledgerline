"""
Ledgerline: reads companies' official SEC filings and looks for numbers that
have started behaving unlike that same company's own past.

Note: the detection method missed its own pre-registered test on 2026-08-30
(29% caught vs a 60% target). The plumbing is solid and tested; the detector
is not good enough yet. Every number is published so anyone can check.

Daily use:
    ledgerline watch --add AAPL,MSFT     choose which companies to watch
    ledgerline fetch                     download their filing history from the SEC
    ledgerline check                     which of them can be assessed, and why not
    ledgerline scan                      read today's filings, keep the numbers current
    ledgerline explain AAPL              one company, in plain words
    ledgerline status                    what is set up, what the last test said

Records:
    ledgerline runs                      the log of past scans and fetches
    ledgerline restatements              figures that companies later revised
    ledgerline provenance AAPL           which SEC filings a reading came from
    ledgerline signals                   every saved assessment, kept verbatim
                                         and permanently
    ledgerline peers                     which companies have industry peers
                                         to compare against (measured, unused)

Research (the validation experiment; most are one-shot):
    build-cases, periods, split, commit-rule, calibrate, run-test,
    phase0-freeze, replay
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date

import typer

from . import backtest, edgar, emit, ingest, render, restate, signals_v3
from . import calibrate as calib
from . import coverage as cov
from . import peers as peer_mod
from . import provenance as prov
from . import status as phase0
from . import universe as uni
from .validate import harness

app = typer.Typer(add_completion=False, help=__doc__)


def _resolve(ticker: str) -> str | None:
    tickers = {v["ticker"]: k for k, v in edgar.universe().items()}
    return tickers.get(ticker.upper())


def _no_watchlist_exit() -> None:
    typer.echo("No companies are being watched yet. Add some:")
    typer.echo("  ledgerline watch --add AAPL,MSFT")
    raise typer.Exit(1)


# ------------------------------------------------------------------ daily use


@app.command()
def watch(add: str = typer.Option(None, help="Stock tickers to start watching, "
                                             "e.g. AAPL,MSFT. US SEC filers only."),
          tickers: str = typer.Option(None, hidden=True,
                                      help="Old spelling of --add.")):
    """List the companies being watched, or add more with --add."""
    add = add or tickers
    if add:
        rows = edgar.set_universe([t.strip() for t in add.split(",")])
        typer.echo(f"Now watching {len(rows)} more compan"
                   f"{'y' if len(rows) == 1 else 'ies'}. "
                   "(Any ticker the SEC does not recognise is listed above.)")
        typer.echo("Next: ledgerline fetch")
        return
    u = edgar.universe()
    if not u:
        _no_watchlist_exit()
    typer.echo(f"Watching {len(u)} companies:")
    tick = sorted(v["ticker"] for v in u.values())
    for i in range(0, len(tick), 12):
        typer.echo("  " + " ".join(f"{t:7}" for t in tick[i:i + 12]))


@app.command()
def fetch(only: str = typer.Option(None, help="Fetch just these tickers, "
                                              "e.g. AAPL,MSFT."),
          refresh: bool = typer.Option(False, "--refresh/--no-refresh",
                                       help="Re-download filing histories even "
                                            "when a copy is already on disk. Use "
                                            "after a company files something new."),
          limit: int = typer.Option(None, help="Stop after this many companies."),
          resume: bool = typer.Option(True, "--resume/--no-resume",
                                      help="Skip companies already fetched "
                                           "successfully. On by default.")):
    """Download every watched company's filing history from the SEC.

    Slow the first time (one request per company, politely throttled); nearly
    instant after that, because finished downloads are kept and skipped. If it
    stops partway, run it again -- it picks up where it left off. Run
    `ledgerline check` next.
    """
    if not edgar.universe():
        _no_watchlist_exit()
    tickers = [t.strip() for t in only.split(",")] if only else None
    out = ingest.backfill(only=tickers, refresh=refresh, limit=limit,
                          resume=resume)
    for f in out["filers"]:
        if f["status"] == "no_facts":
            typer.echo(f"  {f['ticker']:6} no machine-readable filings at the SEC")
        elif f["status"] == "error":
            typer.echo(f"  {f['ticker']:6} could not fetch ({f['error']}) -- "
                       "run `ledgerline fetch` again to retry")
        else:
            note = ""
            if f["low_coverage"]:
                note = ("  (gaps in "
                        + ", ".join(render.plain_metric(m) for m in f["low_coverage"])
                        + " -- `ledgerline check` has details)")
            typer.echo(f"  {f['ticker']:6} {f['rows']:5} quarterly figures stored{note}")
    c = out["counters"]
    if not out["filers"]:
        typer.echo("Everything is already fetched. Use --refresh to re-download, "
                   "or --no-resume to start over.")
    typer.echo(f"Downloaded {c['requests']} file{'s' if c['requests'] != 1 else ''} "
               f"from the SEC; {c['cache_hits']} came free from the local copy.")
    typer.echo("Next: ledgerline check")


@app.command()
def check(as_of: str = typer.Option(None, help="Check as of this date "
                                    "(YYYY-MM-DD), using only figures filed "
                                    "by then. Defaults to today."),
          ticker: str = typer.Option(None, help="Full detail for one company "
                                     "instead of the whole watchlist."),
          limit: int = typer.Option(None, help="Stop after this many "
                                    "companies -- a quick look while the full "
                                    "list would be slow."),
          persist: bool = typer.Option(True, "--persist/--no-persist",
                                       help="Record the results in the local "
                                            "database and write a report file "
                                            "under reports/. On by default for "
                                            "the whole-watchlist view."),
          out: str = typer.Option(None, help="Directory for the report files. "
                                             "Defaults to reports/.")):
    """Which watched companies can be assessed, why the rest cannot be, and how
    many of the thirteen measures each assessable company actually gets.

    Per company it shows READY or CANNOT ASSESS by the same rule the scanner
    uses, then a summary: which measures are most often unavailable and why,
    and how much a typical company's best possible score is compressed by
    missing measures. Slow on a cold cache (one polite SEC download per
    company); fast after `ledgerline fetch`. --ticker gives one company in
    full detail and records nothing.
    """
    if ticker:
        cik = _resolve(ticker)
        if not cik:
            typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
            typer.echo(f"  ledgerline watch --add {ticker.upper()}")
            raise typer.Exit(1)
        cutoff = as_of or date.today().isoformat()
        fc = cov.filer_coverage(cik, ticker.upper(), edgar.normalize(cik), cutoff)
        sic = edgar.sic_map()
        ps = peer_mod.peer_set(cik, sic)
        fc = dataclasses.replace(fc, peer_level=ps.level, peer_n=ps.n())
        typer.echo(cov.render_filer(fc))
        return

    if not edgar.universe():
        _no_watchlist_exit()
    # The verdict prints before any number -- this surface says what the tool
    # can and cannot assess, which reads as the tool working.
    typer.echo(phase0.banner())
    typer.echo("")
    dash = cov.build(as_of=as_of, limit=limit)
    for fc in dash.filers:
        typer.echo(render.check_line(
            fc.ticker, fc.scoreable,
            None if fc.scoreable else fc.detail,
            sorted(m for m, c in fc.metrics.items()
                   if m not in signals_v3.REQUIRED_COVERAGE
                   and c.get("n") and not c.get("scoreable"))))
    typer.echo("")
    typer.echo(cov.render_text(dash))
    if persist:
        cov.persist(dash)
        jpath, mpath = cov.write(dash, out_dir=out)
        typer.echo("")
        typer.echo(f"Recorded in the local database; report written to {mpath} "
                   f"(and {jpath} for machines).")


@app.command()
def peers(ticker: str = typer.Option(None, help="Show one company's peer "
                                                "group instead of the "
                                                "overview.")):
    """How many watched companies have enough same-industry peers to compare
    against -- a measurement only. Nothing in the tool uses peer groups yet,
    and nothing will until there is a way to test whether they help.

    Groups are built from each company's SEC industry code, widening from the
    exact industry to the broader group to the sector until at least 6 peers
    are found. Industry codes are today's, not historical.
    """
    sic = edgar.sic_map()
    if not sic:
        _no_watchlist_exit()
    if ticker:
        cik = _resolve(ticker)
        if not cik:
            typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
            typer.echo(f"  ledgerline watch --add {ticker.upper()}")
            raise typer.Exit(1)
        ps = peer_mod.peer_set(cik, sic)
        if ps.level is None:
            typer.echo(f"{ticker.upper()} has no usable peer group: "
                       + ("the SEC's record does not say what industry it is in."
                          if ps.reason == "UNKNOWN_SECTOR"
                          else "too few watched companies share its industry, "
                               "even at the broadest grouping."))
            return
        names = {v["cik"]: v["ticker"] for v in edgar.universe().values()}
        depth = {4: "its exact industry", 3: "its broader industry group",
                 2: "its sector"}[ps.level]
        typer.echo(f"{ticker.upper()} has {ps.n()} comparable companies within "
                   f"{depth}:")
        listed = [names.get(c, c) for c in ps.members]
        for i in range(0, len(listed), 10):
            typer.echo("  " + " ".join(f"{t:7}" for t in listed[i:i + 10]))
        typer.echo("(Measurement only -- nothing uses peer groups. Industry "
                   "codes are today's, not historical.)")
        return
    census = peer_mod.ladder_census(peer_mod.peer_sets(sic))
    total = sum(census.values())
    typer.echo(f"Of {total} watched companies:")
    typer.echo(f"  {census['4']:4} have at least 6 peers in their exact industry")
    typer.echo(f"  {census['3']:4} only in their broader industry group")
    typer.echo(f"  {census['2']:4} only in their sector")
    typer.echo(f"  {census['none']:4} have no usable group at any level")
    typer.echo(f"  {census['unknown_sector']:4} have no industry code on file")
    typer.echo("")
    typer.echo("Measurement only: nothing in the tool uses peer groups, and "
               "nothing will until there is a way to test whether they help. "
               "Industry codes are today's, not historical.")


@app.command()
def scan(days_back: int = typer.Option(1, help="How many days of SEC filing "
                                               "lists to catch up on."),
         as_of: str = typer.Option(None, help="Scan the filing lists ending on "
                                              "this date (YYYY-MM-DD) instead "
                                              "of today."),
         score: bool = typer.Option(False, "--score/--no-score",
                                    help="Also assess each company that filed. "
                                         "Off by default: the detector failed "
                                         "its own test on 2026-08-30, so its "
                                         "verdicts are opt-in, not a daily "
                                         "feed."),
         refresh: bool = typer.Option(True, "--refresh/--no-refresh",
                                      help="Re-download the filing history of "
                                           "each company that filed, so today's "
                                           "filing is actually in the data. On "
                                           "by default.")):
    """Read the SEC's daily filing list and keep watched companies up to date.

    One request fetches every filing accepted market-wide that day. Companies
    from your watchlist that filed get their numbers re-downloaded and any
    revised past figures are recorded (`ledgerline restatements` lists them).
    Most days nothing happens -- that is normal, and the run is logged either
    way (`ledgerline runs`). Add --score to also assess each filer.
    """
    if not edgar.universe():
        _no_watchlist_exit()
    if score:
        # The verdict prints BEFORE the first result line. A feed that leads
        # with flags and buries the failed test is an alert with a disclaimer.
        typer.echo(phase0.banner())
        typer.echo("")
    out = ingest.scan(days_back=days_back, as_of=as_of, score=score,
                      refresh=refresh)
    if not out["filers"]:
        if out["index_rows"] == 0 and date.today().weekday() >= 5:
            typer.echo("The SEC publishes no filing list at weekends or on "
                       "holidays, so there is nothing to check today.")
        else:
            typer.echo(f"No new filings from your watched companies in the last "
                       f"{days_back} day{'s' if days_back != 1 else ''}. "
                       "That is normal on most days.")
        typer.echo("The run is logged either way: ledgerline runs")
        return

    c = out["counters"]
    for f in out["filers"]:
        forms = ", ".join(f["forms"])
        if f["status"] == "error":
            typer.echo(f"  {f['ticker']:6} filed ({forms}) but the download "
                       f"failed -- the next scan will retry it")
        elif f["status"] == "recorded":
            typer.echo(f"  {f['ticker']:6} filed ({forms}) -- noted; this kind "
                       "of filing carries no quarterly figures")
        else:
            n = f.get("restatements", 0)
            note = (f"; {n} past figure{'s' if n != 1 else ''} revised"
                    if n else "")
            typer.echo(f"  {f['ticker']:6} filed ({forms}) -- figures "
                       f"updated{note}")

    if score:
        typer.echo("")
        for res in out["results"]:
            if not res["scoreable"]:
                typer.echo(f"  {res['ticker']:6} cannot assess -- "
                           f"{render.plain_reason(res['reason'])}")
                continue
            mark = "FLAGGED" if res["gated_in"] else "ok     "
            names = ", ".join(render.PLAIN.get(f["code"].lower(), (f["code"],))[0]
                              for f in res["flags"])
            typer.echo(f"  {mark} {res['ticker']:6} score {res['score']:5.1f} "
                       "of 100" + (f"  ({names})" if names else ""))
        typer.echo(f"\nAssessed {c['scored']} "
                   f"compan{'y' if c['scored'] == 1 else 'ies'}; "
                   f"{c['gated_in']} flagged.")
        if c["gated_in"]:
            typer.echo(render.CAVEAT)
        typer.echo("Details for any company: ledgerline explain TICKER")
    else:
        typer.echo(f"\nUpdated {c['filers_done']} "
                   f"compan{'y' if c['filers_done'] == 1 else 'ies'}; "
                   f"{c['restatements']} revised past "
                   f"figure{'s' if c['restatements'] != 1 else ''} recorded. "
                   "No assessment was made (use --score to assess).")


@app.command()
def explain(ticker: str,
            as_of: str = typer.Option(None, help="Assess as of this date "
                                      "(YYYY-MM-DD), using only figures that had "
                                      "been filed by then. Defaults to today.")):
    """One company, in plain words: flagged or not, which measures moved, and
    what could not be computed. The full numbers: `ledgerline score TICKER`."""
    cik = _resolve(ticker)
    if not cik:
        typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
        typer.echo(f"  ledgerline watch --add {ticker.upper()}")
        raise typer.Exit(1)
    name = edgar.universe()[cik]["name"]
    res = phase0.stamp(signals_v3.evaluate(ticker.upper(), cik, as_of=as_of))
    typer.echo(render.explain(res, name=name))


@app.command()
def score(ticker: str, as_of: str = typer.Option(None, help="YYYY-MM-DD; uses only "
                                                 "figures filed by this date."),
          save: bool = typer.Option(False, "--emit/--no-emit",
                                    help="Also save this assessment to the "
                                         "permanent record that `ledgerline "
                                         "signals` reads. Saved entries can "
                                         "never be edited or deleted, only "
                                         "added to.")):
    """One company, one date, as machine-readable JSON.

    For the human-readable version run `ledgerline explain TICKER`.
    """
    cik = _resolve(ticker)
    if not cik:
        typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
        typer.echo(f"  ledgerline watch --add {ticker.upper()}")
        raise typer.Exit(1)
    # Banner to stderr so the JSON on stdout stays pipeable; the stamp travels
    # inside the JSON so a piped consumer cannot lose the verdict.
    typer.echo(phase0.banner(), err=True)
    res = phase0.stamp(signals_v3.evaluate(ticker.upper(), cik, as_of=as_of))
    if save:
        emit.emit(res, source="score", run_date=date.today().isoformat())
        typer.echo("Saved to the permanent record (ledgerline signals shows "
                   "it; saving the same assessment twice records nothing "
                   "new).", err=True)
    typer.echo(json.dumps(res, indent=2))


@app.command()
def status():
    """What is set up, what is missing, and what the last test said."""
    u = edgar.universe()
    typer.echo(f"Watching        {len(u)} companies"
               + ("" if u else "   (ledgerline watch --add ...)"))
    conn = edgar.db()
    n_metrics = conn.execute("SELECT COUNT(DISTINCT cik) FROM metrics").fetchone()[0]
    last_run = conn.execute(
        "SELECT started_at, status, filers_done, restatements, scored, gated_in "
        "FROM job_runs WHERE job='scan' ORDER BY run_id DESC LIMIT 1").fetchone()
    conn.close()
    typer.echo(f"Fetched         {n_metrics} companies' filing histories"
               + ("" if n_metrics else "   (ledgerline fetch)"))
    if last_run:
        started, st, done, rest, scored_n, gated = last_run
        day = (started or "")[:10]
        if st == "failed":
            typer.echo(f"Last scan       {day}: stopped with an error "
                       "(ledgerline runs has details)")
        elif scored_n:
            typer.echo(f"Last scan       {day}: {scored_n} assessed, "
                       f"{gated} flagged")
        else:
            typer.echo(f"Last scan       {day}: {done} compan"
                       f"{'y' if done == 1 else 'ies'} updated, "
                       f"{rest} revised figure{'s' if rest != 1 else ''}")
    else:
        typer.echo("Last scan       never   (ledgerline scan)")
    typer.echo("")
    # Generated from the committed record, never typed here: a second copy of
    # the result in a string literal is a copy that drifts.
    typer.echo(phase0.banner())


@app.command()
def runs(job: str = typer.Option(None, help="Show only one kind of run: "
                                            "scan or backfill."),
         limit: int = typer.Option(20, help="How many recent runs to show.")):
    """The log of every scan and fetch: when it ran, what it cost, what it found.

    A quiet day appears as a row too -- thousands of filings read, none from
    your watchlist -- which is what proves the daily check stays cheap no
    matter how many companies you watch.
    """
    rows = ingest.run_log(job=job, limit=limit)
    if not rows:
        typer.echo("No runs recorded yet. A scan or fetch writes one:")
        typer.echo("  ledgerline scan")
        return
    for r in rows:
        day = (r["started_at"] or "")[:16].replace("T", " ")
        if r["status"] == "failed":
            first_line = (r["error"] or "").splitlines()[0] if r["error"] else ""
            typer.echo(f"  {day}  {r['job']:8} stopped with an error: "
                       f"{first_line}")
            continue
        if r["status"] == "running":
            typer.echo(f"  {day}  {r['job']:8} still running (or interrupted "
                       "-- a later run will say so)")
            continue
        cost = (f"{r['requests']} download{'s' if r['requests'] != 1 else ''}, "
                f"{r['cache_hits']} from local copies")
        if r["job"] == "scan":
            found = (f"read {r['index_rows']} filings market-wide, "
                     f"{r['universe_hits']} from watched companies")
        else:
            found = (f"{r['filers_done']} compan"
                     f"{'y' if r['filers_done'] == 1 else 'ies'} fetched, "
                     f"{r['filers_failed']} failed")
        extra = ""
        if r["restatements"]:
            extra += f"; {r['restatements']} revised past figures"
        if r["scored"]:
            extra += f"; {r['scored']} assessed, {r['gated_in']} flagged"
        typer.echo(f"  {day}  {r['job']:8} {found}; {cost}{extra}")


@app.command()
def restatements(ticker: str = typer.Option(None, help="One company's revisions "
                                                       "only."),
                 since: str = typer.Option(None, help="Only revisions announced "
                                                      "on or after this date "
                                                      "(YYYY-MM-DD)."),
                 material: bool = typer.Option(
                     True, "--material/--all",
                     help="--material (default) hides revisions under 1%; "
                          "--all shows every recorded revision, however "
                          "small.")):
    """Past figures that a company later revised in a newer filing.

    Detected by comparing each filing against what the same company reported
    before -- not by waiting for a formally amended filing, which is how
    fewer than 1 in 100 revisions actually arrive. Most revisions are tiny
    (rounding, reclassification); the default view hides those under 1%.
    """
    cik = None
    if ticker:
        cik = _resolve(ticker)
        if not cik:
            typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
            typer.echo(f"  ledgerline watch --add {ticker.upper()}")
            raise typer.Exit(1)
    rows = restate.events(cik=cik, since=since, material_only=material)
    if not rows:
        typer.echo("No revised figures recorded"
                   + (f" for {ticker.upper()}" if ticker else "")
                   + (" yet. They are collected as `ledgerline scan` and "
                      "`ledgerline fetch` run." if material is False else
                      ". Try --all to include revisions under 1%, or run "
                      "`ledgerline fetch --refresh` to collect them."))
        return
    names = {v["cik"]: v["ticker"] for v in edgar.universe().values()}
    for r in rows:
        tick = names.get(r["cik"], r["cik"])
        direction = "up" if r["value"] > r["prior_value"] else "down"
        size = f"{r['rel_change']:.1%}"
        note = " (in a formally amended filing)" if r["on_amendment"] else ""
        typer.echo(f"  {tick:6} {render.plain_metric(r['metric'])} for the "
                   f"period ending {r['end_date']}: revised {direction} "
                   f"{size} on {r['filed']} (was {r['prior_value']:,.0f}, "
                   f"now {r['value']:,.0f}){note}")
    typer.echo(f"\n{len(rows)} revision{'s' if len(rows) != 1 else ''} shown"
               + ("" if not material else
                  " -- revisions under 1% are hidden (--all shows them)")
               + ".")


@app.command()
def provenance(ticker: str,
               as_of: str = typer.Option(None, help="Trace the reading as of "
                                                    "this date (YYYY-MM-DD), "
                                                    "using only figures filed "
                                                    "by then.")):
    """Where one company's numbers came from: the exact SEC filings behind them.

    For each measure, shows whether the figure was reported directly, derived
    by arithmetic from reported figures, or summed from components -- and the
    SEC filing identifiers (accession numbers) to check. No score in this
    output: where the numbers came from is true regardless of whether the
    detector works.
    """
    cik = _resolve(ticker)
    if not cik:
        typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
        typer.echo(f"  ledgerline watch --add {ticker.upper()}")
        raise typer.Exit(1)
    cutoff = as_of or date.today().isoformat()
    norm = edgar.normalize(cik)
    if not norm:
        typer.echo(f"{ticker.upper()} has no machine-readable filings at the SEC.")
        raise typer.Exit(1)
    snap = edgar.as_of(norm, cutoff)
    rev = snap.get("revenue")
    if not rev:
        typer.echo(f"{ticker.upper()} had no sales figures on file by {cutoff}, "
                   "so there is nothing to trace at that date.")
        raise typer.Exit(1)
    period = rev[-1]["end"]
    typer.echo(f"{ticker.upper()} as of {cutoff} -- latest quarter ends "
               f"{period}. Each figure below names the SEC filings "
               "(accession numbers) it came from.\n")
    origins = {"reported": "reported directly",
               "derived": "worked out by subtracting reported year-to-date "
                          "figures",
               "summed": "summed from reported components"}
    for metric in sorted(snap):
        t = prov.trace(snap, period, [metric])[metric]
        if not t.get("sources"):
            continue
        how = origins.get(t.get("origin") or "", t.get("origin") or "unknown")
        typer.echo(f"  {render.plain_metric(metric)}: {how}, filed "
                   f"{t['filed']}, from " + ", ".join(t["sources"]))
    typer.echo("\nHow much of this company's record is derived rather than "
               "directly reported is normal to see: across measured filers "
               "the typical share is about 29%, because most companies file "
               "cash-flow figures cumulatively and quarters must be "
               "subtracted out.")


@app.command("signals")
def signals_cmd(ticker: str = typer.Option(None, help="One company's saved "
                                                      "assessments only."),
                since: str = typer.Option(None, help="Only assessments made as "
                                                     "of this date (YYYY-MM-DD) "
                                                     "or later."),
                gated_in: bool = typer.Option(False, "--gated-in",
                                              help="Only assessments that "
                                                   "flagged the company."),
                gate_version: str = typer.Option(None, help="Only assessments "
                                                 "made by one exact version of "
                                                 "the detector (--json shows "
                                                 "each entry's version)."),
                limit: int = typer.Option(50, help="How many entries to show, "
                                                   "newest first."),
                as_json: bool = typer.Option(False, "--json",
                                             help="Full machine-readable "
                                                  "entries instead of "
                                                  "sentences.")):
    """Every saved assessment, exactly as it was made at the time.

    Entries are added by `ledgerline scan --score`, `ledgerline score TICKER
    --emit` and `ledgerline replay`; nothing can edit or delete one. Each entry
    keeps the verdict, the measures behind it, the SEC filings it traces to,
    and the version of the detector that made it -- so a change to the
    detector can later be compared against what the old one actually said.
    Companies that could NOT be assessed are entries too, with the reason:
    without them, "how often was it wrong" has no denominator.
    """
    rows = emit.load_signals(ticker=ticker, since=since,
                             gated_in=True if gated_in else None,
                             gate_version=gate_version, limit=limit)
    if as_json:
        typer.echo(phase0.banner(), err=True)
        typer.echo(json.dumps(rows, indent=2))
        return
    typer.echo(phase0.banner())
    typer.echo("")
    if not rows:
        typer.echo("No saved assessments match. They are added by "
                   "`ledgerline scan --score`, `ledgerline score TICKER "
                   "--emit`, or `ledgerline replay`.")
        return
    for r in rows:
        day, tick = r["as_of"], (r["ticker"] or r["cik"])
        if not r["scoreable"]:
            typer.echo(f"  {day}  {tick:6} cannot assess -- "
                       f"{render.plain_reason(r['reason'])}")
            continue
        mark = "FLAGGED    " if r["gated_in"] else "not flagged"
        names = ", ".join(render.PLAIN.get(f["code"].lower(), (f["code"],))[0]
                          for f in r["flags"])
        typer.echo(f"  {day}  {tick:6} {mark} scored {r['score']:5.1f} of 100"
                   + (f"  ({names})" if names else ""))
    n_flag = sum(1 for r in rows if r["gated_in"])
    n_no = sum(1 for r in rows if not r["scoreable"])
    typer.echo(f"\n{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} shown, "
               f"newest first: {n_flag} flagged, {n_no} could not be "
               "assessed. Full detail, including which SEC filings each "
               "entry traces to: --json.")
    versions = {r["gate_version"] for r in rows}
    if len(versions) > 1:
        typer.echo(f"These entries were made by {len(versions)} different "
                   "versions of the detector. Scores from different versions "
                   "must not be averaged together (--json shows each "
                   "entry's version).")


# ------------------------------------------------------- research / experiment


@app.command(name="build-cases")
def build_cases():
    """Build the answer key: which watched companies later went wrong, and when.

    "Went wrong" is defined by filings, not share price: at least 2 of 5 signs
    (sales slump, margin collapse, cash drying up, a big write-down, restated
    accounts) within four quarters. Cases are generated from the data, not
    hand-picked, so there is no cherry-picking step to get wrong.
    """
    tickers = {v["ticker"]: k for k, v in edgar.universe().items()}
    if not tickers:
        _no_watchlist_exit()
    payload = harness.build_cases(tickers)
    typer.echo(f"{payload['n_positive']} companies that later went wrong, "
               f"{payload['n_control']} that stayed fine.")
    typer.echo("Market eras represented: "
               + (", ".join(payload["regimes"]) or "none"))
    for r in payload["rejected"]:
        typer.echo(f"  left out {r['ticker']:6} {r['reason']}")

    ready = harness.readiness(payload)
    for name_, c in ready["checks"].items():
        mark = "PASS" if c["pass"] else "FAIL"
        typer.echo(f"  {mark}  {name_}: {c['value']} (need {c['limit']})")
    if not ready["ready"]:
        typer.echo("\nNot enough material to run a fair test yet -- see the FAIL "
                   "lines above.")
        raise typer.Exit(1)


@app.command()
def periods():
    """The six market eras a test case has to fall into, and why each is here.

    Nothing before 2011: the SEC's machine-readable format only became
    mandatory then, so earlier years cannot be tested honestly.
    """
    for name_, (start, end, why) in uni.REGIMES.items():
        typer.echo(f"  {name_:26} {start} .. {end}\n      {why}")
    typer.echo("\nBanks, insurers and property trusts are excluded throughout: "
               "every measure here assumes an operating company.")


@app.command()
def split(seed: int = typer.Option(..., help="Random seed; record it in the "
                                             "commit message so the draw is "
                                             "reproducible.")):
    """Divide the cases into a practice half and a sealed test half.

    The sealed half is scored exactly once, later. Changing it after this
    point voids the whole experiment, so the file is committed with a
    fingerprint and the tool refuses to redraw it.
    """
    ready = harness.readiness()
    if not ready["ready"]:
        typer.echo("The case set is not ready -- run `ledgerline build-cases` "
                   "and read the FAIL lines.")
        raise typer.Exit(1)
    payload = harness.make_split(seed=seed)
    typer.echo(f"practice half  {len(payload['tuning'])}")
    typer.echo(f"sealed half    {len(payload['holdout'])}")
    typer.echo(f"fingerprint    {payload['sha256']}")
    typer.echo("Commit ledgerline/data/split.json now. Editing it later voids "
               "the test.")


@app.command(name="commit-rule")
def commit_rule():
    """Write down the pass mark before the test runs. Refuses to overwrite."""
    typer.echo(json.dumps(harness.write_prereg(), indent=2))


@app.command()
def calibrate(split: str = "tuning"):
    """Set the detector's dials using the practice half only.

    Never touches the sealed half; it refuses if asked. Commit
    ledgerline/data/calibration.json before running the test.
    """
    def progress(i, n, rows):
        if i % 25 == 0 or i == n:
            typer.echo(f"  {i}/{n} companies, {rows} company-quarters")

    payload = calib.run(split=split, progress=progress)
    c = payload["chosen"]
    typer.echo(f"\n{payload['n_rows']} company-quarters, "
               f"{payload['n_positive_rows']} followed by a bad turn")
    typer.echo(f"trigger {c['z_trigger']} | raw cutoff {c.get('raw_cutoff')} | "
               f"score divisor {payload['SCORE_DIVISOR']}")
    typer.echo(f"practice-half false alarms {c.get('tuning_fpr_per_quarter')} "
               f"per company-quarter; catches "
               f"{c.get('tuning_recall_on_deteriorating_quarters')}")
    for f, w in sorted(c["weights"].items(), key=lambda kv: -kv[1]):
        short, _ = render.PLAIN.get(f, (f, ""))
        typer.echo(f"  {short:22} {w:7.3f}")


@app.command(name="phase0-freeze")
def phase0_freeze(report: str = typer.Option(None, help="Path to the one-shot test "
                                             "report. Defaults to "
                                             "reports/backtest_holdout.json.")):
    """Copy the failed test's numbers into a small file that gets committed.

    The full test report is not kept in git, so without this file a fresh copy
    of the project holds no record of the 2026-08-30 result. Every command
    that shows a score reads the file and stops with an error if it is
    missing. Run this once, commit ledgerline/data/phase0.json, then leave it
    alone -- it refuses to overwrite.
    """
    phase0.freeze(report)
    typer.echo(f"Wrote {phase0.PHASE0_PATH}. Commit it: it is the record every "
               "score-showing command reads.")
    typer.echo("")
    typer.echo(phase0.banner())


@app.command(name="run-test")
def run_test(split: str = "tuning", start_year: int = 2005, end_year: int = 2025):
    """Score a half of the cases. On the sealed half this is one-shot and
    prints the pass/fail verdict against the committed pass mark."""
    report = backtest.run(split=split, start_year=start_year, end_year=end_year)
    if "verdict" in report:
        typer.echo("Sealed test half, scored against the pass mark committed in "
                   "ledgerline/data/prereg.json before the run.\n")
        typer.echo(render.verdict_text(report["verdict"]))
        if report["verdict"]["verdict"] == "KILL":
            sys.exit(2)
    else:
        fired = sum(1 for o in report["outcomes"] if o["fired"])
        typer.echo(f"practice half: {fired} of {len(report['outcomes'])} "
                   "companies flagged at least once")


@app.command()
def replay(split: str = typer.Option("tuning", help="Which half of the cases "
                                     "to replay. Only 'tuning' (the practice "
                                     "half) is allowed."),
           start_year: int = typer.Option(2005, help="First year of quarterly "
                                                     "checkpoints."),
           end_year: int = typer.Option(2025, help="Last year of quarterly "
                                                   "checkpoints.")):
    """Re-assess every practice-half company at every past quarterly checkpoint
    and save each result to the permanent record, so a future revision of the
    detector has a history to be compared against instead of zero rows.

    Prints row counts only -- never a hit rate or an alarm rate. Judging the
    detector's performance is what the one-shot sealed test was for, and that
    already happened. Safe to re-run: an assessment already on record is
    recognised and skipped, not duplicated.
    """
    if split != "tuning":
        typer.echo("replay works on the practice half only ('tuning'). The "
                   "sealed test half was scored exactly once, on 2026-08-30, "
                   "and that one measurement is only meaningful while it "
                   "stays the only one -- re-assessing those companies, even "
                   "quietly into a database table, would be a second look. "
                   "The tool refuses, and there is no override flag.")
        raise typer.Exit(2)
    # The results being saved are scores, so the failed-test banner leads.
    typer.echo(phase0.banner())
    typer.echo("")
    cases = harness.load_split("tuning")
    cutoffs = backtest.quarterly_cutoffs(start_year, end_year)
    typer.echo(f"Re-assessing {len(cases)} practice-half companies at "
               f"{len(cutoffs)} quarterly checkpoints "
               f"({cutoffs[0]} .. {cutoffs[-1]}). Companies that cannot be "
               "assessed at a checkpoint are recorded too, with the reason.")
    norms = {c.cik: edgar.normalize(c.cik) for c in cases}
    written = already = 0
    conn = edgar.db()
    try:
        for i, cutoff in enumerate(cutoffs, 1):
            # One emit per checkpoint, AFTER every company is evaluated at it:
            # each saved entry carries that checkpoint's full denominator.
            verdicts = [signals_v3.evaluate(c.ticker, c.cik, as_of=cutoff,
                                            norm=norms[c.cik]) for c in cases]
            out = emit.emit_run(verdicts, source="replay", run_date=cutoff,
                                split="tuning", conn=conn)
            written += out["written"]
            already += out["already"]
            if i % 8 == 0 or i == len(cutoffs):
                typer.echo(f"  {i}/{len(cutoffs)} checkpoints done; "
                           f"{written} new entries saved so far")
    finally:
        conn.close()
    typer.echo(f"\nSaved {written} new assessment"
               f"{'s' if written != 1 else ''} to the permanent record; "
               f"{already} {'was' if already == 1 else 'were'} already there "
               "and left untouched. Row counts only -- this command never "
               "reports performance. Browse them: ledgerline signals")


# -------------------------------------------------- old names, kept working


def _alias(name: str, target) -> None:
    app.command(name=name, hidden=True)(target)


_alias("universe", watch)
_alias("backfill", fetch)
_alias("coverage", check)
_alias("cases", build_cases)
_alias("regimes", periods)
_alias("prereg", commit_rule)
_alias("validate", run_test)


if __name__ == "__main__":
    app()
