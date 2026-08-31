"""
The frozen Phase 0 verdict, and the stamp every scored payload must carry.

Why this module exists: the holdout was scored once on 2026-08-30 and the
pre-registered answer was KILL. The gate caught 28.7% of the deteriorations it
was built to find against a required 60%, and its false-alarm rate (3.83% per
control filer-quarter) failed to beat the naive two-line baseline (0.51%) it
had to better. That verdict lived only in reports/backtest_holdout.json, which
is gitignored -- the sole record of the failure did not survive a fresh clone,
while signals_v3.py's CALIBRATED docstring read end to end as the record of a
working gate. A score shown without its verdict is a claim the project cannot
support, so the verdict is frozen into ledgerline/data/phase0.json (committed)
and stamped onto every scored payload here, at one enforcement point.

Three deliberate choices:

  * banner() is GENERATED from the frozen file, never a string literal. Two
    hand-typed banners that can drift apart are worse than none, because a
    reader cannot tell which one is current.
  * load() RAISES when the file is missing rather than defaulting to
    "unvalidated". A default would produce the right label today on a machine
    holding no evidence, and would silently mislabel a future gate that
    actually passed -- the same defect as a pre-registration that lives only
    on the machine that ran the test. There is no path from a missing
    evidence file to an emitted score.
  * load() cross-checks the file against the pinned numbers below, the same
    discipline harness.load_prereg() applies to prereg.json: editing the
    committed record in place to move the bar raises instead of passing.

The same file answers a second question: has the one shot been taken?
refuse_spent_holdout() is the guard every scoring entry point calls, because
the frozen record IS the evidence the sealed half was already scored.
"""
from __future__ import annotations

import json
import os
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PHASE0_PATH = os.path.join(DATA, "phase0.json")
CALIBRATION_PATH = os.path.join(DATA, "calibration.json")
DEFAULT_REPORT = os.path.join(os.path.dirname(ROOT), "reports", "backtest_holdout.json")

GATE_STATUS: str = "UNVALIDATED-KILL"

# The pinned result, for the drift check only -- every displayed number comes
# from the frozen file via load(). Values carry the rounding the holdout report
# itself uses; the two failed criteria are positive_hit_rate and the naive-
# baseline comparison. fpr_per_filer is reported, not part of the rule: half
# the companies that stayed fine were flagged at least once.
PHASE0: dict[str, object] = {
    "verdict": "KILL",
    "scored_on": "2026-08-30",
    "positive_hit_rate": 0.287,
    "positive_hit_rate_floor": 0.6,
    "fpr_per_control_quarter": 0.0383,
    "fpr_ceiling": 0.04,
    "naive_baseline_fpr": 0.0051,
    "fpr_per_filer": 0.512,
}

_cache: dict[str, dict] = {}


def _round(v: object) -> object:
    return round(v, 4) if isinstance(v, (int, float)) else v


def _summary(payload: dict) -> dict[str, object]:
    """The frozen file reduced to the keys PHASE0 pins, rounded the same way."""
    checks = payload.get("checks", {})

    def check(name: str, key: str) -> object:
        return _round(checks.get(name, {}).get(key))

    return {
        "verdict": payload.get("verdict"),
        "scored_on": payload.get("scored_on"),
        "positive_hit_rate": check("positive_hit_rate", "value"),
        "positive_hit_rate_floor": check("positive_hit_rate", "limit"),
        "fpr_per_control_quarter": check("false_positive_rate_per_quarter", "value"),
        "fpr_ceiling": check("false_positive_rate_per_quarter", "limit"),
        "naive_baseline_fpr": check("beats_naive_baseline", "limit"),
        "fpr_per_filer": _round(payload.get("false_positive_rate_per_filer")),
    }


def load() -> dict:
    """The frozen Phase 0 record, read from disk. Raises if absent or edited.

    No default. A machine without ledgerline/data/phase0.json holds no evidence
    of the 2026-08-30 test, and labeling the gate from a built-in constant
    would get the right answer for the wrong reason today -- and the wrong
    answer silently the day a future gate passes a new pre-registration.
    """
    if PHASE0_PATH in _cache:
        return _cache[PHASE0_PATH]
    if not os.path.exists(PHASE0_PATH):
        raise RuntimeError(
            "ledgerline/data/phase0.json is missing -- it is the committed record "
            "of the failed 2026-08-30 test, and no score may be shown without it. "
            "Restore the committed file, or run `ledgerline phase0-freeze` on the "
            "machine that holds reports/backtest_holdout.json. There is "
            "deliberately no default value."
        )
    with open(PHASE0_PATH) as fh:
        payload = json.load(fh)
    seen = _summary(payload)
    drift = {k for k in PHASE0 if seen.get(k) != _round(PHASE0[k])}
    if drift:
        raise RuntimeError(
            "phase0.json disagrees with status.PHASE0 on "
            f"{sorted(drift)} -- the committed record is the record. Revert the "
            "file or the module; do not edit either to match the other. A new "
            "result needs a new pre-registration, not an edit to this one."
        )
    _cache[PHASE0_PATH] = payload
    return payload


