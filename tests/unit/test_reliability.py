"""
Reliability-toolkit tests: the Wilson interval and the raw score-bin table.

Every numeric assertion is against a value computable by hand from the test
body, not against a recorded output -- a regression test that only pins what
the code did on the day it was written cannot catch the code being wrong on
that day. Each test names the defect it prevents.
"""
from __future__ import annotations

import pytest

from ledgerline import reliability, signals_v3


def test_wilson_matches_hand_computed_values():
    """k=0/n=20 gives (0.0, 0.1611): z^2/(n+z^2) = 3.8415/23.8415 exactly.
    k=10/n=20 gives (0.2993, 0.7007), the standard Wilson interval at p=0.5.
    Pinning both ends catches a swapped center/half-width term."""
    lo, hi = reliability.wilson(0, 20)
    assert (round(lo, 4), round(hi, 4)) == (0.0, 0.1611)
    lo, hi = reliability.wilson(10, 20)
    assert (round(lo, 4), round(hi, 4)) == (0.2993, 0.7007)


def test_wilson_is_not_degenerate_at_zero_successes():
    """The normal-approximation interval collapses to a point at k=0 --
    reporting certainty exactly where live counts start. Wilson must not."""
    lo, hi = reliability.wilson(0, 5)
    assert lo == 0.0
    assert hi > 0.0


def test_wilson_returns_none_on_an_empty_count():
    """An interval on zero trials would be a guess wearing brackets."""
    assert reliability.wilson(0, 0) is None


def test_wilson_refuses_an_impossible_count():
    with pytest.raises(ValueError):
        reliability.wilson(6, 5)


def test_score_bins_are_half_open_and_cover_both_endpoints():
    """A score of exactly 0.0 and exactly 100.0 each land in exactly one bin
    and the counts sum to n. A table that silently drops its endpoints is the
    classic version of this bug."""
    rows = [{"score": 0.0}, {"score": 100.0}, {"score": 10.0},
            {"score": 45.0}]
    bins = reliability.score_bins(rows)
    assert sum(b["n"] for b in bins) == len(rows)
    holding_zero = [b for b in bins if b["lo"] <= 0.0 and b["n"] and b["lo"] == 0.0]
    assert len([b for b in bins if b["lo"] == 0.0])
    # 10.0 belongs to [10, 20), not [0, 10): bins are half-open on the right.
    b_low = next(b for b in bins if b["lo"] == 0.0)
    b_ten = next(b for b in bins if b["lo"] == 10.0)
    assert b_low["n"] == 1 and b_ten["n"] == 1
    # 100.0 lands in the FINAL bin, which is closed.
    assert bins[-1]["n"] == 1
    assert holding_zero


def test_the_operating_point_is_a_bin_edge():
    """Fires and non-fires must never share a bin, or the table blurs the one
    boundary the product acts on."""
    assert signals_v3.THRESHOLD in reliability.SCORE_EDGES


def test_score_bins_report_outcome_rates_with_wilson_intervals():
    """A row with an unknown outcome counts toward n but stays out of the
    rate -- treating absence-of-answer as CLEAN is the abstention defect at
    the bin level."""
    rows = [
        {"score": 50.0, "deteriorated": True},
        {"score": 50.0, "deteriorated": False},
        {"score": 55.0, "deteriorated": None},
    ]
    bins = reliability.score_bins(rows)
    b = next(b for b in bins if b["lo"] == 45.0)
    assert b["n"] == 3
    assert b["n_with_outcome"] == 2
    assert b["n_deteriorated"] == 1
    assert b["deterioration_rate"] == 0.5
    assert b["wilson_low"] is not None and b["wilson_high"] is not None


def test_unscoreable_rows_are_excluded_not_binned_at_zero():
    """score=None must not land in the lowest bin: NULL-not-zero is the same
    rule the signal store enforces, applied here."""
    bins = reliability.score_bins([{"score": None, "deteriorated": False}])
    assert sum(b["n"] for b in bins) == 0
