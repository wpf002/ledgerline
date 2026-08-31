"""
Ledgerline Signal -- Tier 2 diagnostics.

Pure arithmetic on the normalized metric dictionary. Zero credits, zero
inference. No scoring decisions live here; those are in signals_v3.

FIXES APPLIED (see FINDINGS.md):
  §2  ttm() now refuses to sum four non-contiguous quarters. The old version
      returned a plausible float for a gappy series, which silently corrupted
      accrual_ratio, ocf_to_revenue and net_debt_to_ttm_ocf.
  §3  revenue_accel no longer rebuilds a synthetic norm dict; it indexes the
      series directly.
  §3  dilution_yoy carries a corporate-action guard. The shipped eval.json
      flagged BYND for DILUTION on +673.8% YoY diluted shares -- a split or
      concept switch, not issuance. PTON shows the same shape across its IPO
      (22.9M -> 279.9M).

The v1 absolute-threshold rule set (`flags()` / `score()`) has been REMOVED.
It fired on 60-90% of quarters across the case set because thresholds like
"DSO up >10 days" measure a business model, not a change in one.

METRIC LAYER (Phase 2):
  * diagnose() records WHY each tracked diagnostic is None, at the branch
    that decided it (Diagnostics.reasons / reason_detail, codes from
    reasons.py). Motivating defect: 1 of 169 scoreable filers in a 250-filer
    sample had all 13 diagnostics evaluated, and nothing said so.
  * Gate-side YoY comparisons refuse a 14-week-vs-13-week span mismatch
    (~7% calendar artifact on a 52/53-week filer's long quarter; see
    fiscal.py). label.py's calls keep the as-reported default so the outcome
    label stays exactly reproducible. This moves scores: signals_v3
    GATE_VERSION was bumped for it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import median

from . import derive, fiscal, reasons

# A YoY move larger than this in share count is a corporate action -- split,
# reverse split, IPO, exchange offer -- not economic dilution.
CORPORATE_ACTION_THRESHOLD = 0.50

# A year-ago quarter is one whose period end is roughly 365 days earlier. The
# window absorbs 52/53-week fiscal calendars, which the old calendar-year test
# could not.
YOY_MIN_DAYS = 330
YOY_MAX_DAYS = 400

# A balance-sheet value may only be divided by a flow window that ends at the
# same time. One quarter of slack covers filers whose balance and flow series
# are tagged with slightly different period ends.
STALE_TOLERANCE_DAYS = 100


# ------------------------------------------------------------------- helpers


def series(norm: dict, metric: str, kind: str = "Q") -> list[dict]:
    """Chronological facts for one metric at one duration kind."""
    return [r for r in norm.get(metric, []) if r["kind"] == kind]


def at(norm: dict, metric: str, kind: str = "Q", back: int = 0):
    s = series(norm, metric, kind)
    idx = len(s) - 1 - back
    return s[idx] if 0 <= idx < len(s) else None


def pit(norm: dict, metric: str, back: int = 0):
    return at(norm, metric, "PIT", back)


def _yoy_explained(
    rows: list[dict], idx: int, subject: str,
    require_comparable_span: bool = False,
) -> tuple[float | None, reasons.Abstention | None]:
    """Year-over-year growth at `idx`, or (None, why-not).

    The single implementation behind yoy_at/yoy, so the value a caller gets
    and the reason a dashboard reports cannot come from two code paths.
    """
    plain = subject.replace("_", " ")
    if not rows:
        return None, reasons.Abstention(
            reasons.INPUT_METRIC_ABSENT,
            f"no quarterly {plain} figures in this company's filings", subject)
    if not 0 <= idx < len(rows):
        return None, reasons.Abstention(
            reasons.NO_YEAR_AGO_QUARTER,
            f"not enough {plain} quarters for this comparison", subject)
    cur = rows[idx]
    cur_d = date.fromisoformat(cur["end"])
    prior = None
    for r in reversed(rows[:idx]):
        # Match on elapsed days, not on calendar year plus month. A 52/53-week
        # filer whose Q4 ends 2021-01-02 has its year-ago quarter ending
        # 2019-12-28 -- two calendar years earlier by the old test, so the true
        # comparison period was rejected and yoy silently returned None.
        gap = (cur_d - date.fromisoformat(r["end"])).days
        if YOY_MIN_DAYS <= gap <= YOY_MAX_DAYS:
            prior = r
            break
    if not prior:
        return None, reasons.Abstention(
            reasons.NO_YEAR_AGO_QUARTER,
            f"no {plain} quarter from roughly a year earlier", subject)
    # A 14-week quarter compared against a 13-week quarter manufactures a ~7%
    # move that is calendar, not business. Rescaling by 13/14 would invent a
    # number the filer never reported, so the only honest options are refuse
    # (the gate's choice) or report as filed (the default, so label.py's
    # outcome arithmetic -- the one clean measurement -- stays byte-identical).
    if require_comparable_span and not fiscal.comparable(cur, prior):
        return None, reasons.Abstention(
            reasons.FISCAL_SPAN_MISMATCH,
            f"the current {plain} quarter covers {fiscal.span_days(cur)} days "
            f"against the year-ago quarter's {fiscal.span_days(prior)} -- a "
            "53rd fiscal week, not a business change", subject)
    if prior["value"] == 0:
        return None, reasons.Abstention(
            reasons.NONPOSITIVE_DENOMINATOR,
            f"the year-ago {plain} figure is zero", subject)
    return (cur["value"] - prior["value"]) / abs(prior["value"]), None


def yoy_at(rows: list[dict], idx: int,
           require_comparable_span: bool = False) -> float | None:
    """Year-over-year growth at position `idx`, matched on elapsed days so
    52/53-week fiscal calendars still find their year-ago quarter.

    `require_comparable_span=False` by default: label.py calls this and the
    outcome label must stay exactly reproducible (the 52/53-week label guard
    was measured at ~0.5% of trips and deliberately NOT applied -- see
    FINDINGS). The gate's diagnose() opts in, because a score is versioned and
    a label is not.
    """
    val, _ = _yoy_explained(rows, idx, "value", require_comparable_span)
    return val


def yoy(norm: dict, metric: str, kind: str = "Q", back: int = 0) -> float | None:
    s = series(norm, metric, kind)
    return yoy_at(s, len(s) - 1 - back)


def ttm(norm: dict, metric: str, back: int = 0) -> float | None:
    """Trailing twelve months, or None. Contiguity-gated -- see derive.ttm."""
    return derive.ttm(series(norm, metric, "Q"), back)


def ttm_end(norm: dict, metric: str, back: int = 0) -> str | None:
    """Period end of the last quarter in that TTM window."""
    rows = series(norm, metric, "Q")
    idx = len(rows) - back - 1
    return rows[idx]["end"] if 0 <= idx < len(rows) else None


def _aligned(balance: dict | None, flow_end: str | None) -> bool:
    """True if a balance-sheet row and a flow window describe the same moment."""
    if not balance or not flow_end:
        return False
    gap = abs((date.fromisoformat(balance["end"]) - date.fromisoformat(flow_end)).days)
    return gap <= STALE_TOLERANCE_DAYS


# ------------------------------------------------------- derived diagnostics


@dataclass
class Diagnostics:
    ticker: str
    cik: str
    period: str | None = None

    revenue_yoy: float | None = None
    revenue_yoy_prior: float | None = None
    revenue_accel: float | None = None
    gross_margin: float | None = None
    gross_margin_delta_yoy: float | None = None
    op_margin: float | None = None
    ocf_yoy: float | None = None

    # quality of earnings
    accrual_ratio: float | None = None
    cash_conversion_gap: float | None = None
    ocf_to_revenue: float | None = None

    # working capital stress
    dso: float | None = None
    dso_delta_yoy: float | None = None
    dio: float | None = None
    dio_delta_yoy: float | None = None
    receivables_vs_revenue: float | None = None
    inventory_vs_revenue: float | None = None

    # forward demand proxy
    deferred_rev_yoy: float | None = None
    deferred_vs_revenue_gap: float | None = None

    # balance sheet
    net_debt: float | None = None
    net_debt_to_ttm_ocf: float | None = None
    dilution_yoy: float | None = None

    # provenance
    derived_fraction: float = 0.0
    excluded_metrics: tuple[str, ...] = ()

    # Why a tracked diagnostic is None: diagnostic name -> reason code from
    # reasons.py, with the human sentence beside it. Populated by diagnose() at
    # the branch that decided the None -- direct instrumentation, not a
    # parallel table that re-derives the cause and can silently disagree with
    # the code it describes. Before this, a filer scored on 2 of 13
    # diagnostics was indistinguishable from one scored on all 13.
    reasons: dict[str, str] = field(default_factory=dict)
    reason_detail: dict[str, str] = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def _row_at_end(norm, metric, end, kind="Q"):
    """The row for one metric at one exact period end, or None."""
    return next((r for r in series(norm, metric, kind) if r["end"] == end), None)


def _gross_profit(norm, back=0):
    """Gross profit for the SAME period as revenue at this offset, or None.

    The old version took a positional index into the gross_profit series and
    returned it if truthy, never checking that its period matched the revenue
    row diagnose() divides it by. Filers routinely stop tagging
    us-gaap:GrossProfit while still tagging Revenues, so the series end at
    different periods: GM at 2015-12-31 paired a 2012 gross profit with 2015
    revenue for a gross margin of -13.6%, and EXPD at 2024-09-30 reached back
    11.5 years. That fabricated MARGIN_COLLAPSE, which is one of the five
    deterioration criteria -- so it invented positives and moved break dates.
    """
    rev = at(norm, "revenue", "Q", back)
    if not rev:
        return None
    gp = _row_at_end(norm, "gross_profit", rev["end"])
    if gp:
        return gp["value"]
    cor = _row_at_end(norm, "cost_of_revenue", rev["end"])
    if cor:
        return rev["value"] - cor["value"]
    return None


def _margin(norm, num_metric):
    """A margin needs numerator and denominator from the same quarter.

    Same defect class as _gross_profit: indexing the numerator series
    positionally paired it with whatever revenue happened to be last.
    """
    rev = at(norm, "revenue")
    if not rev or rev["value"] == 0:
        return None
    num = _row_at_end(norm, num_metric, rev["end"])
    if not num:
        return None
    return num["value"] / rev["value"]


# ------------------------------------------------------ abstention attribution
#
# Each helper answers "why is this input None" at the branch that knows, with
# the most upstream cause first: a filer with no inventory facts is told that,
# never "the TTM was not contiguous". These exist so diagnose() records its
# own refusals -- the alternative, a declarative requirements table maintained
# outside this file, was designed and rejected because it duplicates knowledge
# that lives in these branches and drifts silently when a branch changes.


def _ttm_explained(norm: dict, metric: str) -> tuple[float | None, reasons.Abstention | None]:
    plain = metric.replace("_", " ")
    rows = series(norm, metric, "Q")
    if not rows:
        return None, reasons.Abstention(
            reasons.INPUT_METRIC_ABSENT,
            f"no quarterly {plain} figures in this company's filings", metric)
    val = derive.ttm(rows)
    if val is None:
        return None, reasons.Abstention(
            reasons.TTM_NONCONTIGUOUS,
            f"no four consecutive quarters of {plain} to sum into a trailing "
            "year", metric)
    return val, None


def _pit_why(row: dict | None, metric: str) -> reasons.Abstention:
    plain = metric.replace("_", " ")
    if not row:
        return reasons.Abstention(
            reasons.INPUT_METRIC_ABSENT,
            f"no {plain} figures in this company's filings", metric)
    return reasons.Abstention(
        reasons.NONPOSITIVE_DENOMINATOR, f"{plain} is reported as zero", metric)


def _margin_why(norm: dict, num_metric: str) -> reasons.Abstention:
    plain = num_metric.replace("_", " ")
    rev = at(norm, "revenue")
    if not rev or rev["value"] == 0:
        return _pit_why(rev, "revenue")
    if not series(norm, num_metric, "Q"):
        return reasons.Abstention(
            reasons.INPUT_METRIC_ABSENT,
            f"no quarterly {plain} figures in this company's filings", num_metric)
    return reasons.Abstention(
        reasons.PERIOD_MISALIGNED,
        f"no {plain} figure for the same quarter as the latest revenue",
        num_metric)


def _gross_margin_why(norm: dict) -> reasons.Abstention:
    rev = at(norm, "revenue")
    if not rev or not rev["value"]:
        return _pit_why(rev, "revenue")
    if not series(norm, "gross_profit", "Q") and not series(norm, "cost_of_revenue", "Q"):
        return reasons.Abstention(
            reasons.INPUT_METRIC_ABSENT,
            "neither gross profit nor cost of sales appears in this company's "
            "filings", "gross_profit")
    return reasons.Abstention(
        reasons.PERIOD_MISALIGNED,
        "no gross profit or cost of sales figure for the same quarter as the "
        "latest revenue", "gross_profit")


def diagnose(ticker: str, cik: str, norm: dict) -> Diagnostics:
    d = Diagnostics(ticker=ticker, cik=cik)

    def note(name: str, why: reasons.Abstention | None) -> None:
        # First reason wins: the most upstream cause is the one recorded.
        if why is not None and name not in d.reasons:
            d.reasons[name] = why.code
            d.reason_detail[name] = why.detail

    rev_rows = series(norm, "revenue", "Q")
    rev = rev_rows[-1] if rev_rows else None
    if rev:
        d.period = rev["end"]

    # Gate-side YoY comparisons refuse a 14-week-vs-13-week span (the ~7%
    # calendar artifact); label.py's calls keep the default and stay
    # byte-identical. This changed which quarters evaluate, hence GATE_VERSION.
    d.revenue_yoy, rev_yoy_why = _yoy_explained(
        rev_rows, len(rev_rows) - 1, "revenue", require_comparable_span=True)
    d.revenue_yoy_prior, prior_why = _yoy_explained(
        rev_rows, len(rev_rows) - 2, "revenue", require_comparable_span=True)
    if d.revenue_yoy is not None and d.revenue_yoy_prior is not None:
        d.revenue_accel = d.revenue_yoy - d.revenue_yoy_prior
    else:
        note("revenue_accel", rev_yoy_why or prior_why)

    ocf_rows = series(norm, "operating_cash_flow", "Q")
    d.ocf_yoy, ocf_yoy_why = _yoy_explained(
        ocf_rows, len(ocf_rows) - 1, "operating_cash_flow",
        require_comparable_span=True)
    def_rows = series(norm, "deferred_revenue", "PIT")
    def_now = def_rows[-1] if def_rows else None
    d.deferred_rev_yoy, def_why = _yoy_explained(
        def_rows, len(def_rows) - 1, "deferred_revenue")

    # margins
    gp_now = _gross_profit(norm)
    if gp_now is not None and rev and rev["value"]:
        d.gross_margin = gp_now / rev["value"]
        gp_prior, rev_prior = _gross_profit(norm, back=4), at(norm, "revenue", "Q", 4)
        if gp_prior is not None and rev_prior and rev_prior["value"]:
            d.gross_margin_delta_yoy = d.gross_margin - gp_prior / rev_prior["value"]
    else:
        note("gross_margin", _gross_margin_why(norm))
    d.op_margin = _margin(norm, "operating_income")
    if d.op_margin is None:
        note("op_margin", _margin_why(norm, "operating_income"))

    # quality of earnings -- all gated on a real TTM
    ni_ttm, ni_why = _ttm_explained(norm, "net_income")
    ocf_ttm, ocf_ttm_why = _ttm_explained(norm, "operating_cash_flow")
    rev_ttm, rev_ttm_why = _ttm_explained(norm, "revenue")
    assets = pit(norm, "total_assets")
    if ni_ttm is not None and ocf_ttm is not None and assets and assets["value"]:
        d.accrual_ratio = (ni_ttm - ocf_ttm) / assets["value"]
    else:
        note("accrual_ratio", ni_why or ocf_ttm_why or _pit_why(assets, "total_assets"))
    if ocf_ttm is not None and rev_ttm:
        d.ocf_to_revenue = ocf_ttm / rev_ttm
    else:
        note("ocf_to_revenue",
             ocf_ttm_why or rev_ttm_why or _pit_why({"value": 0}, "revenue"))
    if d.revenue_yoy is not None and d.ocf_yoy is not None:
        d.cash_conversion_gap = d.revenue_yoy - d.ocf_yoy
    else:
        note("cash_conversion_gap", rev_yoy_why or ocf_yoy_why)

    # Working capital. A days-outstanding ratio divides a point-in-time balance
    # by a trailing flow, so the two must describe the same moment. ttm() checks
    # that its four quarters are contiguous but not that they are RECENT, so a
    # stale-but-internally-contiguous block returns a float: FTI at cutoff
    # 2020-08-15 divided a 2020-06-30 inventory balance by a cost_of_revenue TTM
    # ending 2018-06-30 and reported dio = 218 days.
    ar, inv = pit(norm, "receivables"), pit(norm, "inventory")
    if ar and rev_ttm and _aligned(ar, ttm_end(norm, "revenue")):
        d.dso = ar["value"] / rev_ttm * 365
        ar_p, rev_ttm_p = pit(norm, "receivables", 4), ttm(norm, "revenue", 4)
        if ar_p and rev_ttm_p and _aligned(ar_p, ttm_end(norm, "revenue", 4)):
            d.dso_delta_yoy = d.dso - (ar_p["value"] / rev_ttm_p * 365)
    elif not ar:
        note("dso", _pit_why(ar, "receivables"))
    elif rev_ttm is None or not rev_ttm:
        note("dso", rev_ttm_why or _pit_why({"value": 0}, "revenue"))
    else:
        note("dso", reasons.Abstention(
            reasons.PERIOD_MISALIGNED,
            "the receivables balance and the trailing-year revenue it would "
            "be divided by describe different moments", "receivables"))
    cor_ttm, cor_ttm_why = _ttm_explained(norm, "cost_of_revenue")
    if inv and cor_ttm and _aligned(inv, ttm_end(norm, "cost_of_revenue")):
        d.dio = inv["value"] / cor_ttm * 365
        inv_p, cor_ttm_p = pit(norm, "inventory", 4), ttm(norm, "cost_of_revenue", 4)
        if inv_p and cor_ttm_p and _aligned(inv_p, ttm_end(norm, "cost_of_revenue", 4)):
            d.dio_delta_yoy = d.dio - (inv_p["value"] / cor_ttm_p * 365)
    elif not inv:
        note("dio", _pit_why(inv, "inventory"))
    elif cor_ttm is None or not cor_ttm:
        note("dio", cor_ttm_why or _pit_why({"value": 0}, "cost_of_revenue"))
    else:
        note("dio", reasons.Abstention(
            reasons.PERIOD_MISALIGNED,
            "the inventory balance and the trailing-year cost of sales it "
            "would be divided by describe different moments", "inventory"))

    ar_rows = series(norm, "receivables", "PIT")
    ar_yoy, ar_yoy_why = _yoy_explained(ar_rows, len(ar_rows) - 1, "receivables")
    if ar_yoy is not None and d.revenue_yoy is not None:
        d.receivables_vs_revenue = ar_yoy - d.revenue_yoy
    else:
        note("receivables_vs_revenue", ar_yoy_why or rev_yoy_why)
    inv_rows = series(norm, "inventory", "PIT")
    inv_yoy, inv_yoy_why = _yoy_explained(inv_rows, len(inv_rows) - 1, "inventory")
    if inv_yoy is not None and d.revenue_yoy is not None:
        d.inventory_vs_revenue = inv_yoy - d.revenue_yoy
    else:
        note("inventory_vs_revenue", inv_yoy_why or rev_yoy_why)
    # The same stale-balance defect the dio guard above was written for, one
    # diagnostic over: _yoy_explained only checks the 330-400 day gap BETWEEN
    # the two deferred balances, never that the newer one is near the revenue
    # quarter it is subtracted from. WBD at cutoff 2013-08-15 had deferred
    # revenue rows only at 2008-12-31 and 2009-12-31, so a 2009-vs-2008 change
    # of -2.2% was set against revenue growth for the quarter ending
    # 2013-06-30 and published as a 4.26-sigma "forward bookings breaking from
    # trend" -- a break the arithmetic does not describe.
    if d.deferred_rev_yoy is not None and d.revenue_yoy is not None:
        if _aligned(def_now, d.period):
            d.deferred_vs_revenue_gap = d.deferred_rev_yoy - d.revenue_yoy
        else:
            note("deferred_vs_revenue_gap", reasons.Abstention(
                reasons.PERIOD_MISALIGNED,
                "the deferred revenue balance and the revenue quarter it "
                "would be compared against describe different moments",
                "deferred_revenue"))
    else:
        note("deferred_vs_revenue_gap", def_why or rev_yoy_why)

    # balance sheet. pit() returns the NEWEST balance unconditionally, which is
    # not necessarily a recent one: CHH at cutoff 2013-08-15 divided a
    # 2012-06-30 total_debt balance, a full year stale, by a cash-flow year
    # ending 2013-06-30. Both legs are gated the way dso and dio are, and
    # net_debt itself is only formed from two balances that describe the same
    # moment -- a debt figure from one year minus a cash figure from the next
    # is a number no filing contains.
    cash, debt = pit(norm, "cash"), pit(norm, "total_debt")
    ocf_end = ttm_end(norm, "operating_cash_flow")
    if cash and debt and _aligned(cash, ocf_end) and _aligned(debt, ocf_end):
        d.net_debt = debt["value"] - cash["value"]
        if ocf_ttm and ocf_ttm > 0:
            d.net_debt_to_ttm_ocf = d.net_debt / ocf_ttm
        else:
            note("net_debt_to_ttm_ocf", ocf_ttm_why or reasons.Abstention(
                reasons.NONPOSITIVE_DENOMINATOR,
                "cash from operations over the trailing year is zero or "
                "negative, so debt-to-cash has no meaning",
                "operating_cash_flow"))
    elif not cash or not debt:
        note("net_debt_to_ttm_ocf", _pit_why(
            cash if not cash else debt, "cash" if not cash else "total_debt"))
    elif ocf_end is None:
        note("net_debt_to_ttm_ocf", ocf_ttm_why)
    else:
        note("net_debt_to_ttm_ocf", reasons.Abstention(
            reasons.PERIOD_MISALIGNED,
            "the cash and debt balances and the trailing year of operating "
            "cash flow they would be divided by describe different moments",
            "total_debt"))

    # FIX §3: a share-count move this large is a corporate action, not dilution.
    # No span guard here: a weighted-AVERAGE share count over 14 weeks
    # estimates the same quantity as one over 13, so the extra week cancels.
    dil_rows = series(norm, "diluted_shares", "Q")
    dil, dil_why = _yoy_explained(dil_rows, len(dil_rows) - 1, "diluted_shares")
    if dil is not None and abs(dil) > CORPORATE_ACTION_THRESHOLD:
        d.dilution_yoy = None
        note("dilution_yoy", reasons.Abstention(
            reasons.CORPORATE_ACTION,
            f"the share count moved {dil:+.0%} year over year -- a split, "
            "listing or buyout, not gradual dilution", "diluted_shares"))
    else:
        d.dilution_yoy = dil
        note("dilution_yoy", dil_why)

    # provenance rollup: how much of this reading rests on differenced YTD
    # figures rather than directly reported quarters
    tracked = ("revenue", "operating_cash_flow", "net_income", "cost_of_revenue",
               "operating_income")
    flow_rows = [r for m in tracked for r in series(norm, m, "Q")]
    if flow_rows:
        d.derived_fraction = round(
            sum(1 for r in flow_rows if r.get("origin") == "derived") / len(flow_rows), 3
        )
    return d


# ------------------------------------------------------------ peer anomaly

# The one number peers.py builds its sets against. A constant rather than a
# literal inside peer_z so the set builder and the statistic cannot drift on
# what "enough peers" means.
MIN_PEERS = 6


def peer_z(diags: list[Diagnostics], field: str) -> dict[str, float]:
    """Robust cross-sectional z within a peer set. Uses median/MAD so one
    blown-up peer does not swallow the whole distribution.

    UNWIRED, PERMANENTLY, not "until a later phase": a peer overlay on a gate
    that failed its own recall test cannot be evaluated, and suppression can
    only remove fires when fires missed are what failed. peers.py builds and
    measures the sets; nothing consumes them.
    """
    vals = [(d.ticker, getattr(d, field)) for d in diags]
    vals = [(t, v) for t, v in vals if v is not None]
    if len(vals) < MIN_PEERS:
        return {}
    nums = [v for _, v in vals]
    mu = median(nums)
    mad = median([abs(v - mu) for v in nums])
    sd = 1.4826 * mad
    if sd <= 0:
        return {}
    return {t: (v - mu) / sd for t, v in vals}
