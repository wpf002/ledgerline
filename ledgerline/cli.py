"""
Ledgerline: reads companies' official SEC filings and looks for numbers that
have started behaving unlike that same company's own past.

Note: the detection method missed its own pre-registered test on 2026-08-30
(29% caught vs a 60% target). The plumbing is solid and tested; the detector
is not good enough yet. Every number is published so anyone can check.

Daily use:
    ledgerline watch --add AAPL,MSFT     choose which companies to watch
    ledgerline watch --import list.csv   add a whole spreadsheet of them
    ledgerline groups                    your own groupings of watched
                                         companies; most commands take
                                         --group NAME
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
    ledgerline narrate AAPL              a machine-drafted summary of a flagged
                                         assessment -- every figure in it is
                                         checked against the computed numbers,
                                         or no summary is published
    ledgerline narrations                the record of every summary written
                                         or refused, and what each one cost
    ledgerline peers                     which companies have industry peers
                                         to compare against (measured, unused)
    ledgerline publish                   write every saved assessment, the
                                         watchlist and the run log to files
                                         other programs can read
    ledgerline export watchlist --out f.csv
                                         the watchlist, the saved assessments,
                                         or the run log as a spreadsheet
    ledgerline digest                    one run's results as a short text
                                         report, written to a file
    ledgerline contract-schema           the exact shape of a published entry
    ledgerline resolve                   judge saved assessments against the
                                         filings that actually followed
    ledgerline pending                   assessments still waiting on the
                                         filings that will decide them
    ledgerline track                     how past assessments turned out, next
                                         to the failed test's own numbers
    ledgerline registry                  every company that has filed routine
                                         reports with the SEC since 2011,
                                         survivors and casualties alike
    ledgerline cost                      what a daily check would cost at
                                         larger watchlist sizes, replayed
                                         against the real filing calendar

Research (the validation experiment; most are one-shot):
    build-cases, periods, split, commit-rule, calibrate, run-test,
    phase0-freeze, replay

    ledgerline retest reserve/register/status
                                         set aside FUTURE company-quarters now,
                                         so a revised detector can one day be
                                         tested on data nobody has seen
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import date

import typer

from . import backtest, csvio, edgar, emit, fullindex, ingest, render, restate, signals_v3
from . import calibrate as calib
from . import cost as cost_mod
from . import coverage as cov
from . import groups as group_mod
from . import narrate as narr
from . import peers as peer_mod
from . import provenance as prov
from . import status as phase0
from . import track as trackrec
from . import universe as uni
from .api import contract, views
from .api import digest as run_digest
from .api import schema as api_schema
from .validate import harness, retest

app = typer.Typer(add_completion=False, help=__doc__)

retest_app = typer.Typer(
    add_completion=False,
    help="Set aside future company-quarters now, before anyone designs a "
         "revised detector, so a revision can one day be tested on data "
         "nobody has seen. The 2026-08-30 test can never be re-run: its "
         "companies are known, so a second look could always have been "
         "peeked at. Fresh data only accrues forward -- every month not "
         "reserved is a month lost.")
app.add_typer(retest_app, name="retest")


def _resolve(ticker: str) -> str | None:
    tickers = {v["ticker"]: k for k, v in edgar.universe().items()}
    return tickers.get(ticker.upper())


def _no_watchlist_exit() -> None:
    typer.echo("No companies are being watched yet. Add some:")
    typer.echo("  ledgerline watch --add AAPL,MSFT")
    raise typer.Exit(1)


def _group_or_exit(name: str) -> dict[str, str]:
    """The companies in one group as {cik: ticker}, or a refusal that says why.

    Three different situations produce zero companies, and a filtered view that
    printed nothing for all three would read as "none of your companies
    qualified" in every one of them. So an unknown name, an empty group and a
    group whose companies have left the watchlist each get their own sentence
    and a non-zero exit -- never a silent empty list.
    """
    ciks = group_mod.members(name)
    if ciks is None:
        typer.echo(f'There is no group called "{name}".')
        known = group_mod.listing()
        if known:
            typer.echo("Groups you have: "
                       + ", ".join(g["name"] for g in known))
            typer.echo("  ledgerline groups          (with how many companies "
                       "are in each)")
        else:
            typer.echo("You have no groups yet. Make one:")
            typer.echo(f"  ledgerline groups --add {name}")
        raise typer.Exit(1)
    uni = edgar.universe()
    out = {c: uni[c]["ticker"] for c in ciks if c in uni}
    if not out:
        typer.echo(f'The group "{name}" has no watched companies in it.')
        typer.echo(f"  ledgerline groups --assign {name} --tickers AAPL,MSFT")
        raise typer.Exit(1)
    return out


# ------------------------------------------------------------------ daily use


@app.command()
def watch(add: str = typer.Option(None, help="Stock tickers to start watching, "
                                             "e.g. AAPL,MSFT. US SEC filers only."),
          import_: str = typer.Option(None, "--import", metavar="FILE.csv",
                                      help="Add every company listed in a "
                                           "spreadsheet file. The first line "
                                           "must name the columns; only a "
                                           "ticker column is required."),
          group: str = typer.Option(None, help="List only the companies in "
                                               "this group."),
          tickers: str = typer.Option(None, hidden=True,
                                      help="Old spelling of --add.")):
    """List the companies being watched, or add more with --add or --import.

    --import reads a CSV file: ticker, name, sector, cik, group and status are
    understood, in any order, and any other column is ignored. Only ticker is
    required. A company already being watched keeps the details already on
    file, and every line of the file is reported back -- added, already
    watched, or not recognised by the SEC. Nothing is downloaded from the SEC
    beyond the ticker list itself; run `ledgerline fetch` afterwards to pull
    the filing histories.
    """
    add = add or tickers
    if add:
        rows = edgar.set_universe([t.strip() for t in add.split(",")])
        typer.echo(f"Now watching {len(rows)} more compan"
                   f"{'y' if len(rows) == 1 else 'ies'}. "
                   "(Any ticker the SEC does not recognise is listed above.)")
        typer.echo("Next: ledgerline fetch")
        return
    if import_:
        _watch_import(import_)
        return
    u = edgar.universe()
    if not u:
        _no_watchlist_exit()
    if group:
        picked = _group_or_exit(group)
        typer.echo(f"{len(picked)} of the {len(u)} watched companies are in "
                   f'"{group}":')
        tick = sorted(picked.values())
    else:
        typer.echo(f"Watching {len(u)} companies:")
        tick = sorted(v["ticker"] for v in u.values())
    for i in range(0, len(tick), 12):
        typer.echo("  " + " ".join(f"{t:7}" for t in tick[i:i + 12]))


def _watch_import(path: str) -> None:
    """Read a watchlist CSV and report what happened to every line of it.

    Per row and not per file: a person who exported 500 tickers from somewhere
    else needs to see which ones the SEC does not recognise, and a single
    "imported 412" cannot be checked against anything.
    """
    if not os.path.exists(path):
        typer.echo(f"There is no file at {path}. Check the path and try again:")
        typer.echo("  ledgerline watch --import ./watchlist.csv")
        raise typer.Exit(1)
    try:
        out = csvio.import_watchlist(path)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    for r in out["rows"]:
        who = r.ticker or "(no ticker)"
        if r.outcome == "added":
            typer.echo(f"  line {r.line:4} {who:6} added to the watchlist")
        elif r.outcome == "already":
            typer.echo(f"  line {r.line:4} {who:6} already being watched -- "
                       "left exactly as it was")
        elif r.outcome == "repeated":
            typer.echo(f"  line {r.line:4} {who:6} skipped -- {r.detail}")
        elif r.outcome == "unresolved":
            typer.echo(f"  line {r.line:4} {who:6} not added -- {r.detail}. "
                       "This is usually a fund, a foreign listing, or a "
                       "symbol that has changed.")
        else:
            typer.echo(f"  line {r.line:4} {who:6} skipped -- {r.detail}")

    c = out["counts"]
    typer.echo("")
    typer.echo(f"{c['added']} added, {c['already']} already being watched, "
               f"{c['unresolved']} not recognised by the SEC, "
               f"{c['repeated']} listed twice in the file, "
               f"{c['malformed']} unreadable.")
    for name, res in out["groups"].items():
        n = res["added"]
        typer.echo(f'Group "{name}": {n} compan{"y" if n == 1 else "ies"} added'
                   + (f", {res['already']} already in it" if res["already"]
                      else "") + ".")
    ig = out["ignored"]
    if ig["sector"]:
        n = ig["sector"]
        typer.echo(f"{n} sector value{'' if n == 1 else 's'} "
                   f"{'was' if n == 1 else 'were'} read and not stored: this "
                   "tool takes a company's industry from the SEC's own record. "
                   "A numeric SEC industry code is kept; a sector name is not.")
    if ig["status"]:
        n = ig["status"]
        typer.echo(f"{n} status value{'' if n == 1 else 's'} "
                   f"{'was' if n == 1 else 'were'} read and not stored: a "
                   "company is either watched or it is not.")
    if c["added"]:
        typer.echo("Nothing was downloaded from the SEC for these companies "
                   "yet. Next: ledgerline fetch")


@app.command("groups")
def groups_cmd(add: str = typer.Option(None, help="Start a new group with this "
                                                  "name, e.g. --add semis."),
               assign: str = typer.Option(None, help="Put companies into this "
                                          "group; name them with --tickers. "
                                          "The group is created if it is new."),
               unassign: str = typer.Option(None, help="Take companies out of "
                                            "this group; name them with "
                                            "--tickers."),
               tickers: str = typer.Option(None, help="Which companies, "
                                           "e.g. AAPL,MSFT. Goes with "
                                           "--assign or --unassign."),
               delete: str = typer.Option(None, help="Remove a group. The "
                                          "companies in it stay on your "
                                          "watchlist; only the grouping "
                                          "goes away.")):
    """Your own groupings of watched companies -- list them, or change one.

    A company can be in as many groups as you like ("semis", "the ones I
    actually own"). Groups are labels over the watchlist and nothing more:
    `ledgerline watch --group semis`, `check --group semis` and `scan --group
    semis` all narrow to one group, and deleting a group never removes a
    company or anything downloaded for it.
    """
    if add:
        made = group_mod.create(add)
        name = group_mod.clean(add)
        if made:
            typer.echo(f'Group "{name}" now exists and is empty. Put '
                       "companies in it:")
        else:
            typer.echo(f'Group "{name}" already exists. Put more companies '
                       "in it:")
        typer.echo(f"  ledgerline groups --assign {name} --tickers AAPL,MSFT")
        return

    if delete:
        n = group_mod.delete(delete)
        if n is None:
            typer.echo(f'There is no group called "{delete}". '
                       "See what you have: ledgerline groups")
            raise typer.Exit(1)
        if n:
            typer.echo(f'Deleted the group "{group_mod.clean(delete)}". The '
                       f"{n} compan{'y' if n == 1 else 'ies'} in it "
                       f"{'is' if n == 1 else 'are'} still on your watchlist, "
                       "with everything downloaded for "
                       f"{'it' if n == 1 else 'them'}.")
        else:
            typer.echo(f'Deleted the group "{group_mod.clean(delete)}". It was '
                       "empty, so nothing else changed.")
        return

    if assign or unassign:
        name = assign or unassign
        if not tickers:
            verb = "put into" if assign else "taken out of"
            typer.echo(f"Name the companies to be {verb} \"{name}\":")
            typer.echo(f"  ledgerline groups "
                       f"--{'assign' if assign else 'unassign'} {name} "
                       "--tickers AAPL,MSFT")
            raise typer.Exit(1)
        wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        ciks, missing = [], []
        for t in wanted:
            cik = _resolve(t)
            if cik:
                ciks.append(cik)
            else:
                missing.append(t)
        for t in missing:
            typer.echo(f"  {t:6} is not on your watchlist, so it cannot be "
                       f"grouped. Add it first: ledgerline watch --add {t}")
        if assign:
            res = group_mod.assign(name, [c for c in ciks if c])
            typer.echo(f'Group "{group_mod.clean(name)}": {res["added"]} '
                       f"compan{'y' if res['added'] == 1 else 'ies'} added"
                       + (f", {res['already']} already in it"
                          if res["already"] else "") + ".")
            typer.echo(f"See them: ledgerline watch --group {name}")
        else:
            removed = group_mod.unassign(name, [c for c in ciks if c])
            if removed is None:
                typer.echo(f'There is no group called "{name}". '
                           "See what you have: ledgerline groups")
                raise typer.Exit(1)
            typer.echo(f'Group "{group_mod.clean(name)}": {removed} '
                       f"compan{'y' if removed == 1 else 'ies'} taken out. "
                       "They are still on your watchlist.")
        if missing:
            raise typer.Exit(1)
        return

    rows = group_mod.listing()
    if not rows:
        typer.echo("You have no groups yet. A group is your own label over "
                   "the watchlist. Make one:")
        typer.echo("  ledgerline groups --add semis")
        typer.echo("  ledgerline groups --assign semis --tickers NVDA,AMD,INTC")
        return
    for g in rows:
        n = g["n"]
        typer.echo(f"  {g['name']:20} {n} compan{'y' if n == 1 else 'ies'}"
                   + ("   (empty -- nothing has been put in it yet)"
                      if not n else ""))
    typer.echo(f"\n{len(rows)} group{'s' if len(rows) != 1 else ''}. "
               "See one: ledgerline watch --group NAME")


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
          group: str = typer.Option(None, help="Check only the companies in "
                                               "this group."),
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
    picked = _group_or_exit(group) if group else None
    if picked:
        typer.echo(f'Checking the {len(picked)} companies in "{group}".')
        typer.echo("")
    dash = cov.build(as_of=as_of, tickers=picked, limit=limit)
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
                                           "by default."),
         group: str = typer.Option(None, help="Only pay attention to the "
                                              "companies in this group. The "
                                              "one filing-list request is the "
                                              "same either way."),
         narrate: bool = typer.Option(False, "--narrate",
                                      help="Also write a short machine-drafted "
                                           "summary for each company that gets "
                                           "flagged. Needs --score, costs one "
                                           "model call per flagged company, "
                                           "and is off by default.")):
    """Read the SEC's daily filing list and keep watched companies up to date.

    One request fetches every filing accepted market-wide that day. Companies
    from your watchlist that filed get their numbers re-downloaded and any
    revised past figures are recorded (`ledgerline restatements` lists them).
    Most days nothing happens -- that is normal, and the run is logged either
    way (`ledgerline runs`). Add --score to also assess each filer.
    """
    if not edgar.universe():
        _no_watchlist_exit()
    if narrate and not score:
        typer.echo("--narrate summarises flagged assessments, and assessing "
                   "is opt-in. Run both together:")
        typer.echo("  ledgerline scan --score --narrate")
        raise typer.Exit(1)
    picked = _group_or_exit(group) if group else None
    if score:
        # The verdict prints BEFORE the first result line. A feed that leads
        # with flags and buries the failed test is an alert with a disclaimer.
        typer.echo(phase0.banner())
        typer.echo("")
    if picked:
        typer.echo(f'Watching for filings from the {len(picked)} companies in '
                   f'"{group}". The SEC filing list is read once either way -- '
                   "narrowing to a group saves per-company work, not the "
                   "request that starts the run.")
    out = ingest.scan(days_back=days_back, as_of=as_of, score=score,
                      refresh=refresh,
                      ciks=set(picked) if picked else None)
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
        if narrate:
            # Only the flagged results reach the model, under the per-run
            # budget; unchanged payloads come back from the store for free.
            for nres in narr.narrate_batch([r for r in out["results"]
                                            if r.get("gated_in")]):
                if nres.status == "narrated":
                    typer.echo(f"  {nres.ticker:6} summary written: "
                               f"{nres.headline}")
                elif nres.status == "cached":
                    typer.echo(f"  {nres.ticker:6} summary unchanged since "
                               "the last run (nothing new was written)")
                else:
                    typer.echo(f"  {nres.ticker:6} no summary -- "
                               f"{nres.reason}")
            typer.echo("Read one in full: ledgerline narrate TICKER")
        typer.echo(f"\nAssessed {c['scored']} "
                   f"compan{'y' if c['scored'] == 1 else 'ies'}; "
                   f"{c['gated_in']} flagged.")
        if c["gated_in"]:
            typer.echo(render.caveat())
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
    # The verdict prints BEFORE the first result line, as on every other
    # score-showing surface. This one printed "FLAGGED. Concern score 100 of
    # 100" on line 4 and left the reader thirty lines to reach a closing
    # sentence about the case they were not in -- and this is the command the
    # README, the web footer and `scan --score` all point a person to.
    typer.echo(phase0.banner())
    typer.echo("")
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


@app.command()
def narrate(ticker: str,
            as_of: str = typer.Option(None, help="Assess as of this date "
                                      "(YYYY-MM-DD), using only figures filed "
                                      "by then. Defaults to today."),
            dry_run: bool = typer.Option(False, "--dry-run",
                                         help="Show exactly what would be "
                                              "sent to the model, without "
                                              "sending it. Free."),
            force: bool = typer.Option(False, "--force",
                                       help="Write a fresh summary even if "
                                            "these exact numbers were already "
                                            "summarised. Costs a model call."),
            as_json: bool = typer.Option(False, "--json",
                                         help="Machine-readable result "
                                              "instead of the printed page.")):
    """Assess one company and, if it is flagged, have a language model draft a
    short plain-English summary of which measures moved and by how much.

    The model only describes numbers this tool already computed -- a program
    checks every figure in the draft against them, and if the draft cannot be
    verified after one correction, no summary is published and the computed
    sentences stand on their own. Costs one model call per flagged company
    (set ANTHROPIC_API_KEY or TRIDENT_ENDPOINT); --dry-run costs nothing.

    Exit codes: 0 summary written (or unchanged), 1 not flagged so nothing to
    summarise, 2 the draft could not be verified.
    """
    cik = _resolve(ticker)
    if not cik:
        typer.echo(f"{ticker.upper()} is not on your watchlist. Add it first:")
        typer.echo(f"  ledgerline watch --add {ticker.upper()}")
        raise typer.Exit(1)
    res = signals_v3.evaluate(ticker.upper(), cik, as_of=as_of)

    from .narrate import payload as npayload
    if dry_run:
        # Builds and prints the full payload without constructing a client:
        # the measurement tool for prompt size and call volume, and the only
        # honest way to see the cost before spending it.
        pl = npayload.build(res)
        typer.echo(json.dumps(pl, indent=2, sort_keys=True))
        typer.echo(f"\nPayload fingerprint: {npayload.payload_sha(pl)}",
                   err=True)
        typer.echo("Nothing was sent to a model and nothing was spent "
                   "(--dry-run).", err=True)
        return

    try:
        nres = narr.narrate(res, force=force)
    except RuntimeError as exc:
        # build_client() already words its refusal for a person: which env
        # var to set, and that --dry-run is the free alternative.
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    if as_json:
        typer.echo(phase0.banner(), err=True)
        typer.echo(json.dumps(nres.as_dict(), indent=2))
    else:
        typer.echo(narr.render(nres))
    if nres.status == "skipped":
        raise typer.Exit(1)
    if nres.status == "abstained":
        raise typer.Exit(2)


@app.command()
def narrations(as_of: str = typer.Option(None, help="Only summaries written "
                                         "about this assessment date "
                                         "(YYYY-MM-DD)."),
               ticker: str = typer.Option(None, help="Only one company's "
                                          "summaries."),
               status: str = typer.Option(None, help="Only one outcome: "
                                          "narrated, abstained, or skipped."),
               limit: int = typer.Option(50, help="How many entries to show, "
                                         "newest first.")):
    """The permanent record of every machine-written summary: what was
    written, what was refused and why, and what each one cost in tokens.

    A rising number of refusals ('abstained') means the drafts kept failing
    verification -- a defect in the prompt or the payload, not a model mood,
    and worth measuring before wiring --narrate into any scheduled job.
    """
    conn = edgar.db()
    q = ("SELECT ticker, as_of, status, attempts, headline, reason, "
         "input_tokens, output_tokens FROM narrations WHERE 1=1")
    args: list = []
    if as_of:
        q += " AND as_of = ?"
        args.append(as_of)
    if ticker:
        q += " AND ticker = ?"
        args.append(ticker.upper())
    if status:
        q += " AND status = ?"
        args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(q, args).fetchall()
    conn.close()
    # Only flagged assessments are ever narrated, so every "summary written"
    # line below is confident model prose about a company this gate fired on.
    # The single-narration view already led with the verdict; this listing --
    # the most product-like output the tool produces -- carried none at all.
    typer.echo(phase0.banner())
    typer.echo("")
    if not rows:
        typer.echo("No summaries recorded yet. They are written by "
                   "`ledgerline narrate TICKER` or `ledgerline scan --score "
                   "--narrate`.")
        return
    itot = otot = 0
    for tick, day, st, attempts, headline, reason, itok, otok in rows:
        itot += itok or 0
        otot += otok or 0
        if st == "narrated":
            typer.echo(f"  {day}  {tick:6} summary written: {headline}")
        elif st == "abstained":
            typer.echo(f"  {day}  {tick:6} refused after {attempts} "
                       f"attempt{'s' if attempts != 1 else ''} -- {reason}")
        else:
            typer.echo(f"  {day}  {tick:6} {st} -- {reason}")
    typer.echo(f"\n{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} shown, "
               f"newest first; {itot:,} tokens sent to the model and "
               f"{otot:,} received across them.")


@app.command()
def publish(since_seq: int = typer.Option(0, help="Continue an earlier export: "
                                                  "write only entries newer than "
                                                  "this position (printed by the "
                                                  "previous publish). 0 rewrites "
                                                  "the whole file."),
            out: str = typer.Option(None, help="File to write; defaults to "
                                               "reports/feed/signals.jsonl."),
            pages: bool = typer.Option(True, "--pages/--no-pages",
                                       help="Also write the files the local "
                                            "viewer reads: the watchlist, the "
                                            "run log, and one file per "
                                            "company. On by default.")):
    """Write every saved assessment to one file, one JSON entry per line.

    This is how other programs read Ledgerline: the local viewer in service/
    reads this file, and so can anything else that speaks JSON. Every line
    carries the record of the failed 2026-08-30 test inside it -- a program
    cannot receive a score from this file without also receiving that fact.
    Companies that could not be assessed are lines too, with the reason.
    Nothing is uploaded or sent anywhere; this writes local files.

    Beside the feed it writes watchlist.json (who is watched, who can be
    assessed and why not, what the last saved assessment said), runs.json (the
    job log) and companies/TICKER.json (one company in full: the saved
    assessment, the filings its numbers came from, the figures it later
    revised, and the same plain-language reading `ledgerline explain` prints).
    Every one of those carries the failed-test record too. Nothing is
    re-assessed here -- publishing reads what has already been saved.
    """
    path = out or contract.FEED_PATH
    n, max_seq = contract.export_jsonl(path, since_seq=since_seq)
    typer.echo(f"Wrote {n} entr{'y' if n == 1 else 'ies'} to {path} "
               f"(now at position {max_seq}).")
    if pages:
        res = views.write_all(os.path.dirname(os.path.abspath(path)))
        typer.echo(f"Also wrote watchlist.json ({res['watched']} companies), "
                   f"runs.json ({res['runs']} runs) and {res['companies']} "
                   f"company files under {os.path.join(res['dir'], 'companies')}.")
        if res["refused"]:
            names = ", ".join(res["refused"][:10])
            more = ("" if len(res["refused"]) <= 10
                    else f" and {len(res['refused']) - 10} more")
            typer.echo(f"No page was written for {names}{more}: a company "
                       f"file is named after its ticker, and "
                       f"{'those are' if len(res['refused']) > 1 else 'that is'} "
                       f"not a ticker symbol. Fix the symbol in the list you "
                       f"imported and run `ledgerline watch --import` again.")
        if res["dropped"]:
            one = res["dropped"] == 1
            typer.echo(f"Removed {res['dropped']} company "
                       f"file{'' if one else 's'} left over from an earlier "
                       f"publish: no watched company answers to "
                       f"{'that symbol' if one else 'those symbols'} now.")
    typer.echo(f"To export only what comes next, later: "
               f"ledgerline publish --since-seq {max_seq}")


@app.command("export")
def export_cmd(what: str = typer.Argument(..., metavar="WHAT",
                                          help="watchlist, signals or runs."),
               out: str = typer.Option(..., "--out", metavar="FILE.csv",
                                       help="The spreadsheet file to write.")):
    """Write the watchlist, every saved assessment, or the job log to a CSV file.

    watchlist  every watched company: name, industry code, its groups, whether
               it can be assessed and why not, and how many of the thirteen
               measures it gets. Read from what `ledgerline check` last
               recorded -- this command assesses nothing.
    signals    one line per saved assessment, including the companies that
               could not be assessed and the reason. Without those there is no
               denominator.
    runs       the log of every scan and fetch: when, what it cost, what it
               found.

    The first line of every file written is a comment carrying the result of
    the failed 2026-08-30 test, so a spreadsheet that leaves this machine
    still says the detector missed its own bar. Nothing is sent anywhere.
    """
    try:
        n = csvio.export(what, out)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {n} row{'s' if n != 1 else ''} to {out}. The first line "
               "is a comment carrying the failed-test result; every "
               "spreadsheet program skips it or shows it as a row.")
    if what == "watchlist":
        typer.echo("Companies never checked say so rather than claiming to be "
                   "assessable: ledgerline check fills that in.")


@app.command()
def digest(run_id: str = typer.Option(None, help="Which run to report on; "
                                                 "defaults to the most recent "
                                                 "saved one."),
           out: str = typer.Option(None, help="File to write; defaults to "
                                              "reports/digest/<date>.txt.")):
    """One run's results as a short text report, written to a file.

    The report leads with the failed-test verdict, then how many companies
    could and could not be assessed (and why not), then -- before any company
    is named -- how many flags pure chance alone would have produced. Only
    after that does it name the flagged companies. Nothing is emailed or sent
    anywhere, deliberately: on the measured numbers, a flag is more often
    wrong than right, so this writes a file and a person decides.
    """
    try:
        d = run_digest.build(run_id)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    text = run_digest.render_text(d)
    path = out or os.path.join("reports", "digest", f"{d['date']}.txt")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    typer.echo(text)
    typer.echo(f"Written to {path}. Nothing was sent anywhere.")


@app.command(name="contract-schema")
def contract_schema(out: str = typer.Option(None, help="File to write; defaults "
                                                       "to service/"
                                                       "signal.schema.json.")):
    """Write the exact shape of a published entry as a JSON Schema file.

    For programs (and people) consuming `ledgerline publish` output in other
    languages: the schema marks the validation block as required, so a
    conforming reader cannot accept a score without the record of the failed
    test. Generated from the code, never edited by hand; a test fails if the
    committed copy and the code disagree.
    """
    path = out or api_schema.SCHEMA_OUT
    digest_hex = api_schema.write(path)
    typer.echo(f"Wrote {path} (sha256 {digest_hex[:16]}...). Commit it; a "
               "shape change fails the tests until this file is regenerated.")


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
def run_test(split: str = typer.Option("tuning", help="Which half of the cases "
                                       "to score. Only 'tuning' (the practice "
                                       "half) is allowed: the sealed half was "
                                       "already scored, once."),
             start_year: int = typer.Option(2005, help="First year of quarterly "
                                                       "checkpoints."),
             end_year: int = typer.Option(2025, help="Last year of quarterly "
                                                     "checkpoints.")):
    """Score the practice half of the cases against the committed pass mark.

    The sealed half is refused. It was scored once, on the date recorded in
    ledgerline/data/phase0.json, and the reserved companies in
    ledgerline/data/retests.json are the only legitimate future test.
    """
    # `replay` refused the sealed half and `calibrate` refused it; this command
    # -- the one whose whole job is to score a split -- did not, so a second
    # shot was one flag away, and its output would have overwritten the only
    # full record of the first. Refused here for the message a person reads,
    # and again inside backtest.run() for every caller that is not this one.
    if split == "holdout" and phase0.holdout_is_spent():
        typer.echo("run-test scores the practice half only ('tuning'), "
                   "because " + phase0.spent_refusal())
        raise typer.Exit(2)
    # The verdict leads, on both branches: a sheet of PASS rows with the failed
    # test nowhere on it is the loudest way this repo can imply a working gate.
    typer.echo(phase0.banner())
    typer.echo("")
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


# ------------------------------------------------------- the track record


@app.command()
def resolve(as_of: str = typer.Option(None, help="Judge outcomes using only "
                                      "filings made by this date (YYYY-MM-DD). "
                                      "Defaults to today."),
            gate_version: str = typer.Option(None, help="Judge only "
                                             "assessments made by this exact "
                                             "detector version. Defaults to "
                                             "the current one.")):
    """Judge every saved assessment against what the company actually filed
    in the quarters that followed -- one, two and four quarters out.

    Only settled answers are written down. A company whose deciding filings
    have not arrived yet stays pending rather than being counted as fine.
    Safe to re-run daily: an unchanged answer writes nothing, and a figure a
    company later revised is recorded as a second entry beside the first,
    never as an overwrite -- what was believed on each date stays on record.
    Designed to run right after `ledgerline scan`.
    """
    out = trackrec.resolve(as_of=as_of, gate_version=gate_version)
    typer.echo(f"Newly judged: {out['resolved']}. "
               f"Changed by a company's own revision: {out['revised']}. "
               f"Already judged, unchanged: {out['unchanged']}.")
    typer.echo(f"Still waiting on future filings: {out['pending']} "
               f"(plus {out['immature']} too recent to even check yet).")
    typer.echo("See where things stand: ledgerline track")


@app.command()
def pending(gate_version: str = typer.Option(None, help="Show only "
                                             "assessments made by this exact "
                                             "detector version."),
            limit: int = typer.Option(20, help="How many waiting assessments "
                                               "to list.")):
    """Saved assessments still waiting to be judged, and the earliest date
    the filings that decide each one could have arrived.

    A short track record usually means a young one, not a bad one -- this
    shows which answers are still in the future.
    """
    rows = trackrec.pending(gate_version=gate_version)
    if not rows:
        typer.echo("Nothing is waiting. Either every saved assessment has "
                   "been judged, or none have been saved yet "
                   "(ledgerline scan / ledgerline replay save them).")
        return
    typer.echo(f"{len(rows)} assessment-horizon pairs are waiting for the "
               "filings that will decide them:")
    for r in rows[:limit]:
        typer.echo(f"  {(r['ticker'] or r['cik']):8} assessed {r['as_of']}, "
                   f"judged {r['horizon_q']} quarter"
                   f"{'s' if r['horizon_q'] != 1 else ''} out -- "
                   f"answer possible from {r['earliest_resolvable']}")
    if len(rows) > limit:
        typer.echo(f"  ... and {len(rows) - limit} more (--limit shows them)")


def _echo_rate(name: str, block: dict, bar: str) -> None:
    """One proportion as a sentence: value, count, interval, and its bar."""
    if block["value"] is None:
        typer.echo(f"  {name}: nothing to measure yet "
                   f"({block['quarters']} settled quarters).")
        return
    typer.echo(f"  {name}: {block['value']:.1%} "
               f"({block['fired']} of {block['quarters']} quarters; the true "
               f"rate is plausibly {block['wilson_low']:.1%} to "
               f"{block['wilson_high']:.1%}). {bar}")


@app.command()
def track(gate_version: str = typer.Option(None, help="Report on this exact "
                                           "detector version. Defaults to "
                                           "the current one."),
          as_json: bool = typer.Option(False, "--json", help="Machine-readable "
                                       "output, and save a dated snapshot so "
                                       "changes over time can be measured.")):
    """How saved assessments actually turned out, shown next to the numbers
    the detector posted when it failed its own test.

    The failed test's numbers are a floor a future revision must beat, never
    a grade being defended. Judged-after-the-fact rates here are per
    company-quarter and are only ever compared to the practice-half rate
    measured the same way -- never to the sealed test's per-company figure,
    which counted something different.
    """
    try:
        payload = trackrec.record_payload(gate_version=gate_version)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from exc
    if as_json:
        trackrec.snapshot(payload)
        typer.echo(json.dumps(payload, indent=2))
        return
    # The banner leads. There is no flag that suppresses it.
    typer.echo(phase0.banner())
    typer.echo("")
    ref = payload["reference"]
    hold = ref["holdout_per_case"]
    tune = ref["tuning_per_quarter"]
    typer.echo("The floor -- the failed detector's own numbers, not a target:")
    typer.echo(f"  Sealed test (per company): caught "
               f"{hold['positive_hit_rate']:.1%} of companies that went on "
               f"to deteriorate (needed at least "
               f"{hold['required_hit_rate']:.0%}).")
    typer.echo(f"  Practice half (per company-quarter): flagged "
               f"{tune['recall_on_deteriorating_quarters']:.1%} of quarters "
               "that were followed by deterioration. Only this per-quarter "
               "rate is comparable to the judged rates below.")
    typer.echo("")
    for h, block in payload["horizons"].items():
        live = block["live"]
        back = block["replay_backfill"]
        typer.echo(f"Judged {h} quarter{'s' if h != '1' else ''} out"
                   + (" (the horizon the test used):" if
                      block["comparable_to_reference"] else
                      " (a shorter window than the test used -- not "
                      "comparable to the floor):"))
        typer.echo(f"  Settled: {live['n_resolved']} live, "
                   f"{back['n_resolved']} replayed from the practice half "
                   f"(kept apart; the practice half proves nothing new). "
                   f"Waiting: {block['n_pending']}.")
        if live["n_resolved"]:
            _echo_rate("Caught, of quarters that turned bad",
                       live["recall_per_quarter"],
                       f"Floor to beat: "
                       f"{live['recall_per_quarter']['floor']['value']:.1%}.")
            _echo_rate("False alarms, among never-deteriorated companies",
                       live["fpr_per_quarter_control_filer"],
                       "The frozen test posted 3.83%; early on this reads "
                       "better than it is, because some quiet companies just "
                       "have not broken yet.")
        typer.echo("")
    mon = payload["monitor"]
    if mon["status"] == "INSUFFICIENT":
        typer.echo(f"Comparison to the floor: too early to say anything. "
                   f"{mon['n_resolved_deteriorating_quarters']} deteriorating "
                   f"quarters have settled; a comparison is noise below "
                   f"{mon['n_required']}.")
    else:
        reading = {
            "BELOW_FLOOR": "running BELOW even the failed test's own rate",
            "CONSISTENT_WITH_FLOOR": "consistent with the failed test's rate",
            "ABOVE_FLOOR": "beating the failed test's own rate -- improvement "
                           "over a failure, not evidence of success",
        }[mon["status"]]
        typer.echo(f"Comparison to the floor: {reading} "
                   f"({mon['recall_per_quarter']:.1%} caught; plausibly "
                   f"{mon['wilson'][0]:.1%} to {mon['wilson'][1]:.1%}, "
                   f"floor {mon['floor']['value']:.1%}).")
    typer.echo("Nothing retunes automatically: adjusting the detector to "
               "its own live results would spoil the only untouched data "
               "left. A change needs a person, and a new sealed test.")


# ------------------------------------------------------- the re-test reserve


@retest_app.command("reserve")
def retest_reserve(name: str = typer.Option(..., help="A short label for this "
                                            "reserved set, e.g. r1. Used to "
                                            "refer to it later; never reused."),
                   after: str = typer.Option(..., help="Reserve quarterly "
                                             "checkpoints strictly after this "
                                             "date (YYYY-MM-DD). Must not be "
                                             "in the past."),
                   end: str = typer.Option(None, help="Last date to reserve "
                                           "through (YYYY-MM-DD). Defaults to "
                                           "two years after --after.")):
    """Set aside a batch of future company-quarters for testing a revised
    detector, and fingerprint the batch so it can never be quietly changed.

    Companies from the sealed 2026-08-30 test are left out entirely, and so is
    any company-quarter the detector's calibration already used. What remains
    is data that does not exist yet -- the only kind a fair test can use.
    Refuses a batch too small to settle anything, and refuses to redraw an
    existing one. Commit ledgerline/data/retests.json afterwards.
    """
    entry = retest.reserve(name, after, end=end)
    typer.echo(f"Reserved '{name}': {entry['n_companies']} companies at "
               f"{len(entry['cutoffs'])} quarterly checkpoints "
               f"({entry['cutoffs'][0]} .. {entry['cutoffs'][-1]}) -- "
               f"{entry['n_pairs']} company-quarters in all, none of which "
               "exist yet.")
    typer.echo(f"Left out: {entry['n_holdout_tickers_excluded']} companies "
               "from the sealed test half, and "
               f"{entry['n_spent_pairs_excluded']} company-quarters the "
               "calibration already used.")
    typer.echo(f"Fingerprint {entry['sha256']}")
    typer.echo(f"Expect roughly {entry['projected_deteriorating_quarters']} "
               "company-quarters followed by a bad turn (a fair comparison "
               f"needs at least {entry['needed_deteriorating_quarters']}; the "
               "estimate assumes every quarter can be assessed, so the real "
               "count will run lower and only becomes known as results "
               "arrive).")
    typer.echo(f"The first reserved quarter can be judged from "
               f"{entry['earliest_scoreable_h4']}; the whole batch by "
               f"{entry['fully_resolved_h4']}. Nothing speeds that up -- the "
               "deciding filings will not have been made yet.")
    typer.echo("Commit ledgerline/data/retests.json now. Editing a reserved "
               "set later shows up as a fingerprint mismatch and the tool "
               "refuses to use it.")


@retest_app.command("register")
def retest_register(gate_version: str = typer.Option(..., help="The revised "
                                                     "detector's exact version "
                                                     "fingerprint (`ledgerline "
                                                     "signals --json` shows the "
                                                     "current one)."),
                    reserved: str = typer.Option(..., help="Name of the "
                                                 "reserved set this revision "
                                                 "will be tested on."),
                    alpha: float = typer.Option(0.025, help="Share of the "
                                                "0.05 false-discovery budget "
                                                "this attempt draws. Spent "
                                                "whether the revision wins or "
                                                "loses; never refilled."),
                    note: str = typer.Option(..., help="What the revision's "
                                             "author had already seen -- at "
                                             "minimum, that the 2026-08-30 "
                                             "result was known. Cannot be "
                                             "blank.")):
    """Put an intended test on the record BEFORE it runs: which revision,
    against which reserved set, at what share of the error budget, and what
    its author already knew.

    Registering does not run anything -- scoring is deliberately not built
    until a reserved set matures. It draws down a shared budget: unlimited
    quiet attempts would guarantee a lucky win eventually, and a win nobody
    can distinguish from luck is worthless.
    """
    attempt = retest.register(gate_version, reserved, alpha, note)
    remaining = retest.ALPHA_BUDGET - retest.alpha_spent()
    typer.echo(f"Registered: revision {attempt['gate_version']} against "
               f"reserved set '{attempt['reserved']}', drawing "
               f"{attempt['alpha']:.3f} of the error budget "
               f"({remaining:.3f} of {retest.ALPHA_BUDGET:.3f} remains).")
    entry = retest.load_reserved(reserved)
    typer.echo(f"That set can first be judged on "
               f"{entry['earliest_scoreable_h4']}.")
    typer.echo("The bar is the failed detector's own numbers -- it caught "
               "28.7% of what it was built to catch -- and beating a floor "
               "that low is the minimum, not the finish line.")
    typer.echo("Commit ledgerline/data/retests.json now.")


@retest_app.command("status")
def retest_status():
    """Where the re-test effort stands: the error budget spent and left, every
    registered attempt with its author's note, and which reserved sets are
    still waiting to mature."""
    rep = retest.status_report()
    if not rep["reserved"] and not rep["attempts"]:
        typer.echo("Nothing is reserved yet, and every month that passes is "
                   "a month of future data lost to fair testing. Reserve one "
                   "now:")
        typer.echo("  ledgerline retest reserve --name r1 --after "
                   f"{date.today().isoformat()}")
        return
    typer.echo(f"Error budget: {rep['alpha_spent']:.3f} of "
               f"{rep['alpha_budget']:.3f} committed, "
               f"{rep['alpha_remaining']:.3f} remaining.")
    typer.echo("")
    for entry in rep["reserved"].values():
        state = ("already used for a test" if entry["spent"]
                 else f"waiting -- can be judged from "
                      f"{entry['earliest_scoreable_h4']}")
        typer.echo(f"  '{entry['name']}': {entry['n_pairs']} company-quarters "
                   f"({entry['cutoffs'][0]} .. {entry['cutoffs'][-1]}), "
                   f"{state}.")
    if rep["attempts"]:
        typer.echo("")
        typer.echo("Registered attempts:")
        for a in rep["attempts"]:
            done = "scored" if a["scored"] else "not yet scored"
            typer.echo(f"  {a['registered_on']}  revision {a['gate_version']} "
                       f"vs '{a['reserved']}' at {a['alpha']:.3f} of the "
                       f"budget -- {done}.")
            typer.echo(f"      author's note: {a['contamination_note']}")
    typer.echo("")
    typer.echo("The floor any revision must beat is the failed 2026-08-30 "
               "result: 28.7% caught (needed at least 60%). A floor, not a "
               "grade -- beating it proves improvement, not success.")


# ------------------------------------------------------- registry and cost


def _mb(n: float | int | None) -> str:
    return "unknown" if n is None else f"{n / 1_000_000:,.0f} MB"


@app.command()
def registry(from_year: int = typer.Option(2011, help="First year of SEC "
                                           "quarterly indexes to read. Before "
                                           "2011 there is no machine-readable "
                                           "data to assess anyway."),
             to_year: int = typer.Option(None, help="Last year to read; "
                                         "defaults to the current one."),
             gap: bool = typer.Option(False, "--gap",
                                      help="Just measure how many past filers "
                                           "the current watchlist misses. "
                                           "Reads local data only."),
             refresh: bool = typer.Option(False, help="Re-download quarters "
                                          "already ingested.")):
    """Every company that filed a routine report (10-K, 10-Q, 20-F) with the
    SEC since 2011 -- including the ones that later delisted, were bought, or
    went under, which a list of today's companies cannot show.

    Built from the SEC's own quarterly filing indexes: four downloads a year,
    no licence, and each quarter shows the companies that existed THEN. One
    full build is ~60 quarters at ~50 MB each; already-ingested quarters are
    skipped, so a rerun only fetches what is new.
    """
    typer.echo(phase0.banner())
    typer.echo("")
    if gap:
        measured = fullindex.survivorship_gap()
        if not measured["registry_filers"]:
            typer.echo("The filer registry is empty, so there is no gap to "
                       "measure yet. Build it first:")
            typer.echo("  ledgerline registry")
            raise typer.Exit(1)
        share = measured["missing_share"] or 0.0
        typer.echo(f"The SEC's quarterly indexes list "
                   f"{measured['registry_filers']:,} companies that have filed "
                   "routine reports since 2011.")
        typer.echo(f"Of those, {measured['watched_in_registry']:,} are on the "
                   f"current watchlist and {measured['missing_from_watchlist']:,} "
                   f"({share:.0%}) are not -- mostly companies that were "
                   "delisted, acquired, or went under.")
        years = measured["missing_by_last_filing_year"]
        if years:
            typer.echo("When the missing companies last filed:")
            for year, count in years.items():
                typer.echo(f"  {year}: {count:,}")
        typer.echo("")
        # The most tempting number in the project, so the caution prints with
        # it every time, not just in a report nobody rereads.
        typer.echo("A note on what this gap does NOT mean: the missing "
                   "companies are where deterioration actually ends, so the "
                   "failed test's 28.7% was plausibly measured against an "
                   "unflattering sample. That is recorded as a measurement "
                   "only. The 2026-08-30 test cannot be re-run on these "
                   "companies -- a fair re-test needs a new case set, a new "
                   "split, and a new pre-registered rule, committed before "
                   "any scoring.")
        return
    end_q = f"{to_year}Q4" if to_year else fullindex.current_quarter()

    def progress(quarter: str, rows: int | None) -> None:
        if rows is None:
            typer.echo(f"  {quarter}: could not be downloaded (skipped; a "
                       "rerun will retry it)")
        else:
            typer.echo(f"  {quarter}: {rows:,} routine filings recorded")

    try:
        summary = fullindex.ingest(start=f"{from_year}Q1", end=end_q,
                                   refresh=refresh, progress=progress)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    if summary["skipped"]:
        typer.echo(f"  ({summary['skipped']} quarter"
                   f"{'s' if summary['skipped'] != 1 else ''} already "
                   "ingested, skipped)")
    filers = len(fullindex.registry())
    typer.echo(f"Registry now covers {filers:,} distinct companies. See how "
               "many the watchlist misses:")
    typer.echo("  ledgerline registry --gap")


@app.command()
def cost(sizes: str = typer.Option("100,500,1500,3000",
                                   help="Watchlist sizes to project, "
                                        "comma-separated."),
         window: str = typer.Option("2023-01-01:2025-12-31",
                                    help="Historical span to replay, "
                                         "START:END dates."),
         live: bool = typer.Option(False, "--live",
                                   help="Refused. See the command's output "
                                        "for why.")):
    """What the daily check would cost at larger watchlist sizes -- replayed
    against the real filing calendar, using only data already on this machine.

    Two different answers, reported separately because they disagree: the
    market-wide change check stays ONE download a day at any size, while the
    per-company refresh work grows in step with the watchlist. Writes the full
    measurement to reports/cost.json and books it in the database.
    """
    if live:
        # Cut deliberately, not deferred: a live measurement at 3,000
        # companies is ~400 downloads and ~1.4 GB from the SEC on a peak day,
        # spent confirming arithmetic the replay already does offline. The
        # meter on real runs (ledgerline runs) is the live ground truth.
        typer.echo("A live cost measurement is refused: it would spend "
                   "hundreds of real SEC downloads to confirm arithmetic the "
                   "replay does offline. Every real run already meters its "
                   "own downloads and bytes -- see them with:")
        typer.echo("  ledgerline runs")
        raise typer.Exit(1)
    typer.echo(phase0.banner())
    typer.echo("")
    start, _, end = window.partition(":")
    try:
        payload = cost_mod.measure_scaling(
            [int(s) for s in sizes.split(",")], start, end)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    consts = payload["constants"]
    typer.echo(f"Replaying the real filing calendar from {start} to {end}, "
               f"with per-company sizes measured from {consts['n_sampled']} "
               "already-downloaded companies:")
    typer.echo("")
    for size, entry in payload["sizes"].items():
        t1 = entry["tier1_requests_per_day"]
        by = entry["bytes_per_day"]
        typer.echo(f"  Watching {int(size):,} companies "
                   f"(sample of {entry['n_filers']:,}):")
        typer.echo("    market-wide check: still one download a day, at any "
                   "size")
        typer.echo(f"    refreshes: {t1['median']:.0f} companies on a typical "
                   f"day ({_mb(by['median'])}), {t1['p90']:.0f} on a busy day "
                   f"({_mb(by['p90'])}), {t1['max']:.0f} at the worst "
                   f"({_mb(by['max'])})")
        typer.echo(f"    disk for their filing histories: about "
                   f"{_mb(entry['disk_bytes_projected'])}")
    typer.echo("")
    typer.echo("The daily check stays flat as the watchlist grows; the "
               "refresh work does not -- it grows in step with it. "
               + payload["bias"])
    cost_mod.persist_samples(payload)
    typer.echo(f"Full measurement written to {cost_mod.report(payload)}")


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