def summary() -> dict[str, object]:
    """The frozen record reduced to the pinned keys, for surfaces that build a
    sentence from the numbers rather than reprinting banner() whole.

    Public so render.caveat() cannot hand-type its own copy of the result: a
    literal caveat is the same second-copy-that-drifts defect banner() exists
    to avoid, and it drifted -- the shipped one claimed "29%" and a date, with
    nothing binding either to this file.
    """
    return _summary(load())


def holdout_is_spent() -> bool:
    """True once the one-shot result is frozen: the shot has been taken.

    Keyed on the frozen record rather than on a constant, because that file IS
    the evidence the sealed half was scored. On a machine that has not frozen
    it there is no verdict to print either, so no score can reach a person
    there regardless -- load() raises.
    """
    return os.path.exists(PHASE0_PATH)


def spent_refusal() -> str:
    """Why the sealed half cannot be scored again, in one written reason.

    One copy of the words, shared by backtest.run() and the CLI, so the
    refusal a person reads and the refusal a program hits cannot diverge.
    """
    return (
        f"the sealed test half was scored exactly once, on {load()['scored_on']}, "
        "and that one measurement is only meaningful while it stays the only "
        "one. Scoring it again -- after a retune, or quietly into a report "
        "file -- is a second look at the answer it was reserved for, so the "
        "tool refuses and there is no override flag. The reserved companies "
        "in ledgerline/data/retests.json are the only legitimate future test "
        "(ledgerline retest reserve/register/status). The result that was "
        "already taken is in ledgerline/data/phase0.json and reports/PHASE0.md."
    )


def refuse_spent_holdout(split: str) -> None:
    """Raise if `split` names the sealed half and its one shot is already spent.

    The guard every scoring entry point calls. calibrate.build_dataset had one
    and cli.replay had one, but backtest.run() -- the one function whose job is
    to score a split -- had none, so `ledgerline run-test --split holdout` ran
    today with no refusal and overwrote reports/backtest_holdout.json, the only
    full record of the 2026-08-30 failure.
    """
    if split != "holdout" or not holdout_is_spent():
        return
    raise RuntimeError("refusing to score the holdout: " + spent_refusal())


def banner() -> str:
    """The failure, in plain words, built from the frozen numbers.

    Never a literal: a hand-typed banner is a second copy of the result that
    can drift from the committed one, and a banner that cannot be produced on
    a machine without the evidence file is the point, not a bug.
    """
    p = load()
    checks = p["checks"]
    hit = checks["positive_hit_rate"]
    fpr = checks["false_positive_rate_per_quarter"]
    naive = checks["beats_naive_baseline"]
    return (
        f"NOT VALIDATED. This detector failed its own test, scored once on "
        f"{p['scored_on']}:\n"
        f"it caught {hit['value']:.1%} of the deteriorations it was built to find "
        f"(needed at least {hit['limit']:.0%}),\n"
        f"and its false alarms -- {fpr['value']:.2%} per quiet company-quarter -- did "
        f"not beat the {naive['limit']:.2%}\n"
        f"of the crude two-line rule it had to better. "
        f"{p['false_positive_rate_per_filer']:.1%} of companies that stayed\n"
        f"fine were flagged at least once. Full result: reports/PHASE0.md. "
        f"A quiet result\nis not a clean bill of health."
    )


def stamp(payload: dict) -> dict:
    """Attach the frozen verdict to a scored payload, in place, and return it.

    Mutates rather than wraps so every dict-returning path (Verdict.as_dict,
    backtest outcomes) stays shape-compatible. Every surface that emits a
    score calls this; a payload without the stamp is a claim the project
    cannot support, and assert_stamped() is how tests pin that.
    """
    p = load()
    payload["gate_status"] = GATE_STATUS
    payload["phase0"] = dict(_summary(p), writeup="reports/PHASE0.md")
    return payload


