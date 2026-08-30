"""Integrity of the re-test reserve.

Every test here pins a defect that would make a future re-test meaningless --
the same class of defect test_validation_integrity.py guards for Phase 0: a
reserved set that quietly contains spent data, a set redrawn until it looks
favourable, an attempt with no record of what its author knew, or an error
budget that refills itself.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from ledgerline import calibrate, status
from ledgerline.validate import harness, retest

TODAY = date.today().isoformat()


@pytest.fixture
def reserve_ws(tmp_path, monkeypatch):
    """Isolated registry, split and tuning dataset. phase0.json stays the real
    committed record -- the reserve deliberately refuses to run without it."""
    monkeypatch.setattr(retest, "DATA", str(tmp_path))
    monkeypatch.setattr(retest, "RETESTS_PATH", str(tmp_path / "retests.json"))
    monkeypatch.setattr(harness, "SPLIT_PATH", str(tmp_path / "split.json"))
    monkeypatch.setattr(calibrate, "DATASET_PATH",
                        str(tmp_path / "tuning_dataset.json"))

    split = {"seed": 1, "created": "2026-08-30", "tuning_frac": 0.55,
             "tuning": ["AAA"], "holdout": ["HOLD"], "cases": {},
             "prereg_sha256": "irrelevant-here"}
    split["sha256"] = harness._hash(split)
    (tmp_path / "split.json").write_text(json.dumps(split))

    # One tuning row lands exactly on a future reserved checkpoint, so the
    # spent-pair exclusion has something real to exclude.
    spent_cutoff = retest.quarterly_cutoffs_between(
        TODAY, (date.today() + timedelta(days=400)).isoformat())[0]
    (tmp_path / "tuning_dataset.json").write_text(json.dumps(
        {"rows": [{"ticker": "AAA", "cutoff": spent_cutoff}]}))
    return tmp_path


def _tickers(n=400):
    out = {f"T{i:04d}": f"{i:010d}" for i in range(n - 2)}
    out["AAA"] = "0000009001"
    out["HOLD"] = "0000009002"
    return out


# ---------------------------------------------------------- what gets reserved


def test_reserved_set_excludes_every_spent_filer_quarter(reserve_ws):
    """A reserved set containing anything the tuning fit saw or the sealed
    test scored is the holdout burn with a delay timer. Holdout companies are
    out entirely; tuning (company, checkpoint) pairs are out individually; and
    every surviving checkpoint is strictly in the future."""
    entry = retest.reserve("r1", TODAY, tickers=_tickers())
    assert "HOLD" not in entry["companies"]
    assert entry["n_holdout_tickers_excluded"] == 1
    assert entry["n_spent_pairs_excluded"] == 1
    pairs = set(retest._expand(entry))
    assert ("0000009001", entry["cutoffs"][0]) not in pairs
    assert all(c > TODAY for c in entry["cutoffs"])
    assert entry["sha256"] == retest.reserved_hash(entry)


def test_reserve_refuses_to_overwrite(reserve_ws):
    """Redrawing a reserved set until it looks favourable is the same burn as
    editing split.json -- make_split() refuses for the identical reason."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    with pytest.raises(RuntimeError, match="never redrawn"):
        retest.reserve("r1", TODAY, tickers=_tickers())


def test_reserve_refuses_checkpoints_already_in_the_past(reserve_ws):
    """Data that exists can have been looked at, and a look cannot be proven
    not to have happened -- that is precisely why the holdout is spent."""
    past = (date.today() - timedelta(days=200)).isoformat()
    with pytest.raises(RuntimeError, match="in the past"):
        retest.reserve("r1", past, tickers=_tickers())


def test_reserve_refuses_an_underpowered_window(reserve_ws):
    """A set that cannot reach the needed count of deteriorating quarters is
    months of waiting for a coin flip; the refusal happens at reserve time,
    when widening the window still fixes it."""
    with pytest.raises(RuntimeError, match="underpowered"):
        retest.reserve("tiny", TODAY,
                       tickers={f"T{i}": f"{i:010d}" for i in range(5)})


def test_reserve_requires_the_frozen_kill_record(reserve_ws, monkeypatch):
    """The floor a revision must beat comes from phase0.json, and a machine
    without that file holds no evidence of the failure -- the same no-default
    rule status.load() enforces at every scoring surface."""
    monkeypatch.setattr(status, "PHASE0_PATH",
                        str(reserve_ws / "no-such-phase0.json"))
    with pytest.raises(RuntimeError, match="phase0.json"):
        retest.reserve("r1", TODAY, tickers=_tickers())


