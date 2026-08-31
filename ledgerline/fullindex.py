"""
Survivorship-free filer registry from the SEC quarterly full-index.

Why this module exists: scripts/sp1500.py reads CURRENT index membership, so
every filer delisted, acquired or bankrupted before today is absent -- a bias
that script documents and cannot fix. The SEC's quarterly full-index is
point-in-time by construction: a company that filed in 2015Q1 is in the 2015Q1
index whether or not it exists today. Measured from it: 8,021 CIKs filed a
periodic report in 2011Q1, 6,190 in 2024Q1, and only 2,629 appear in both --
67% attrition over thirteen years that the Wikipedia scrape cannot see. The
index costs 4 requests a year and needs no licence, which makes it a better-
defined universe than any index whose membership is someone's intellectual
property.

The gap bears on the KILL's denominator, and the direction is one-way: the
missing two thirds are where deterioration actually ENDS, so the generated
positive set is enriched in mild recoverable cases and the measured 28.7% hit
rate is plausibly biased downward. That is recorded by survivorship_gap() as a
measurement and nothing more. It does not license a re-run: prereg.json says
do not retune and re-run against this holdout, and a survivorship-free case
set is a DIFFERENT case set needing a new split and a new pre-registration
committed before it is scored. This module builds no cases and scores nothing.

One source serves two jobs: the registry above, and the exact historical
per-day filing-arrival series that cost.py replays -- so the cost measurement
is driven by what actually happened, not by a model of it.
"""
from __future__ import annotations

import os
import urllib.error
from datetime import date

from . import edgar

# Forms that mark a filer as alive and carrying fundamentals. Extends
# edgar.PERIODIC_FORMS with the amended foreign form: a 20-F/A filer is as
# alive as a 10-K/A filer, and the registry's whole job is not losing filers.
PERIODIC_FORMS: tuple[str, ...] = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A")

# Fixed-width column offsets of company.idx, verified against the 2015Q1
# header line. Parsed by COLUMN SLICE, never by rsplit(): edgar.daily_index()
# uses rsplit(None, 3) on form.idx and that is correct there, but company.idx
# puts the company name FIRST and carries form types containing spaces
# ('SC 13G/A'), so the same heuristic silently mangles the form column here.
COL_FORM, COL_CIK, COL_FILED, COL_FILE = 62, 74, 86, 98

# universe.XBRL_FLOOR is 2011-06-15; earlier quarters hold no contemporaneous
# XBRL, so a filer visible only before this could never be scored anyway.
FIRST_QUARTER: str = "2011Q1"


def _parse_quarter(quarter: str) -> tuple[int, int]:
    year, q = quarter.split("Q")
    return int(year), int(q)


