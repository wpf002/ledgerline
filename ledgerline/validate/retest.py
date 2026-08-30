"""
The re-test reserve: future evaluation data set aside NOW, before any revision
of the detector is designed.

Why this module exists, and why it lands early: the 387-case holdout was
scored exactly once, on 2026-08-30, and the answer was KILL. It is spent --
prereg.json says do not retune and re-run against it, and everyone who has
read the write-up now knows recall is what failed, which no hash can undo. So
the only data a revised detector can ever be tested on fairly is data that did
not exist when the revision was designed. That window only opens forward:
every month that passes unreserved is a month of company-quarters someone may
have already looked at, and that can therefore never be reserved cleanly.
Reserving is cheap; waiting is the expensive part.

Three refusals do the real work here, in the register write_prereg() and
make_split() established:

  * reserve() builds a set of (company, checkpoint) pairs that are absent from
    the tuning dataset, absent from the spent holdout, and strictly in the
    future -- then hashes it and refuses to overwrite. A reserved set that can
    be quietly redrawn until it looks favourable is the split.json burn again.
  * register() records intent BEFORE any scoring: which detector revision,
    against which reserved set, at what share of the alpha budget, with a
    mandatory note saying what the revision's author already knew. Five
    untracked tests at 0.05 each are a 23% chance of a spurious win; only a
    budget prevents that, so the budget is enforced, not advised.
  * reserve() refuses an underpowered set. The power arithmetic
    (two_proportion_n) says how many resolved deteriorating quarters a
    comparison needs before it is anything but noise, and a set that cannot
    reach that number is fifteen months of waiting for a coin flip.

What is deliberately NOT here: the scoring half -- score(), McNemar, the
paired-comparison machinery. A quarter reserved at cutoff T is not resolvable
at the pre-registered horizon until roughly T + 15 months, so the first
legitimate comparison lands around 2028. A statistical test written eighteen
months before its first use drifts from what it will actually be asked; build
the comparison when there is something to compare. The Phase 0 numbers travel
in this file's header as the FLOOR a revision must beat -- never as a grade
being defended, because a failed test has no grade to defend.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, timedelta
from typing import Any

from .. import status
from . import harness

DATA = harness.DATA
RETESTS_PATH = os.path.join(DATA, "retests.json")

# Total false-discovery budget across every re-test that will ever run against
# reserved data. Drawn down by register(), never refilled: alpha spent on a
# revision that lost is still spent.
ALPHA_BUDGET = 0.05

# Resolved deteriorating quarters a comparison needs before it can be scored.
# two_proportion_n(0.14, 0.25) = 203 unpaired; a paired comparison on the same
# filer-quarters needs fewer, so 200 is the honest round figure for the
# refusal threshold. 0.14 is the tuning per-quarter recall (the only
# per-quarter recall that exists -- no holdout per-quarter number was ever
# computed, by design); 0.25 is the smallest improvement worth a draw on the
# budget.
MIN_DETERIORATING_QUARTERS = 200
TARGET_RECALL = 0.25

# A quarter scored at cutoff T resolves at horizon h only after h quarters
# have ended AND the filings reporting them have arrived. 91 days per quarter
# plus a filing lag: most 10-Qs land inside 90 days of the period end.
FILING_LAG_DAYS = 90

# How far forward reserve() reaches when --end is not given. Two years of
# quarterly checkpoints across the watchlist clears the power floor with room;
# a longer reach delays nothing since scoring can start once enough of the
# set has resolved.
DEFAULT_RESERVE_MONTHS = 24


def _hash_pairs(pairs: list[tuple[str, str]]) -> str:
    """Fingerprint of the exact (cik, cutoff) pairs. The hash covers the
    expanded set, not the recipe that generated it, so editing the company
    list OR the checkpoint list after commit is detectable either way."""
    body = json.dumps(sorted(pairs), sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _expand(entry: dict) -> list[tuple[str, str]]:
    """The reserved pairs, rebuilt from a stored entry: every company at every
    checkpoint, minus the pairs recorded as excluded at reserve time."""
    excluded = {(t, c) for t, c in entry.get("excluded_pairs", [])}
    return [
        (cik, cutoff)
        for _, cik in sorted(entry["companies"].items())
        for cutoff in entry["cutoffs"]
        if (cik, cutoff) not in excluded
    ]


def two_proportion_n(p1: float, p2: float, alpha: float = 0.025,
                     power: float = 0.80) -> int:
    """Quarters per arm to tell rate p1 from rate p2, one-sided.

    Standard unpaired two-proportion arithmetic, kept in plain Python for the
    same reason calibrate.py hand-rolls its IRLS: a sample-size claim nobody
    can re-derive is a number that implies more measurement than happened.
    Only alpha 0.025 / power 0.80 are supported -- the z values are pinned
    rather than computed because the codebase has no inverse-normal function
    and approximating one badly here would corrupt the single number this
    module exists to state.
    """
    if not 0 < p1 < 1 or not 0 < p2 < 1 or p1 == p2:
        raise ValueError("two_proportion_n needs two distinct rates in (0, 1)")
    if (alpha, power) != (0.025, 0.80):
        raise ValueError(
            "only alpha=0.025, power=0.80 is supported -- the z values are "
            "pinned constants, not computed, so other operating points would "
            "be silently wrong rather than differently right"
        )
    z_alpha = 1.959963984540054   # Phi^-1(0.975)
    z_beta = 0.8416212335729143   # Phi^-1(0.80)
    pbar = (p1 + p2) / 2
    numerator = (z_alpha * math.sqrt(2 * pbar * (1 - pbar))
                 + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(numerator / (p1 - p2) ** 2)


def quarterly_cutoffs_between(after: str, end: str) -> list[str]:
    """Filing-season checkpoints strictly after `after`, up to and including
    `end`. Same mid-month dates as backtest.quarterly_cutoffs, so a reserved
    checkpoint is directly comparable to a replayed one."""
    return [
        f"{y}-{m:02d}-15"
        for y in range(int(after[:4]), int(end[:4]) + 1)
        for m in (2, 5, 8, 11)
        if after < f"{y}-{m:02d}-15" <= end
    ]


def earliest_scoreable_date(after: str, horizon_q: int = 4) -> str:
    """When the FIRST reserved quarter becomes resolvable at this horizon.

    Not when the whole set is scoreable -- the power floor decides that -- but
    the date before which looking at outcomes is not merely premature but
    impossible: the filings that decide them have not been made. Roughly 15
    months out at the pre-registered horizon of 4 quarters, and nothing
    accelerates it. Pretending otherwise is how a spent holdout gets re-used.
    """
    cutoffs = quarterly_cutoffs_between(after, f"{int(after[:4]) + 1}-12-31")
    first = date.fromisoformat(cutoffs[0])
    return (first + timedelta(days=horizon_q * 91 + FILING_LAG_DAYS)).isoformat()


def _floor_block() -> dict[str, Any]:
    """The Phase 0 numbers a revision must beat, read from the frozen record.

    status.stamp() raises if phase0.json is absent -- deliberately inherited:
    a reserve drawn on a machine that holds no evidence of the failure would
    record a floor it cannot state.
    """
    stamped: dict[str, Any] = status.stamp({})
    frozen = status.load()
    calib = frozen.get("calibration") or {}
    n_rows = calib.get("n_rows")
    n_pos = calib.get("n_positive_rows")
    return {
        "meaning": (
            "These are the numbers the failed detector actually posted -- the "
            "floor any revision must beat, never a grade being defended."
        ),
        "gate_status": stamped["gate_status"],
        **stamped["phase0"],
        "tuning_recall_per_quarter": calib.get(
            "tuning_recall_on_deteriorating_quarters"),
        "tuning_base_rate": (round(n_pos / n_rows, 4)
                             if n_rows and n_pos is not None else None),
    }


def load_retests() -> dict:
    """The registry, or its empty shape before the first reserve. An absent
    file is a legitimate state exactly once -- nothing reserved yet; after the
    first reserve the file is committed and its absence means a fresh clone
    is missing the record."""
    if not os.path.exists(RETESTS_PATH):
        return {"created": None, "alpha_budget": ALPHA_BUDGET, "floor": None,
                "reserved": {}, "attempts": []}
    with open(RETESTS_PATH) as fh:
        payload: dict = json.load(fh)
    return payload


def _verify_all(payload: dict) -> None:
    """Every stored reserved set must still hash to its committed fingerprint.
    Run before any write, so the file is append-only in effect: an edit to an
    existing entry surfaces on the next touch rather than propagating."""
    for name, entry in payload.get("reserved", {}).items():
        if _hash_pairs(_expand(entry)) != entry.get("sha256"):
            raise RuntimeError(
                f"reserved set '{name}' in retests.json no longer matches its "
                "fingerprint -- the set was edited after it was reserved, "
                "which is the split.json burn again. Restore the committed "
                "file; a different set needs a different name."
            )


def _write(payload: dict) -> None:
    _verify_all(payload)
    os.makedirs(DATA, exist_ok=True)
    with open(RETESTS_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def load_reserved(name: str) -> dict:
    """One reserved set, fingerprint verified. Raises if absent or edited."""
    payload = load_retests()
    entry = payload.get("reserved", {}).get(name)
    if entry is None:
        raise RuntimeError(
            f"no reserved set named '{name}'. Existing sets: "
            f"{sorted(payload.get('reserved', {})) or 'none'}. Reserve one: "
            "ledgerline retest reserve --name r1 --after YYYY-MM-DD"
        )
    if _hash_pairs(_expand(entry)) != entry.get("sha256"):
        raise RuntimeError(
            f"reserved set '{name}' no longer matches its fingerprint -- it "
            "was edited after it was reserved. Restore the committed "
            "retests.json; a different set needs a different name."
        )
    return entry


def reserved_hash(entry: dict) -> str:
    """Recompute a stored set's fingerprint from its pairs."""
    return _hash_pairs(_expand(entry))


