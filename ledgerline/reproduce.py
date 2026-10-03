"""
Re-derive the Phase 0 result from the code that produced it.

ROADMAP_V1 1.0-a. Two audits found 59 reproduced defects in this codebase,
several of which changed published numbers, and the total_debt double-count
predated the whole phase build. A result nobody can re-run has to be taken on
trust, and this project's whole argument is that it doesn't ask for that.

WHY IT RUNS AN OLD COMMIT. The holdout was scored at
e53912fc1dc306f7f374fa295ed5cd5a0a3839b2 (identical scoring code to 693bb77,
which only added the ROADMAP entry). Since then the audit fixes changed 1,291
lines of edgar.py, signals.py and signals_v3.py -- debt counted once, balances
aligned to flows, a structural-abstention rule -- while every constant in
gate_fingerprint() stayed the same. HEAD therefore scores differently and
cannot reproduce 0.287, and the constants alone don't identify the gate that
did. The commit does. So this module checks that commit out into a throwaway
git worktree, points it at the shared filing cache, runs its own holdout
scoring, and compares every number against ledgerline/data/phase0.json.

WHY THIS ISN'T A SECOND LOOK AT THE HOLDOUT. The rule against re-scoring
exists to stop someone retuning the gate and scoring the sealed half again.
This runs the identical gate at the identical commit, so it can't produce new
information about how good the gate is. It can only match, or show that
something moved: non-determinism, a leak in the point-in-time handling, or
the SEC revising history in its own files. Every one of those is a finding.
`reproduce` never runs HEAD's scorer against the holdout; a test pins that.

WHAT A MATCH ALSO PROVES. The cache has been refreshed by daily scans since
2026-08-30, so it now holds vintages filed after every holdout cutoff. The
Phase 0 code truncates by filed date. If those newer vintages leaked into the
2013-2025 cutoffs, the numbers would move. An exact match is evidence the
point-in-time truncation held.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from . import edgar, status
from .validate import harness

PHASE0_COMMIT = "e53912fc1dc306f7f374fa295ed5cd5a0a3839b2"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARKER = "@@LEDGERLINE-REPRODUCE@@"

# phase0.json stores what harness.verdict() returned, so a deterministic rerun
# must match to the last digit. A tolerance would hide exactly the small drift
# this exists to catch.
FLOAT_TOL = 1e-12


@dataclass
class Comparison:
    name: str
    frozen: object
    reproduced: object
    match: bool
    delta: float | None = None


@dataclass
class Report:
    commit: str
    comparisons: list[Comparison] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return bool(self.comparisons) and all(c.match for c in self.comparisons)


def holdout_ciks() -> list[tuple[str, str]]:
    """(ticker, cik) for every holdout case, from the frozen split records."""
    with open(harness.SPLIT_PATH) as fh:
        payload = json.load(fh)
    frozen = payload["cases"]
    return sorted((t, frozen[t]["cik"]) for t in payload["holdout"])


def ensure_cache(pairs: list[tuple[str, str]]) -> dict:
    """Download any holdout company's filing history that isn't cached.

    This is the cold path for a fresh clone. It needs LEDGERLINE_UA and takes
    roughly a minute at the SEC's rate limit. The download is TODAY's file,
    which carries vintages filed after Phase 0; the point-in-time truncation
    in the Phase 0 code has to ignore them, and a match proves it did.
    """
    present, fetched, failed = 0, 0, []
    for ticker, cik in pairs:
        path = os.path.join(edgar.CACHE, "facts", f"CIK{edgar.pad(cik)}.json")
        if os.path.exists(path):
            present += 1
            continue
        try:
            edgar.companyfacts(cik)
            fetched += 1
        except Exception as exc:  # noqa: BLE001 -- recorded and reported per company
            failed.append({"ticker": ticker, "cik": cik, "error": str(exc)[:200]})
    return {"present": present, "fetched": fetched, "failed": failed}


_SCRIPT = """
import json, os, sys
root = sys.argv[1]
sys.path.insert(0, root)
import ledgerline
here = os.path.realpath(os.path.dirname(ledgerline.__file__))
if not here.startswith(os.path.realpath(root)):
    raise SystemExit("imported ledgerline from " + here + ", not the pinned worktree")
