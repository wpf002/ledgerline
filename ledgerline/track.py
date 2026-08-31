"""
The track record: every persisted assessment scored forward against what the
company actually filed next, kept beside the numbers the detector posted when
it failed its own test.

Why this module exists: the 2026-08-30 holdout run was a one-shot measurement
of a rule that can never be re-run, and the KILL it returned is frozen in
status.py. What that measurement cannot say is how the detector behaves on
data that did not exist yet. This module accrues that answer the slow way --
each saved signal is judged at +1, +2 and +4 quarters against the same
deterioration label the validation used, on facts truncated to the resolution
date -- and reports it without ever grading it. Grading was the sealed test's
job, and the sealed test already answered.

The decisions that carry the honesty:

  * A resolution is a VINTAGE, not a fact. The label is computed from later
    filings and a restatement can flip it (RESTATEMENT is itself one of the
    five criteria), so signal_scores is append-only on resolved_at: a flipped
    outcome is a second row beside the first, never an overwrite. resolve()
    truncates facts with edgar.as_of() before labeling, so every past
    resolution is reproducible forever from the vintage store.
  * PENDING is a return value, never a stored row. A signal whose forward
    window is shorter than the horizon writes nothing; counting it CLEAN
    would manufacture precision out of the calendar (the same shape of defect
    as FINDINGS 2's sum(series[-4:])).
  * Levels of measurement never cross. The holdout 0.287 is a per-CASE hit
    rate with censoring and a 24-month creditable-lead cap; the only
    per-QUARTER recall that exists is the tuning 0.1396. Live per-quarter
    numbers are compared to the tuning floor only, live case-level numbers to
    the holdout only, and live_stats() must reproduce harness.verdict()'s
    arithmetic number-for-number -- pinned by a test, because "measured on
    one definition" is otherwise a docstring claim.
  * The two false-positive denominators are named apart. Phase 0's 0.0383
    counts fires among quarters of filers that NEVER deteriorated; the naive
    live analogue counts fires among quarters not FOLLOWED by deterioration,
    which includes the quiet quarters of filers that broke later. Both are
    computed, under distinct keys, and only fpr_per_quarter_control_filer is
    marked comparable to the reference -- with its bias direction stated,
    since over a short window its control set still holds filers that simply
    have not broken yet.
  * The Phase 0 numbers are a FLOOR a revision must beat, never a grade being
    defended. monitor() says INSUFFICIENT below 60 resolved deteriorating
    quarters (at the tuning rate, roughly where a halving first becomes
    distinguishable from noise), and it never retunes and never pulls --
    auto-retuning against live outcomes would overfit the only unspent data
    that exists, so a decaying gate stays live until a human reads the report.
    That is a stated operating decision, not an accident.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from statistics import median
from typing import Any

from . import edgar, emit, label, reliability, signals_v3, status
from .validate import harness

HORIZONS: tuple[int, ...] = (1, 2, 4)

# A quarter assessed at T cannot resolve at horizon h before roughly
# T + h*91 days of periods plus a filing lag. A pure cost gate: skipping a
# signal this young can never change an outcome, because label() would return
# an under-filled window (PENDING) anyway.
RESOLUTION_LAG_DAYS = 120

# Resolved deteriorating quarters before monitor() will compare anything.
# Derivation: at the tuning per-quarter recall of 0.1396, n=60 with an
# observed halving (~0.07) gives a Wilson upper bound near the floor -- below
# 60 the comparison is noise and saying so is the honest output.
MIN_RESOLVED_DETERIORATING = 60

# Sources that constitute the live record, versus the tuning-split replay.
# The two are never pooled: replayed quarters are already spent and prove
# nothing about behaviour on unseen data.
LIVE_SOURCES: tuple[str, ...] = ("scan", "score", "emit")
BACKFILL_SOURCES: tuple[str, ...] = ("replay",)


def label_rule(horizon_q: int) -> str:
    """The rule string a resolution row carries. Only horizon 4 is the
    pre-registered rule; the string differs at every other horizon so a +1q
    recall can never be silently compared to the +4q reference numbers."""
    rule = f"fundamental_deterioration_2of5_within_{horizon_q}q"
    if horizon_q == label.HORIZON_QUARTERS and rule != harness.PREREG["label"]:
        raise RuntimeError(
            "the horizon-4 label rule no longer matches prereg.json -- the "
            "resolution loop would be scoring against a different experiment "
            "than the one that was pre-registered"
        )
    return rule


def earliest_resolvable(as_of: str, horizon_q: int) -> str:
    """First date a signal could possibly resolve at this horizon: the window
    must have elapsed AND the deciding filings must have had time to arrive."""
    start = date.fromisoformat(as_of)
    return (start + timedelta(days=horizon_q * 91 + RESOLUTION_LAG_DAYS)).isoformat()


def _fired(row: dict) -> bool:
    return row.get("score") is not None and row["score"] >= signals_v3.THRESHOLD


# ------------------------------------------------------------ the resolver


def _truncate_vintages(snap: dict, cutoff: str) -> dict:
    """Trim each row's vintage list to vintages filed by the cutoff.

    edgar.as_of() picks the right VALUE per row but deliberately keeps the
    full vintage list attached (its other consumers want the history).
    label._restatement scans that list for /A forms, so without this trim an
    amendment filed AFTER the resolution date would trip the RESTATEMENT
    criterion in a resolution computed "as of" a day before the amendment
    existed -- lookahead in the one loop whose whole job is knowing what was
    knowable when. Found by the reproducibility test, not by inspection.
    """
    return {
        metric: [{**r, "vintages": [v for v in r.get("vintages", [])
                                    if (v.get("filed") or "") <= cutoff]}
                 for r in rows]
        for metric, rows in snap.items()
    }


def resolve_signal(row: dict, horizon_q: int, resolution_date: str,
                   norm: dict) -> dict:
    """Judge one signal at one horizon, using only facts a reader could have
    seen on the resolution date.

    edgar.as_of() -- the codebase's ONLY truncation primitive -- rewinds the
    vintage store to the resolution date before label() runs, which gives two
    properties at once: the resolution is what could have been computed that
    day, and re-running the loop reproduces every earlier resolution exactly,
    restatements included.
    """
    snap = _truncate_vintages(edgar.as_of(norm, resolution_date),
                              resolution_date)
    lbl = label.label(row.get("ticker") or "", row["cik"], snap,
                      as_of=row["as_of"], horizon=horizon_q)
    if lbl.deteriorated:
        outcome = "DETERIORATED"
    elif lbl.n_quarters_observed >= horizon_q:
        outcome = "CLEAN"
    else:
        # The window is under-filled and nothing tripped in the part that
        # exists. Not CLEAN: the deciding filings have not arrived.
        outcome = "PENDING"
    return {
        "signal_id": row["signal_id"],
        "horizon_q": horizon_q,
        "resolved_at": resolution_date,
        "outcome": outcome,
        "event_period": lbl.event_period,
        "n_quarters_observed": lbl.n_quarters_observed,
        "criteria": lbl.criteria,
        "label_rule": label_rule(horizon_q),
    }


def resolve(as_of: str | None = None, gate_version: str | None = None,
            horizons: tuple[int, ...] = HORIZONS,
            normalizer: Callable[[str], dict] | None = None,
            conn: sqlite3.Connection | None = None) -> dict:
    """The forward-scoring loop. Judges every scoreable persisted signal at
    each horizon and writes TERMINAL outcomes only.

    Idempotent: an unchanged outcome writes nothing, so a daily re-run on
    quiet data is a no-op. A changed outcome -- a restatement flipped the
    label -- APPENDS a second row, counted as revised; the original row is
    history and stays.
    """
    as_of = as_of or date.today().isoformat()
    gate = gate_version or emit.gate_version()
    normalizer = normalizer or edgar.normalize
    counts = {"resolved": 0, "revised": 0, "unchanged": 0, "pending": 0,
              "immature": 0}
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    # normalize() is the expensive step; memoized per run, per filer.
    norms: dict[str, dict] = {}
    try:
        sigs = conn.execute(
            "SELECT signal_id, cik, ticker, as_of FROM signals "
            "WHERE scoreable = 1 AND gate_version = ? ORDER BY cik, as_of",
            (gate,)).fetchall()
        for signal_id, cik, ticker, sig_as_of in sigs:
            for h in horizons:
                if as_of < earliest_resolvable(sig_as_of, h):
                    counts["immature"] += 1
                    continue
                if cik not in norms:
                    norms[cik] = normalizer(cik)
                res = resolve_signal(
                    {"signal_id": signal_id, "cik": cik, "ticker": ticker,
                     "as_of": sig_as_of}, h, as_of, norms[cik])
                if res["outcome"] == "PENDING":
                    counts["pending"] += 1
                    continue
                prior = conn.execute(
                    "SELECT outcome, event_period FROM signal_scores "
                    "WHERE signal_id = ? AND horizon_q = ? "
                    "ORDER BY resolved_at DESC LIMIT 1",
                    (signal_id, h)).fetchone()
                if prior and (prior[0], prior[1]) == (res["outcome"],
                                                      res["event_period"]):
                    counts["unchanged"] += 1
                    continue
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO signal_scores (signal_id, "
                        "horizon_q, resolved_at, outcome, event_period, "
                        "n_quarters_observed, criteria, label_rule) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (res["signal_id"], res["horizon_q"],
                         res["resolved_at"], res["outcome"],
                         res["event_period"], res["n_quarters_observed"],
                         json.dumps(res["criteria"]), res["label_rule"]))
                counts["revised" if prior else "resolved"] += 1
    finally:
        if own:
            conn.close()
    return counts


# ------------------------------------------------------------- the readers


def resolutions(gate_version: str, horizon_q: int, *,
                revisions: str = "first",
                conn: sqlite3.Connection | None = None) -> list[dict]:
    """Resolved signals at one horizon, joined to what the gate said.

    revisions='first' is the default reporting view: the outcome as it was
    FIRST known, which is the honest basis for "what did forward scoring say"
    (a later restatement is real information, but re-grading history with it
    is lookahead). 'latest' and 'all' exist for auditing; 'all' rows carry a
    `revision` index so a flip is visible as a flip.
    """
    if revisions not in ("first", "latest", "all"):
        raise ValueError("revisions must be 'first', 'latest' or 'all'")
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        rows = conn.execute(
            "SELECT s.signal_id, s.cik, s.ticker, s.as_of, s.score, "
            "s.gated_in, s.source, r.resolved_at, r.outcome, r.event_period, "
            "r.n_quarters_observed, r.label_rule "
            "FROM signal_scores r JOIN signals s ON s.signal_id = r.signal_id "
            "WHERE s.gate_version = ? AND r.horizon_q = ? "
            "ORDER BY s.cik, s.as_of, r.resolved_at",
            (gate_version, horizon_q)).fetchall()
    finally:
        if own:
            conn.close()
    cols = ("signal_id", "cik", "ticker", "as_of", "score", "gated_in",
            "source", "resolved_at", "outcome", "event_period",
            "n_quarters_observed", "label_rule")
    dicts = [dict(zip(cols, r, strict=True)) for r in rows]
    if revisions == "all":
        seen: dict[str, int] = {}
        for d in dicts:
            d["revision"] = seen.get(d["signal_id"], 0)
            seen[d["signal_id"]] = d["revision"] + 1
        return dicts
    keep: dict[str, dict] = {}
    for d in dicts:  # ordered by resolved_at within each signal
        if revisions == "latest" or d["signal_id"] not in keep:
            keep[d["signal_id"]] = d
    return sorted(keep.values(), key=lambda d: (d["cik"], d["as_of"]))


def pending(gate_version: str | None = None, as_of: str | None = None,
            horizons: tuple[int, ...] = HORIZONS,
            conn: sqlite3.Connection | None = None) -> list[dict]:
    """Signals with no terminal outcome yet, with the earliest date each
    could resolve -- so a thin track record is read as young, not bad."""
    gate = gate_version or emit.gate_version()
    as_of = as_of or date.today().isoformat()
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    try:
        rows = conn.execute(
            "SELECT s.signal_id, s.cik, s.ticker, s.as_of, s.source "
            "FROM signals s WHERE s.scoreable = 1 AND s.gate_version = ? "
            "ORDER BY s.as_of, s.cik", (gate,)).fetchall()
        resolved = {
            (sid, h) for sid, h in conn.execute(
                "SELECT r.signal_id, r.horizon_q FROM signal_scores r "
                "JOIN signals s ON s.signal_id = r.signal_id "
                "WHERE s.gate_version = ?", (gate,)).fetchall()
        }
    finally:
        if own:
            conn.close()
    out = []
    for sid, cik, ticker, sig_as_of, source in rows:
        for h in horizons:
            if (sid, h) in resolved:
                continue
            out.append({
                "signal_id": sid, "cik": cik, "ticker": ticker,
                "as_of": sig_as_of, "source": source, "horizon_q": h,
                "earliest_resolvable": earliest_resolvable(sig_as_of, h),
            })
    return out


# ------------------------------------------------- per-quarter measurement


def quarter_stats(rows: list[dict]) -> dict:
    """Per-quarter rates over resolved rows, every proportion carrying its
    Wilson interval and the reference it may -- or must not -- be compared to.

    Wilson rather than a bare rate because live counts are small and start at
    zero fires, where a point estimate reads as certainty.
    """
    det = [r for r in rows if r["outcome"] == "DETERIORATED"]
    clean = [r for r in rows if r["outcome"] == "CLEAN"]
    det_ciks = {r["cik"] for r in det}
    # Filers with no resolved deterioration anywhere in this row set -- the
    # closest live analogue of Phase 0's never-deteriorated controls.
    control = [r for r in clean if r["cik"] not in det_ciks]
    k_det = sum(1 for r in det if _fired(r))
    k_clean = sum(1 for r in clean if _fired(r))
    k_ctrl = sum(1 for r in control if _fired(r))

    def block(k: int, n: int) -> dict[str, Any]:
        ci = reliability.wilson(k, n)
        return {
            "fired": k, "quarters": n,
            "value": round(k / n, 4) if n else None,
            "wilson_low": round(ci[0], 4) if ci else None,
            "wilson_high": round(ci[1], 4) if ci else None,
        }

    recall = block(k_det, len(det))
    # The only per-quarter recall reference that exists is the TUNING 0.1396;
    # no holdout per-quarter recall was ever computed (calibrate.build_dataset
    # refuses the holdout by design) and none can be now. The holdout 0.287 is
    # per-case and must never appear beside a per-quarter rate.
    recall["level"] = "per-quarter"
    recall["floor"] = {
        "value": _tuning_floor()["recall_per_quarter"],
        "split": "tuning", "level": "per-quarter",
        "meaning": "the failed detector's own tuning-split rate -- a floor a "
                   "revision must beat, never a grade being defended",
    }
    fpr_clean = block(k_clean, len(clean))
    fpr_clean["comparable_to_reference"] = False
    fpr_clean["note"] = (
        "denominator: quarters not followed by deterioration, INCLUDING the "
        "quiet quarters of filers that broke later -- not comparable to the "
        "frozen 0.0383, whose controls never deteriorated at all"
    )
    fpr_ctrl = block(k_ctrl, len(control))
    fpr_ctrl["comparable_to_reference"] = True
    fpr_ctrl["note"] = (
        "denominator: quarters of filers with no resolved deterioration -- "
        "the analogue of the frozen control-filer 0.0383, biased DOWNWARD "
        "over a short window because its control set still contains filers "
        "that simply have not broken yet"
    )
    return {
        "n_resolved": len(rows),
        "n_deteriorating_quarters": len(det),
        "n_clean_quarters": len(clean),
        "n_control_filer_quarters": len(control),
        "recall_per_quarter": recall,
        "fpr_per_quarter_clean": fpr_clean,
        "fpr_per_quarter_control_filer": fpr_ctrl,
    }


# ---------------------------------------------------- case-level measurement


def case_outcomes(gate_version: str, horizon_q: int = 4,
                  sources: tuple[str, ...] = LIVE_SOURCES,
                  conn: sqlite3.Connection | None = None) -> list[harness.Outcome]:
    """Reconstruct harness.Outcome records from the live ledger, one per
    filer, so case-level live numbers share the holdout's exact arithmetic.

    Two stated approximations, both conservative: `censored` means fired at
    the first RESOLVED cutoff (the ledger cannot see earlier unresolved
    ones), and lead is measured to the event period's END because the
    resolution row does not carry the break's first filing date -- period end
    precedes publication by 4-6 weeks, so live leads read SHORTER than the
    holdout's, never longer.
    """
    rows = [r for r in resolutions(gate_version, horizon_q, conn=conn)
            if r["source"] in sources]
    by_cik: dict[str, list[dict]] = {}
    for r in rows:
        by_cik.setdefault(r["cik"], []).append(r)
    outs = []
    for cik, rs in sorted(by_cik.items()):
        rs.sort(key=lambda r: r["as_of"])
        fired_idx = next((i for i, r in enumerate(rs) if _fired(r)), None)
        first = rs[fired_idx]["as_of"] if fired_idx is not None else None
        censored = fired_idx == 0
        det = [r for r in rs if r["outcome"] == "DETERIORATED"]
        broke = min(r["event_period"] for r in det)[:7] if det else None
        lead = None
        if first and broke and not censored:
            lead = harness.months_between(first[:7], broke)
            if not 0 < lead <= harness.MAX_CREDITABLE_LEAD_MONTHS:
                lead = None
        n_fires = sum(1 for r in rs if _fired(r))
        outs.append(harness.Outcome(
            ticker=rs[0]["ticker"] or cik,
            is_positive=bool(det),
            fired=first is not None,
            censored=censored,
            first_fire=first,
            lead_months=lead,
            fire_rate=n_fires / len(rs),
            scoreable_quarters=len(rs),
            n_fires=n_fires,
        ))
    return outs


def live_stats(outcomes: list[harness.Outcome],
               baseline_fpr: float | None = None) -> dict:
    """harness.verdict()'s arithmetic on live outcomes, with NO verdict verb.

    Deliberately re-derived rather than calling verdict(): running the
    pre-registered rule on live data would look like re-scoring a spent
    one-shot test. The agreement between the two computations is enforced by
    a test, number for number, which is what makes 'live and backtest share
    one definition' a fact instead of a claim. Status is MONITORING, always;
    a live surface that printed SHIP would be re-grading a rule that has
    already been graded.
    """
    pos = [o for o in outcomes if o.is_positive]
    neg = [o for o in outcomes if not o.is_positive]
    fpr_filer = (sum(1 for o in neg if o.fired) / len(neg)) if neg else None
    ctrl_q = sum(o.scoreable_quarters for o in neg)
    fpr = (sum(o.n_fires for o in neg) / ctrl_q) if ctrl_q else None
    leads = [o.lead_months for o in pos if o.lead_months is not None]
    assessable = [o for o in pos if not o.censored]
    n_led = sum(1 for o in assessable if o.lead_months and o.lead_months > 0)
    hit_rate = (n_led / len(assessable)) if assessable else 0.0
    hit_ci = reliability.wilson(n_led, len(assessable))
    fpr_ci = reliability.wilson(sum(o.n_fires for o in neg), ctrl_q)
    regimes = {o.regime for o in outcomes
               if o.is_positive and o.regime and o.fired
               and (o.lead_months or 0) > 0}
    return {
        "status": "MONITORING",
        "level": "per-case",
        "positive_hit_rate": round(hit_rate, 3),
        "positive_hit_rate_wilson": ([round(v, 4) for v in hit_ci]
                                     if hit_ci else None),
        "median_lead_months": median(leads) if leads else None,
        "false_positive_rate_per_quarter": (None if fpr is None
                                            else round(fpr, 4)),
        "fpr_per_quarter_wilson": ([round(v, 4) for v in fpr_ci]
                                   if fpr_ci else None),
        "false_positive_rate_per_filer": (None if fpr_filer is None
                                          else round(fpr_filer, 4)),
        "control_filer_quarters": ctrl_q,
        "n_positive": len(pos),
        "n_control": len(neg),
        "n_censored_positives": sum(1 for o in pos if o.censored),
        "n_assessable_positives": len(assessable),
        "regimes_detected": sorted(r for r in regimes if r),
        "baseline_fpr": baseline_fpr,
        "note": (
            "Live monitoring only. These numbers are measured with the "
            "holdout's exact arithmetic but they are not a test result -- "
            "the one-shot pre-registered test already ran and returned KILL."
        ),
    }


# ------------------------------------------------------------- the monitor


def _tuning_floor() -> dict:
    """The failed detector's own tuning-split rates, from the frozen record.
    The only per-quarter reference that exists; a floor, never a target."""
    calib = status.load().get("calibration") or {}
    return {
        "recall_per_quarter": calib.get("tuning_recall_on_deteriorating_quarters"),
        "fpr_per_quarter": calib.get("tuning_fpr_per_quarter"),
    }


def monitor(gate_version: str | None = None, horizon_q: int = 4,
            conn: sqlite3.Connection | None = None) -> dict:
    """Where the live per-quarter recall stands against the tuning floor.

    Reads and reports; mutates NOTHING -- no constant, no table, no file.
    Auto-retuning against live outcomes would overfit the only unspent data
    there is, so a decaying gate stays live until a person reads this.

    Statuses: INSUFFICIENT (below the minimum resolved count -- the honest
    answer, not a hedge), BELOW_FLOOR, CONSISTENT_WITH_FLOOR, ABOVE_FLOOR.
    ABOVE_FLOOR means "beating the failed detector's own rate" -- improvement
    over a failure, never evidence of success.
    """
    gate = gate_version or emit.gate_version()
    floor = _tuning_floor()["recall_per_quarter"]
    rows = [r for r in resolutions(gate, horizon_q, conn=conn)
            if r["source"] in LIVE_SOURCES]
    det = [r for r in rows if r["outcome"] == "DETERIORATED"]
    k = sum(1 for r in det if _fired(r))
    n = len(det)
    out: dict[str, Any] = {
        "gate_version": gate,
        "horizon_q": horizon_q,
        "n_resolved_deteriorating_quarters": n,
        "n_required": MIN_RESOLVED_DETERIORATING,
        "floor": {
            "value": floor, "split": "tuning", "level": "per-quarter",
            "meaning": "the failed detector's own rate -- a floor a revision "
                       "must beat, never a grade being defended",
        },
    }
    if n < MIN_RESOLVED_DETERIORATING:
        out["status"] = "INSUFFICIENT"
        out["recall_per_quarter"] = None
        out["wilson"] = None
        return out
    ci = reliability.wilson(k, n)
    assert ci is not None
    out["recall_per_quarter"] = round(k / n, 4)
    out["wilson"] = [round(v, 4) for v in ci]
    if floor is None:
        out["status"] = "INSUFFICIENT"
    elif ci[1] < floor:
        out["status"] = "BELOW_FLOOR"
    elif ci[0] > floor:
        out["status"] = "ABOVE_FLOOR"
    else:
        out["status"] = "CONSISTENT_WITH_FLOOR"
    return out


# ------------------------------------------------------------- the payload


def record_payload(gate_version: str | None = None,
                   horizons: tuple[int, ...] = HORIZONS,
                   conn: sqlite3.Connection | None = None) -> dict:
    """The full track record as one JSON payload, KILL record attached.

    status.load() runs FIRST and is allowed to raise: there is no code path
    that produces a track-record payload on a machine that does not hold the
    committed record of the failure. Live and replayed rows are reported
    under separate keys and never averaged -- replayed tuning quarters are
    already spent and prove nothing about unseen data.
    """
    frozen = status.load()
    gate = gate_version or emit.gate_version()
    checks = frozen["checks"]
    tuning = _tuning_floor()
    payload: dict[str, Any] = {
        "banner": status.banner(),
        "gate_version": gate,
        "computed_at": date.today().isoformat(),
        "reference": {
            "meaning": (
                "The numbers the detector posted when it FAILED its "
                "pre-registered test on 2026-08-30. A floor any revision "
                "must beat, never a grade being defended."
            ),
            "holdout_per_case": {
                "level": "per-case",
                "positive_hit_rate": checks["positive_hit_rate"]["value"],
                "required_hit_rate": checks["positive_hit_rate"]["limit"],
                "fpr_per_control_quarter":
                    checks["false_positive_rate_per_quarter"]["value"],
                "naive_baseline_fpr": checks["beats_naive_baseline"]["limit"],
                "fpr_per_filer": frozen.get("false_positive_rate_per_filer"),
                "not_comparable_to": (
                    "any per-quarter rate below -- the hit rate is per CASE, "
                    "with censoring and a 24-month creditable-lead cap"
                ),
            },
            "tuning_per_quarter": {
                "level": "per-quarter",
                "recall_on_deteriorating_quarters": tuning["recall_per_quarter"],
                "fpr_per_quarter": tuning["fpr_per_quarter"],
                "comparable_to": "the live per-quarter rates below",
            },
        },
        "horizons": {},
        "case_level": live_stats(case_outcomes(gate, 4, conn=conn)),
        "monitor": monitor(gate, 4, conn=conn),
    }
    pend = pending(gate, conn=conn)
    for h in horizons:
        rows = resolutions(gate, h, conn=conn)
        live = [r for r in rows if r["source"] in LIVE_SOURCES]
        back = [r for r in rows if r["source"] not in LIVE_SOURCES]
        payload["horizons"][str(h)] = {
            "label_rule": label_rule(h),
            "comparable_to_reference": h == label.HORIZON_QUARTERS,
            "n_pending": sum(1 for p in pend if p["horizon_q"] == h),
            "live": quarter_stats(live),
            "replay_backfill": quarter_stats(back),
            "note": (
                "live and replayed rows are never pooled: the replay walks "
                "the tuning split, which the calibration already used"
            ),
        }
    payload["score_bins"] = reliability.score_bins([
        {"score": r["score"],
         "deteriorated": r["outcome"] == "DETERIORATED"}
        for r in resolutions(gate, 4, conn=conn)
        if r["source"] in LIVE_SOURCES
    ])
    return status.stamp(payload)


def snapshot(payload: dict, conn: sqlite3.Connection | None = None) -> int:
    """A dated track_record row per horizon, from the payload's LIVE stats.
    ROADMAP 10 asks whether performance decays; without dated snapshots
    'decays' has nothing to be measured against. Same-day re-runs write
    nothing new (the primary key absorbs them)."""
    own = conn is None
    if own:
        conn = edgar.db()
    assert conn is not None
    written = 0
    try:
        with conn:
            for h, block in payload["horizons"].items():
                live = block["live"]
                cur = conn.execute(
                    "INSERT OR IGNORE INTO track_record (gate_version, "
                    "horizon_q, computed_at, n_resolved, n_fires, recall, "
                    "fpr_per_quarter_control_filer, fpr_per_quarter_clean, "
                    "payload) VALUES (?,?,?,?,?,?,?,?,?)",
                    (payload["gate_version"], int(h), payload["computed_at"],
                     live["n_resolved"],
                     live["recall_per_quarter"]["fired"],
                     live["recall_per_quarter"]["value"],
                     live["fpr_per_quarter_control_filer"]["value"],
                     live["fpr_per_quarter_clean"]["value"],
                     json.dumps(payload)))
                written += cur.rowcount
    finally:
        if own:
            conn.close()
    return written
