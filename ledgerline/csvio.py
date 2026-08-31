"""
Bulk CSV in and out: a watchlist arrives as a spreadsheet, and everything the
tool knows leaves as one.

Why import is deliberately incurious: it writes watchlist rows and group
memberships and NOTHING else. It never fetches a filing history -- that is
`ledgerline fetch`, one polite SEC request per company, and hiding thousands
of them inside a command a person thinks reads a file is how a "quick import"
becomes an hour of unexplained network traffic. It never overwrites a company
already being watched either: the row on disk carries a name and an industry
code that were fetched from the SEC, and a spreadsheet is not a better source
for those than the SEC is. Nor does it rebind a symbol: a line that hands a
watched ticker to a different CIK is reported as a conflict and skipped, or
every command a person addresses by that symbol would answer about the other
company.

Why every row gets its own outcome line: a bulk operation that prints one
number ("imported 412") cannot be checked. A person who exported 500 tickers
from somewhere else needs to know which ones the SEC does not recognise --
those are usually foreign listings, funds, or tickers that changed -- and one
malformed row must not abort the other 499.

Why every export leads with a comment line carrying the failed-test statement:
a CSV is the format that leaves this machine. Inside the repo every surface
prints the Phase 0 KILL because status.stamp() is enforced at the scoring
path; a spreadsheet mailed to somebody else carries whatever the first line
says and nothing more. The line is COMPUTED from the frozen record through
api.contract, never typed here, so it cannot drift from the committed numbers
-- and on a machine missing ledgerline/data/phase0.json it raises before a
single row is written.

The `sector` and `status` columns are read and mostly not stored, on purpose.
This tool takes a company's industry from the SEC's own record; a sector NAME
from a spreadsheet ("Technology") written into the industry-code column would
corrupt the field that admission and peer grouping read. A numeric SEC
industry code is accepted for a company being added for the first time, and
anything else is reported as read-and-ignored rather than silently dropped.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from . import edgar, emit, groups, ingest, render
from .api import contract

# Recognised header names. Everything else in the file is ignored, and only
# `ticker` is required -- order is irrelevant, since columns are located by
# name and not by position.
COLUMNS = ("ticker", "name", "sector", "cik", "group", "status")

EXPORTS = ("watchlist", "signals", "runs")


@dataclass(frozen=True)
class Row:
    """One line of an imported file and what became of it."""

    line: int
    ticker: str | None
    cik: str | None
    group: str | None
    outcome: str      # added | already | unresolved | repeated | conflict
                      # | malformed
    detail: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def _header_index(header: list[str]) -> dict[str, int]:
    idx = {}
    for i, cell in enumerate(header):
        key = (cell or "").strip().lower()
        if key in COLUMNS and key not in idx:
            idx[key] = i
    return idx


def _cell(row: list[str], idx: dict[str, int], key: str) -> str:
    i = idx.get(key)
    if i is None or i >= len(row):
        return ""
    return (row[i] or "").strip()


def import_watchlist(path: str, tmap: dict[str, dict] | None = None,
                     conn: sqlite3.Connection | None = None) -> dict:
    """Read a CSV of companies onto the watchlist. Fetches nothing.

    Returns {"rows": [Row, ...], "counts": {...}, "groups": {name: n},
    "ignored": {...}}. A row is `added` (new to the watchlist), `already`
    (watched before this file was read), `unresolved` (the SEC's ticker map
    has no CIK for it), `repeated` (a ticker the same file already listed),
    `conflict` (its symbol already belongs to a different company) or
    `malformed` (unreadable, reported with its line number and skipped).

    `tmap` is the SEC ticker map; it is loaded only if some row needs it, so a
    file that carries its own CIKs costs no network at all.
    """
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise RuntimeError(
                f"{path} is empty. The first line must name the columns, "
                "starting with a ticker column:\n"
                "  ticker,name,cik,group"
            ) from None
        idx = _header_index(header)
        if "ticker" not in idx:
            raise RuntimeError(
                f"{path} has no column called 'ticker', and a company cannot "
                "be added without one. The first line must name the columns; "
                "ticker, name, sector, cik, group and status are understood "
                "in any order, and anything else is ignored:\n"
                "  ticker,name,cik,group"
            )
        raw = [(reader.line_num, row) for row in reader]

    own = conn is None
    conn = conn or edgar.db()
    try:
        watched = {r[0] for r in conn.execute("SELECT cik FROM universe")}
        # symbol -> the company that already answers to it. `universe` has a
        # primary key on cik and nothing at all on ticker, so a file carrying
        # its own cik column could append a second row under a watched symbol;
        # cli._resolve then took whichever row the SELECT happened to return
        # last, and every ticker-addressed command read the other company.
        held = {(r[1] or "").upper(): r[0]
                for r in conn.execute("SELECT cik, ticker FROM universe")}
        rows: list[Row] = []
        seen: set[str] = set()
        pending: list[tuple[str, str, str | None, str | None]] = []
        by_group: dict[str, list[str]] = {}
        ignored = {"sector": 0, "status": 0}

        for line, raw_row in raw:
            if not any((c or "").strip() for c in raw_row):
                continue  # a blank line is not a malformed row
            ticker = _cell(raw_row, idx, "ticker").upper()
            group = _cell(raw_row, idx, "group") or None
            if not ticker:
                rows.append(Row(line, None, None, group, "malformed",
                                "this line has no ticker in it"))
                continue

            cik_cell = _cell(raw_row, idx, "cik")
            cik: str | None = None
            if cik_cell:
                digits = cik_cell.lstrip("Cc IiKk").strip()
                if not digits.isdigit():
                    rows.append(Row(line, ticker, None, group, "malformed",
                                    f"the cik column holds \"{cik_cell}\", "
                                    "which is not a number"))
                    continue
                cik = edgar.pad(digits)
            else:
                if tmap is None:
                    tmap = edgar.load_ticker_map()
                hit = tmap.get(ticker)
                if hit is None:
                    rows.append(Row(line, ticker, None, group, "unresolved",
                                    "the SEC's ticker list has no company "
                                    "under this symbol"))
                    continue
                cik = hit["cik"]

            if cik in seen:
                rows.append(Row(line, ticker, cik, group, "repeated",
                                "this company is listed earlier in the same "
                                "file"))
                continue

            # A symbol is an address a person types, so it may name exactly
            # one company. Rebinding it here would point explain, score,
            # provenance and narrate at a different filer under the label the
            # person still reads as theirs -- silently, in every surface.
            holder = held.get(ticker)
            if holder is not None and holder != cik:
                rows.append(Row(
                    line, ticker, cik, group, "conflict",
                    f"the symbol {ticker} already belongs to CIK {holder} "
                    f"here, and this line gives it to CIK {cik}. Nothing was "
                    "changed. Remove one of the two, or drop this line's cik "
                    "column and let the SEC's ticker list decide"))
                continue
            held[ticker] = cik
            seen.add(cik)

            name = _cell(raw_row, idx, "name") or None
            if name is None and tmap is not None and ticker in tmap:
                name = tmap[ticker]["name"]
            sector = _cell(raw_row, idx, "sector")
            sic = sector if sector.isdigit() and 3 <= len(sector) <= 4 else None
            if sector and sic is None:
                ignored["sector"] += 1
            if _cell(raw_row, idx, "status"):
                ignored["status"] += 1

            if cik in watched:
                rows.append(Row(line, ticker, cik, group, "already", None))
            else:
                pending.append((cik, ticker, name, sic))
                rows.append(Row(line, ticker, cik, group, "added", None))
            if group:
                by_group.setdefault(groups.clean(group), []).append(cik)

        with conn:
            # DO NOTHING, never DO UPDATE: a company already watched keeps the
            # name and industry code fetched from the SEC. A spreadsheet is
            # not a better source for those than the SEC is, and set_universe
            # already carries the scar from an update that nulled 1,496 SIC
            # codes in one command.
            conn.executemany(
                "INSERT INTO universe (cik, ticker, name, sic) VALUES (?,?,?,?) "
                "ON CONFLICT(cik) DO NOTHING", pending)
        assigned = {name: groups.assign(name, ciks, conn=conn)
                    for name, ciks in sorted(by_group.items())}
    finally:
        if own:
            conn.close()

    counts = {k: sum(1 for r in rows if r.outcome == k)
              for k in ("added", "already", "unresolved", "repeated",
                        "conflict", "malformed")}
    return {"rows": rows, "counts": counts, "groups": assigned,
            "ignored": ignored}


# ------------------------------------------------------------------- exports


def validation_comment() -> str:
    """The failed-test statement as one leading comment line.

    Built through api.contract, which builds it through status.stamp: on a
    machine without the frozen record this raises. The sentence is computed
    from the committed numbers, so it cannot drift from them.
    """
    return "# " + contract.validation_block()["statement"]


@contextmanager
def sheet(path: str) -> Iterator[Any]:
    """A CSV open for writing with the failed-test line already on it.

    The comment is built BEFORE the file is created, the same order
    export_jsonl uses: on a machine holding no evidence of the 2026-08-30 test
    this raises and no file exists at all. A zero-byte spreadsheet, or one
    holding rows and no verdict line, is worse than no spreadsheet.

    It goes through the csv writer and not fh.write(): the statement contains
    four commas, so written raw it parsed as four cells and a spreadsheet drew
    it as four clipped columns with the measured numbers hidden behind them.
    Quoted, it is one cell in A1 that overflows across the empty cells beside
    it -- the whole sentence, legible, on the artifact most likely to be
    forwarded.
    """
    comment = validation_comment()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([comment])
        yield w


def _plain_flags(flags: list[dict] | None) -> str:
    return ", ".join(render.PLAIN.get((f.get("code") or "").lower(),
                                      (f.get("code") or "",))[0]
                     for f in flags or [])


def watchlist_rows(conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every watched company with what the tool last recorded about it.

    Reads the scoreability table rather than re-assessing: `ledgerline check`
    is what decides assessability, one evaluation per company against a warm
    fact cache, and an export that silently re-ran it would turn writing a
    file into the slowest command in the tool. A company never checked says
    so, and says which command to run.
    """
    own = conn is None
    conn = conn or edgar.db()
    try:
        latest = {
            r[0]: r[1:] for r in conn.execute(
                "SELECT s.cik, s.as_of, s.scoreable, s.detail, s.n_evaluated, "
                "s.n_tracked FROM scoreability s JOIN "
                "(SELECT cik, MAX(as_of) AS m FROM scoreability GROUP BY cik) t "
                "ON t.cik = s.cik AND t.m = s.as_of").fetchall()
        }
        member = groups.memberships(conn=conn)
        rows = conn.execute(
            "SELECT cik, ticker, name, sic FROM universe "
            "ORDER BY ticker IS NULL, ticker").fetchall()
    finally:
        if own:
            conn.close()

    out = []
    for cik, ticker, name, sic in rows:
        hit = latest.get(cik)
        if hit is None:
            assessable, reason, avail, total, checked = None, None, None, None, None
        else:
            checked, ok, detail, n_eval, n_tracked = hit
            assessable = bool(ok)
            reason = None if ok else render.plain_reason(detail)
            avail, total = n_eval, n_tracked
        out.append({
            "cik": cik,
            "ticker": ticker,
            "name": name,
            "sic": sic,
            "groups": member.get(cik, []),
            "assessable": assessable,
            "assessable_reason": reason,
            "checked_on": checked,
            "measures_available": avail,
            "measures_total": total,
        })
    return out


