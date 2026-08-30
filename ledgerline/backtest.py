"""
Historical validation driver.

The only question that matters: does the deterministic gate fire BEFORE a
narrative-vs-reality break became consensus, and does it stay quiet otherwise?

FIX (see FINDINGS.md §3): there is no separate backtest scoring path any more.
This module calls `signals_v3.evaluate(ticker, cik, as_of=cutoff)` -- the same
function production calls -- and truncation happens inside it via
`edgar.as_of()`, on the XBRL `filed` date. Previously the backtest truncated on
`filed` while `signals_v2._history()` truncated on period `end`, so the two
computed different functions and no backtest result would have transferred.

Case labels and thresholds are NOT set here. Cases come from
`validate.cases`, the split from `data/split.json`, and the pass/fail rule from
`data/prereg.json` -- all committed before a run.
"""
from __future__ import annotations

import json
import os

from . import edgar, signals_v3
from .validate import harness

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def quarterly_cutoffs(start_year: int, end_year: int) -> list[str]:
    """Filing-season checkpoints. Mid-month so the prior quarter's 10-Q has
    landed for most calendar-year filers."""
    return [
        f"{y}-{m:02d}-15"
        for y in range(start_year, end_year + 1)
        for m in (2, 5, 8, 11)
    ]


def timeline(ticker: str, cik: str, cutoffs: list[str], norm: dict | None = None) -> list[dict]:
    """Score one filer at every cutoff. `norm` is passed in so companyfacts is
    fetched once per filer rather than once per cutoff."""
    full = norm if norm is not None else edgar.normalize(cik)
    rows = []
    for c in cutoffs:
        res = signals_v3.evaluate(ticker, cik, as_of=c, norm=full)
        rows.append(
            {
                "cutoff": c,
                "period": res.get("period"),
                "score": res.get("score") if res.get("scoreable") else None,
                "scoreable": res.get("scoreable"),
                "reason": res.get("reason"),
                "flags": [f["code"] for f in res.get("flags", [])],
                "derived_fraction": res.get("derived_fraction"),
            }
        )
    return rows


def scorer_factory(cutoffs: list[str]):
    """Adapter matching harness.evaluate_case's `scorer(ticker, cik, as_of)`.
    Caches the normalized fact set per CIK."""
    cache: dict[str, dict] = {}

    def scorer(ticker: str, cik: str, as_of: str):
        if cik not in cache:
            cache[cik] = edgar.normalize(cik)
        if not cache[cik]:
            return None
        return signals_v3.evaluate(ticker, cik, as_of=as_of, norm=cache[cik])

    return scorer


def run(split: str = "tuning", start_year: int = 2005, end_year: int = 2025) -> dict:
    """Run the gate across one split and apply the pre-registered rule.

    `split` must be 'tuning' or 'holdout'. The holdout is scored once; running
    it a second time after retuning voids the test, and harness.verify_split()
    will refuse if the split file was edited.
    """
    harness.verify_split()
    cases = harness.load_split(split)
    cutoffs = quarterly_cutoffs(start_year, end_year)
    scorer = scorer_factory(cutoffs)

    outcomes = [
        harness.evaluate_case(c, cutoffs, scorer, signals_v3.THRESHOLD) for c in cases
    ]
    report = {
        "split": split,
        "threshold": signals_v3.THRESHOLD,
        "z_trigger": signals_v3.Z_TRIGGER,
        "cutoffs": [cutoffs[0], cutoffs[-1]],
        "outcomes": [o.__dict__ for o in outcomes],
    }
    if split == "holdout":
        report["verdict"] = harness.verdict(outcomes)

    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, f"backtest_{split}.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    return report
