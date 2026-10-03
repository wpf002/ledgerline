"""Measure the survivorship gap's direction (ROADMAP_V1 1.0-c). JSON to stdout.

The case set was generated from today's S&P 1500, so every company in it
survived to 2026. The claim on record (FINDINGS §6e) is that the companies
that left skew toward deterioration, so the case set is short of the severe
cases and 0.287 is probably biased low. This measures the first half of that
claim: do sizable companies that stopped filing deteriorate more often, and
more severely, than sizable companies that are still here?

Selection, applied to both groups the same way:
  - filed a 10-K/10-Q in 2014Q1 (SEC full-index)
  - total assets >= $1B at 2013-12-31 (one XBRL frames request covers every
    filer), roughly the S&P 1500 size floor
  - not a bank, insurer, REIT or unknown sector (universe.sic_excluded)
Leavers didn't file in 2024Q1. Survivors did, and are on the current
watchlist, because that's the population the case set came from.

Outcome: a fundamental deterioration event (label.py's 2-of-5 rule) at any
quarter ending 2014-01-01..2019-12-31. Severity: how many of the five
criteria tripped.

Nothing here scores the gate. A case set drawn from leavers would be a new
case set needing a new split and pre-registration before anything is scored
on it.
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ledgerline import edgar, label, signals, universe  # noqa: E402
from ledgerline.reliability import wilson  # noqa: E402

MIN_ASSETS = 1e9
WINDOW = ("2014-01-01", "2019-12-31")
SAMPLE = 300


def assets_2013q4() -> dict[str, float]:
    url = "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2013Q4I.json"
    data = edgar.fetch_json(url, "frames/Assets-CY2013Q4I.json")
    return {edgar.pad(d["cik"]): float(d["val"]) for d in data["data"]}


def filers(quarter: str) -> set[str]:
    conn = edgar.db()
    rows = conn.execute("SELECT DISTINCT cik FROM filer_registry WHERE quarter = ?",
                        (quarter,)).fetchall()
    conn.close()
    return {r[0] for r in rows}


def deterioration(cik: str) -> dict | None:
    norm = edgar.normalize(cik)
    if not norm or not norm.get("revenue"):
        return None
    worst = 0
    first = None
    for r in signals.series(norm, "revenue", "Q"):
        if not WINDOW[0] <= r["end"] <= WINDOW[1]:
            continue
        hits = [c for c in (fn(norm, r["end"]) for fn in label.CRITERIA) if c is not None]
        if len(hits) >= label.MIN_CRITERIA:
            worst = max(worst, len(hits))
            first = first or r["end"]
    return {"deteriorated": first is not None, "worst_criteria": worst, "first": first}


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r is not None]
    k = sum(r["deteriorated"] for r in ok)
    severe = sum(r["worst_criteria"] >= 3 for r in ok)
    return {"n": len(ok), "deteriorated": k,
            "rate": k / len(ok) if ok else None, "wilson": wilson(k, len(ok)),
            "three_plus_criteria": severe,
            "three_plus_rate": severe / len(ok) if ok else None,
            "three_plus_wilson": wilson(severe, len(ok))}


def main() -> None:
    rng = random.Random(20261003)
    size = assets_2013q4()
    q14, q24 = filers("2014Q1"), filers("2024Q1")
    watched = edgar.sic_map()

    big = {c for c in q14 if size.get(c, 0) >= MIN_ASSETS}
    leavers = sorted(big - q24)
    survivors = sorted(c for c in big & q24 if c in watched)
    rng.shuffle(leavers)
    rng.shuffle(survivors)

    def admissible(cik: str) -> bool:
        sic = watched.get(cik) or universe.fetch_sic(cik)
        return not universe.sic_excluded(sic)

    leaver_ok, checked = [], 0
    for cik in leavers:
        checked += 1
        if admissible(cik):
            leaver_ok.append(cik)
        if len(leaver_ok) >= SAMPLE:
            break
    survivor_ok = [c for c in survivors if admissible(c)][:SAMPLE]

    def run(ciks: list[str]) -> list[dict]:
        out = []
        for cik in ciks:
            try:
                out.append(deterioration(cik))
            except Exception:  # noqa: BLE001 -- a filer that can't be read is left out
                out.append(None)
        return out

    print(json.dumps({
        "filers_2014q1": len(q14), "filers_2024q1": len(q24),
        "sizable_2014q1": len(big), "sizable_leavers": len(leavers),
        "sizable_survivors_on_watchlist": len(survivors),
        "leavers_sic_checked": checked,
        "leavers": summarize(run(leaver_ok)),
        "survivors": summarize(run(survivor_ok)),
    }, indent=1))


if __name__ == "__main__":
    main()
