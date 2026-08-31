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

from . import derive, edgar, provenance, reasons, status
from .signals import Diagnostics, diagnose, series

# Which arithmetic produced a score. "3.0.0" is the gate exactly as frozen for
# the Phase 0 holdout on 2026-08-30; "3.1.0" adds the 52/53-week span guard
# (diagnose refuses a 14-week-vs-13-week YoY comparison, so some scores move)
# and the structural-abstention fix (a filer whose computable diagnostics
# cannot reach THRESHOLD at any z is unscoreable, not score 0.0). Scores from
# different gate versions must never be pooled into one average -- the version
# travels on every Verdict so a future track record can hold them apart.
#
# "3.2.0" is the metric-arithmetic pass. Four changes move what a reading says:
#   * total_debt no longer double-counts current maturities when a filer tags
#     the all-in LongTermDebt (edgar.SUBSUMED_GROUPS). 1,701 of 52,328 stored
#     total_debt rows across 219 filers carried the inflated shape, and 871
#     published signals computed net_debt_to_ttm_ocf from one -- QCOM at
#     2018-08-15 read 19.291bn against a true 15.378bn, +25.4%. The direction
#     is one-sided: every affected debt figure falls.
#   * deferred_vs_revenue_gap and net_debt_to_ttm_ocf abstain with
#     PERIOD_MISALIGNED when the balance sheet leg is stale, as dso and dio
#     already did. Roughly a fifth of each diagnostic's evaluations were on a
#     balance more than 135 days older than the assessed quarter, so those
#     flags stop firing. Both carry weight 0.0000, so no SCORE moves -- but
#     gated_in can, because MIN_FLAGS counts flags, not weight.
#   * the accession trace keys on PROVENANCE_INPUTS, so flag `sources`,
#     `filed` and the TRACED/PARTIAL/UNTRACED label are computed over every
#     input the arithmetic read rather than the coverage-gated subset.
#   * derived_fraction is None, not 0.0, on the paths that return before
#     diagnose() runs.
GATE_VERSION = "3.2.0"

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

# Which COVERAGE-GATED metrics each diagnostic consumes -- a gate table, not a
# provenance table. A metric that coverage_report() marks scoreable=False must
# not silently produce a diagnostic value: FTI at cutoff 2020-08-15 had
# cost_of_revenue coverage of 56%, and dio was computed anyway from a 2020
# inventory balance over a 2018 COGS window. The old gate only enforced
# REQUIRED_COVERAGE, so a filer failing on any other metric was still scored on
# it. Balance-sheet metrics are absent by design: coverage is a statement about
# quarterly flow completeness. For "which filings did this number come from",
# use PROVENANCE_INPUTS.
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

