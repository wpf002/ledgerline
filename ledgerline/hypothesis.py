"""
Hypotheses: a named, versioned claim about the filings, registered before the
data that will test it exists.

ROADMAP_V1 1.0-b. Until 1.0 the code could score exactly one gate, and the
retest registry recorded an attempt as a bare version string. That isn't
enough to hold anyone to anything, for a reason 1.0-a made concrete: the
Phase 0 gate and the current code share every constant in gate_fingerprint()
and still score differently, because the audit fixes changed 1,291 lines of
arithmetic. Constants don't identify a gate.

So a hypothesis here is identified three ways:

  commit                the code that IS the hypothesis. Re-runnable with
                        reproduce.run_at_commit; uncommitted code can't be.
  fingerprint           the gate's constants (weights, trigger, threshold...).
                        Readable, diffable, and insufficient on its own.
  arithmetic_sha256     a hash of the hypothesis's outputs on the 40 pinned
                        golden cases. Two commits with the same arithmetic
                        hash score those cases identically; registering the
                        second would spend budget testing the same gate twice.

h0 is the Phase 0 gate, already scored on the holdout and failed. Every later
hypothesis is compared against h0 ON THE SAME RESERVED SET, not against h0's
holdout numbers: the holdout covers 2013-2025 cutoffs and r1 covers 2026-2028,
and comparing across them would credit or blame a hypothesis for the period
it happened to be tested in. When a hypothesis registers against a set, h0 is
added to that set as the comparator, at no cost to the error budget, since
h0 is not being tested for discovery.

Registration closes at a set's first checkpoint. Data that exists can have
been looked at, which is why reserve() refuses past checkpoints; a hypothesis
designed after r1's first checkpoint could have been shaped by it. Reserve a
new set instead.

Scoring is deliberately unbuilt, as retest.py explains: a reserved quarter
isn't resolvable at the four-quarter horizon until about fifteen months after
its checkpoint, so r1's first legitimate comparison is 2028-02-12. `score`
refuses unregistered hypotheses outright and refuses registered ones until
their set matures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import date
from typing import Any

from . import reproduce as repro
from . import signals_v3, status
from .validate import golden, harness, retest

HYPOTHESES_PATH = os.path.join(harness.DATA, "hypotheses.json")
ID_PATTERN = re.compile(r"^h[0-9]{1,3}$")

_GOLDEN_AT_COMMIT = """
import importlib.util, json, sys
root, marker, golden_file = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
import os
import ledgerline.validate
here = os.path.realpath(os.path.dirname(ledgerline.__file__))
if not here.startswith(os.path.realpath(root)):
    raise SystemExit("imported ledgerline from " + here + ", not the pinned worktree")
