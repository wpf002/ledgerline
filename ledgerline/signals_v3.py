"""
Ledgerline Signal -- Tier 3, the gate.

Scores each diagnostic as a deviation from THAT FILER'S OWN trailing
distribution. The question is "is this abnormal for this filer?", not "is this
number big?". Absolute thresholds (v1) measure a business model rather than a
change in one, which is why v1 fired on 60-90% of quarters.

FIXES over v2 (see FINDINGS.md §3):

  1. POINT-IN-TIME BASELINES. v2's _history() truncated on period `end`, so
     baselines were built from restated figures that were not public at the
     time. Production and backtest therefore computed different functions and
     no backtest result would have transferred. This module truncates on
     `filed` via edgar.as_of() -- the same primitive the backtest uses, so
     there is exactly one code path.

  2. ROBUST SCALE WITH A FLOOR. v2 used mean/pstdev on overlapping TTM windows.
     Consecutive observations share three of four quarters, which understates
     the spread and inflates every z. With no floor on sd, a filer with a flat
     stretch got sd -> 0 and any move read as a 5-sigma break. Now median/MAD
     with a per-metric floor set to that ratio's measurement noise.

  3. SAMPLE SIZE. MIN_HISTORY 6 -> 12, and a diagnostic needs 8 non-null
     baseline observations. Nominal n overstates effective n badly here.

  4. COVERAGE GATE. A filer whose flow-metric coverage falls below
     derive.COVERAGE_MIN is excluded with a logged reason instead of scored on
     partial data.

WEIGHTS AND THRESHOLD ARE NOT CALIBRATED. The values below are placeholders
carried over from v2 so the module runs. Z_TRIGGER, the weight table and
THRESHOLD in v2 were all chosen while looking at the same eight cases the lead
times were reported on -- no holdout, one macro regime. Per ROADMAP Phase 3
these get fit on the tuning split by logistic regression against labeled
outcomes. Until then, treat any score from this module as uncalibrated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import median

from . import edgar
from .signals import Diagnostics, diagnose, series

# diagnostic -> (direction, weight, scale_floor)
#   direction +1 : unusually HIGH is bad
#   direction -1 : unusually LOW is bad
#   scale_floor  : minimum sd, in the diagnostic's own units. Prevents a quiet
#                  stretch from turning ordinary noise into a 5-sigma event.
TRACKED: dict[str, tuple[int, float, float]] = {
    "cash_conversion_gap":     (+1, 2.0, 0.05),
    "accrual_ratio":           (+1, 2.0, 0.010),
    "receivables_vs_revenue":  (+1, 1.5, 0.05),
    "inventory_vs_revenue":    (+1, 1.5, 0.05),
    "dso":                     (+1, 1.0, 2.0),
    "dio":                     (+1, 1.0, 3.0),
    "deferred_vs_revenue_gap": (-1, 2.0, 0.05),
    "revenue_accel":           (-1, 1.5, 0.02),
    "gross_margin":            (-1, 1.5, 0.005),
    "op_margin":               (-1, 1.0, 0.005),
    "ocf_to_revenue":          (-1, 1.5, 0.010),
    "net_debt_to_ttm_ocf":     (+1, 1.0, 0.25),
    "dilution_yoy":            (+1, 1.0, 0.005),
}

LABELS = {
    "cash_conversion_gap": "Cash conversion breaking from trend",
    "accrual_ratio": "Accruals abnormal vs own history",
    "receivables_vs_revenue": "Receivables growth abnormal vs own history",
    "inventory_vs_revenue": "Inventory build abnormal vs own history",
    "dso": "Collection period abnormal vs own history",
    "dio": "Inventory turns abnormal vs own history",
    "deferred_vs_revenue_gap": "Forward bookings breaking from trend",
    "revenue_accel": "Growth deceleration abnormal vs own history",
    "gross_margin": "Gross margin abnormal vs own history",
    "op_margin": "Operating margin abnormal vs own history",
    "ocf_to_revenue": "Cash generation abnormal vs own history",
    "net_debt_to_ttm_ocf": "Leverage abnormal vs own history",
    "dilution_yoy": "Share issuance abnormal vs own history",
}

# Metrics whose absence makes the filer unscoreable rather than partially scored.
REQUIRED_COVERAGE = ("revenue", "operating_cash_flow", "net_income")

MIN_HISTORY = 12       # quarters of own history before a filer is scoreable
MIN_BASELINE_N = 8     # non-null observations required per diagnostic
MAX_BASELINE = 20      # cap the lookback so the baseline tracks the business
Z_TRIGGER = 2.0        # UNCALIBRATED -- see module docstring
THRESHOLD = 45.0       # UNCALIBRATED -- see module docstring
SCORE_DIVISOR = 8.0    # UNCALIBRATED -- see module docstring
Z_CAP = 2.5            # a 10-sigma print should not outvote breadth


@dataclass
class ZFlag:
    code: str
    label: str
    weight: float
    z: float
    value: float
    baseline_median: float
    baseline_scale: float
    baseline_n: int
    floored: bool
    detail: str


@dataclass
class Verdict:
    ticker: str
    cik: str
    as_of: str
    period: str | None = None
    score: float = 0.0
    gated_in: bool = False
    scoreable: bool = True
    reason: str | None = None
    flags: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    derived_fraction: float = 0.0
    diagnostics: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


# ------------------------------------------------------------- robust scale


def mad_scale(values: list[float]) -> tuple[float, float]:
    """Median and MAD-derived sd. Robust to the single blown-up quarter that
    makes pstdev explode and then blinds the detector for three years."""
    if not values:
        return 0.0, 0.0
    mu = median(values)
    mad = median([abs(v - mu) for v in values])
    return mu, 1.4826 * mad


def robust_z(
    current: float, baseline: list[float], floor: float
) -> tuple[float, float, float, bool] | None:
    """(z, median, scale, floored) or None if the baseline is too thin."""
    if len(baseline) < MIN_BASELINE_N:
        return None
    mu, sd = mad_scale(baseline)
    floored = sd < floor
    sd = max(sd, floor)
    if sd <= 0:
        return None
    return (current - mu) / sd, mu, sd, floored


# ---------------------------------------------------------------- baselines


def _history(ticker: str, cik: str, norm: dict, as_of_date: str) -> list[Diagnostics]:
    """The filer's own baseline, rebuilt point-in-time at each prior quarter.

    FIX §3: truncation is by `filed` through edgar.as_of(), not by period end.
    Each snapshot contains only what a reader could have seen on the day that
    quarter's filing landed.
    """
    filed_dates = sorted({r["filed"] for r in series(norm, "revenue", "Q") if r.get("filed")})
    filed_dates = [f for f in filed_dates if f <= as_of_date]
    out: list[Diagnostics] = []
    for f in filed_dates[-(MAX_BASELINE + 1) : -1]:  # exclude the current quarter
        snap = edgar.as_of(norm, f)
        if not snap.get("revenue"):
            continue
        out.append(diagnose(ticker, cik, snap))
    return out


def _coverage_gate(norm: dict) -> tuple[bool, str | None, dict]:
    report = edgar.coverage_report(norm)
    failed = [m for m in REQUIRED_COVERAGE if m in report and not report[m]["scoreable"]]
    if failed:
        detail = ", ".join(f"{m} {report[m]['ratio']:.0%}" for m in failed)
        return False, f"insufficient quarterly coverage: {detail}", report
    return True, None, report


# ------------------------------------------------------------------ evaluate


def evaluate(ticker: str, cik: str, as_of: str | None = None, norm: dict | None = None) -> dict:
    """Score one filer as of a date.

    `as_of` defaults to today. Backtest and production call this identically --
    there is no separate backtest path, which is what makes a validated result
    transferable.
    """
    cutoff = as_of or date.today().isoformat()
    full = norm if norm is not None else edgar.normalize(cik)
    if not full:
        return Verdict(ticker, cik, cutoff, scoreable=False, reason="no XBRL facts").as_dict()

    snap = edgar.as_of(full, cutoff)
    if not snap.get("revenue"):
        return Verdict(ticker, cik, cutoff, scoreable=False,
                       reason="no revenue facts filed as of cutoff").as_dict()

    ok, reason, cov = _coverage_gate(snap)
    if not ok:
        return Verdict(ticker, cik, cutoff, scoreable=False, reason=reason,
                       coverage=cov).as_dict()

    current = diagnose(ticker, cik, snap)
    hist = _history(ticker, cik, full, cutoff)
    if len(hist) < MIN_HISTORY:
        return Verdict(ticker, cik, cutoff, period=current.period, scoreable=False,
                       reason=f"insufficient own-history ({len(hist)}q of {MIN_HISTORY})",
                       coverage=cov, derived_fraction=current.derived_fraction,
                       diagnostics=current.as_dict()).as_dict()

    flags: list[ZFlag] = []
    for name, (direction, weight, floor) in TRACKED.items():
        cur = getattr(current, name)
        if cur is None:
            continue
        baseline = [v for v in (getattr(h, name) for h in hist) if v is not None]
        res = robust_z(cur, baseline, floor)
        if res is None:
            continue
        z, mu, sd, floored = res
        signed = z * direction
        if signed < Z_TRIGGER:
            continue
        flags.append(
            ZFlag(
                code=name.upper(),
                label=LABELS.get(name, name),
                weight=weight,
                z=round(signed, 2),
                value=cur,
                baseline_median=mu,
                baseline_scale=sd,
                baseline_n=len(baseline),
                floored=floored,
                detail=(
                    f"{name} at {cur:,.3f} vs own trailing median {mu:,.3f} "
                    f"(scale {sd:,.3f}, n={len(baseline)}) -- a {signed:.1f}-sigma move "
                    f"against this filer's established pattern"
                    + (" [scale floored]" if floored else "")
                    + "."
                ),
            )
        )

    raw = sum(f.weight * min(f.z / Z_TRIGGER, Z_CAP) for f in flags)
    score = round(min(raw / SCORE_DIVISOR * 100, 100), 1)

    return Verdict(
        ticker=ticker,
        cik=cik,
        as_of=cutoff,
        period=current.period,
        score=score,
        gated_in=score >= THRESHOLD,
        scoreable=True,
        coverage=cov,
        derived_fraction=current.derived_fraction,
        flags=[asdict(f) for f in flags],
        diagnostics=current.as_dict(),
    ).as_dict()
