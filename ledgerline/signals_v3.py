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

KILL (Phase 0, 2026-08-30). The gate this module implements FAILED its
pre-registered holdout test, scored exactly once against data/prereg.json.
Two of six criteria failed: it caught 28.7% of the deteriorations it was built
to find against a required 60%, and its false-alarm rate (0.0383 per control
filer-quarter) did not beat the 0.0051 of the naive two-line baseline it had
to better. 51.2% of control filers were flagged at least once. The CALIBRATED
block below therefore describes the fitted gate that failed, not a working
one. The frozen record is ledgerline/data/phase0.json, read by
ledgerline/status.py, whose stamp every emitted score carries; the write-up is
reports/PHASE0.md. Do not retune and re-run against the spent holdout.

CALIBRATED (Phase 0f, 2026-08-30) on the TUNING split only, split sha256
5c12ce54..., against the pre-registered rule in data/prereg.json.

The weight table below is the coefficient vector of a ridge logistic regression
fitted to 18,480 tuning filer-quarters (1,699 of them followed by a
deterioration event, a 9.2% base rate). The features ARE the gate's own hinge
terms, min(max(z,0)/Z_TRIGGER, Z_CAP), so a coefficient here is directly the
weight applied below -- the score can be recomputed by hand from the published
z values and this table. See ledgerline/calibrate.py and data/calibration.json,
which records the full grid, the intercept and the unclamped coefficients.

Two coefficients came out NEGATIVE and are clamped to zero: deferred_vs_revenue_gap
and net_debt_to_ttm_ocf. On this data they pointed opposite to their declared
direction. Clamping says "contributed nothing" rather than letting a diagnostic
that was supposed to be evidence argue the gate OUT of firing.

Z_TRIGGER was chosen from a grid rather than assumed; 1.5/2.0/2.5/3.0 all land
within 0.13-0.14 recall at the FPR ceiling, so the gate is not sensitive to it.

The operating point is the most sensitive raw cutoff whose per-quarter
false-positive rate on tuning CONTROL quarters stays under the pre-registered
0.04. It binds: tuning FPR is 0.03995 and recall on deteriorating quarters is
0.1396. That number is the honest tuning-set performance and it is low.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import median

from . import edgar, provenance, status
from .signals import Diagnostics, diagnose, series