def _spent_pairs() -> set[tuple[str, str]]:
    """(ticker, cutoff) pairs the calibration already consumed. Read from
    tuning_dataset.json because that file records what the fit actually saw,
    row by row -- not what the split intended it to see."""
    from .. import calibrate as _calib
    if not os.path.exists(_calib.DATASET_PATH):
        return set()
    with open(_calib.DATASET_PATH) as fh:
        rows = json.load(fh).get("rows", [])
    return {(r["ticker"], r["cutoff"]) for r in rows}


def reserve(name: str, after: str, *, end: str | None = None,
            tickers: dict[str, str] | None = None,
            today: str | None = None) -> dict:
    """Define, hash and record a fresh evaluation set. Refuses to overwrite.

    The set is every watched company at every quarterly checkpoint strictly
    after `after` (through `end`), minus three exclusions that make it clean:
    companies in the spent holdout are out entirely (their histories were
    scored once and the result is known); any (company, checkpoint) pair the
    tuning dataset consumed is out (the fit saw it); and no checkpoint may
    already be in the past (data that exists can have been looked at, and a
    look cannot be proven not to have happened).
    """
    today_s = today or date.today().isoformat()
    date.fromisoformat(after)
    if after < today_s:
        raise RuntimeError(
            f"--after {after} is in the past. Checkpoints before today are "
            "data that already exists, and existing data cannot be proven "
            "unexamined -- that is why the holdout is spent. Reserve from "
            f"{today_s} or later."
        )
    end = end or (
        date.fromisoformat(after)
        + timedelta(days=DEFAULT_RESERVE_MONTHS * 30)
    ).isoformat()
    if end <= after:
        raise RuntimeError(f"--end {end} must come after --after {after}.")

    registry = load_retests()
    if name in registry.get("reserved", {}):
        raise RuntimeError(
            f"a reserved set named '{name}' already exists and reserved sets "
            "are never redrawn -- redrawing until the set looks favourable is "
            "the same burn as editing the holdout. Pick a new name for a new "
            "reservation."
        )

    floor = _floor_block()

    if tickers is None:
        from .. import edgar as _edgar
        tickers = {v["ticker"]: cik for cik, v in _edgar.universe().items()}
    if not tickers:
        raise RuntimeError(
            "No companies are being watched yet, so there is nothing to "
            "reserve. Add some: ledgerline watch --add AAPL,MSFT"
        )

    harness.verify_split()
    with open(harness.SPLIT_PATH) as fh:
        holdout = set(json.load(fh)["holdout"])
    spent = _spent_pairs()

    cutoffs = quarterly_cutoffs_between(after, end)
    if not cutoffs:
        raise RuntimeError(
            f"no quarterly checkpoints fall between {after} and {end} -- "
            "widen --end."
        )

    companies = {t: c for t, c in sorted(tickers.items()) if t not in holdout}
    excluded_pairs = sorted(
        (companies[t], c) for t in companies for c in cutoffs
        if (t, c) in spent
    )
    excluded_set = set(excluded_pairs)
    pairs = [
        (cik, cutoff)
        for _, cik in sorted(companies.items())
        for cutoff in cutoffs
        if (cik, cutoff) not in excluded_set
    ]
    if not pairs:
        raise RuntimeError("every candidate pair was excluded -- nothing "
                           "clean is left to reserve in this window.")

    # The power refusal. Projected from the tuning base rate over every
    # reserved pair, which assumes every pair can be assessed -- an
    # overestimate, and the entry says so. The true count is only known as
    # quarters resolve; the projection exists to stop a set that cannot
    # possibly reach the floor from being reserved at all.
    ref_recall = floor.get("tuning_recall_per_quarter")
    base_rate = floor.get("tuning_base_rate")
    if ref_recall is None or base_rate is None:
        raise RuntimeError(
            "phase0.json carries no tuning calibration block, so the power "
            "arithmetic cannot run -- and a reserve without it is a promise "
            "with no delivery date. Re-freeze from the full holdout report."
        )
    needed = max(two_proportion_n(ref_recall, TARGET_RECALL),
                 MIN_DETERIORATING_QUARTERS)
    projected = round(len(pairs) * base_rate)
    if projected < needed:
        raise RuntimeError(
            f"underpowered: about {projected} deteriorating company-quarters "
            f"are expected in this window and a fair comparison needs at "
            f"least {needed}. Widen --end -- a set that cannot reach the "
            "floor is months of waiting for a coin flip."
        )

    entry: dict[str, Any] = {
        "name": name,
        "reserved_on": today_s,
        "after": after,
        "end": end,
        "cutoffs": cutoffs,
        "companies": companies,
        "excluded_pairs": [list(p) for p in excluded_pairs],
        "n_companies": len(companies),
        "n_pairs": len(pairs),
        "n_holdout_tickers_excluded": len(set(tickers) & holdout),
        "n_spent_pairs_excluded": len(excluded_pairs),
        # Projection, not measurement: assumes every pair is assessable.
        "projected_deteriorating_quarters": projected,
        "needed_deteriorating_quarters": needed,
        "assumed_base_rate": base_rate,
        "earliest_scoreable_h4": earliest_scoreable_date(after),
        "fully_resolved_h4": (
            date.fromisoformat(cutoffs[-1])
            + timedelta(days=4 * 91 + FILING_LAG_DAYS)
        ).isoformat(),
        "spent": False,
        "sha256": _hash_pairs(pairs),
    }

    if registry.get("created") is None:
        registry["created"] = today_s
        registry["alpha_budget"] = ALPHA_BUDGET
    # The floor rides in the header, refreshed from the frozen record on every
    # write so it can never drift from phase0.json (which load() pins anyway).
    registry["floor"] = floor
    registry.setdefault("reserved", {})[name] = entry
    registry.setdefault("attempts", [])
    _write(registry)
    return entry


