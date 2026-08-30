"""
Quarterly flow derivation.

Root-cause fix for FINDINGS.md §2.

The old `edgar.normalize()` kept a duration fact only when its span was 80-100
days. Most filers report cash-flow-statement items CUMULATIVE year-to-date in
10-Qs, so only fiscal Q1 survived and everything else was silently dropped. For
PTON that left 17 operating_cash_flow points against 37 revenue points -- two
per fiscal year. `ttm()` then summed four non-adjacent quarters spanning roughly
two years and called it trailing twelve months.

Fix: keep the YTD facts and difference them.

    Q1 = 3M
    Q2 = 6M - 3M
    Q3 = 9M - 6M
    Q4 = FY - 9M

Facts inside one fiscal year share an identical period `start`, so the join key
is exact -- no fuzzy fiscal-calendar matching.

Some filers publish standalone 3-month facts for Q2/Q3 alongside the
cumulatives. Those are kept as `origin="reported"` and take precedence over the
derived value for the same period end.

A derived quarter is only public once BOTH of its inputs are public, so its
`filed` date is the later of the two. That makes post-derivation truncation by
`filed` exactly equivalent to pre-derivation truncation, which is what lets
backtest and production share one code path.

All arithmetic. Nothing inferred. Every row carries the accessions it came from.
"""
from __future__ import annotations

from datetime import date

# Duration buckets in days, with tolerance for 52/53-week fiscal calendars.
SPANS = {
    "Q": (80, 100),
    "H1": (172, 192),
    "9M": (264, 284),
    "FY": (350, 380),
}

# Which cumulative bucket each one is differenced against.
PRIOR_BUCKET = {"H1": "Q", "9M": "H1", "FY": "9M"}

TTM_MIN_DAYS = 330
TTM_MAX_DAYS = 400
QUARTER_DAYS = 91
QUARTER_TOLERANCE = 20
COVERAGE_MIN = 0.90


def classify(start: str, end: str) -> str | None:
    """Bucket a duration fact by its span. None means unusable."""
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    for label, (lo, hi) in SPANS.items():
        if lo <= days <= hi:
            return label
    return None


def derive_quarterly(rows: list[dict]) -> list[dict]:
    """Raw duration facts for one metric -> a contiguous quarterly series.

    Each input row needs: start, end, value, filed. Optional: form, fy, fp,
    concept, accession, rank (concept priority, lower is better).
    """
    # 1. bucket, deduping on (start, bucket) by concept rank then filed date
    buckets: dict[tuple[str, str], dict] = {}
    for r in rows:
        start, end, value = r.get("start"), r.get("end"), r.get("value")
        if not start or not end or value is None:
            continue
        bucket = classify(start, end)
        if bucket is None:
            continue
        key = (start, bucket)
        prior = buckets.get(key)
        if prior is not None and _keep_prior(prior, r):
            continue
        buckets[key] = {**r, "bucket": bucket}

    # 2. emit reported quarters directly, difference the cumulatives
    out: list[dict] = []
    for (start, bucket), fact in buckets.items():
        if bucket == "Q":
            out.append(_row(fact, fact["value"], q_start=start, origin="reported",
                            bucket=bucket, sources=[fact.get("accession")]))
            continue

        prior = buckets.get((start, PRIOR_BUCKET[bucket]))
        if prior is None:
            # Without the preceding cumulative this would emit a multi-quarter
            # figure labelled as one quarter -- the exact bug being fixed. Drop.
            continue
        out.append(
            _row(
                fact,
                fact["value"] - prior["value"],
                q_start=prior["end"],
                origin="derived",
                bucket=bucket,
                sources=[fact.get("accession"), prior.get("accession")],
                filed=max(fact.get("filed") or "", prior.get("filed") or ""),
            )
        )

    # 3. one row per period end; a directly reported quarter beats a derived one
    best: dict[str, dict] = {}
    for r in out:
        cur = best.get(r["end"])
        if cur is None or (cur["origin"] == "derived" and r["origin"] == "reported"):
            best[r["end"]] = r
    return sorted(best.values(), key=lambda r: r["end"])


def _keep_prior(prior: dict, new: dict) -> bool:
    """True if the already-stored fact wins. Higher-priority concept first,
    then most recently filed -- restatements supersede originals."""
    pr, nr = prior.get("rank", 0), new.get("rank", 0)
    if pr != nr:
        return pr < nr
    return (prior.get("filed") or "") >= (new.get("filed") or "")


def _row(fact, value, *, q_start, origin, bucket, sources, filed=None):
    return {
        "metric": fact.get("metric"),
        "kind": "Q",
        "start": q_start,
        "end": fact["end"],
        "value": float(value),
        "fy": fact.get("fy"),
        "fp": fact.get("fp"),
        "form": fact.get("form"),
        "filed": filed if filed is not None else fact.get("filed"),
        "concept": fact.get("concept"),
        "origin": origin,
        "ytd_bucket": bucket,
        "sources": [s for s in sources if s],
    }


# ------------------------------------------------------------- contiguity


def is_contiguous(rows: list[dict], tolerance_days: int = QUARTER_TOLERANCE) -> bool:
    """True if consecutive quarters actually tile, with no gaps or overlaps."""
    for a, b in zip(rows, rows[1:], strict=False):
        gap = (date.fromisoformat(b["end"]) - date.fromisoformat(a["end"])).days
        if abs(gap - QUARTER_DAYS) > tolerance_days:
            return False
    return True


def ttm(rows: list[dict], back: int = 0) -> float | None:
    """Trailing twelve months, or None. Never a wrong number.

    Replaces the old signals.ttm(), which returned a plausible-looking float for
    a gappy series. This refuses instead, and the caller excludes the filer.
    """
    end = len(rows) - back
    if end < 4:
        return None
    window = rows[end - 4 : end]
    if not is_contiguous(window):
        return None
    first_start = window[0].get("start") or window[0]["end"]
    span = (date.fromisoformat(window[-1]["end"]) - date.fromisoformat(first_start)).days
    if not TTM_MIN_DAYS <= span <= TTM_MAX_DAYS:
        return None
    return sum(r["value"] for r in window)


def coverage(quarterly: list[dict], reference: list[dict]) -> float:
    """Fraction of the reference series (normally revenue) this metric covers.

    Phase 0 rule: below COVERAGE_MIN the filer is not scoreable on this metric
    and is excluded with a logged reason rather than scored on partial data.
    """
    if not reference:
        return 0.0
    ref_ends = {r["end"] for r in reference}
    have = {r["end"] for r in quarterly}
    return len(ref_ends & have) / len(ref_ends)