spec = importlib.util.spec_from_file_location("ledgerline.validate.golden", golden_file)
mod = importlib.util.module_from_spec(spec)
sys.modules["ledgerline.validate.golden"] = mod
spec.loader.exec_module(mod)
print(marker + json.dumps(mod.compute()))
"""


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", repro.REPO, *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def arithmetic_signature(commit: str | None = None) -> str:
    """sha256 of a gate's outputs on the pinned golden cases.

    With no commit, the code in this checkout. With a commit, that commit's
    scorer run in a throwaway worktree against this checkout's fixtures, so
    every commit is measured on identical inputs.
    """
    if commit is None:
        return _sha(golden.compute())
    golden_file = os.path.join(repro.REPO, "ledgerline", "validate", "golden.py")
    cases = repro.run_at_commit(commit, script=_GOLDEN_AT_COMMIT, args=(golden_file,),
                                timeout=600)
    return _sha(cases)


# ------------------------------------------------------------------ storage


def _entry_hash(h: dict) -> str:
    return _sha({k: v for k, v in h.items() if k != "entry_sha256"})


def load() -> dict:
    """The registry. Raises when absent: registrations are commitments, and a
    default empty registry would silently forget them."""
    if not os.path.exists(HYPOTHESES_PATH):
        raise RuntimeError(
            "ledgerline/data/hypotheses.json is missing. It's committed; restore it "
            "with `git checkout ledgerline/data/hypotheses.json`. On a brand-new "
            "project, `ledgerline hypothesis init` creates it with h0."
        )
    with open(HYPOTHESES_PATH) as fh:
        reg = json.load(fh)
    for h in reg["hypotheses"].values():
        if h.get("entry_sha256") != _entry_hash(h):
            raise RuntimeError(
                f"hypothesis {h['id']} was edited after it was written. A "
                "registration is only worth anything if it can't be changed "
                "after the fact. Restore the file from git."
            )
    return reg


def _write(reg: dict) -> None:
    for h in reg["hypotheses"].values():
        h["entry_sha256"] = _entry_hash(h)
    with open(HYPOTHESES_PATH, "w") as fh:
        json.dump(reg, fh, indent=1, sort_keys=True)
        fh.write("\n")


def init(*, h0_arithmetic: str | None = None) -> dict:
    """Create the registry with h0, the Phase 0 gate. Refuses to overwrite."""
    if os.path.exists(HYPOTHESES_PATH):
        raise RuntimeError("hypotheses.json already exists; it's never recreated.")
    frozen = status.load()
    with open(harness.PREREG_PATH) as fh:
        rule = json.load(fh)["rule"]
    h0 = {
        "id": "h0",
        "name": "Phase 0 gate",
        "description": ("Thirteen XBRL diagnostics, robust z against each company's own "
                        "history, ridge-logistic weights fitted on the tuning split."),
        "created": frozen["scored_on"],
        "commit": repro.PHASE0_COMMIT,
        # No GATE_VERSION existed at this commit. Its constants are identical
        # to gate 3.2.0's; its arithmetic is not, which is the whole point.
        "gate_version": "phase0",
        "fingerprint": signals_v3.gate_fingerprint(),
        "arithmetic_sha256": h0_arithmetic or arithmetic_signature(repro.PHASE0_COMMIT),
        "label_rule": rule["label"],
        "decision_rule": rule,
        "decision_rule_sha256": _sha(rule),
        "status": "scored",
        "registration": None,
        "result": {
            "set": "holdout",
            "verdict": frozen["verdict"],
            "checks": frozen["checks"],
            "false_positive_rate_per_filer": frozen["false_positive_rate_per_filer"],
            "writeup": frozen["writeup"],
        },
        "comparator_on": [],
    }
    reg = {"created": date.today().isoformat(), "hypotheses": {"h0": h0}}
    _write(reg)
    return reg


# ------------------------------------------------------------------ actions


def new(hid: str, name: str, description: str) -> dict:
    """Draft a hypothesis from the code at HEAD.

    The checkout must be committed: a hypothesis is the code at a commit, and
    code that isn't committed can't be re-run later to check the result.
    """
    if not ID_PATTERN.match(hid):
        raise RuntimeError(f"'{hid}' isn't a hypothesis id. Use h1, h2, ...")
    if not name.strip():
        raise RuntimeError("Give it a name: --name \"what it tests\".")
    reg = load()
    if hid in reg["hypotheses"]:
        raise RuntimeError(f"{hid} already exists. Ids are never reused.")
    dirty = _git("status", "--porcelain", "--", "ledgerline/")
    if dirty:
        raise RuntimeError(
            "ledgerline/ has uncommitted changes. A hypothesis is the code at a "
            "commit, and uncommitted code can't be re-run to check a result. "
            "Commit the gate first."
        )
    commit = _git("rev-parse", "HEAD")
    arith = arithmetic_signature()
    for other in reg["hypotheses"].values():
        if other["commit"] == commit:
            raise RuntimeError(f"{other['id']} is already the code at this commit.")
        if other["arithmetic_sha256"] == arith:
            raise RuntimeError(
                f"this code scores the 40 pinned golden cases exactly as "
                f"{other['id']} does, so it's the same gate. Registering it would "
                "spend error budget testing one gate twice. Change the scoring, "
                "then draft it."
            )
    with open(harness.PREREG_PATH) as fh:
        rule = json.load(fh)["rule"]
    h = {
        "id": hid,
        "name": name.strip(),
        "description": description.strip(),
        "created": date.today().isoformat(),
        "commit": commit,
        "gate_version": signals_v3.GATE_VERSION,
        "fingerprint": signals_v3.gate_fingerprint(),
        "arithmetic_sha256": arith,
        "label_rule": rule["label"],
        "decision_rule": rule,
        "decision_rule_sha256": _sha(rule),
        "status": "draft",
        "registration": None,
        "result": None,
        "comparator_on": [],
    }
    reg["hypotheses"][hid] = h
    _write(reg)
    return h


def registration_deadline(reserved: str) -> str:
    """The last day a hypothesis can register against `reserved`: the day
    before its first checkpoint."""
    return min(retest.load_reserved(reserved)["cutoffs"])


def register(hid: str, reserved: str, alpha: float, note: str,
             *, today: str | None = None) -> dict:
    """Commit a drafted hypothesis to a reserved set, before that set's data
    exists, drawing on the one shared error budget."""
    reg = load()
    h = reg["hypotheses"].get(hid)
    if h is None:
        raise RuntimeError(f"no hypothesis {hid}. Draft it: ledgerline hypothesis new")
    if h["status"] != "draft":
        raise RuntimeError(f"{hid} is {h['status']}, not a draft. Registration happens once.")
    today_s = today or date.today().isoformat()
    first = registration_deadline(reserved)
    if today_s >= first:
        raise RuntimeError(
            f"registration against '{reserved}' closed on {first}, its first "
            "checkpoint. A hypothesis designed after that could have been shaped "
            "by data at that checkpoint. Reserve a new set from a later date "
            "(ledgerline retest reserve) and register against that."
        )
    _git("cat-file", "-e", f"{h['commit']}^{{commit}}")
    attempt = retest.register(f"{hid}@{h['commit'][:12]}", reserved, alpha, note)
    h["registration"] = {
        "reserved": reserved,
        "reserved_sha256": attempt["reserved_sha256"],
        "registered_on": attempt["registered_on"],
        "alpha": alpha,
        "contamination_note": attempt["contamination_note"],
        "scoreable_from": retest.load_reserved(reserved)["earliest_scoreable_h4"],
    }
    h["registration"]["sha256"] = _sha({k: h[k] for k in (
        "id", "commit", "fingerprint", "arithmetic_sha256", "label_rule",
        "decision_rule_sha256")} | {"registration": h["registration"]})
    h["status"] = "registered"
    h0 = reg["hypotheses"]["h0"]
    if reserved not in h0["comparator_on"]:
        h0["comparator_on"].append(reserved)
    _write(reg)
    return h


def score(hid: str, *, today: str | None = None) -> dict:
    reg = load()
    h = reg["hypotheses"].get(hid)
    if h is None or h["status"] == "draft":
        raise RuntimeError(
            f"{hid} isn't registered. Nothing is scored without a registration "
            "made before its data existed; otherwise the test can be fitted to "
            "the answer."
        )
    if h["status"] == "scored":
        raise RuntimeError(f"{hid} was already scored on {h['result']['set']}. Once.")
    when = h["registration"]["scoreable_from"]
    today_s = today or date.today().isoformat()
    if today_s < when:
        raise RuntimeError(
            f"{hid} is registered against '{h['registration']['reserved']}', which "
            f"can't be scored before {when}: its first checkpoint's outcomes need "
            "four quarters of filings to resolve."
        )
    raise RuntimeError(
        f"'{h['registration']['reserved']}' has matured, but the comparison is "
        "deliberately unwritten until now (see validate/retest.py). Write it, "
        "pre-register it as its own commit, and score h0 alongside on the same set."
    )


def compare(a: str, b: str) -> dict:
    """Two hypotheses side by side, and whether their results are comparable."""
    reg = load()
    ha, hb = reg["hypotheses"].get(a), reg["hypotheses"].get(b)
    if ha is None or hb is None:
        raise RuntimeError(f"unknown hypothesis: {a if ha is None else b}")
    fa, fb = ha["fingerprint"], hb["fingerprint"]
    constants = []
    for key in sorted(set(fa) | set(fb)):
        if key == "tracked":
            for m in sorted(set(fa["tracked"]) | set(fb["tracked"])):
                wa = fa["tracked"].get(m, {}).get("weight")
                wb = fb["tracked"].get(m, {}).get("weight")
                if wa != wb:
                    constants.append((f"weight {m}", wa, wb))
        elif fa.get(key) != fb.get(key):
            constants.append((key, fa.get(key), fb.get(key)))
    set_a = (ha["result"] or {}).get("set") or (ha["registration"] or {}).get("reserved")
    set_b = (hb["result"] or {}).get("set") or (hb["registration"] or {}).get("reserved")
    comparable = (ha["status"] == hb["status"] == "scored" and set_a == set_b)
    return {
        "a": ha, "b": hb,
        "same_commit": ha["commit"] == hb["commit"],
        "same_arithmetic": ha["arithmetic_sha256"] == hb["arithmetic_sha256"],
        "constants_that_differ": constants,
        "same_decision_rule": ha["decision_rule_sha256"] == hb["decision_rule_sha256"],
        "set_a": set_a, "set_b": set_b,
        "results_comparable": comparable,
    }
