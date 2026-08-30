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
    ledgerline scan                      read today's filings, flag anything unusual
    ledgerline explain AAPL              one company, in plain words
    ledgerline status                    what is set up, what the last test said

Research (the validation experiment; most are one-shot):
    build-cases, periods, split, commit-rule, calibrate, run-test
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

import typer

from . import backtest, edgar, render, signals_v3
from . import calibrate as calib
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
def fetch():
    """Download every watched company's filing history from the SEC.

    Slow the first time (one request per company, politely throttled); nearly
    instant after that, because accepted filings never change and are cached
    forever. Run `ledgerline check` next.
    """
    u = edgar.universe()
    if not u:
        _no_watchlist_exit()
    for cik, meta in u.items():
        norm = edgar.normalize(cik)
        if not norm:
            typer.echo(f"  {meta['ticker']:6} no machine-readable filings at the SEC")
            continue
        n = edgar.persist_metrics(cik, norm)
        cov = edgar.coverage_report(norm)
        bad = sorted(m for m, c in cov.items() if c["n"] and not c["scoreable"])
        note = ""
        if bad:
            note = ("  (gaps in " + ", ".join(render.plain_metric(m) for m in bad)
                    + " -- `ledgerline check` has details)")
        typer.echo(f"  {meta['ticker']:6} {n:5} quarterly figures stored{note}")
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
                                               "lists to catch up on.")):
    """Read the SEC's daily filing list and assess watched companies that filed.

    One request fetches every filing accepted market-wide that day; anything
    from your watchlist is scored. Most days nothing fires -- that is normal.
    """
    started = datetime.utcnow().isoformat()
    hits = edgar.detect_changes(days_back=days_back)
    if not hits:
        if date.today().weekday() >= 5:
            typer.echo("The SEC publishes no filing list at weekends or on "
                       "holidays, so there is nothing to check today.")
        else:
            typer.echo(f"No new filings from your watched companies in the last "
                       f"{days_back} day{'s' if days_back != 1 else ''}. "
                       "That is normal on most days.")
        return

    scored, gated = 0, 0
    today = date.today().isoformat()
    for h in hits:
        res = signals_v3.evaluate(h["ticker"], h["cik"], as_of=today)
        if not res["scoreable"]:
            typer.echo(f"  {h['ticker']:6} cannot assess -- "
                       f"{render.plain_reason(res['reason'])}")
            continue
        scored += 1
        gated += int(res["gated_in"])
        mark = "FLAGGED" if res["gated_in"] else "ok     "
        names = ", ".join(render.PLAIN.get(f["code"].lower(), (f["code"],))[0]
                          for f in res["flags"])
        typer.echo(f"  {mark} {h['ticker']:6} score {res['score']:5.1f} of 100"
                   + (f"  ({names})" if names else ""))

    conn = edgar.db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_date, scanned, changed, scored, gated_in, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (today, len(hits), len(hits), scored, gated, started,
             datetime.utcnow().isoformat()),
        )
    conn.close()
    typer.echo(f"\nAssessed {scored} compan{'y' if scored == 1 else 'ies'}; "
               f"{gated} flagged.")
    if gated:
        typer.echo(render.CAVEAT)
    typer.echo("Details for any company: ledgerline explain TICKER")


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
    res = signals_v3.evaluate(ticker.upper(), cik, as_of=as_of)
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
    typer.echo(json.dumps(signals_v3.evaluate(ticker.upper(), cik, as_of=as_of),
                          indent=2))


@app.command()
def status():
    """What is set up, what is missing, and what the last test said."""
    u = edgar.universe()
    typer.echo(f"Watching        {len(u)} companies"
               + ("" if u else "   (ledgerline watch --add ...)"))
    conn = edgar.db()
    n_metrics = conn.execute("SELECT COUNT(DISTINCT cik) FROM metrics").fetchone()[0]
    last_run = conn.execute("SELECT run_date, scored, gated_in FROM runs "
                            "ORDER BY run_date DESC LIMIT 1").fetchone()
    conn.close()
    typer.echo(f"Fetched         {n_metrics} companies' filing histories"
               + ("" if n_metrics else "   (ledgerline fetch)"))
    if last_run:
        typer.echo(f"Last scan       {last_run[0]}: {last_run[1]} assessed, "
                   f"{last_run[2]} flagged")
    else:
        typer.echo("Last scan       never   (ledgerline scan)")
    typer.echo("")
    typer.echo("Detection test  MISSED THE BAR (scored once, 2026-08-30):")
    typer.echo("  caught 28.7% of deteriorations; the pre-registered target was 60%.")
    typer.echo("  Warnings averaged 9 months early, false alarms 3.8% per")
    typer.echo("  company-quarter -- but 51% of fine companies were flagged at least")
    typer.echo("  once. Full result: reports/backtest_holdout.json and ROADMAP.md.")


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
