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

Research (the validation experiment; most are one-shot):
    build-cases, periods, split, commit-rule, calibrate, run-test, phase0-freeze
"""
from __future__ import annotations

import json
import sys
from datetime import date

import typer

from . import backtest, edgar, ingest, render, restate, signals_v3
from . import calibrate as calib
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
def check():
    """Which watched companies can be assessed, and what is missing for the rest.

    READY / CANNOT ASSESS follows the same rule the scanner uses: sales, cash
    from operations and profit must each appear in at least 90% of quarters.
    Other gaps only switch off individual measures, and are noted, not fatal.
    """
    conn = edgar.db()
    rows = conn.execute("SELECT cik, ticker FROM universe ORDER BY ticker").fetchall()
    conn.close()
    if not rows:
        _no_watchlist_exit()
    for cik, ticker in rows:
        norm = edgar.normalize(cik)
        if not norm:
            typer.echo(render.check_line(ticker, False, "no XBRL facts", []))
            continue
        rep = edgar.coverage_report(norm)
        hard = [m for m in signals_v3.REQUIRED_COVERAGE
                if m in rep and (not rep[m]["n"] or not rep[m]["scoreable"])]
        soft = [m for m, c in rep.items()
                if m not in signals_v3.REQUIRED_COVERAGE
                and c["n"] and not c["scoreable"]]
        reason = None
        if hard:
            detail = ", ".join(f"{m} {rep[m]['ratio']:.0%}" for m in hard)
            reason = f"insufficient quarterly coverage: {detail}"
        typer.echo(render.check_line(ticker, not hard, reason, soft))


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
                                                 "figures filed by this date.")):
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
    typer.echo(json.dumps(phase0.stamp(signals_v3.evaluate(ticker.upper(), cik,
                                                           as_of=as_of)),
                          indent=2))


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
