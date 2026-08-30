"""The plain-language surface. Pins the audit findings, not the phrasing:
wording may improve, but these behaviours must not regress.
"""
from __future__ import annotations

import urllib.error

import pytest

from ledgerline import edgar, render


def test_unscoreable_explain_shows_no_score():
    """score 0.0 beside scoreable=false read as a clean bill of health."""
    res = {"ticker": "ABNB", "scoreable": False, "score": None,
           "reason": "no revenue facts filed as of cutoff"}
    text = render.explain(res)
    assert "CANNOT ASSESS" in text
    # no numeric score anywhere -- "of 100" only appears on scored output
    assert "of 100" not in text
    assert "No score is shown" in text


def test_flagged_output_carries_the_failed_test_caveat():
    res = {"ticker": "T", "scoreable": True, "score": 53.4, "gated_in": True,
           "as_of": "2018-11-15", "period": "2018-09-30", "derived_fraction": 0.1,
           "z": {"cash_conversion_gap": 2.1},
           "flags": [{"code": "CASH_CONVERSION_GAP", "label": "x", "z": 2.1,
                      "value": 0.5, "baseline_median": -0.07,
                      "baseline_scale": 0.27, "baseline_n": 20,
                      "floored": False, "weight": 1.0, "detail": ""}]}
    text = render.explain(res)
    assert "missed its own test" in text
    assert "FLAGGED" in text


def test_not_flagged_output_also_carries_the_caveat():
    """A quiet result from a detector that misses 7 in 10 is not a clean bill
    of health, and must say so."""
    res = {"ticker": "T", "scoreable": True, "score": 0.0, "gated_in": False,
           "as_of": "2023-05-15", "period": "2023-04-01",
           "derived_fraction": 0.0, "z": {}, "flags": []}
    assert "missed its own test" in render.explain(res)


def test_floored_scale_is_rendered_as_a_ceiling_warning():
    """[scale floored] was the only hint that a sigma figure was a floor
    artifact, on a flag that carried most of the score."""
    res = {"ticker": "T", "scoreable": True, "score": 50.0, "gated_in": True,
           "as_of": "2018-11-15", "period": "2018-09-30", "derived_fraction": 0,
           "z": {"gross_margin": 4.6},
           "flags": [{"code": "GROSS_MARGIN", "label": "x", "z": 4.6,
                      "value": 0.736, "baseline_median": 0.759,
                      "baseline_scale": 0.005, "baseline_n": 20,
                      "floored": True, "weight": 1.0, "detail": ""}]}
    assert "ceiling, not a measurement" in render.explain(res)


def test_no_snake_case_reaches_the_reader_in_prose():
    """Machine keys stay in --json; the terminal gets English. The technical
    parenthetical is the one sanctioned exception."""
    res = {"ticker": "T", "scoreable": False, "score": None,
           "reason": "insufficient quarterly coverage: operating_cash_flow 55%"}
    text = render.explain(res)
    assert "operating_cash_flow" not in text
    assert "cash from operations" in text


def test_plain_reason_translates_short_history():
    out = render.plain_reason("insufficient own-history (6q of 12)")
    assert "6 quarters" in out and "12 are needed" in out


def test_check_line_ready_with_soft_gaps_is_not_excluded():
    """`coverage` used to shout EXCLUDED at companies the gate scored happily,
    because it blocked on ANY metric under 90% instead of the three the gate
    requires."""
    line = render.check_line("AAPL", True, None, ["cost_of_revenue"])
    assert "READY" in line and "EXCLUDED" not in line
    assert "cost of sales" in line


def test_every_tracked_diagnostic_has_a_plain_name():
    from ledgerline import signals_v3
    missing = set(signals_v3.TRACKED) - set(render.PLAIN)
    assert not missing, f"no plain name for {missing}"


# --------------------------------------------------- the weekend 403 crash


def test_fetch_gives_up_immediately_on_client_errors(monkeypatch, tmp_path):
    """SEC returns 403 for a not-yet-published daily index (every weekend).
    fetch() re-raised only 404, so the 403 looped through retries and surfaced
    as a raw traceback blaming the User-Agent. Any 4xx is a fact about the
    request; retrying cannot fix it."""
    monkeypatch.setattr(edgar, "CACHE", str(tmp_path))
    monkeypatch.setenv("LEDGERLINE_UA", "test test@example.com")
    monkeypatch.setattr(edgar, "USER_AGENT", "test test@example.com")

    calls = []

    def fake_urlopen(req, timeout=60):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(edgar.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        edgar.fetch("https://example.com/x")
    assert len(calls) == 1, "a 403 was retried"


def test_daily_index_treats_403_as_no_list_published(monkeypatch):
    """The weekend handler exists in daily_index; the old fetch() made it
    unreachable."""
    def raise_403(url, cache_key=None, retries=3):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(edgar, "fetch", raise_403)
    from datetime import date
    assert edgar.daily_index(date(2026, 8, 30)) == []
