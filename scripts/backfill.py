"""Resilient full-universe backfill.

`ledgerline backfill` is fine for a handful of names, but over 1500 filers a
single transient failure kills the run and loses the work. edgar.normalize()
only catches HTTPError -- a timeout surfaces as RuntimeError, a truncated body
as JSONDecodeError. Both are caught here, recorded, and the run continues.

Resumable: edgar.fetch caches companyfacts permanently (immutable per accepted
filing), and progress is checkpointed, so a rerun skips finished filers.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ledgerline import edgar  # noqa: E402

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill_state.json")


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
        tk = meta["ticker"]
        try:
            norm = edgar.normalize(cik)
            if not norm:
                done[cik] = {"ticker": tk, "status": "no_facts", "rows": 0}
            else:
                rows = edgar.persist_metrics(cik, norm)
                cov = edgar.coverage_report(norm)
                bad = sorted(m for m, c in cov.items() if c["n"] and not c["scoreable"])
                done[cik] = {"ticker": tk, "status": "ok", "rows": rows,
                             "metrics": len(norm), "low_coverage": bad}
        except Exception as exc:
            done[cik] = {"ticker": tk, "status": "error",
                         "error": f"{type(exc).__name__}: {exc}"[:200]}

        if i % 25 == 0 or i == len(todo):
            with open(STATE, "w") as fh:
                json.dump(done, fh)
            rate = i / max(time.time() - t0, 1e-9)
            eta = (len(todo) - i) / max(rate, 1e-9) / 60
            ok = sum(1 for v in done.values() if v["status"] == "ok")
            print(f"  {i}/{len(todo)}  ok={ok}  {rate:.1f}/s  eta {eta:.1f}m", flush=True)

    with open(STATE, "w") as fh:
        json.dump(done, fh)
    for st in ("ok", "no_facts", "error"):
        print(f"{st:9} {sum(1 for v in done.values() if v['status'] == st)}")


if __name__ == "__main__":
    main()
