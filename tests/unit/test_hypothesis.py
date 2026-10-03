"""The hypothesis registry (ROADMAP_V1 1.0-b)."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ledgerline import hypothesis as hyp
from ledgerline import reproduce, signals_v3, status
from ledgerline.validate import retest

FAKE_ARITH = "a" * 64


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """Isolated copies of the hypothesis and retest registries."""
    hpath = tmp_path / "hypotheses.json"
    rpath = tmp_path / "retests.json"
    shutil.copy(hyp.HYPOTHESES_PATH, hpath)
    shutil.copy(retest.RETESTS_PATH, rpath)
    monkeypatch.setattr(hyp, "HYPOTHESES_PATH", str(hpath))
    monkeypatch.setattr(retest, "RETESTS_PATH", str(rpath))
    return hpath


@pytest.fixture
def clean_head(monkeypatch):
    """Pretend HEAD is a committed, clean tree at a new commit with new arithmetic."""
    real_git = hyp._git

    def fake_git(*args):
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "f" * 40
        if args[0] == "cat-file":
            return ""
        return real_git(*args)
    monkeypatch.setattr(hyp, "_git", fake_git)
    monkeypatch.setattr(hyp, "arithmetic_signature", lambda commit=None: FAKE_ARITH)


# ------------------------------------------------------------------- h0


def test_h0_is_the_phase0_gate_and_its_failed_result():
    h0 = hyp.load()["hypotheses"]["h0"]
    frozen = status.load()
    assert h0["commit"] == reproduce.PHASE0_COMMIT
    assert h0["status"] == "scored"
    assert h0["result"]["verdict"] == frozen["verdict"] == "KILL"
    assert h0["result"]["checks"] == frozen["checks"]


@pytest.mark.skipif(
    subprocess.run(["git", "cat-file", "-e", f"{reproduce.PHASE0_COMMIT}^{{commit}}"],
                   capture_output=True).returncode != 0,
    reason="shallow clone without the Phase 0 commit")
def test_h0_arithmetic_signature_is_what_its_commit_actually_computes():
    """The stored hash must be re-derivable, or it's just a string."""
    h0 = hyp.load()["hypotheses"]["h0"]
    assert hyp.arithmetic_signature(reproduce.PHASE0_COMMIT) == h0["arithmetic_sha256"]


def test_constants_do_not_identify_a_gate():
    """1.0-a showed it: the Phase 0 gate and the current code share every
    constant and still score differently. The registry tells them apart by
    arithmetic, which the fingerprint can't."""
    h0 = hyp.load()["hypotheses"]["h0"]
    assert h0["fingerprint"] == signals_v3.gate_fingerprint()
    assert h0["arithmetic_sha256"] != hyp.arithmetic_signature()


# ---------------------------------------------------------------- storage


def test_missing_registry_raises_instead_of_starting_empty(tmp_path, monkeypatch):
    """An empty default would silently forget every registration."""
    monkeypatch.setattr(hyp, "HYPOTHESES_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(RuntimeError, match="missing"):
        hyp.load()


def test_an_edited_entry_is_refused(reg):
    data = json.loads(reg.read_text())
    data["hypotheses"]["h0"]["result"]["verdict"] = "SHIP"
    reg.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="edited after"):
        hyp.load()


def test_init_never_recreates(reg):
    with pytest.raises(RuntimeError, match="never recreated"):
        hyp.init(h0_arithmetic=FAKE_ARITH)


# -------------------------------------------------------------------- new


def test_new_refuses_uncommitted_code(reg, monkeypatch):
    real_git = hyp._git
    monkeypatch.setattr(hyp, "_git", lambda *a: " M ledgerline/signals_v3.py"
                        if a[:2] == ("status", "--porcelain") else real_git(*a))
    with pytest.raises(RuntimeError, match="uncommitted"):
        hyp.new("h1", "test", "")


