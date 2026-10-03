"""Pinned scoring arithmetic (ROADMAP_V1 1.0-a).

The total_debt double-count sat in the code from before Phase 0 until the
second audit, under 370 passing tests, because nothing compared today's
numbers with yesterday's. These tests do. See ledgerline/validate/golden.py.
"""
from __future__ import annotations

import gzip
import json

from ledgerline import signals_v3
from ledgerline.validate import golden


def test_golden_was_produced_by_this_gate_version():
    """An intended arithmetic change bumps GATE_VERSION and regenerates the
    golden file in the same commit, so stored signals stay attributable to the
    gate that produced them."""
    pinned = golden.load_golden()["gate_version"]
    assert pinned == signals_v3.GATE_VERSION, (
        f"GATE_VERSION is {signals_v3.GATE_VERSION} but the golden outputs were "
        f"produced by {pinned}. If the arithmetic changed on purpose, run "
        "`python scripts/golden.py` and say in the commit what moved and why."
    )


def test_scoring_arithmetic_has_not_moved():
    """Outputs that changed while GATE_VERSION stayed put are an undeclared
    arithmetic change: the bug class the second audit found by hand."""
    expected = golden.load_golden()["cases"]
    actual = golden.compute()
    moved = golden.diff(expected, actual)
    assert not moved, (
        f"{len(moved)} scoring outputs moved without a GATE_VERSION bump:\n  "
        + "\n  ".join(moved[:25])
        + ("\n  ..." if len(moved) > 25 else "")
    )


def test_golden_covers_every_outcome_shape():
    """A pin that only covers quiet companies can't catch a change to how
    flags or abstentions are computed."""
    cases = golden.load_golden()["cases"]
    assert any(c["gated_in"] for c in cases), "no flagged case"
    assert any(c["scoreable"] and not c["gated_in"] for c in cases), "no quiet case"
    assert any(not c["scoreable"] for c in cases), "no unassessable case"


def test_fixtures_carry_only_the_concepts_the_metric_layer_reads():
    """Trimmed so the fixtures stay small enough to commit. A concept the
    metric layer starts reading has to be added to the fixtures too, or the
    pin silently stops covering it."""
    wanted = golden.concepts()
    for ticker in golden.COMPANIES:
        with gzip.open(golden.fixture_path(ticker), "rt") as fh:
            have = set(json.load(fh)["facts"]["us-gaap"])
        assert have <= wanted, f"{ticker} carries unread concepts {sorted(have - wanted)}"


def test_diff_names_the_field_that_moved():
    a = [{"ticker": "X", "cutoff": "2020-01-01", "score": 10.0, "z": {"dso": 1.0}}]
    b = [{"ticker": "X", "cutoff": "2020-01-01", "score": 12.0, "z": {"dso": 1.5}}]
    lines = golden.diff(a, b)
    assert "X 2020-01-01 score: 10.0 -> 12.0" in lines
    assert "X 2020-01-01 z.dso: 1.0 -> 1.5" in lines
