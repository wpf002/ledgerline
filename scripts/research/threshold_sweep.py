"""How many deteriorations the gate catches at each flag line, and what that
costs in false alarms. Practice half only; prints a table.

Answers "why not just lower the line to catch 60%?". Read from the saved
replay assessments, so it scores nothing new: for each practice-half company
that deteriorated, did any assessment in the 24 months before the trouble
became public reach the line; and for companies that never deteriorated, what
share of their quarters did.

The windowing here is simpler than the pre-registered test's (no censoring of
first-date fires), so the catch rate at 45 reads 29.9% against the official
practice-half 21.2%. The false-alarm rate matches (4.0%). Use it for the
shape of the trade-off, not as a second result.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ledgerline import edgar, status  # noqa: E402

WINDOW_MONTHS = 24


def main() -> None:
    with open(os.path.join(status.DATA, "split.json")) as fh:
        split = json.load(fh)
    conn = edgar.db()
    saved: dict[str, list[tuple[str, float, int]]] = {}
    for ticker, as_of, score, n_flags in conn.execute(
            "SELECT ticker, as_of, score, n_flags FROM signals "
            "WHERE source = 'replay' AND scoreable = 1"):
        saved.setdefault(ticker, []).append((as_of, score or 0.0, n_flags))
    conn.close()

    chances: list[list[tuple[float, int]]] = []
    quiet: list[tuple[float, int]] = []
    for ticker in split["tuning"]:
        case = split["cases"][ticker]
        rows = saved.get(ticker, [])
        if case["is_positive"] and case["broke_filed"]:
            public = date.fromisoformat(case["broke_filed"])
            window = [(s, n) for a, s, n in rows
                      if 0 < (public - date.fromisoformat(a)).days <= WINDOW_MONTHS * 30.44]
            if window:
                chances.append(window)
        elif not case["is_positive"]:
            quiet += [(s, n) for _, s, n in rows]

    print(f"{len(chances)} deteriorations, {len(quiet)} quiet company-quarters")
    print("line  min measures  caught  false alarms per quarter")
    for need in (2, 1):
        for line in (45, 35, 25, 15, 5):
            caught = sum(any(s >= line and n >= need for s, n in w) for w in chances)
            alarms = sum(s >= line and n >= need for s, n in quiet)
            print(f"{line:4}  {need:12}  {caught / len(chances):6.1%}  {alarms / len(quiet):6.2%}")


if __name__ == "__main__":
    main()