def test_reserved_fingerprint_detects_an_edited_set(reserve_ws):
    """The hash covers the expanded (company, checkpoint) pairs, so removing
    one company after commit -- the smallest favourable edit -- is caught."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    payload = json.loads((reserve_ws / "retests.json").read_text())
    victim = next(iter(payload["reserved"]["r1"]["companies"]))
    del payload["reserved"]["r1"]["companies"][victim]
    (reserve_ws / "retests.json").write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="fingerprint"):
        retest.load_reserved("r1")
    with pytest.raises(RuntimeError):
        retest.register("v4+abc", "r1", 0.025, "author read the KILL report")


# --------------------------------------------------------------- registration


def test_registration_requires_a_contamination_note(reserve_ws):
    """Everyone who read the Phase 0 write-up knows recall is what failed, and
    no hash undoes that knowledge -- recording it is the only honest handling,
    so a blank note is refused rather than defaulted."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    for blank in ("", "   ", "\n"):
        with pytest.raises(RuntimeError, match="note"):
            retest.register("v4+abc", "r1", 0.025, blank)


def test_registration_requires_an_existing_reserved_set(reserve_ws):
    """An attempt against an unreserved set is a test on data someone may
    already have chosen while looking at it."""
    with pytest.raises(RuntimeError, match="no reserved set"):
        retest.register("v4+abc", "ghost", 0.025, "note")


def test_alpha_budget_cannot_be_exceeded(reserve_ws):
    """Five untracked tests at 0.05 each are a 23% chance of a spurious win.
    Two draws of 0.025 exhaust the 0.05 budget; the third raises and the
    status report shows zero remaining -- the budget is never refilled."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    retest.reserve("r2", TODAY, tickers=_tickers())
    retest.reserve("r3", TODAY, tickers=_tickers())
    retest.register("v4+a", "r1", 0.025, "knew the KILL result")
    retest.register("v4+b", "r2", 0.025, "knew the KILL result")
    with pytest.raises(RuntimeError, match="budget"):
        retest.register("v4+c", "r3", 0.025, "knew the KILL result")
    rep = retest.status_report()
    assert rep["alpha_spent"] == 0.05
    assert rep["alpha_remaining"] == 0.0
    assert len(rep["attempts"]) == 2


def test_duplicate_registration_is_refused(reserve_ws):
    """Registering one revision twice against one set would draw the budget
    twice for a single look -- and a genuinely changed revision has a changed
    fingerprint, so a duplicate can only be an accident or a re-roll."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    retest.register("v4+a", "r1", 0.02, "knew the KILL result")
    with pytest.raises(RuntimeError, match="already registered"):
        retest.register("v4+a", "r1", 0.02, "knew the KILL result")


# --------------------------------------------------- the floor and the cut


def test_floor_travels_as_a_floor_not_a_grade(reserve_ws):
    """'Above reference' on a failed test reads as 'performing well' when it
    means 'exceeding a number that already failed'. The registry header
    carries the Phase 0 values with their meaning stated, from the frozen
    record -- never a paraphrase."""
    retest.reserve("r1", TODAY, tickers=_tickers())
    floor = retest.status_report()["floor"]
    assert floor["positive_hit_rate"] == 0.287
    assert floor["gate_status"] == "UNVALIDATED-KILL"
    assert "floor" in floor["meaning"]
    assert "grade" in floor["meaning"]
    assert floor["tuning_recall_per_quarter"] == 0.1396


def test_scoring_machinery_is_deliberately_absent(reserve_ws):
    """The comparison cannot run until a reserved set matures (~2028), and a
    statistical test written eighteen months before first use drifts from
    what it will be asked. Registered attempts are visibly unresolved rather
    than ambiguously absent."""
    assert not hasattr(retest, "score")
    assert not hasattr(retest, "mcnemar_p")
    retest.reserve("r1", TODAY, tickers=_tickers())
    attempt = retest.register("v4+a", "r1", 0.025, "knew the KILL result")
    assert attempt["scored"] is False
    assert attempt["result"] is None


# ------------------------------------------------------------------ the power


def test_power_arithmetic_matches_hand_computed_values():
    """Sample sizes against a value computable by hand, not a recorded output:
    0.14 vs 0.25 at one-sided 0.025 / 80% power needs 203 quarters per arm --
    the derivation behind MIN_DETERIORATING_QUARTERS = 200."""
    assert retest.two_proportion_n(0.14, 0.25) == 203
    assert retest.two_proportion_n(0.25, 0.14) == 203
    assert retest.two_proportion_n(0.1396, 0.25) == 201
    with pytest.raises(ValueError):
        retest.two_proportion_n(0.14, 0.14)
    # Unsupported operating points refuse rather than approximate: the z
    # values are pinned constants, and a bad inverse-normal here would be
    # silently wrong rather than differently right.
    with pytest.raises(ValueError):
        retest.two_proportion_n(0.14, 0.25, alpha=0.05)


def test_earliest_scoreable_date_is_fifteen_months_out():
    """A quarter reserved at cutoff T is undecidable before its deciding
    filings exist: first cutoff after 2026-08-30 is 2026-11-15, plus four
    91-day quarters and a 90-day filing lag lands on 2028-02-12. Nothing
    accelerates this, and pretending otherwise is how spent data gets
    re-used."""
    assert retest.earliest_scoreable_date("2026-08-30") == "2028-02-12"
    # Strictly after: a reservation dated exactly on a checkpoint does not
    # claim that checkpoint.
    assert retest.earliest_scoreable_date("2026-11-15") == "2028-05-14"
