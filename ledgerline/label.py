"""
Outcome labeling.

DECISION (open item #2): the label is a FUNDAMENTAL deterioration event
observable in later filings, not a price drawdown.

The rejected placeholder was ">=30% drawdown versus sector within four
quarters." Three problems with it:

  1. It tests the wrong claim. The product's claim is that a filer's accounting
     is breaking from its own pattern ahead of visible deterioration. Labeling
     on price turns this into a return-prediction model, which has to clear a
     much higher bar (factor exposure, transaction costs, capacity) and which
     the diagnostics were never designed for.
  2. Its base rate is unstable across time. A large fraction of the market fell
     30% versus sector during 2022. The same threshold means something very
     different in 2017.
  3. Hand-picking eight names remembered as blowups is hindsight selection. It
     cannot produce a control group, and it cannot produce a positive set large
     enough to split.

Labeling on subsequent filings instead is computable from the same pipeline,
needs no price or sector-return series, is regime-stable, and lets the positive
and control sets be GENERATED across the whole universe rather than curated.
That removes survivorship and hindsight bias in one move.

Labels are allowed to look forward. Only the SCORER is point-in-time -- an
outcome that has not happened yet is not an outcome.

Price is still computed and reported alongside, as a secondary descriptive
statistic. It is not part of the pass/fail rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import mean

from . import derive, signals

# A deterioration event requires at least this many independent criteria to
# trip inside the horizon. One criterion alone is too easy to hit by noise;
# requiring two means the filer is breaking in more than one place at once.
MIN_CRITERIA = 2
HORIZON_QUARTERS = 4

REVENUE_DECEL_PP = 0.15      # YoY growth drops 15pp below its own trailing norm
GROSS_MARGIN_DROP_PP = 0.05  # gross margin falls 5pp YoY
OCF_DECLINE_FRAC = 0.50      # TTM operating cash flow halves YoY
IMPAIRMENT_OF_ASSETS = 0.05  # impairment charge >= 5% of total assets


@dataclass
class Criterion:
    code: str
    period: str
    value: float
    threshold: float
    detail: str


@dataclass
class Label:
    ticker: str
    cik: str
    as_of: str
    deteriorated: bool = False
    event_period: str | None = None
    criteria: list[dict] = field(default_factory=list)
    horizon_end: str | None = None
    n_quarters_observed: int = 0

    def as_dict(self):
        return asdict(self)


def _forward_window(norm: dict, as_of: str, n: int = HORIZON_QUARTERS) -> list[str]:
    """The next `n` period ends whose filings landed after `as_of`.

    Deliberately forward-looking. This is the outcome side of the experiment.
    """
    rows = signals.series(norm, "revenue", "Q")
    # Two conditions, both required. The period must END after the cutoff, and
    # it must have FIRST become public after the cutoff. Filtering on the
    # top-level `filed` alone used the newest vintage, so a quarter that ended
    # and was published years earlier but was restated later looked like a
    # future quarter -- 42% of cutoffs got a horizon made of quarters that had
    # already closed, which is not an outcome window at all.
    future = [
        r for r in rows
        if r["end"] > as_of and _first_public(r) > as_of
    ]
    return [r["end"] for r in future[:n]]


def _first_public(row: dict) -> str:
    """When this period was first published, not when it was last revised."""
    return min((v.get("filed") or "" for v in row.get("vintages", [row])), default="")


def _snapshot_at(norm: dict, end_period: str) -> dict:
    """All facts with period end <= `end_period`. Used only for label
    computation, never for scoring."""
    out = {}
    for metric, rows in norm.items():
        keep = [r for r in rows if r["end"] <= end_period]
        if keep:
            out[metric] = keep
    return out


# ------------------------------------------------------------------ criteria


def _revenue_decel(norm: dict, period: str) -> Criterion | None:
    """Growth falls sharply below the filer's own recent growth rate. Measured
    against its own trailing norm, so a structurally slow grower is not
    permanently labeled."""
    rows = signals.series(norm, "revenue", "Q")
    idx = next((i for i, r in enumerate(rows) if r["end"] == period), None)
    if idx is None or idx < 8:
        return None
    cur = signals.yoy_at(rows, idx)
    prior = [p for p in (signals.yoy_at(rows, i) for i in range(idx - 4, idx)) if p is not None]
    if cur is None or len(prior) < 3:
        return None
    drop = mean(prior) - cur
    if drop < REVENUE_DECEL_PP:
        return None
    return Criterion(
        "REVENUE_DECEL", period, round(drop, 4), REVENUE_DECEL_PP,
        f"revenue growth {cur:.1%} vs own trailing norm {mean(prior):.1%} -- {drop:.1%} drop",
    )


def _margin_collapse(norm: dict, period: str) -> Criterion | None:
    snap = _snapshot_at(norm, period)
    d = signals.diagnose("", "", snap)
    if d.period != period or d.gross_margin_delta_yoy is None:
        return None
    if d.gross_margin_delta_yoy > -GROSS_MARGIN_DROP_PP:
        return None
    return Criterion(
        "MARGIN_COLLAPSE", period, round(d.gross_margin_delta_yoy, 4),
        -GROSS_MARGIN_DROP_PP,
        f"gross margin {d.gross_margin:.1%}, down {abs(d.gross_margin_delta_yoy):.1%} YoY",
    )


def _ocf_break(norm: dict, period: str) -> Criterion | None:
    """TTM operating cash flow turns negative from positive, or halves.

    Uses the contiguity-gated TTM, so a filer whose OCF series has holes returns
    None rather than a fabricated comparison.
    """
    rows = signals.series(norm, "operating_cash_flow", "Q")
    idx = next((i for i, r in enumerate(rows) if r["end"] == period), None)
    if idx is None or idx < 8:
        return None
    cur = derive.ttm(rows[: idx + 1])
    prior = derive.ttm(rows[: idx + 1], back=4)
    if cur is None or prior is None or prior <= 0:
        return None
    if cur < 0:
        return Criterion(
            "OCF_NEGATIVE", period, cur, 0.0,
            f"TTM operating cash flow turned negative "
            f"({cur/1e6:,.0f}M from {prior/1e6:,.0f}M)",
        )
    decline = (prior - cur) / prior
    if decline < OCF_DECLINE_FRAC:
        return None
    return Criterion("OCF_HALVED", period, round(decline, 4), OCF_DECLINE_FRAC,
                     f"TTM operating cash flow down {decline:.0%} YoY")


def _impairment(norm: dict, period: str) -> Criterion | None:
    imp = next((r for r in signals.series(norm, "impairment", "Q") if r["end"] == period), None)
    assets = next(
        (r for r in reversed(signals.series(norm, "total_assets", "PIT")) if r["end"] <= period),
        None,
    )
    if not imp or not assets or not assets["value"]:
        return None
    # No abs(). A derived quarter can come out negative, and treating its
    # magnitude as a writedown counts an arithmetic artifact as an impairment.
    if imp["value"] <= 0:
        return None
    ratio = imp["value"] / assets["value"]
    if ratio < IMPAIRMENT_OF_ASSETS:
        return None
    return Criterion("IMPAIRMENT", period, round(ratio, 4), IMPAIRMENT_OF_ASSETS,
                     f"impairment charge {ratio:.1%} of total assets")


def _restatement(norm: dict, period: str) -> Criterion | None:
    """An amended filing touching revenue, OCF or net income for this period.

    A restatement is the cleanest possible confirmation that the originally
    filed numbers did not hold.
    """
    for metric in ("revenue", "operating_cash_flow", "net_income"):
        for r in norm.get(metric, []):
            if r["end"] != period:
                continue
            # Every vintage, not just the latest. An amendment superseded by a
            # later ordinary filing leaves no trace in the top-level row, so
            # the restatement that actually happened became invisible.
            for v in r.get("vintages", [r]):
                if (v.get("form") or "").endswith("/A"):
                    return Criterion(
                        "RESTATEMENT", period, 1.0, 1.0,
                        f"{metric} restated for this period via {v['form']} "
                        f"(filed {v.get('filed')})",
                    )
    return None


CRITERIA = (_revenue_decel, _margin_collapse, _ocf_break, _impairment, _restatement)


# -------------------------------------------------------------------- label


def label(ticker: str, cik: str, norm: dict, as_of: str) -> Label:
    """Did this filer deteriorate in the four quarters after `as_of`?

    Used two ways:
      - control group: gate fires but no deterioration follows -> false positive
      - positive set: the FIRST period where deterioration trips becomes the
        `broke` date, derived from filings rather than from memory
    """
    window = _forward_window(norm, as_of)
    out = Label(ticker=ticker, cik=cik, as_of=as_of,
                horizon_end=window[-1] if window else None,
                n_quarters_observed=len(window))
    if not window:
        return out

    for period in window:
        hits = [c for c in (fn(norm, period) for fn in CRITERIA) if c is not None]
        if len(hits) >= MIN_CRITERIA:
            out.deteriorated = True
            out.event_period = period
            out.criteria = [asdict(h) for h in hits]
            return out
    return out


def first_deterioration(ticker: str, cik: str, norm: dict,
                        not_before: str | None = None) -> str | None:
    """The first period where deterioration trips, as YYYY-MM.

    This is what makes the positive set generatable across the universe instead
    of hand-curated from eight remembered blowups.

    `not_before` is the filer's first scoreable cutoff. Deterioration that
    became public at or before that date is skipped, because the gate could not
    have been asked about it -- and returning it anyway got the filer REJECTED
    by universe.admit(), discarding every later break it did have. On the S&P
    1500 that dropped 228 filers, and it dropped them selectively: the ones
    whose trouble came early. A filer that broke in 2012 and again in 2020 is a
    perfectly good 2020 positive.

    Comparison is on the tripping quarter's FILING date, not its period end,
    matching how universe.admit() gates and how lead time is measured.
    """
    for r in signals.series(norm, "revenue", "Q"):
        if not_before:
            public = broke_date_filed(norm, r["end"]) or ""
            if public <= not_before:
                continue
        hits = [c for c in (fn(norm, r["end"]) for fn in CRITERIA) if c is not None]
        if len(hits) >= MIN_CRITERIA:
            return r["end"][:7]
    return None


def broke_date_filed(norm: dict, period: str) -> str | None:
    """When the deterioration became PUBLIC, i.e. the filing date of the
    quarter that tripped it. Lead time must be measured against this, not
    against the period end -- a quarter ending 3/31 is not public until 5/10."""
    row = next((r for r in signals.series(norm, "revenue", "Q") if r["end"] == period), None)
    if not row:
        return None
    # The FIRST vintage. edgar.normalize sets the top-level `filed` from the
    # newest vintage, so reading it dated the break to whenever the quarter was
    # last restated -- which would inflate every lead time by the length of the
    # restatement lag rather than measuring detection.
    return _first_public(row) or None


# ------------------------------------------------- secondary, not the label


def price_drawdown(prices: list[tuple[str, float]], start: str, quarters: int = 4) -> float | None:
    """Max drawdown over the horizon. REPORTED alongside the verdict, never
    part of the pass/fail rule -- see module docstring."""
    if not prices:
        return None
    start_d = date.fromisoformat(start)
    end_d = date(start_d.year + (start_d.month + quarters * 3 - 1) // 12,
                 (start_d.month + quarters * 3 - 1) % 12 + 1, 1)
    window = [p for d, p in prices if start <= d <= end_d.isoformat()]
    if len(window) < 2:
        return None
    return (min(window) - window[0]) / window[0]
