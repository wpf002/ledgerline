"""
Wilson intervals and the raw score-bin table: the two reliability tools that
need no probability link.

Why the rest of the planned toolkit is absent: the Phase 6 design also carried
a Brier score, its Murphy decomposition, a score-to-probability link and a
reliability diagram. All were cut, deliberately. The link's intercept was
fitted on the tuning split, and the only outcomes available to plot against it
today are a replay of that same tuning split -- a calibration curve drawn on
its own training data is the canonical way to publish a number that looks
validated and means nothing. Those pieces get rebuilt when live resolved
outcomes exist to draw them from; until then this module states only what is
true without a fitted transform: how uncertain a counted proportion is, and
how outcomes distribute across raw score bins.

wilson() rather than the textbook normal approximation because live counts
start small, and at k = 0 the normal interval collapses to a point -- reporting
perfect certainty exactly where there is least. The Wilson interval stays open
at zero, which is where every live proportion in this project begins.
"""
from __future__ import annotations

import math

# Phi^-1(0.975), pinned rather than computed: the codebase has no
# inverse-normal function (same reasoning as retest.two_proportion_n).
Z_95 = 1.959963984540054

# Bin edges over the raw 0-100 score. 45.0 is an edge on purpose: it is the
# operating point (signals_v3.THRESHOLD), so fires and non-fires never share
# a bin and the table shows behaviour on both sides of the trigger.
SCORE_EDGES: tuple[float, ...] = (0.0, 10.0, 20.0, 30.0, 45.0, 60.0, 80.0, 100.0)


def wilson(k: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials. None when n = 0 --
    an interval on an empty count would be a guess wearing brackets."""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"wilson() needs 0 <= k <= n, got k={k}, n={n}")
    z2 = z * z
    center = (k + z2 / 2) / (n + z2)
    half = z * math.sqrt(k * (n - k) / n + z2 / 4) / (n + z2)
    return (max(0.0, center - half), min(1.0, center + half))


def score_bins(rows: list[dict],
               edges: tuple[float, ...] = SCORE_EDGES) -> list[dict]:
    """Resolved evaluations bucketed by raw score, with the observed
    deterioration rate and its Wilson interval per bucket.

    Bins are half-open [lo, hi) with the FINAL bin closed, so a score of
    exactly 0.0 and exactly 100.0 each land in exactly one bin -- a table that
    silently drops its endpoints is the classic version of this bug, pinned by
    a test. Rows: {"score": float, "deteriorated": bool | None}; a row with
    score None (unscoreable) is excluded, and a row whose outcome is unknown
    counts toward n but not toward the rate's numerator or denominator.
    """
    bins = []
    last = len(edges) - 2
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == last:
            hit = [r for r in rows
                   if r.get("score") is not None and lo <= r["score"] <= hi]
        else:
            hit = [r for r in rows
                   if r.get("score") is not None and lo <= r["score"] < hi]
        outcomes = [r for r in hit if r.get("deteriorated") is not None]
        k = sum(1 for r in outcomes if r["deteriorated"])
        n_out = len(outcomes)
        interval = wilson(k, n_out)
        bins.append({
            "lo": lo,
            "hi": hi,
            "n": len(hit),
            "n_with_outcome": n_out,
            "n_deteriorated": k,
            "deterioration_rate": round(k / n_out, 4) if n_out else None,
            "wilson_low": round(interval[0], 4) if interval else None,
            "wilson_high": round(interval[1], 4) if interval else None,
        })
    return bins
