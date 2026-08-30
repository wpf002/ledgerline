"""
Universe construction and admission rules.

DECISION (open item #1): pre-2009 regimes are DROPPED, and the regime
requirement is redefined against what XBRL can actually support.

The SEC XBRL mandate phased in by filer size: large accelerated filers with
>$5B float for periods ending after 2009-06-15, remaining large accelerated
filers after 2010-06-15, everyone else after 2011-06-15. So there is no
point-in-time XBRL for the dotcom or GFC regimes, and coverage is not universal
until 2011.

Subtle trap worth naming: `companyfacts` DOES return pre-2011 figures, because
filers include comparative prior periods in later filings. Those facts carry the
LATER filing's `filed` date, so `edgar.as_of()` correctly hides them from any
earlier cutoff -- the system fails safe rather than silently backtesting on data
that did not exist. The consequence is that pre-2011 cutoffs return
`scoreable=False`, not a wrong answer.

Requiring 12 quarters of own history on top of that puts the first genuinely
scoreable cutoffs at roughly 2012 for large accelerated filers and 2014 for the
long tail.

So: the "3 macro regimes" requirement is replaced with "4 regimes drawn from
2011-2025." That window contains plenty of genuinely distinct ones -- see
REGIMES below -- including two that are not broad macro selloffs at all, which
is the more useful test. A gate that only works when the whole market is falling
is a beta detector.

Second admission rule, unrelated to dates: FINANCIALS AND REITS ARE EXCLUDED.
Every diagnostic in the tracked set -- DSO, DIO, inventory, gross margin,
deferred revenue, accruals over assets -- assumes an operating company. Banks,
insurers and REITs either do not tag these concepts or tag them with entirely
different meaning. Drawing 200 controls at random from the Russell 3000 pulls in
roughly 20% financials, which would either be silently unscoreable or scoreable
and meaningless. Neither is acceptable in a control group.
"""
from __future__ import annotations

from datetime import date

from . import edgar, signals

# XBRL mandate phase-in. Facts before this are comparatives inside later
# filings, not contemporaneous disclosures.
XBRL_FLOOR = "2011-06-15"

# Own-history requirement, mirroring signals_v3.MIN_HISTORY.
MIN_HISTORY_QUARTERS = 12

# Earliest cutoff worth attempting for any filer.
EARLIEST_CUTOFF = "2013-01-01"

# SIC ranges excluded from both the positive set and the control group.
#   6000-6499  banks, credit agencies, brokers, insurers
#   6500-6599  real estate operators
#   6798       REITs
#   6700-6799  holding and investment offices, blank-check vehicles
EXCLUDED_SIC_RANGES = ((6000, 6499), (6500, 6599), (6700, 6799))

REGIMES = {
    "2014-16-energy": ("2014-07-01", "2016-06-30",
                       "Oil from ~$100 to ~$26. Shale, services, offshore, mining."),
    "2015-18-retail": ("2015-01-01", "2018-12-31",
                       "Bricks-and-mortar retail and consumer brands losing share."),
    "2017-19-idiosyncratic": ("2017-01-01", "2019-12-31",
                              "No macro tide. Single-name accounting and demand breaks. "
                              "The most informative regime in the set -- a gate that only "
                              "works in a falling market is measuring beta."),
    "2020-covid": ("2020-01-01", "2020-12-31",
                   "Demand shock in both directions. Tests false positives on filers "
                   "whose working capital moved violently and then recovered."),
    "2021-22-growth-unwind": ("2021-01-01", "2022-12-31",
                              "Post-stimulus normalization. The original eight cases."),
    "2023-25-rate-shock": ("2023-01-01", "2025-12-31",
                           "Higher-for-longer. Refinancing walls, CRE, leveraged names."),
}

MIN_REGIMES = 4


def sic_excluded(sic: str | int | None) -> bool:
    if sic in (None, ""):
        return True  # unknown sector is not admissible to a control group
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return True
    return any(lo <= code <= hi for lo, hi in EXCLUDED_SIC_RANGES)


def fetch_sic(cik: str) -> str | None:
    try:
        return edgar.submissions(cik).get("sic")
    except Exception:
        return None


def scoreable_from(norm: dict) -> str | None:
    """The earliest cutoff at which this filer has both contemporaneous XBRL and
    MIN_HISTORY_QUARTERS of its own history. None means never scoreable."""
    rows = [r for r in signals.series(norm, "revenue", "Q")
            if (r.get("filed") or "") >= XBRL_FLOOR]
    if len(rows) < MIN_HISTORY_QUARTERS + 1:
        return None
    return max(rows[MIN_HISTORY_QUARTERS]["filed"], EARLIEST_CUTOFF)


def regime_for(period: str) -> str | None:
    """Which regime a YYYY-MM or YYYY-MM-DD falls in."""
    d = period if len(period) > 7 else f"{period}-15"
    for name, (start, end, _) in REGIMES.items():
        if start <= d <= end:
            return name
    return None


def admit(cik: str, ticker: str, norm: dict, sic: str | None,
          broke: str | None = None) -> tuple[bool, str | None]:
    """Admission gate for a case, positive or control.

    Returns (admitted, reason_if_not). Rejections are logged, not swallowed --
    a silently dropped filer is a survivorship bias with extra steps.
    """
    if sic_excluded(sic):
        return False, f"excluded sector (SIC {sic})"
    if not norm or not norm.get("revenue"):
        return False, "no XBRL revenue facts"

    start = scoreable_from(norm)
    if start is None:
        return False, f"fewer than {MIN_HISTORY_QUARTERS}q of post-mandate XBRL history"

    cov = edgar.coverage_report(norm)
    blocked = [m for m in ("revenue", "operating_cash_flow", "net_income")
               if m in cov and cov[m]["n"] and not cov[m]["scoreable"]]
    if blocked:
        detail = ", ".join(f"{m} {cov[m]['ratio']:.0%}" for m in blocked)
        return False, f"insufficient quarterly coverage: {detail}"

    if broke:
        broke_d = f"{broke}-15" if len(broke) == 7 else broke
        if broke_d <= start:
            return False, (f"break at {broke} precedes first scoreable cutoff {start} -- "
                           "the gate could not have fired in time regardless of merit")
        if regime_for(broke) is None:
            return False, f"break at {broke} falls outside the defined regime windows"
    return True, None


def cutoffs_for(norm: dict, end: str | None = None) -> list[str]:
    """Filing-season checkpoints from this filer's first scoreable date.

    Starting at the filer's own scoreable date rather than a fixed calendar year
    is what makes censoring meaningful: a fire on the first cutoff genuinely
    means "as early as this filer could be assessed," rather than "as early as
    the loop happened to start."
    """
    start = scoreable_from(norm)
    if start is None:
        return []
    end = end or date.today().isoformat()
    out = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        for m in (2, 5, 8, 11):
            c = f"{y}-{m:02d}-15"
            if start <= c <= end:
                out.append(c)
    return out
