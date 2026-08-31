"""
The coverage dashboard: who is assessable at a date, why the rest are not,
and -- the part that did not exist before -- how much of the diagnostic set
each assessable filer actually got.

Why it exists: the filer-level coverage gate was the right fix applied to the
right function, and it LOOKED total because no test ever asked how many
diagnostics a scoreable filer actually evaluated. Measured at 2024-05-15 on a
250-filer sample: 67.6% scoreable, and of those exactly 1 of 169 had all 13
diagnostics evaluated -- median 10, minimum 2, with dilution_yoy absent in
92.3% and gross_margin (the heaviest weight, 0.3818) absent in 24.3%. Those
sample numbers are PROVISIONAL; the first full-universe run of this dashboard
replaces every one of them.

The discipline that matters: this module calls signals_v3.evaluate() rather
than reimplementing the scoreability predicates. A dashboard computing its own
idea of "scoreable" would be measuring a different function than the one that
ships -- the single-code-path rule that made the Phase 0 result mean anything.

None of this measurement may be read as explaining the KILL. "Half the
universe is scored on a fraction of the diagnostic set" is a hypothesis this
dashboard makes measurable for the first time; only a fresh pre-registration
on data that did not exist on 2026-08-30 is entitled to test it.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median

from . import edgar, fiscal, peers, reasons, render, signals_v3, status
from .signals import series

# The achievable coverage ceiling per metric, defaulting to 1.0. Only one
# entry is justified today: AVERAGED_FLOWS correctly refuses to difference a
# weighted-average share count (differencing produced 266 negative share
# counts before that rule existed), so a filer tagging quarterly diluted
# shares in each 10-Q but only an annual figure in the 10-K structurally
# cannot exceed 3 of 4 quarters -- and the global COVERAGE_MIN of 0.90 is
# then applied to a metric whose ceiling is 0.75.
#
# MEASUREMENT ONLY. This table feeds `expected`/`achieved` in the report and
# nothing else: judging diluted_shares against its ceiling would unsuppress
# dilution_yoy in ~92% of the universe and apply a weight (0.0949) fitted on
# the ~8% of tuning rows where the diagnostic existed -- an uninterpretable
# score change that would inevitably be read as an improvement. Measure now;
# act only after a re-measurement under a new pre-registration.
COVERAGE_EXPECTED: dict[str, float] = {"diluted_shares": 0.75}


def expected_for(metric: str) -> float:
    return COVERAGE_EXPECTED.get(metric, 1.0)


@dataclass(frozen=True)
class FilerCoverage:
    """One filer at one date: the verdict's scoreability facts, no score."""

    cik: str
    ticker: str
    as_of: str
    scoreable: bool
    code: str | None            # reasons.* filer-level code, None if scoreable
    detail: str | None          # the sentence evaluate() produced
    metrics: dict               # metric -> coverage entry + expected/achieved
    abstentions: dict           # diagnostic -> reason code
    abstention_detail: dict     # diagnostic -> sentence
    evaluated: tuple[str, ...]  # diagnostics that produced a z
    evaluated_weight: float
    weight_total: float
    can_reach_threshold: bool | None
    derived_fraction: float | None
    fiscal: dict
    peer_level: int | None = None
    peer_n: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Dashboard:
    as_of: str
    generated: str
    gate_status: str
    n_filers: int
    n_scoreable: int
    reasons: dict               # filer-level code -> count ("SCOREABLE" incl.)
    abstentions: dict           # diagnostic -> {code: count}, assessed filers
    missing_share: dict         # diagnostic -> fraction of assessed filers
    evaluated_weight_median: float
    n_cannot_reach_threshold: int
    n_unexplained: int
    fiscal: dict                # calendar kind -> count
    peer_levels: dict           # ladder census over assessable filers
    filers: tuple[FilerCoverage, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def _metric_entries(cov: dict) -> dict:
    """Coverage entries with the structural ceiling beside the raw ratio.
    `achieved` = ratio / expected, capped at 1.0 -- reported, never judged."""
    out = {}
    for m, c in cov.items():
        exp = expected_for(m)
        ratio = c.get("ratio")
        achieved = None if ratio is None else round(min(ratio / exp, 1.0), 3)
        out[m] = {**c, "expected": exp, "achieved": achieved}
    return out


def filer_coverage(cik: str, ticker: str, norm: dict, as_of: str) -> FilerCoverage:
    """One filer's coverage record, from the same evaluate() that ships.

    Every field comes from the SAME truncation. evaluate() truncates internally
    through edgar.as_of, but the fiscal profile used to be read off the full
    normalized dict while the record was stamped with the cutoff -- so a record
    keyed by as_of carried period ends that had not happened yet. BKE at cutoff
    2014-05-15 listed long quarters ending 2018-02-03 and 2024-02-03; at that
    date only 2013-02-02 existed. Across the first 80 watched filers, 78 stored
    profiles differed from the point-in-time one and 26 differed in the
    calendar label itself, which is the 52/53-week census the dashboard reports
    per date.
    """
    res = signals_v3.evaluate(ticker, cik, as_of=as_of, norm=norm)
    scoreable = bool(res.get("scoreable"))
    code = res.get("reason_code")
    if scoreable:
        can_reach: bool | None = True
    elif code == reasons.CANNOT_REACH_THRESHOLD:
        can_reach = False
    else:
        can_reach = None  # never got far enough for the question to arise
    return FilerCoverage(
        cik=cik,
        ticker=ticker,
        as_of=as_of,
        scoreable=scoreable,
        code=None if scoreable else (code or reasons.UNEXPLAINED),
        detail=res.get("reason"),
        metrics=_metric_entries(res.get("coverage", {})),
        abstentions=dict(res.get("abstentions", {})),
        abstention_detail=dict(res.get("abstention_detail", {})),
        evaluated=tuple(sorted(res.get("z", {}))),
        evaluated_weight=res.get("evaluated_weight", 0.0),
        weight_total=res.get("weight_total", signals_v3.WEIGHT_TOTAL),
        can_reach_threshold=can_reach,
        derived_fraction=res.get("derived_fraction"),
        fiscal=fiscal.profile(series(edgar.as_of(norm, as_of),
                                     "revenue", "Q")).as_dict(),
    )


def build(as_of: str | None = None, tickers: dict[str, str] | None = None,
          normalizer=None, sic_map: dict[str, str | None] | None = None,
          limit: int | None = None, progress=None) -> Dashboard:
    """The dashboard for one date. Two passes, because peer sets must be built
    from the filers assessable AT the cutoff -- membership from filers that
    only became assessable later is survivorship selection.

    Needs a warm facts cache: cold, this is one SEC request per filer at the
    polite throttle. `limit` exists for exactly that.
    """
    cutoff = as_of or date.today().isoformat()
    if tickers is None:
        tickers = {cik: v["ticker"] for cik, v in edgar.universe().items()}
    if sic_map is None:
        sic_map = edgar.sic_map()
    normalizer = normalizer or edgar.normalize

    items = sorted(tickers.items(), key=lambda kv: kv[1])
    if limit:
        items = items[:limit]

    fcs: list[FilerCoverage] = []
    for i, (cik, ticker) in enumerate(items, 1):
        fcs.append(filer_coverage(cik, ticker, normalizer(cik), cutoff))
        if progress:
            progress(i, len(items))

    # Pass two: peer sets over the assessable-set only.
    scoreable_ciks = {fc.cik for fc in fcs if fc.scoreable}
    psets = peers.peer_sets(
        {cik: sic_map.get(cik) for cik, _ in items}, scoreable_ciks)
    fcs = [
        dataclasses.replace(fc, peer_level=psets[fc.cik].level,
                            peer_n=psets[fc.cik].n())
        if fc.cik in psets else fc
        for fc in fcs
    ]

    # Aggregations are over CODES only; detail sentences travel on the
    # per-filer records and never become a histogram key.
    reason_counts: dict[str, int] = {}
    for fc in fcs:
        key = "SCOREABLE" if fc.scoreable else (fc.code or reasons.UNEXPLAINED)
        reason_counts[key] = reason_counts.get(key, 0) + 1

    # Per-diagnostic histogram over ASSESSED filers: those whose verdict got
    # far enough to account for every tracked diagnostic (scoreable, plus the
    # structural abstentions, which carry the same accounting).
    assessed = [fc for fc in fcs
                if fc.scoreable or fc.code == reasons.CANNOT_REACH_THRESHOLD]
    abst: dict[str, dict[str, int]] = {}
    for fc in assessed:
        for diag, code_ in fc.abstentions.items():
            abst.setdefault(diag, {})
            abst[diag][code_] = abst[diag].get(code_, 0) + 1
    missing_share = {
        diag: round(sum(counts.values()) / len(assessed), 3)
        for diag, counts in abst.items()
    } if assessed else {}

    weights = [fc.evaluated_weight for fc in fcs if fc.scoreable]
    fiscal_counts: dict[str, int] = {}
    for fc in fcs:
        kind = fc.fiscal.get("calendar", fiscal.UNKNOWN)
        fiscal_counts[kind] = fiscal_counts.get(kind, 0) + 1

    return Dashboard(
        as_of=cutoff,
        generated=date.today().isoformat(),
        gate_status=status.GATE_STATUS,
        n_filers=len(fcs),
        n_scoreable=len(scoreable_ciks),
        reasons=reason_counts,
        abstentions=abst,
        missing_share=missing_share,
        evaluated_weight_median=round(median(weights), 4) if weights else 0.0,
        n_cannot_reach_threshold=reason_counts.get(
            reasons.CANNOT_REACH_THRESHOLD, 0),
        n_unexplained=sum(
            1 for fc in assessed for c in fc.abstentions.values()
            if c == reasons.UNEXPLAINED),
        fiscal=fiscal_counts,
        peer_levels=peers.ladder_census(
            {cik: ps for cik, ps in psets.items()
             if cik in scoreable_ciks}) if psets else {},
        filers=tuple(fcs),
    )


def persist(dash: Dashboard) -> int:
    """Write coverage_pit and scoreability rows for every filer on the
    dashboard. Both tables key on (cik, as_of, ...), so re-running a date
    corrects it rather than accumulating."""
    rows = []
    n = 0
    for fc in dash.filers:
        if fc.metrics:
            n += edgar.persist_coverage(fc.cik, fc.as_of, fc.metrics)
        rows.append({
            "cik": fc.cik, "as_of": fc.as_of, "ticker": fc.ticker,
            "scoreable": fc.scoreable, "code": fc.code, "detail": fc.detail,
            "n_evaluated": len(fc.evaluated),
            "n_tracked": len(signals_v3.TRACKED),
            "evaluated_weight": fc.evaluated_weight,
            "weight_total": fc.weight_total,
            "can_reach_threshold": fc.can_reach_threshold,
            "abstentions": fc.abstentions,
            "derived_fraction": fc.derived_fraction,
            "fiscal_calendar": fc.fiscal.get("calendar"),
            "peer_level": fc.peer_level, "peer_n": fc.peer_n,
        })
    n += edgar.persist_scoreability(rows)
    return n


# ------------------------------------------------------------------ rendering
# Plain language per docs/VOICE.md: short names from render.PLAIN, sentences
# from the recorded details, every number beside the bar it is judged against.


def _diag_name(diag: str) -> str:
    short, _ = render.PLAIN.get(diag, (diag.replace("_", " "), ""))
    return short


def render_text(dash: Dashboard, top: int = 12) -> str:
    lines: list[str] = []
    lines.append(
        f"Of the {dash.n_filers} companies checked at {dash.as_of}, "
        f"{dash.n_scoreable} can be assessed.")
    others = {k: v for k, v in dash.reasons.items() if k != "SCOREABLE"}
    if others:
        lines.append("")
        lines.append("Why the rest cannot be:")
        for code, count in sorted(others.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:4}  " + reasons.TEXT.get(code, code))
    if dash.missing_share:
        lines.append("")
        lines.append("Among the assessable companies, the measures most often "
                     "unavailable:")
        ranked = sorted(dash.missing_share.items(), key=lambda kv: -kv[1])[:top]
        for diag, share in ranked:
            counts = dash.abstentions.get(diag, {})
            top_code = max(counts, key=lambda c: counts[c]) if counts else None
            why = reasons.TEXT.get(top_code, "") if top_code else ""
            lines.append(f"  {_diag_name(diag):22} missing for {share:.0%}"
                         + (f" -- mostly: {why[:1].lower()}{why[1:]}" if why else ""))
    if dash.n_scoreable:
        frac = (dash.evaluated_weight_median /
                signals_v3.WEIGHT_TOTAL if signals_v3.WEIGHT_TOTAL else 0)
        lines.append("")
        lines.append(
            f"The typical assessable company is judged on measures carrying "
            f"{frac:.0%} of the full weight, so its highest possible score is "
            f"compressed to roughly {frac:.0%} of a fully-covered company's.")
    if dash.n_cannot_reach_threshold:
        n = dash.n_cannot_reach_threshold
        lines.append(
            f"{n} compan{'y' if n == 1 else 'ies'} cannot reach the flag "
            "threshold at all on the measures available -- marked 'cannot "
            "assess', never scored 0.")
    if dash.n_unexplained:
        lines.append(
            f"{dash.n_unexplained} refusals had no recorded reason -- a gap in "
            "the reason taxonomy itself; it is counted here so it gets fixed.")
    week = dash.fiscal.get(fiscal.WEEK_52_53, 0)
    if week:
        lines.append("")
        lines.append(
            f"{week} compan{'y' if week == 1 else 'ies'} use a 52/53-week "
            "fiscal calendar; comparisons across their occasional 14-week "
            "quarter are refused rather than reported as a change.")
    if dash.peer_levels:
        pl = dash.peer_levels
        lines.append("")
        lines.append(
            "Peer groups (measured only -- nothing uses them): "
            f"{pl.get('4', 0)} companies have enough peers within their exact "
            f"industry, {pl.get('3', 0)} only at the broader group, "
            f"{pl.get('2', 0)} only at the sector, and "
            f"{pl.get('none', 0) + pl.get('unknown_sector', 0)} have no usable "
            "group. Industry codes are today's, not historical.")
    return "\n".join(lines)


def render_filer(fc: FilerCoverage) -> str:
    """One company, full explanation -- for `ledgerline check --ticker`."""
    lines: list[str] = []
    lines.append(f"{fc.ticker} at {fc.as_of}")
    lines.append("")
    if not fc.scoreable:
        lines.append("CANNOT ASSESS.  " + render.plain_reason(fc.detail))
    else:
        n_eval, n_tracked = len(fc.evaluated), len(signals_v3.TRACKED)
        frac = fc.evaluated_weight / fc.weight_total if fc.weight_total else 0
        lines.append(
            f"READY.  {n_eval} of {n_tracked} measures computable, together "
            f"carrying {frac:.0%} of the full weight -- this company's highest "
            f"possible score is about {frac:.0%} of a fully-covered one's.")
    if fc.abstentions:
        lines.append("")
        lines.append("Measures that cannot be computed, and why:")
        for diag in sorted(fc.abstentions):
            detail = fc.abstention_detail.get(
                diag, reasons.TEXT.get(fc.abstentions[diag], ""))
            lines.append(f"  {_diag_name(diag):22} {detail}")
    if fc.metrics:
        lines.append("")
        lines.append("Underlying figures (share of quarters each appears in):")
        for m in sorted(fc.metrics):
            c = fc.metrics[m]
            note = ""
            if expected_for(m) < 1.0:
                note = (f"  (best possible for most filers is "
                        f"{expected_for(m):.0%}: the yearly filing carries only "
                        "an annual share count)")
            lines.append(f"  {render.plain_metric(m):22} {c['ratio']:.0%} of "
                         "quarters" + note)
    cal = fc.fiscal.get("calendar")
    if cal == fiscal.WEEK_52_53:
        lines.append("")
        lines.append("This company uses a 52/53-week fiscal calendar; the "
                     "occasional 14-week quarter is never compared against a "
                     "13-week one.")
    if fc.peer_level is not None:
        depth = {4: "its exact industry", 3: "its broader industry group",
                 2: "its sector"}[fc.peer_level]
        lines.append("")
        lines.append(f"Peer group: {fc.peer_n} comparable companies within "
                     f"{depth} (measured only -- nothing uses peer groups; "
                     "industry codes are today's, not historical).")
    return "\n".join(lines)


def render_markdown(dash: Dashboard) -> str:
    lines = [
        f"# Coverage at {dash.as_of}",
        "",
        "> " + status.banner().replace("\n", "\n> "),
        "",
        render_text(dash),
        "",
        "## Every company",
        "",
        "| Company | Assessable | Measures computed | Weight carried | Why not |",
        "|---|---|---|---|---|",
    ]
    for fc in sorted(dash.filers, key=lambda f: f.ticker):
        why = "" if fc.scoreable else reasons.TEXT.get(fc.code or "", fc.code or "")
        lines.append(
            f"| {fc.ticker} | {'yes' if fc.scoreable else 'no'} "
            f"| {len(fc.evaluated)} of {len(signals_v3.TRACKED)} "
            f"| {fc.evaluated_weight:g} of {fc.weight_total:g} | {why} |")
    return "\n".join(lines) + "\n"


def write(dash: Dashboard, out_dir: str | None = None) -> tuple[str, str]:
    """reports/coverage_<as_of>.json (machine) and .md (human). The JSON is
    stamped with the frozen Phase 0 verdict like every other emitted payload."""
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, f"coverage_{dash.as_of}.json")
    mpath = os.path.join(out_dir, f"coverage_{dash.as_of}.md")
    with open(jpath, "w") as fh:
        json.dump(status.stamp(dash.as_dict()), fh, indent=2)
        fh.write("\n")
    with open(mpath, "w") as fh:
        fh.write(render_markdown(dash))
    return jpath, mpath
