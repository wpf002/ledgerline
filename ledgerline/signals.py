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
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from statistics import median

from . import derive

# A YoY move larger than this in share count is a corporate action -- split,
# reverse split, IPO, exchange offer -- not economic dilution.
CORPORATE_ACTION_THRESHOLD = 0.50


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


def yoy_at(rows: list[dict], idx: int) -> float | None:
    """Year-over-year growth at position `idx`, matched on calendar month to
    avoid seasonality. Tolerates +/-1 month for 52/53-week fiscal calendars."""
    if not 0 <= idx < len(rows):
        return None
    cur = rows[idx]
    cur_d = date.fromisoformat(cur["end"])
    prior = None
    for r in reversed(rows[:idx]):
        d = date.fromisoformat(r["end"])
        if d.year == cur_d.year - 1 and abs(d.month - cur_d.month) <= 1:
            prior = r
            break
    if not prior or prior["value"] == 0:
        return None
    return (cur["value"] - prior["value"]) / abs(prior["value"])


def yoy(norm: dict, metric: str, kind: str = "Q", back: int = 0) -> float | None:
    s = series(norm, metric, kind)
    return yoy_at(s, len(s) - 1 - back)


def ttm(norm: dict, metric: str, back: int = 0) -> float | None:
    """Trailing twelve months, or None. Contiguity-gated -- see derive.ttm."""
    return derive.ttm(series(norm, metric, "Q"), back)


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

    def as_dict(self):
        return asdict(self)


def _gross_profit(norm, back=0):
    gp = at(norm, "gross_profit", "Q", back)
    if gp:
        return gp["value"]
    rev, cor = at(norm, "revenue", "Q", back), at(norm, "cost_of_revenue", "Q", back)
    if rev and cor:
        return rev["value"] - cor["value"]
    return None


def _margin(norm, num_metric):
    rev, num = at(norm, "revenue"), at(norm, num_metric)
    if not rev or not num or rev["value"] == 0:
        return None
    return num["value"] / rev["value"]


def diagnose(ticker: str, cik: str, norm: dict) -> Diagnostics:
    d = Diagnostics(ticker=ticker, cik=cik)
    rev_rows = series(norm, "revenue", "Q")
    rev = rev_rows[-1] if rev_rows else None
    if rev:
        d.period = rev["end"]

    d.revenue_yoy = yoy_at(rev_rows, len(rev_rows) - 1)
    d.revenue_yoy_prior = yoy_at(rev_rows, len(rev_rows) - 2)
    if d.revenue_yoy is not None and d.revenue_yoy_prior is not None:
        d.revenue_accel = d.revenue_yoy - d.revenue_yoy_prior

    d.ocf_yoy = yoy(norm, "operating_cash_flow", "Q")
    d.deferred_rev_yoy = yoy(norm, "deferred_revenue", "PIT")

    # margins
    gp_now = _gross_profit(norm)
    if gp_now is not None and rev and rev["value"]:
        d.gross_margin = gp_now / rev["value"]
        gp_prior, rev_prior = _gross_profit(norm, back=4), at(norm, "revenue", "Q", 4)
        if gp_prior is not None and rev_prior and rev_prior["value"]:
            d.gross_margin_delta_yoy = d.gross_margin - gp_prior / rev_prior["value"]
    d.op_margin = _margin(norm, "operating_income")

    # quality of earnings -- all gated on a real TTM
    ni_ttm, ocf_ttm, rev_ttm = (
        ttm(norm, "net_income"),
        ttm(norm, "operating_cash_flow"),
        ttm(norm, "revenue"),
    )
    assets = pit(norm, "total_assets")
    if ni_ttm is not None and ocf_ttm is not None and assets and assets["value"]:
        d.accrual_ratio = (ni_ttm - ocf_ttm) / assets["value"]
    if ocf_ttm is not None and rev_ttm:
        d.ocf_to_revenue = ocf_ttm / rev_ttm
    if d.revenue_yoy is not None and d.ocf_yoy is not None:
        d.cash_conversion_gap = d.revenue_yoy - d.ocf_yoy

    # working capital
    ar, inv = pit(norm, "receivables"), pit(norm, "inventory")
    if ar and rev_ttm:
        d.dso = ar["value"] / rev_ttm * 365
        ar_p, rev_ttm_p = pit(norm, "receivables", 4), ttm(norm, "revenue", 4)
        if ar_p and rev_ttm_p:
            d.dso_delta_yoy = d.dso - (ar_p["value"] / rev_ttm_p * 365)
    cor_ttm = ttm(norm, "cost_of_revenue")
    if inv and cor_ttm:
        d.dio = inv["value"] / cor_ttm * 365
        inv_p, cor_ttm_p = pit(norm, "inventory", 4), ttm(norm, "cost_of_revenue", 4)
        if inv_p and cor_ttm_p:
            d.dio_delta_yoy = d.dio - (inv_p["value"] / cor_ttm_p * 365)

    ar_yoy = yoy(norm, "receivables", "PIT")
    if ar_yoy is not None and d.revenue_yoy is not None:
        d.receivables_vs_revenue = ar_yoy - d.revenue_yoy
    inv_yoy = yoy(norm, "inventory", "PIT")
    if inv_yoy is not None and d.revenue_yoy is not None:
        d.inventory_vs_revenue = inv_yoy - d.revenue_yoy
    if d.deferred_rev_yoy is not None and d.revenue_yoy is not None:
        d.deferred_vs_revenue_gap = d.deferred_rev_yoy - d.revenue_yoy

    # balance sheet
    cash, debt = pit(norm, "cash"), pit(norm, "total_debt")
    if cash and debt:
        d.net_debt = debt["value"] - cash["value"]
        if ocf_ttm and ocf_ttm > 0:
            d.net_debt_to_ttm_ocf = d.net_debt / ocf_ttm

    # FIX §3: a share-count move this large is a corporate action, not dilution
    dil = yoy(norm, "diluted_shares", "Q")
    d.dilution_yoy = None if dil is not None and abs(dil) > CORPORATE_ACTION_THRESHOLD else dil

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


def peer_z(diags: list[Diagnostics], field: str) -> dict[str, float]:
    """Robust cross-sectional z within a peer set. Uses median/MAD so one
    blown-up peer does not swallow the whole distribution."""
    vals = [(d.ticker, getattr(d, field)) for d in diags]
    vals = [(t, v) for t, v in vals if v is not None]
    if len(vals) < 6:
        return {}
    nums = [v for _, v in vals]
    mu = median(nums)
    mad = median([abs(v - mu) for v in nums])
    sd = 1.4826 * mad
    if sd <= 0:
        return {}
    return {t: (v - mu) / sd for t, v in vals}