def export_watchlist(path: str, conn: sqlite3.Connection | None = None) -> int:
    rows = watchlist_rows(conn=conn)
    with sheet(path) as w:
        w.writerow(["ticker", "name", "cik", "sic", "groups", "assessable",
                    "reason_if_not", "measures_available", "checked_on"])
        for r in rows:
            if r["assessable"] is None:
                assessable = "not checked yet"
                reason = "run ledgerline check to find out"
            else:
                assessable = "yes" if r["assessable"] else "no"
                reason = r["assessable_reason"] or ""
            measures = ("" if r["measures_available"] is None
                        else f"{r['measures_available']} of {r['measures_total']}")
            w.writerow([r["ticker"] or "", r["name"] or "", r["cik"],
                        r["sic"] or "", "; ".join(r["groups"]), assessable,
                        reason, measures, r["checked_on"] or ""])
    return len(rows)


def export_signals(path: str, limit: int = 1_000_000,
                   conn: sqlite3.Connection | None = None) -> int:
    """One row per saved assessment, unassessable filers included.

    Unassessable rows are the denominator: an export of fires alone can show
    precision and can never show recall, which is the criterion the detector
    failed on. `score` is empty, never 0, where nothing was assessed.
    """
    rows = emit.load_signals(limit=limit, conn=conn)
    with sheet(path) as w:
        w.writerow(["ticker", "as_of", "period", "scoreable", "score",
                    "flagged", "flags", "reason", "gate_version"])
        for r in rows:
            w.writerow([
                r["ticker"] or r["cik"],
                r["as_of"], r["period"] or "",
                "yes" if r["scoreable"] else "no",
                "" if r["score"] is None else f"{r['score']:.1f}",
                "yes" if r["gated_in"] else "no",
                _plain_flags(r["flags"]),
                render.plain_reason(r["reason"]) if r["reason"] else "",
                r["gate_version"],
            ])
    return len(rows)


def export_runs(path: str, limit: int = 1_000_000) -> int:
    """The job log: what ran, when, what it cost, what it found."""
    rows = ingest.run_log(limit=limit)
    cols = ("run_id", "job", "as_of", "status", "started_at", "finished_at",
            "requests", "cache_hits", "bytes_fetched", "index_rows",
            "universe_hits", "filers_done", "filers_failed", "restatements",
            "scored", "gated_in", "error")
    with sheet(path) as w:
        w.writerow(list(cols))
        for r in rows:
            w.writerow([r.get(c) if r.get(c) is not None else "" for c in cols])
    return len(rows)


def export(what: str, path: str, conn: sqlite3.Connection | None = None) -> int:
    if what == "watchlist":
        return export_watchlist(path, conn=conn)
    if what == "signals":
        return export_signals(path, conn=conn)
    if what == "runs":
        return export_runs(path)
    raise RuntimeError(
        f"There is nothing called \"{what}\" to export. Choose one of: "
        + ", ".join(EXPORTS) + ". For example:\n"
        f"  ledgerline export watchlist --out watchlist-{date.today().isoformat()}.csv"
    )
