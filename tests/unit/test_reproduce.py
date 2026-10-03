"""Reproducing the Phase 0 result (ROADMAP_V1 1.0-a).

The full reproduction needs the 5 GB filing cache and runs locally:
`ledgerline reproduce`. These tests pin the parts that must hold regardless.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from ledgerline import reproduce, status

REPO = reproduce.REPO


def _have_commit(sha: str) -> bool:
    return subprocess.run(["git", "-C", REPO, "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def test_reproduce_runs_the_pinned_commit_never_head(monkeypatch):
    """HEAD scores differently: the audit fixes changed 1,291 lines of scoring
    arithmetic with every constant unchanged. Running HEAD against the holdout
    would be a second, different look at the sealed half."""
    seen = {}
    monkeypatch.setattr(reproduce, "ensure_cache", lambda pairs: {"present": len(pairs)})

    def fake(commit, **kw):
        seen["commit"] = commit
        return {"verdict": status.load()}
    monkeypatch.setattr(reproduce, "run_at_commit", fake)
    report = reproduce.reproduce()
    assert seen["commit"] == reproduce.PHASE0_COMMIT
    assert report.matched


@pytest.mark.skipif(not _have_commit(reproduce.PHASE0_COMMIT),
                    reason="shallow clone without the Phase 0 commit")
def test_pinned_commit_has_the_code_that_scored_the_holdout():
    """693bb77 recorded the result. Its scoring code must be identical to the
    pinned commit, or the pin points at the wrong gate."""
    out = subprocess.run(["git", "-C", REPO, "diff", "--stat", reproduce.PHASE0_COMMIT,
                          "693bb77", "--", "ledgerline/"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_a_moved_number_is_reported_with_its_delta():
    frozen = status.load()
    moved = {**frozen, "checks": {**frozen["checks"],
             "positive_hit_rate": {**frozen["checks"]["positive_hit_rate"], "value": 0.29}}}
    comps = {c.name: c for c in reproduce.compare(frozen, moved)}
    hit = comps["positive_hit_rate"]
    assert not hit.match and hit.delta == pytest.approx(0.003)


def test_no_tolerance_hides_a_last_digit_drift():
    """phase0.json is what verdict() returned. A deterministic rerun matches
    exactly; any slack would hide the drift this exists to catch."""
    frozen = status.load()
    v = frozen["checks"]["beats_naive_baseline"]["value"]
    nudged = {**frozen, "checks": {**frozen["checks"], "beats_naive_baseline": {
        **frozen["checks"]["beats_naive_baseline"], "value": v + 1e-9}}}
    comps = {c.name: c for c in reproduce.compare(frozen, nudged)}
    assert not comps["beats_naive_baseline"].match


def test_every_published_number_is_compared():
    names = {c.name for c in reproduce.compare(status.load(), status.load())}
    for key in ("verdict", "positive_hit_rate", "median_lead_months",
                "false_positive_rate_per_quarter", "beats_naive_baseline.baseline",
                "false_positive_rate_per_filer", "regimes_detected",
                "sample_size.positives", "sample_size.controls"):
        assert key in names


@pytest.mark.skipif(not _have_commit("HEAD~1"), reason="no history")
def test_worktree_run_is_isolated_and_cleaned_up(tmp_path):
    """The pinned run gets its own reports/ and state.db, imports the package
    from the worktree rather than this checkout, and leaves nothing behind."""
    script = (
        "import json, os, sys\n"
        "root = sys.argv[1]; sys.path.insert(0, root)\n"
        "import ledgerline\n"
        "print(sys.argv[2] + json.dumps({'file': ledgerline.__file__, 'cwd': os.getcwd()}))\n"
    )
    before = subprocess.run(["git", "-C", REPO, "worktree", "list"],
                            capture_output=True, text=True).stdout
    out = reproduce.run_at_commit("HEAD~1", cache_dir=str(tmp_path), script=script)
    after = subprocess.run(["git", "-C", REPO, "worktree", "list"],
                           capture_output=True, text=True).stdout
    assert "ledgerline-reproduce-" in out["file"]
    assert os.path.realpath(out["cwd"]) != os.path.realpath(REPO)
    assert after == before
