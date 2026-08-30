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


def newest_at(vintages: list[dict], cutoff: str) -> dict | None:
    """The most recent vintage of a fact published on or before `cutoff`.

    This is what "point in time" actually means for XBRL: a fact is not one
    value, it is a sequence of vintages. The original 10-Q number, then
    whatever a later filing restated it to. A reader on 2012-08-15 saw the
    2012-05-08 vintage, not the 2014-02-21 one.
    """
    hit = None
    for v in vintages:  # ascending by filed
        if (v.get("filed") or "") > cutoff:
            break
        hit = v
    return hit


def collect_vintages(rows: list[dict], key) -> dict:
    """key -> [vintage, ...] ascending by `filed`, one entry per filed date.

    Two facts for the same key on the same date means two candidate concepts;
    the higher-priority concept (lower rank) wins, matching _pick semantics.
    Consecutive vintages carrying an identical value are collapsed, so a fact
    restated three times but never changed stores one entry, not four.
    """
    by_key: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        filed = r.get("filed") or ""
        slot = by_key.setdefault(k, {})
        prev = slot.get(filed)
        if prev is None or r.get("rank", 0) < prev.get("rank", 0):
            slot[filed] = r

    out = {}
    for k, slot in by_key.items():
        seq, last = [], object()
        for filed in sorted(slot):
            row = slot[filed]
            if row["value"] != last:
                seq.append(row)
                last = row["value"]
        out[k] = seq
    return out


def derive_quarterly(rows: list[dict]) -> list[dict]:
    """Raw duration facts for one metric -> a contiguous quarterly series,
    every period end carrying its full vintage history.

    Each input row needs: start, end, value, filed. Optional: form, fy, fp,
    concept, accession, rank (concept priority, lower is better).

    FIX: the previous version collapsed each (start, bucket) to a single fact,
    keeping the most recently filed one -- "restatements supersede originals."
    Combined with truncation on `filed`, that produced the opposite of
    point-in-time. ABT's Q1 2012 was filed 2012-05-08 at $9.457B and restated
    to $5.284B for the AbbVie spin-off; the surviving row carried the 2014-02-21
    10-K's filed date, so as_of() hid a quarter that had been public for 21
    months, and any baseline that did include it used the restated figure.
    Across 150 filers that delayed first scoreability by a median of 56 months.

    Each returned row is the LATEST vintage (correct for outcome labeling,
    which is allowed to look forward) and carries `vintages` so edgar.as_of()
    can rewind it to what was public at a given cutoff.
    """
    valid = []
    for r in rows:
        start, end, value = r.get("start"), r.get("end"), r.get("value")
        if not start or not end or value is None:
            continue
        bucket = classify(start, end)
        if bucket is None:
            continue
        valid.append({**r, "bucket": bucket})

    buckets = collect_vintages(valid, key=lambda r: (r["start"], r["bucket"]))

    # per period end: {filed -> candidate row}
    per_end: dict[str, dict[str, dict]] = {}

    def offer(row: dict) -> None:
        slot = per_end.setdefault(row["end"], {})
        cur = slot.get(row["filed"])
        # at the same filed date a directly reported quarter beats a derived one
        if cur is None or (cur["origin"] == "derived" and row["origin"] == "reported"):
            slot[row["filed"]] = row

    for (start, bucket), vints in buckets.items():
        if bucket == "Q":
            for v in vints:
                offer(_row(v, v["value"], q_start=start, origin="reported",
                           bucket=bucket, sources=[v.get("accession")]))
            continue

        prior_vints = buckets.get((start, PRIOR_BUCKET[bucket]))
        if not prior_vints:
            # Without the preceding cumulative this would emit a multi-quarter
            # figure labelled as one quarter -- the exact bug being fixed. Drop.
            continue

        # A derived quarter is public once BOTH inputs are, and it CHANGES
        # whenever either input is restated -- so it has a vintage at every
        # date either side moved.
        for d in sorted({v.get("filed") or "" for v in vints}
                        | {p.get("filed") or "" for p in prior_vints}):
            cum, prior = newest_at(vints, d), newest_at(prior_vints, d)
            if cum is None or prior is None:
                continue
            offer(
                _row(
                    cum,
                    cum["value"] - prior["value"],
                    q_start=prior["end"],
                    origin="derived",
                    bucket=bucket,
                    sources=[cum.get("accession"), prior.get("accession")],
                    filed=d,
                )
            )

    out = []
    for end in sorted(per_end):
        seq = [per_end[end][f] for f in sorted(per_end[end])]
        seq = _collapse(seq)
        out.append({**seq[-1], "vintages": seq})
    return out


def _collapse(seq: list[dict]) -> list[dict]:
    """Drop vintages that restated nothing, so the stored history is the list
    of actual revisions rather than one entry per filing that mentioned it."""
    kept: list[dict] = []
    for row in seq:
        if kept and kept[-1]["value"] == row["value"] and kept[-1]["origin"] == row["origin"]:
            continue
        kept.append(row)
    return kept


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