from ledgerline import backtest
r = backtest.run(split="holdout")
print(sys.argv[2] + json.dumps({"verdict": r["verdict"], "baseline": r.get("baseline")}))
"""


def run_at_commit(commit: str, *, cache_dir: str | None = None,
                  timeout: int = 3600, script: str = _SCRIPT) -> dict:
    """Score the holdout with the code at `commit`, in an isolated worktree.

    The worktree gets its own reports/ and state.db, so nothing in this
    checkout is written. Only the filing cache is shared, read through a
    symlink. PYTHONHASHSEED is pinned so set iteration can't reorder a float
    sum between runs.
    """
    cache_dir = cache_dir or edgar.CACHE
    tmp = tempfile.mkdtemp(prefix="ledgerline-reproduce-")
    tree = os.path.join(tmp, "tree")
    try:
        subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", tree, commit],
                       check=True, capture_output=True, text=True)
        os.symlink(cache_dir, os.path.join(tree, "ledgerline", "data", "cache"))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONHASHSEED"] = "0"
        proc = subprocess.run([sys.executable, "-c", script, tree, MARKER],
                              cwd=tree, env=env, capture_output=True, text=True,
                              timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"the commit {commit[:7]} run failed:\n"
                               + (proc.stderr or proc.stdout)[-2000:])
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(MARKER)), None)
        if line is None:
            raise RuntimeError("the pinned run finished without reporting a result")
        return json.loads(line[len(MARKER):])
    finally:
        subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force", tree],
                       capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _same(a: object, b: object) -> tuple[bool, float | None]:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b, None
    if isinstance(a, int | float) and isinstance(b, int | float):
        d = float(b) - float(a)
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=FLOAT_TOL), d
    return a == b, None


def compare(frozen: dict, reproduced_verdict: dict) -> list[Comparison]:
    """Every number phase0.json publishes, against the rerun."""
    out: list[Comparison] = []

    def add(name: str, a: object, b: object) -> None:
        ok, d = _same(a, b)
        out.append(Comparison(name, a, b, ok, d))

    add("verdict", frozen["verdict"], reproduced_verdict.get("verdict"))
    for key, chk in frozen["checks"].items():
        got = reproduced_verdict.get("checks", {}).get(key, {})
        if isinstance(chk["value"], dict):
            for sub, v in chk["value"].items():
                add(f"{key}.{sub}", v, (got.get("value") or {}).get(sub))
        else:
            add(key, chk["value"], got.get("value"))
        if key == "beats_naive_baseline":
            add(f"{key}.baseline", chk["limit"], got.get("limit"))
        add(f"{key}.pass", chk["pass"], got.get("pass"))
    for key in ("false_positive_rate_per_filer", "control_filer_quarters",
                "n_positive", "n_control", "n_censored_positives",
                "n_assessable_positives"):
        add(key, frozen[key], reproduced_verdict.get(key))
    add("regimes_detected", sorted(frozen["regimes_detected"]),
        sorted(reproduced_verdict.get("regimes_detected") or []))
    return out


def reproduce(*, fetch_missing: bool = True) -> Report:
    frozen = status.load()
    report = Report(commit=PHASE0_COMMIT)
    pairs = holdout_ciks()
    report.cache = ensure_cache(pairs) if fetch_missing else {"present": None}
    if report.cache.get("failed"):
        names = ", ".join(f["ticker"] for f in report.cache["failed"][:8])
        raise RuntimeError(
            f"{len(report.cache['failed'])} holdout companies couldn't be "
            f"downloaded ({names}). Reproduction needs every one. Check "
            "LEDGERLINE_UA and the network, then run again."
        )
    result = run_at_commit(PHASE0_COMMIT)
    report.comparisons = compare(frozen, result["verdict"])
    return report