def test_new_refuses_a_gate_that_scores_identically_to_an_existing_one(reg, clean_head,
                                                                     monkeypatch):
    """A README-only commit would otherwise be a 'new' hypothesis and spend
    budget re-testing the same gate."""
    h0 = hyp.load()["hypotheses"]["h0"]
    monkeypatch.setattr(hyp, "arithmetic_signature",
                        lambda commit=None: h0["arithmetic_sha256"])
    with pytest.raises(RuntimeError, match="same gate"):
        hyp.new("h1", "test", "")


def test_new_rejects_bad_and_reused_ids(reg, clean_head):
    with pytest.raises(RuntimeError, match="isn't a hypothesis id"):
        hyp.new("revised", "x", "")
    with pytest.raises(RuntimeError, match="never reused"):
        hyp.new("h0", "x", "")


def test_new_records_commit_constants_and_arithmetic(reg, clean_head):
    h = hyp.new("h1", "coverage-weighted gate", "test")
    assert h["status"] == "draft"
    assert h["commit"] == "f" * 40
    assert h["arithmetic_sha256"] == FAKE_ARITH
    assert h["fingerprint"] == signals_v3.gate_fingerprint()


# --------------------------------------------------------------- register


def test_registration_closes_at_the_sets_first_checkpoint(reg, clean_head):
    hyp.new("h1", "x", "")
    deadline = hyp.registration_deadline("r1")
    with pytest.raises(RuntimeError, match="closed"):
        hyp.register("h1", "r1", 0.01, "knew the KILL", today=deadline)


def test_registration_draws_the_one_shared_budget(reg, clean_head):
    hyp.new("h1", "x", "")
    before = retest.alpha_spent()
    hyp.register("h1", "r1", 0.02, "knew the Phase 0 KILL", today="2026-10-03")
    assert retest.alpha_spent() == pytest.approx(before + 0.02)


def test_h0_rides_along_as_comparator(reg, clean_head):
    """Comparing a 2026-28 result with h0's 2013-25 holdout numbers would credit
    or blame a hypothesis for the period it was tested in."""
    hyp.new("h1", "x", "")
    hyp.register("h1", "r1", 0.02, "knew the KILL", today="2026-10-03")
    assert "r1" in hyp.load()["hypotheses"]["h0"]["comparator_on"]


def test_registration_happens_once(reg, clean_head):
    hyp.new("h1", "x", "")
    hyp.register("h1", "r1", 0.02, "knew the KILL", today="2026-10-03")
    with pytest.raises(RuntimeError, match="not a draft"):
        hyp.register("h1", "r1", 0.01, "again", today="2026-10-03")


def test_contamination_note_is_mandatory(reg, clean_head):
    hyp.new("h1", "x", "")
    with pytest.raises(RuntimeError, match="contamination note"):
        hyp.register("h1", "r1", 0.02, "   ", today="2026-10-03")


# ------------------------------------------------------------------ score


def test_nothing_unregistered_is_scored(reg, clean_head):
    hyp.new("h1", "x", "")
    with pytest.raises(RuntimeError, match="isn't registered"):
        hyp.score("h1")


def test_a_registered_hypothesis_waits_for_its_set_to_mature(reg, clean_head):
    hyp.new("h1", "x", "")
    hyp.register("h1", "r1", 0.02, "knew the KILL", today="2026-10-03")
    with pytest.raises(RuntimeError, match="can't be scored before 2028-02-12"):
        hyp.score("h1", today="2027-06-01")


def test_h0_is_not_scored_twice(reg):
    with pytest.raises(RuntimeError, match="already scored"):
        hyp.score("h0")


# ---------------------------------------------------------------- compare


def test_compare_says_why_results_are_not_comparable(reg, clean_head):
    hyp.new("h1", "x", "")
    c = hyp.compare("h0", "h1")
    assert c["constants_that_differ"] == []
    assert not c["same_arithmetic"]
    assert not c["results_comparable"]
    assert c["set_a"] == "holdout"