def current_quarter(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year}Q{(today.month - 1) // 3 + 1}"


def quarters(start: str, end: str) -> list[str]:
    """Every quarter label from start to end inclusive, in calendar order."""
    y0, q0 = _parse_quarter(start)
    y1, q1 = _parse_quarter(end)
    out = []
    y, q = y0, q0
    while (y, q) <= (y1, q1):
        out.append(f"{y}Q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def quarter_is_closed(quarter: str, today: date | None = None) -> bool:
    """Whether a quarter's index can no longer change.

    Gates caching: a closed quarter's index is immutable and is cached
    permanently; the CURRENT quarter is still accumulating filings, so it is
    always refetched and never trusted as complete. Caching a partial quarter
    would freeze a truncated filer list and silently shrink the registry with
    no error anywhere.
    """
    y, q = _parse_quarter(quarter)
    ty, tq = _parse_quarter(current_quarter(today))
    return (y, q) < (ty, tq)


def parse_company_idx(raw: bytes) -> list[dict]:
    """The periodic-form rows of one quarter's company.idx, by column slice.

    Keeps ~8,300 of ~318,000 lines per quarter: the 49.7 MB payload is parsed
    and discarded, and only periodic filings land in sqlite. Header and
    separator lines fail the digit/date checks and fall out without special
    casing.
    """
    out = []
    for line in raw.decode("latin-1").splitlines():
        if len(line) <= COL_FILE:
            continue
        form = line[COL_FORM:COL_CIK].strip()
        if form not in PERIODIC_FORMS:
            continue
        cik = line[COL_CIK:COL_FILED].strip()
        filed = line[COL_FILED:COL_FILE].strip()
        if not cik.isdigit() or len(filed) != 10:
            continue
        fname = line[COL_FILE:].strip()
        out.append(
            {
                "cik": edgar.pad(cik),
                "name": line[:COL_FORM].strip(),
                "form": form,
                "filed": filed,
                "accession": os.path.basename(fname).removesuffix(".txt"),
            }
        )
    return out


def fetch_quarter(quarter: str, refresh: bool = False) -> list[dict]:
    """One quarter's periodic filings from the SEC full-index. One request."""
    if not quarter_is_closed(quarter):
        refresh = True  # a still-open quarter must never be served from cache
    year, q = _parse_quarter(quarter)
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/company.idx"
    raw = edgar.fetch(url, f"fullidx/{quarter}-company.idx", refresh=refresh)
    rows = parse_company_idx(raw)
    for r in rows:
        r["quarter"] = quarter
    return rows


def ingest(start: str = FIRST_QUARTER, end: str | None = None,
           refresh: bool = False, progress=None) -> dict:
    """Build or extend the registry. Idempotent and resumable.

    A closed quarter already in the table is skipped unless refresh -- its
    index cannot have changed. The open quarter is always refetched and its
    rows replaced wholesale, so a partial morning ingest never freezes a
    truncated filer list. A quarter that fails to download is recorded and
    skipped rather than aborting the run: a registry short one quarter is
    visibly short, a crashed ingest is a mystery.
    """
    end = end or current_quarter()
    conn = edgar.db()
    done = {r[0] for r in conn.execute("SELECT DISTINCT quarter FROM filer_registry")}
    summary: dict = {"quarters": {}, "rows": 0, "skipped": 0, "errors": 0}
    for quarter in quarters(start, end):
        if quarter in done and quarter_is_closed(quarter) and not refresh:
            summary["skipped"] += 1
            summary["quarters"][quarter] = {"rows": None, "skipped": True}
            continue
        try:
            rows = fetch_quarter(quarter, refresh=refresh)
        except (urllib.error.URLError, RuntimeError) as exc:
            if "LEDGERLINE_UA" in str(exc):
                conn.close()
                raise  # not a flaky quarter: no request can succeed until set
            summary["errors"] += 1
            summary["quarters"][quarter] = {"error": str(exc)}
            if progress:
                progress(quarter, None)
            continue
        with conn:
            conn.execute("DELETE FROM filer_registry WHERE quarter = ?", (quarter,))
            conn.executemany(
                "INSERT OR REPLACE INTO filer_registry "
                "(cik, quarter, form, filed, accession, name) VALUES (?,?,?,?,?,?)",
                [(r["cik"], r["quarter"], r["form"], r["filed"], r["accession"],
                  r["name"]) for r in rows],
            )
        summary["rows"] += len(rows)
        summary["quarters"][quarter] = {"rows": len(rows), "skipped": False}
        if progress:
            progress(quarter, len(rows))
    conn.close()
    return summary


def registry(min_quarters: int = 1) -> list[dict]:
    """Every CIK ever seen filing a periodic report, with its filing span.

    A filer whose last_periodic is 2016 stays in the list. That is the whole
    point: dropping it is exactly the bias scripts/sp1500.py documents and
    cannot fix, and the one Phase 7 deliverable whose value does not depend
    on the gate is that these filers remain visible.
    """
    conn = edgar.db()
    rows = conn.execute(
        "SELECT cik, MIN(filed), MAX(filed), COUNT(DISTINCT quarter), COUNT(*), "
        "MAX(name) FROM filer_registry GROUP BY cik "
        "HAVING COUNT(DISTINCT quarter) >= ? ORDER BY cik",
        (min_quarters,),
    ).fetchall()
    conn.close()
    return [
        {"cik": r[0], "first_periodic": r[1], "last_periodic": r[2],
         "n_quarters": r[3], "n_filings": r[4], "name": r[5]}
        for r in rows
    ]


def arrivals(ciks: set[str], start: str, end: str) -> dict[str, int]:
    """Distinct filers among `ciks` that filed a periodic form, per day.

    The cost model's input, and it reads only sqlite -- no network, so the
    cost measurement is deterministic and runs in CI. Distinct CIKs, not
    filings: a 10-K and its same-day amendment trigger one refetch, not two.
    """
    conn = edgar.db()
    rows = conn.execute(
        "SELECT filed, cik FROM filer_registry WHERE filed BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    conn.close()
    per_day: dict[str, set[str]] = {}
    for filed, cik in rows:
        if cik in ciks:
            per_day.setdefault(filed, set()).add(cik)
    return {day: len(members) for day, members in per_day.items()}


def survivorship_gap(current_ciks: set[str] | None = None) -> dict:
    """The registry measured against the current watchlist. A gap, not a plan.

    The note travels inside the payload because this number is the most
    tempting sentence in the project: the missing filers are where
    deterioration ends, so the holdout's positive set was drawn from
    survivors and its 28.7% is plausibly biased DOWNWARD. Recording that is
    not relitigating the KILL -- prereg.json says do not retune and re-run
    against this holdout, and a survivorship-free case set is a different
    case set needing a new split and a new pre-registration first.
    """
    reg = registry()
    if current_ciks is None:
        current_ciks = set(edgar.universe())
    reg_ciks = {r["cik"] for r in reg}
    missing = reg_ciks - current_ciks
    by_year: dict[str, int] = {}
    for r in reg:
        if r["cik"] in missing:
            year = (r["last_periodic"] or "")[:4]
            by_year[year] = by_year.get(year, 0) + 1
    return {
        "registry_filers": len(reg_ciks),
        "watched": len(current_ciks),
        "watched_in_registry": len(reg_ciks & current_ciks),
        "missing_from_watchlist": len(missing),
        "missing_share": (len(missing) / len(reg_ciks)) if reg_ciks else None,
        "missing_by_last_filing_year": dict(sorted(by_year.items())),
        "note": (
            "A measured gap, not a licence to re-run: the 2026-08-30 holdout "
            "was scored once against a committed rule. A case set drawn from "
            "this registry is a different case set and needs a new split and "
            "a new pre-registration committed before it is scored."
        ),
    }
