"""
Ledgerline CLI.

    ledgerline universe --tickers AAPL,MSFT      set the tracked universe
    ledgerline backfill                          pull companyfacts, persist metrics
    ledgerline coverage                          which filers are scoreable, and why not
    ledgerline scan                              Tier 0 change detection -> score
    ledgerline score TICKER --as-of 2023-05-15   one filer, one date
    ledgerline split --seed 20260807             build + hash the tuning/holdout split
    ledgerline prereg                            write the decision rule (once)
    ledgerline validate --split tuning           run the gate, print SHIP/KILL on holdout
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

import typer

from . import backtest, edgar, signals_v3
from . import calibrate as calib
from . import universe as uni
from .validate import harness

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def universe(tickers: str = typer.Option(..., help="comma-separated")):
    rows = edgar.set_universe([t.strip() for t in tickers.split(",")])
    typer.echo(f"universe set: {len(rows)} filers")


@app.command()
def backfill():
    """Pull companyfacts for every filer in the universe and persist metrics."""
    uni = edgar.universe()
    if not uni:
        typer.echo("universe is empty -- run `ledgerline universe` first")
        raise typer.Exit(1)
    for cik, meta in uni.items():
        norm = edgar.normalize(cik)
        if not norm:
            typer.echo(f"  {meta['ticker']:6} no XBRL facts")
            continue
        n = edgar.persist_metrics(cik, norm)
        cov = edgar.coverage_report(norm)
        bad = [m for m, c in cov.items() if c["n"] and not c["scoreable"]]
        note = f"  [low coverage: {', '.join(bad)}]" if bad else ""
        typer.echo(f"  {meta['ticker']:6} {n:5} rows{note}")


@app.command()
def coverage():
    """Report which filers are scoreable. A gappy filer must be excluded loudly,
    not scored on partial data -- that is how the OCF derivation bug stayed
    invisible for as long as it did."""
    conn = edgar.db()
    for cik, ticker, _ in conn.execute("SELECT cik, ticker, name FROM universe"):
        norm = edgar.normalize(cik)
        if not norm:
            continue
        rep = edgar.coverage_report(norm)
        blocked = [f"{m} {c['ratio']:.0%}" for m, c in rep.items() if c["n"] and not c["scoreable"]]
        status = "SCOREABLE" if not blocked else "EXCLUDED: " + ", ".join(blocked)
        typer.echo(f"  {ticker:6} {status}")
    conn.close()


@app.command()
def scan(days_back: int = 1):
    """Tier 0 change detection, then score only what actually filed.

    On a quiet day this exits having made one HTTP request.
    """
    started = datetime.utcnow().isoformat()
    hits = edgar.detect_changes(days_back=days_back)
    if not hits:
        typer.echo("no new filings in universe")
        return

    scored, gated = 0, 0
    today = date.today().isoformat()
    for h in hits:
        res = signals_v3.evaluate(h["ticker"], h["cik"], as_of=today)
        if not res["scoreable"]:
            typer.echo(f"  {h['ticker']:6} skipped: {res['reason']}")
            continue
        scored += 1
        gated += int(res["gated_in"])
        mark = "FIRE" if res["gated_in"] else "    "
        codes = ",".join(f["code"] for f in res["flags"])
        typer.echo(f"  {mark} {h['ticker']:6} {res['score']:5.1f}  {codes}")

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
    typer.echo(f"scored {scored}, gated in {gated}")


@app.command()
def score(ticker: str, as_of: str = typer.Option(None, help="YYYY-MM-DD, defaults to today")):
    uni = {v["ticker"]: k for k, v in edgar.universe().items()}
    cik = uni.get(ticker.upper())
    if not cik:
        typer.echo(f"{ticker} not in universe")
        raise typer.Exit(1)
    typer.echo(json.dumps(signals_v3.evaluate(ticker.upper(), cik, as_of=as_of), indent=2))


@app.command()
def cases():
    """Generate the case registry across the admissible universe.

    Positives and controls are DERIVED from filings, not curated. A filer whose
    later filings show a fundamental deterioration event is a positive; one that
    never does is a control. Every rejection is recorded with its reason.
    """
    tickers = {v["ticker"]: k for k, v in edgar.universe().items()}
    if not tickers:
        typer.echo("universe is empty -- run `ledgerline universe` first")
        raise typer.Exit(1)
    payload = harness.build_cases(tickers)
    typer.echo(f"positives {payload['n_positive']}  controls {payload['n_control']}")
    typer.echo(f"regimes   {', '.join(payload['regimes']) or 'none'}")
    for r in payload["rejected"]:
        typer.echo(f"  rejected {r['ticker']:6} {r['reason']}")

    ready = harness.readiness(payload)
    for name, c in ready["checks"].items():
        mark = "PASS" if c["pass"] else "FAIL"
        typer.echo(f"  {mark}  {name}: {c['value']} (need {c['limit']})")
    if not ready["ready"]:
        typer.echo("\ncase set is not large or broad enough to build a split from")
        raise typer.Exit(1)


@app.command()
def regimes():
    """The regime windows a case must fall into. Pre-2011 is not available:
    XBRL coverage does not exist contemporaneously before the mandate."""
    for name, (start, end, why) in uni.REGIMES.items():
        typer.echo(f"  {name:26} {start} .. {end}\n      {why}")
    typer.echo(f"\nexcluded sectors: SIC {uni.EXCLUDED_SIC_RANGES}")


@app.command()
def split(seed: int = typer.Option(..., help="record this in the commit message")):
    """Build and hash the tuning/holdout split. Commit before touching thresholds."""
    ready = harness.readiness()
    if not ready["ready"]:
        typer.echo("case set not ready -- run `ledgerline cases` and read the FAILs")
        raise typer.Exit(1)
    payload = harness.make_split(seed=seed)
    typer.echo(f"tuning  {len(payload['tuning'])}")
    typer.echo(f"holdout {len(payload['holdout'])}")
    typer.echo(f"sha256  {payload['sha256']}")
    typer.echo("commit ledgerline/data/split.json now -- editing it later burns the holdout")


@app.command()
def prereg():
    """Write the decision rule. Refuses to overwrite."""
    typer.echo(json.dumps(harness.write_prereg(), indent=2))


@app.command()
def calibrate(split: str = "tuning"):
    """Phase 0f. Fit weights and the operating point on the TUNING split.

    Never touches the holdout. Commit calibration.json before running
    `validate --split holdout`, which may only be run once.
    """
    def progress(i, n, rows):
        if i % 25 == 0 or i == n:
            typer.echo(f"  {i}/{n} cases, {rows} filer-quarters")

    payload = calib.run(split=split, progress=progress)
    c = payload["chosen"]
    typer.echo(f"\nrows {payload['n_rows']}  deteriorating {payload['n_positive_rows']}")
    typer.echo(f"Z_TRIGGER {c['z_trigger']}  raw_cutoff {c.get('raw_cutoff')}  "
               f"SCORE_DIVISOR {payload['SCORE_DIVISOR']}")
    typer.echo(f"tuning fpr/quarter {c.get('tuning_fpr_per_quarter')}  "
               f"recall {c.get('tuning_recall_on_deteriorating_quarters')}")
    for f, w in sorted(c["weights"].items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {f:24} {w:7.3f}")


@app.command()
def validate(split: str = "tuning", start_year: int = 2005, end_year: int = 2025):
    """Run the gate across a split. On holdout, prints SHIP or KILL."""
    report = backtest.run(split=split, start_year=start_year, end_year=end_year)
    if "verdict" in report:
        v = report["verdict"]
        for name, c in v["checks"].items():
            mark = "PASS" if c["pass"] else "FAIL"
            typer.echo(f"  {mark}  {name}: {c['value']} (limit {c['limit']})")
        typer.echo(f"\n{v['verdict']}\n{v['note']}")
        if v["verdict"] == "KILL":
            sys.exit(2)
    else:
        fired = sum(1 for o in report["outcomes"] if o["fired"])
        typer.echo(f"tuning split: {fired}/{len(report['outcomes'])} fired")


if __name__ == "__main__":
    app()
