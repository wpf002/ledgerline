"""Populate the universe.sic column from SEC submissions.

Run once after the companyfacts backfill. universe.admit() rejects an unknown
SIC outright ("unknown sector is not admissible to a control group"), so
without this every filer is rejected and the case set comes back empty.

Run this SEPARATELY from scripts/backfill.py, not alongside it: edgar's
throttle is a module-level counter, so two processes each pace themselves to
~9/sec and together exceed SEC's ceiling.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ledgerline import edgar  # noqa: E402

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sic_state.json")


def main():
    uni = edgar.universe()
    done: dict = {}
    if os.path.exists(STATE):
        with open(STATE) as fh:
            done = json.load(fh)
    todo = [(c, m) for c, m in sorted(uni.items()) if c not in done]
    print(f"{len(uni)} filers, {len(done)} done, {len(todo)} to go", flush=True)

    t0 = time.time()
    for i, (cik, meta) in enumerate(todo, 1):
        try:
            sub = edgar.submissions(cik)
            done[cik] = {"ticker": meta["ticker"], "sic": sub.get("sic") or None,
                         "sicDescription": sub.get("sicDescription")}
        except Exception as exc:
            done[cik] = {"ticker": meta["ticker"], "sic": None,
                         "error": f"{type(exc).__name__}: {exc}"[:200]}
        if i % 50 == 0 or i == len(todo):
            with open(STATE, "w") as fh:
                json.dump(done, fh)
            rate = i / max(time.time() - t0, 1e-9)
            eta = (len(todo) - i) / max(rate, 1e-9) / 60
            print(f"  {i}/{len(todo)}  {rate:.1f}/s  eta {eta:.1f}m", flush=True)

    with open(STATE, "w") as fh:
        json.dump(done, fh)
    n = edgar.set_sic([(cik, v.get("sic")) for cik, v in done.items()])
    missing = sum(1 for v in done.values() if not v.get("sic"))
    print(f"wrote {n} sic values, {missing} unresolved")


if __name__ == "__main__":
    main()