# Every metric each diagnostic's ARITHMETIC reads, which is what a trace has to
# cover. The two tables were one table, and the trace keyed on the gate's --
# so six of thirteen diagnostics published a strict subset of the accessions
# their number came from and the reading was still labelled TRACED. WBD at
# cutoff 2013-08-15 fired DEFERRED_VS_REVENUE_GAP at -0.3243 citing only the
# 10-Q of 2013-07-30; the deferred-revenue balances that produced half of that
# number came from accession 0001193125-10-035850, which appeared nowhere in
# the flag's trace. Worse, the UNTRACED abstention checks only the metrics it
# is handed, so it was structurally unable to fire on the missing half.
# Measured: of 5,367 (fired flag, omitted input) pairs in the store, 382 (7.1%)
# had an omitted input whose accession is nowhere in the flag's sources.
PROVENANCE_INPUTS: dict[str, tuple[str, ...]] = {
    "cash_conversion_gap":     ("revenue", "operating_cash_flow"),
    "accrual_ratio":           ("net_income", "operating_cash_flow", "total_assets"),
    "receivables_vs_revenue":  ("revenue", "receivables"),
    "inventory_vs_revenue":    ("revenue", "inventory"),
    "dso":                     ("revenue", "receivables"),
    "dio":                     ("cost_of_revenue", "inventory"),
    "deferred_vs_revenue_gap": ("revenue", "deferred_revenue"),
    "revenue_accel":           ("revenue",),
    "gross_margin":            ("revenue", "gross_profit", "cost_of_revenue"),
    "op_margin":               ("revenue", "operating_income"),
    "ocf_to_revenue":          ("revenue", "operating_cash_flow"),
    "net_debt_to_ttm_ocf":     ("operating_cash_flow", "cash", "total_debt"),
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

# The least summed weight from which THRESHOLD is reachable at all. The score
# is a weighted hinge sum over a FIXED divisor, so a filer's ceiling is
# evaluated_weight * Z_CAP / SCORE_DIVISOR * 100 -- missing diagnostics
# compress the scale rather than renormalizing it. Below this weight
# (~0.399 of the 1.992 total) even every-diagnostic-at-maximum cannot reach
# THRESHOLD, and reporting score 0.0 for such a filer is a structural
# abstention wearing the costume of a clean assessment: one existed in a
# 250-filer sample as score 0.0 / gated_in False / scoreable True.
MIN_SCOREABLE_WEIGHT = THRESHOLD / 100 * SCORE_DIVISOR / Z_CAP

# Sum of every shipped weight; on a Verdict next to evaluated_weight it says
# how much of the diagnostic set a filer was actually judged on.
WEIGHT_TOTAL = sum(w for _, w, _ in TRACKED.values())

# How many trailing period ends per metric count as the CURRENT reading's
# evidence. Five because a TTM window is four quarters and a YoY comparison
# reaches five. Deliberately the reading's evidence, not the 20-quarter
# baseline's: the baseline is reproducible from gate_version + as_of + the
# immutable fact cache, whereas the reading is what a reader is asked to
# believe.
EVIDENCE_QUARTERS = 5


def gate_fingerprint() -> dict:
    """Every constant that can change a score, as one ordered dict.

    The persistence layer hashes this into the gate_version written on every
    stored signal; without it a retune silently interleaves two different
    gates in one track record and any later comparison measures a blend.
    Rule for whoever edits this module next: if a constant can change a
    score, it belongs in this dict.
    """
    return {
        "tracked": {
            name: {"direction": d, "weight": w, "scale_floor": f}
            for name, (d, w, f) in TRACKED.items()
        },
        "z_trigger": Z_TRIGGER,
        "threshold": THRESHOLD,
        "score_divisor": SCORE_DIVISOR,
        "z_cap": Z_CAP,
        "min_flags": MIN_FLAGS,
        "min_history": MIN_HISTORY,
        "min_baseline_n": MIN_BASELINE_N,
        "max_baseline": MAX_BASELINE,
        "required_coverage": list(REQUIRED_COVERAGE),
        "coverage_min": derive.COVERAGE_MIN,
    }


def evidence_accessions(snap: dict, quarters: int = EVIDENCE_QUARTERS) -> list[str]:
    """The accessions behind the current reading: union of `sources` over each
    metric's last `quarters` period ends in the truncated snapshot, deduped
    and sorted. Populated on every path where a snapshot exists -- including
    abstentions, because "why was this filer not scoreable" is a claim that
    also has to trace to filings."""
    accs: set[str] = set()
    for rows in snap.values():
        ends = sorted({r.get("end") for r in rows if r.get("end")})[-quarters:]
        for r in rows:
            if r.get("end") in ends:
                accs.update(s for s in r.get("sources", []) if s)
    return sorted(accs)


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
    # None, not 0.0, until diagnose() has actually measured it. The coverage
    # gate returns before diagnose() runs, so 104 of 1,498 filers at as_of
    # 2026-08-31 published derived_fraction 0.0 -- the positive claim that none
    # of their quarterly figures were worked out by differencing YTD reports --
    # where the measurable values ran to 0.491 (ACT), just under the
    # DERIVED_FRACTION_HIGH tripwire. Same failure mode as `score` above.
    derived_fraction: float | None = None
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
    # Additive fields, all defaulted so asdict() stays shape-compatible for
    # every existing consumer (backtest, calibrate, cli, render).
    #   gate_version    which arithmetic produced this -- see GATE_VERSION.
    #   reason_code     the countable code beside the free-text `reason`; the
    #                   sentence stays exactly as it was.
    #   abstentions     diagnostic -> reason code for every tracked diagnostic
    #                   that did NOT evaluate; abstention_detail carries the
    #                   sentence. Invariant, pinned by a test: on a scoreable
    #                   verdict len(z) + len(abstentions) == len(TRACKED).
    #   evaluated_weight/weight_total  how much of the diagnostic set this
    #                   filer was actually judged on -- the scale compression
    #                   that made THRESHOLD mean something different per filer.
    #   accessions      the current reading's evidence (evidence_accessions),
    #                   populated on every path where a snapshot exists. The
    #                   signal store's NOT NULL trace column reads this, and
    #                   emit() refuses a scoreable verdict where it is empty.
    gate_version: str = GATE_VERSION
    reason_code: str | None = None
    abstentions: dict = field(default_factory=dict)
    abstention_detail: dict = field(default_factory=dict)
    evaluated_weight: float = 0.0
    weight_total: float = WEIGHT_TOTAL
    accessions: list = field(default_factory=list)

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
    construction the vintage that was public at the cutoff.

    Keyed on PROVENANCE_INPUTS, not the coverage gate's table: a citation that
    omits the balance sheet half of a ratio sends a reader to a filing that
    does not contain the number."""
    if not period:
        return [], None
    sources: list[str] = []
    filed: str | None = None
    for metric in PROVENANCE_INPUTS.get(name, ()):
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
                    reason="no XBRL facts",
                    reason_code=reasons.NO_XBRL_FACTS).as_dict())

    snap = edgar.as_of(full, cutoff)
    # The trace travels on every verdict a snapshot exists for, abstentions
    # included -- the persisted record outlives the fact cache, so the claim
    # "could not assess" has to cite filings the same way a score does.
    accs = evidence_accessions(snap)
    if not snap.get("revenue"):
        return status.stamp(
            Verdict(ticker, cik, cutoff, scoreable=False,
                    reason="no revenue facts filed as of cutoff",
                    reason_code=reasons.NO_REVENUE_AT_CUTOFF,
                    accessions=accs).as_dict())

    ok, reason, cov = _coverage_gate(snap)
    if not ok:
        return status.stamp(
            Verdict(ticker, cik, cutoff, scoreable=False, reason=reason,
                    reason_code=reasons.REQUIRED_COVERAGE_LOW,
                    coverage=cov, accessions=accs).as_dict())

    current = diagnose(ticker, cik, snap)
    hist = _history(ticker, cik, full, cutoff)
    if len(hist) < MIN_HISTORY:
        return status.stamp(
            Verdict(ticker, cik, cutoff, period=current.period, scoreable=False,
                    reason=f"insufficient own-history ({len(hist)}q of {MIN_HISTORY})",
                    reason_code=reasons.SHORT_HISTORY,
                    coverage=cov, derived_fraction=current.derived_fraction,
                    diagnostics=current.as_dict(), accessions=accs).as_dict())

    flags: list[ZFlag] = []
    all_z: dict[str, float] = {}
    # Every tracked diagnostic is either evaluated (lands in all_z) or
    # accounted for here -- the accounting invariant that makes the coverage
    # dashboard's per-diagnostic histogram countable. The same three `continue`
    # branches as before; the only change is that each now says why.
    abstentions: dict[str, str] = {}
    abstention_detail: dict[str, str] = {}

    def record_abstention(name: str, code: str, detail: str) -> None:
        abstentions[name] = code
        abstention_detail[name] = detail

    for name, (direction, weight, floor) in TRACKED.items():
        cur = getattr(current, name)
        if cur is None:
            # diagnose() recorded its own reason at the branch that decided
            # the None. UNEXPLAINED means a new None-branch forgot to -- the
            # dashboard counts it so the gap surfaces instead of hiding.
            record_abstention(name,
                    current.reasons.get(name, reasons.UNEXPLAINED),
                    current.reason_detail.get(name, reasons.TEXT[reasons.UNEXPLAINED]))
            continue
        if _uncovered(name, cov):
            gaps = ", ".join(
                m for m in DIAGNOSTIC_INPUTS.get(name, ())
                if cov.get(m, {}).get("n") and not cov.get(m, {}).get("scoreable"))
            record_abstention(name, reasons.INPUT_COVERAGE_LOW,
                    f"an input this measure needs ({gaps.replace('_', ' ')}) is "
                    "missing from too many quarters to trust")
            continue
        baseline = [v for v in (getattr(h, name) for h in hist) if v is not None]
        res = robust_z(cur, baseline, floor)
        if res is None:
            record_abstention(name, reasons.BASELINE_TOO_THIN,
                    f"only {len(baseline)} past readings of this measure; "
                    f"{MIN_BASELINE_N} are needed to know what is normal")
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

    evaluated_weight = round(sum(TRACKED[n][1] for n in all_z), 4)

    # Structural abstention: the diagnostics that DID evaluate carry too
    # little weight to reach THRESHOLD at any z, so "score 0.0, not flagged"
    # would be indistinguishable from "assessed, looks clean" -- the exact
    # defect the filer-level coverage gate was written to prevent, resurfacing
    # one level down. Unscoreable with a written reason, never score 0.0.
    if evaluated_weight < MIN_SCOREABLE_WEIGHT:
        ceiling = evaluated_weight * Z_CAP / SCORE_DIVISOR * 100
        return status.stamp(
            Verdict(ticker, cik, cutoff, period=current.period, scoreable=False,
                    reason=(f"cannot reach the flag threshold: the "
                            f"{len(all_z)} computable measures carry weight "
                            f"{evaluated_weight:g} of {WEIGHT_TOTAL:g}, so the "
                            f"score tops out at {ceiling:.0f} of the "
                            f"{THRESHOLD:g} needed"),
                    reason_code=reasons.CANNOT_REACH_THRESHOLD,
                    coverage=cov, derived_fraction=current.derived_fraction,
                    diagnostics=current.as_dict(), z=all_z,
                    abstentions=abstentions, abstention_detail=abstention_detail,
                    evaluated_weight=evaluated_weight,
                    accessions=accs).as_dict())

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
        abstentions=abstentions,
        abstention_detail=abstention_detail,
        evaluated_weight=evaluated_weight,
        accessions=accs,
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
        verdict.reason_code = reasons.UNTRACED
    return status.stamp(verdict.as_dict())