def alpha_spent(registry: dict | None = None) -> float:
    reg = registry if registry is not None else load_retests()
    return round(sum(a["alpha"] for a in reg.get("attempts", [])), 10)


def register(gate_version: str, reserved: str, alpha: float,
             contamination_note: str) -> dict:
    """Record intent BEFORE scoring: which revision, which set, what share of
    the budget, and what its author already knew.

    The note is mandatory and cannot be empty: everyone who has read the
    Phase 0 write-up knows recall is what failed, and that knowledge cannot
    be hashed away -- recording it is the only honest handling.
    """
    if not gate_version.strip():
        raise RuntimeError("register needs the revision's version fingerprint "
                           "-- an attempt with no named revision cannot be "
                           "held to its operating point later.")
    if not contamination_note.strip():
        raise RuntimeError(
            "the contamination note is mandatory and cannot be blank. Say "
            "what the revision's author had already seen -- at minimum, that "
            "the Phase 0 result and its recall failure were known. An "
            "attempt with no note reads later as an attempt with something "
            "to hide."
        )
    entry = load_reserved(reserved)
    if entry.get("spent"):
        raise RuntimeError(
            f"reserved set '{reserved}' has already been scored, and one "
            "score per set is the whole point. Reserve a new set from a "
            "later date."
        )
    if alpha <= 0:
        raise RuntimeError("--alpha must be a positive share of the budget.")
    registry = load_retests()
    spent = alpha_spent(registry)
    remaining = round(ALPHA_BUDGET - spent, 10)
    if alpha > remaining + 1e-12:
        raise RuntimeError(
            f"the false-discovery budget cannot cover this attempt: "
            f"{spent:.3f} of {ALPHA_BUDGET:.3f} is already committed and "
            f"{remaining:.3f} remains, less than the requested {alpha:.3f}. "
            "The budget exists because five untracked tests at 0.05 each are "
            "a 23% chance of a spurious win; it is never refilled."
        )
    for a in registry.get("attempts", []):
        if a["gate_version"] == gate_version and a["reserved"] == reserved:
            raise RuntimeError(
                f"revision {gate_version} is already registered against "
                f"'{reserved}' -- re-registering would draw the budget twice "
                "for one look. A changed revision has a changed fingerprint."
            )
    attempt: dict[str, Any] = {
        "registered_on": date.today().isoformat(),
        "gate_version": gate_version,
        "reserved": reserved,
        "reserved_sha256": entry["sha256"],
        "alpha": alpha,
        "contamination_note": contamination_note.strip(),
        # Scoring is deliberately unbuilt until a set matures (~2028): a
        # statistical test written years before first use drifts from what it
        # will be asked. These fields exist so an attempt is visibly
        # unresolved rather than ambiguously absent.
        "scored": False,
        "result": None,
    }
    registry.setdefault("attempts", []).append(attempt)
    _write(registry)
    return attempt


def status_report() -> dict:
    """The registry at a glance: budget, attempts, and which sets wait."""
    registry = load_retests()
    _verify_all(registry)
    spent = alpha_spent(registry)
    return {
        "alpha_budget": registry.get("alpha_budget", ALPHA_BUDGET),
        "alpha_spent": spent,
        "alpha_remaining": round(
            registry.get("alpha_budget", ALPHA_BUDGET) - spent, 10),
        "floor": registry.get("floor"),
        "reserved": registry.get("reserved", {}),
        "attempts": registry.get("attempts", []),
    }