def assert_stamped(payload: dict) -> None:
    """Raise unless the payload carries the frozen verdict, numbers intact.

    Exists so tests can pin the invariant at every emission point instead of
    re-asserting key presence by hand -- and so the check compares the actual
    numbers, not just the presence of a key that could hold anything.
    """
    if payload.get("gate_status") != GATE_STATUS:
        raise AssertionError(
            "scored payload is missing gate_status -- a score without the Phase 0 "
            "verdict is a claim the project cannot support; route it through "
            "status.stamp()"
        )
    ph = payload.get("phase0")
    if not isinstance(ph, dict):
        raise AssertionError("scored payload carries no phase0 block; route it "
                             "through status.stamp()")
    wrong = {k for k in PHASE0 if ph.get(k) != _round(PHASE0[k])}
    if wrong:
        raise AssertionError(
            f"phase0 stamp disagrees with the pinned result on {sorted(wrong)} -- "
            "the stamp must carry the committed numbers, not a paraphrase"
        )


def freeze(report_path: str | None = None) -> dict:
    """Lift the holdout verdict into ledgerline/data/phase0.json. Run once.

    reports/*.json is gitignored, so before this file existed the only record
    of the KILL that survived a fresh clone was prose in ROADMAP.md. Refuses
    to overwrite for the same reason harness.write_prereg() does: rewriting
    the record is how a failed test gets relabeled.

    scored_on comes from the pinned constant because the holdout report does
    not timestamp itself -- a defect worth naming: the one-shot result file
    never recorded when the shot was taken.
    """
    if os.path.exists(PHASE0_PATH):
        raise RuntimeError(
            "phase0.json already exists -- the record is frozen exactly once. "
            "A new result belongs to a new pre-registration and a new file, "
            "not to an overwrite of this one."
        )
    report_path = report_path or DEFAULT_REPORT
    if not os.path.exists(report_path):
        raise RuntimeError(
            f"{report_path} is missing -- it holds the verdict being frozen. "
            "This command runs on the machine that scored the holdout; on any "
            "other machine, restore the committed phase0.json instead."
        )
    if not os.path.exists(CALIBRATION_PATH):
        raise RuntimeError(
            f"{CALIBRATION_PATH} is missing -- it records the split and prereg "
            "hashes that bind the verdict to the committed experiment."
        )
    with open(report_path) as fh:
        report = json.load(fh)
    verdict = report.get("verdict")
    if not verdict or "checks" not in verdict:
        raise RuntimeError(
            f"{report_path} carries no verdict block -- only the one-shot "
            "holdout report can be frozen, not a practice-half run."
        )
    with open(CALIBRATION_PATH) as fh:
        calib = json.load(fh)

    payload: dict[str, object] = {
        "frozen_on": date.today().isoformat(),
        "scored_on": PHASE0["scored_on"],
        "verdict": verdict["verdict"],
        "split": report.get("split"),
        "checks": verdict["checks"],
        "false_positive_rate_per_filer": verdict.get("false_positive_rate_per_filer"),
        "control_filer_quarters": verdict.get("control_filer_quarters"),
        "n_positive": verdict.get("n_positive"),
        "n_control": verdict.get("n_control"),
        "n_censored_positives": verdict.get("n_censored_positives"),
        "n_assessable_positives": verdict.get("n_assessable_positives"),
        "regimes_detected": verdict.get("regimes_detected"),
        "baseline": report.get("baseline"),
        "threshold": report.get("threshold"),
        "z_trigger": report.get("z_trigger"),
        "split_sha256": calib.get("split_sha256"),
        "prereg_sha256": calib.get("prereg_sha256"),
        "calibration": {
            "fitted": calib.get("fitted"),
            "split": calib.get("split"),
            "n_rows": calib.get("n_rows"),
            "n_positive_rows": calib.get("n_positive_rows"),
            "tuning_fpr_per_quarter": calib.get("chosen", {}).get("tuning_fpr_per_quarter"),
            "tuning_recall_on_deteriorating_quarters": calib.get("chosen", {}).get(
                "tuning_recall_on_deteriorating_quarters"),
        },
        "note": verdict.get("note"),
        "source_report": os.path.relpath(report_path, os.path.dirname(ROOT)),
        "writeup": "reports/PHASE0.md",
    }
    # Same drift check load() applies, run BEFORE the write: freezing a report
    # that disagrees with the pinned result would produce a file every later
    # command refuses, which is a worse failure mode than refusing now.
    seen = _summary(payload)
    drift = {k for k in PHASE0 if seen.get(k) != _round(PHASE0[k])}
    if drift:
        raise RuntimeError(
            f"the report disagrees with status.PHASE0 on {sorted(drift)} -- "
            "only the 2026-08-30 holdout result can be frozen here. A different "
            "result belongs to a new pre-registration."
        )
    with open(PHASE0_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return payload