# diagnostic -> (direction, weight, scale_floor)
#   direction +1 : unusually HIGH is bad
#   direction -1 : unusually LOW is bad
#   scale_floor  : minimum sd, in the diagnostic's own units. Prevents a quiet
#                  stretch from turning ordinary noise into a 5-sigma event.
TRACKED: dict[str, tuple[int, float, float]] = {
    "cash_conversion_gap":     (+1, 0.2881, 0.05),
    "accrual_ratio":           (+1, 0.1548, 0.01),
    "receivables_vs_revenue":  (+1, 0.2305, 0.05),
    "inventory_vs_revenue":    (+1, 0.1297, 0.05),
    "dso":                     (+1, 0.0298, 2.0),
    "dio":                     (+1, 0.2221, 3.0),
    "deferred_vs_revenue_gap": (-1, 0.0000, 0.05),
    "revenue_accel":           (-1, 0.3539, 0.02),
    "gross_margin":            (-1, 0.3818, 0.005),
    "op_margin":               (-1, 0.0042, 0.005),
    "ocf_to_revenue":          (-1, 0.1025, 0.01),
    "net_debt_to_ttm_ocf":     (+1, 0.0000, 0.25),
    "dilution_yoy":            (+1, 0.0949, 0.005),
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

# Which coverage-gated metrics each diagnostic actually consumes. A metric that
# coverage_report() marks scoreable=False must not silently produce a
# diagnostic value: FTI at cutoff 2020-08-15 had cost_of_revenue coverage of
# 56%, and dio was computed anyway from a 2020 inventory balance over a 2018
# COGS window. The old gate only enforced REQUIRED_COVERAGE, so a filer failing
# on any other metric was still scored on it.
DIAGNOSTIC_INPUTS: dict[str, tuple[str, ...]] = {
    "cash_conversion_gap":     ("revenue", "operating_cash_flow"),
    "accrual_ratio":           ("net_income", "operating_cash_flow"),
    "receivables_vs_revenue":  ("revenue",),
    "inventory_vs_revenue":    ("revenue",),
    "dso":                     ("revenue",),
    "dio":                     ("cost_of_revenue",),
    "deferred_vs_revenue_gap": ("revenue",),
    "revenue_accel":           ("revenue",),
    "gross_margin":            ("revenue", "gross_profit", "cost_of_revenue"),
    "op_margin":               ("revenue", "operating_income"),
    "ocf_to_revenue":          ("revenue", "operating_cash_flow"),
    "net_debt_to_ttm_ocf":     ("operating_cash_flow",),
    "dilution_yoy":            ("diluted_shares",),
}


def _uncovered(name: str, cov: dict) -> bool:
    """True if any metric this diagnostic consumes failed its coverage check.

    gross_margin is special-cased: it needs revenue plus EITHER gross_profit or
    cost_of_revenue, so one of the two being sparse is not disqualifying.
    """
    inputs = DIAGNOSTIC_INPUTS.get(name, ())
    if name == "gross_margin":
        if cov.get("revenue", {}).get("scoreable") is False:
            return True
        alts = [cov.get(m, {}) for m in ("gross_profit", "cost_of_revenue")]
        return all(a.get("n") and not a.get("scoreable") for a in alts) if any(
            a.get("n") for a in alts
        ) else False
    return any(
        cov.get(m, {}).get("n") and not cov.get(m, {}).get("scoreable") for m in inputs
    )

MIN_HISTORY = 12       # quarters of own history before a filer is scoreable
MIN_BASELINE_N = 8     # non-null observations required per diagnostic
MAX_BASELINE = 20      # cap the lookback so the baseline tracks the business
Z_TRIGGER = 2.0        # fitted on tuning, Phase 0f
THRESHOLD = 45.0       # operating point, Phase 0f (see SCORE_DIVISOR)
SCORE_DIVISOR = 2.2178  # maps the fitted raw cutoff onto THRESHOLD
Z_CAP = 2.5            # a 10-sigma print should not outvote breadth
# Z_CAP was supposed to make a single extreme print unable to fire on its own,
# and before calibration it did not: the heaviest weight was 2.0, so one flag
# reached 2.0 * 2.5 / 8.0 * 100 = 62.5, past THRESHOLD = 45. Breadth is an
# explicit condition rather than an emergent property of three constants that
# recalibration keeps changing. Pinned by a test.
MIN_FLAGS = 2          # distinct diagnostics that must fire together


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
    # The accessions and filing date behind the current-quarter value that
    # fired. The flag used to publish z and its baseline statistics with no
    # way to find the filing -- the README's "a score traces back to
    # accessions or it does not ship" was a promise the payload could not keep.
    sources: list[str] = field(default_factory=list)
    filed: str | None = None


@dataclass
class Verdict:
    ticker: str
    cik: str
    as_of: str
    period: str | None = None
    # None until the filer is actually scored. The default used to be 0.0, so
    # an unscoreable filer's JSON read "score": 0.0 next to "scoreable": false
    # -- a reader scanning for the number saw a clean bill of health when the
    # truth was "could not assess".
    score: float | None = None
    gated_in: bool = False
    scoreable: bool = True
    reason: str | None = None
    flags: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    derived_fraction: float = 0.0
    diagnostics: dict = field(default_factory=dict)
    # Signed z for EVERY diagnostic that could be computed, including those
    # below Z_TRIGGER. Calibration fits on these, so the weights are fitted to
    # the same numbers production computes rather than to a parallel
    # reimplementation that could drift from the gate.
    z: dict = field(default_factory=dict)
    # Accession traces for the fired flags (provenance.reading_trace), the
    # TRACED / PARTIAL / UNTRACED verdict on them, and -- when UNTRACED forces
    # abstention -- why. All defaulted so asdict() carries them and every
    # existing consumer keeps working untouched.
    provenance: dict = field(default_factory=dict)
    provenance_label: str = "TRACED"
    abstain_reason: str | None = None

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
    # Every date on which a revenue fact was first published OR restated. Using
    # only the top-level `filed` would collapse to the latest vintage of each
    # quarter -- which is what made a filer with 73 quarters of history report
    # 33 distinct filing dates, and then "6q of 12" at a 2017 cutoff.
    filed_dates = sorted({
        v["filed"]
        for r in series(norm, "revenue", "Q")
        for v in r.get("vintages", [r])
        if v.get("filed")
    })
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


def _current_trace(snap: dict, period: str | None,
                   name: str) -> tuple[list[str], str | None]:
    """(accessions, newest filed date) behind one diagnostic's current-quarter
    inputs, straight off the as_of() snapshot so the citation is by
    construction the vintage that was public at the cutoff."""
    if not period:
        return [], None
    sources: list[str] = []
    filed: str | None = None
    for metric in DIAGNOSTIC_INPUTS.get(name, ()):
        rows = [r for r in snap.get(metric, []) if r.get("end", "") <= period]
        if not rows:
            continue
        row = rows[-1]
        sources += [s for s in row.get("sources", []) if s]
        f = row.get("filed")
        if f and (filed is None or f > filed):
            filed = f
    deduped: list[str] = []
    for s in sources:
        if s not in deduped:
            deduped.append(s)
    return deduped, filed


def evaluate(ticker: str, cik: str, as_of: str | None = None, norm: dict | None = None) -> dict:
    """Score one filer as of a date.

    `as_of` defaults to today. Backtest and production call this identically --
    there is no separate backtest path, which is what makes a validated result
    transferable.

    Every return is stamped by status.stamp() with the frozen Phase 0 KILL --
    this is the single scoring surface, and the stamp is the enforcement point
    for "no score is shown without the fact that it failed its own test".
    """
    cutoff = as_of or date.today().isoformat()
    full = norm if norm is not None else edgar.normalize(cik)
    if not full:
        return status.stamp(
            Verdict(ticker, cik, cutoff, scoreable=False,
                    reason="no XBRL facts").as_dict())

    snap = edgar.as_of(full, cutoff)
    if not snap.get("revenue"):
        return status.stamp(
            Verdict(ticker, cik, cutoff, scoreable=False,
                    reason="no revenue facts filed as of cutoff").as_dict())

    ok, reason, cov = _coverage_gate(snap)
    if not ok:
        return status.stamp(
            Verdict(ticker, cik, cutoff, scoreable=False, reason=reason,
                    coverage=cov).as_dict())

    current = diagnose(ticker, cik, snap)
    hist = _history(ticker, cik, full, cutoff)
    if len(hist) < MIN_HISTORY:
        return status.stamp(
            Verdict(ticker, cik, cutoff, period=current.period, scoreable=False,
                    reason=f"insufficient own-history ({len(hist)}q of {MIN_HISTORY})",
                    coverage=cov, derived_fraction=current.derived_fraction,
                    diagnostics=current.as_dict()).as_dict())

    flags: list[ZFlag] = []
    all_z: dict[str, float] = {}
    for name, (direction, weight, floor) in TRACKED.items():
        cur = getattr(current, name)
        if cur is None:
            continue
        if _uncovered(name, cov):
            continue
        baseline = [v for v in (getattr(h, name) for h in hist) if v is not None]
        res = robust_z(cur, baseline, floor)
        if res is None:
            continue
        z, mu, sd, floored = res
        signed = z * direction
        all_z[name] = round(signed, 4)
        if signed < Z_TRIGGER:
            continue
        srcs, src_filed = _current_trace(snap, current.period, name)
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
                sources=srcs,
                filed=src_filed,
            )
        )

    raw = sum(f.weight * min(f.z / Z_TRIGGER, Z_CAP) for f in flags)
    score = round(min(raw / SCORE_DIVISOR * 100, 100), 1)

    verdict = Verdict(
        ticker=ticker,
        cik=cik,
        as_of=cutoff,
        period=current.period,
        score=score,
        gated_in=score >= THRESHOLD and len(flags) >= MIN_FLAGS,
        scoreable=True,
        coverage=cov,
        derived_fraction=current.derived_fraction,
        flags=[asdict(f) for f in flags],
        diagnostics=current.as_dict(),
        z=all_z,
    )
    # The README invariant, enforced at the scoring surface: a fired flag that
    # cannot cite a filing makes the reading UNTRACED and the gate abstains
    # through its existing channel. Measured, 0 of 21,032 rows lack an
    # accession, so this is a regression guard, not a filter.
    verdict.provenance = provenance.reading_trace(snap, current.period,
                                                  verdict.flags)
    # derived_fraction is surfaced with the measured universe distribution
    # beside it, never judged: derivation is the normal path (~3/4 of every
    # OCF series exists only because derive.py differences YTD cumulatives),
    # and the HIGH marker is a tripwire above the observed maximum, not a
    # quality gate. It labels; it does not suppress.
    verdict.provenance["derived_fraction"] = current.derived_fraction
    verdict.provenance["derived_fraction_high"] = (
        current.derived_fraction >= provenance.DERIVED_FRACTION_HIGH)
    verdict.provenance["derived_fraction_observed"] = (
        provenance.DERIVED_FRACTION_OBSERVED)
    verdict.provenance_label, abstain = provenance.label(
        verdict.provenance, current.derived_fraction)
    if verdict.provenance_label == "UNTRACED":
        verdict.scoreable = False
        verdict.gated_in = False
        verdict.score = None
        verdict.reason = abstain
        verdict.abstain_reason = abstain
    return status.stamp(verdict.as_dict())
