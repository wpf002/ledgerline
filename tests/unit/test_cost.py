"""
Cost-model tests: the empirical verification ROADMAP §10 asked for, expressed
as tests rather than a report someone has to read.

The tier0/tier1 pair is the deliverable: one test asserts the market-wide
change check is identical at N=10 and N=1000, the other asserts refresh work
grows at least 5x between N=100 and N=1000. Together they pin "flat in
universe size" as true of the change detector and false of the run. The rest
pin that the model is actually a model (zero network), that an empty cache
yields a refusal rather than a guess, and that no cost artifact ships without
the frozen KILL verdict.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from ledgerline import cost, edgar, status

CONSTANTS = {
    "n_sampled": 2,
    "bytes": {"median": 1000.0, "p90": 2000.0, "max": 3000.0},
    "parse_seconds": {"median": 0.01, "p90": 0.02, "max": 0.03},
    "bias": cost.BIAS_NOTE,
}


def _isolated_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def _synthetic_registry(n_filers: int) -> None:
    """n_filers CIKs, each filing one 10-Q per quarter of 2023, with filing
    days spread the way filing season spreads them: clustered, not uniform."""
    conn = edgar.db()
    rows = []
    for i in range(n_filers):
        cik = edgar.pad(str(i + 1))
        for q, month in enumerate((2, 5, 8, 11)):
            day = (i % 15) + 1  # cluster within a two-and-a-bit week window
            rows.append((cik, f"2023Q{q + 1}", "10-Q", f"2023-{month:02d}-{day:02d}",
                         f"acc-{cik}-{q}", f"FILER {i} INC"))
    with conn:
        conn.executemany(
            "INSERT INTO filer_registry (cik, quarter, form, filed, accession, "
            "name) VALUES (?,?,?,?,?,?)", rows)
    conn.close()


def _no_network(monkeypatch) -> None:
    def refuse(*a, **k):
        raise AssertionError("the cost model touched the network")
    monkeypatch.setattr(edgar, "fetch", refuse)


def test_replay_makes_no_network_calls(tmp_path, monkeypatch):
    """A cost model that quietly hits the network is not a model and cannot
    run in CI. The whole measurement runs with edgar.fetch() booby-trapped."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(50)
    _no_network(monkeypatch)
    payload = cost.measure_scaling([10], "2023-01-01", "2023-12-31",
                                   constants=CONSTANTS)
    assert payload["sizes"]["10"]["days"] == 260


def test_tier0_request_count_is_flat_in_universe_size(tmp_path, monkeypatch):
    """The half of the ROADMAP claim that is TRUE: the market-wide change
    check is one request per run at every universe size."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(1000)
    _no_network(monkeypatch)
    payload = cost.measure_scaling([10, 1000], "2023-01-01", "2023-12-31",
                                   constants=CONSTANTS)
    assert payload["sizes"]["10"]["tier0_requests_per_run"] == 1.0
    assert payload["sizes"]["1000"]["tier0_requests_per_run"] == 1.0


def test_tier1_request_count_grows_with_universe_size(tmp_path, monkeypatch):
    """The half that is FALSE: per-run cost is 1 + K(N) requests and K is
    linear in N, so a 10x universe means roughly 10x the busy-day refresh
    work. Asserted at >= 5x to leave sampling noise room without letting a
    flat implementation pass."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(1000)
    _no_network(monkeypatch)
    payload = cost.measure_scaling([100, 1000], "2023-01-01", "2023-12-31",
                                   constants=CONSTANTS)
    small = payload["sizes"]["100"]["tier1_requests_per_day"]["p90"]
    large = payload["sizes"]["1000"]["tier1_requests_per_day"]["p90"]
    assert large >= 5 * small
    # bytes scale with the same K, so the disagreement shows up there too
    assert (payload["sizes"]["1000"]["bytes_per_day"]["p90"]
            >= 5 * payload["sizes"]["100"]["bytes_per_day"]["p90"])


def test_empty_cache_refuses_a_guess(tmp_path, monkeypatch):
    """House rule: a value that cannot be computed is None, never a guess.
    With no companyfacts on disk the constants are None, and replay() refuses
    to project instead of inventing a byte figure that would be read as a
    measurement."""
    monkeypatch.setattr(edgar, "CACHE", str(tmp_path / "cache"))
    consts = cost.measure_constants()
    assert consts["n_sampled"] == 0
    assert consts["bytes"] is None
    with pytest.raises(ValueError, match="ledgerline fetch"):
        cost.replay({"0000000001"}, "2023-01-01", "2023-03-31", consts)


def test_constants_are_measured_from_the_local_cache(tmp_path, monkeypatch):
    """The per-filer byte constant is the size of real cached documents, not a
    number typed into the source -- and the payload names its own directional
    bias (large-cap cache overstates bytes at scale) the same way
    scripts/sp1500.py documents its survivorship bias."""
    facts = tmp_path / "cache" / "facts"
    facts.mkdir(parents=True)
    (facts / "CIK0000000001.json").write_text(json.dumps({"pad": "x" * 100}))
    (facts / "CIK0000000002.json").write_text(json.dumps({"pad": "x" * 100}))
    monkeypatch.setattr(edgar, "CACHE", str(tmp_path / "cache"))
    consts = cost.measure_constants()
    assert consts["n_sampled"] == 2
    assert consts["bytes"]["median"] == (facts / "CIK0000000001.json").stat().st_size
    assert "OVERSTATE" in consts["bias"]


def test_cost_payload_carries_the_kill_verdict(tmp_path, monkeypatch):
    """A cost curve for scaling a detector is only honest next to the fact
    that the detector failed its own test. Every payload is stamped with the
    frozen Phase 0 numbers, and report() refuses an unstamped one."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(20)
    _no_network(monkeypatch)
    payload = cost.measure_scaling([10], "2023-01-01", "2023-06-30",
                                   constants=CONSTANTS)
    status.assert_stamped(payload)  # numbers intact, not just a key present
    assert payload["phase0"]["positive_hit_rate"] == 0.287
    assert payload["phase0"]["fpr_per_control_quarter"] == 0.0383
    with pytest.raises(AssertionError, match="stamp"):
        cost.report({"sizes": {}}, path=str(tmp_path / "cost.json"))


def test_measurement_is_committed_to_cost_samples(tmp_path, monkeypatch):
    """A verification that lives only in a printed table cannot be re-checked
    later: each size lands as a cost_samples row, mode 'replay', with the full
    per-size breakdown preserved in the note."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(50)
    _no_network(monkeypatch)
    payload = cost.measure_scaling([10, 40], "2023-01-01", "2023-12-31",
                                   constants=CONSTANTS)
    assert cost.persist_samples(payload) == 2
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    rows = conn.execute(
        "SELECT mode, universe_size, note FROM cost_samples "
        "ORDER BY universe_size").fetchall()
    conn.close()
    assert [(r[0], r[1]) for r in rows] == [("replay", 10), ("replay", 40)]
    assert json.loads(rows[0][2])["tier0_requests_per_run"] == 1.0


def test_scaling_samples_are_deterministic(tmp_path, monkeypatch):
    """--limit-style sampling must be reproducible: the same seed and size
    pick the same filers, so a committed cost measurement can be re-derived
    rather than merely believed."""
    _isolated_db(tmp_path, monkeypatch)
    _synthetic_registry(200)
    _no_network(monkeypatch)
    one = cost.measure_scaling([50], "2023-01-01", "2023-12-31",
                               constants=CONSTANTS)
    two = cost.measure_scaling([50], "2023-01-01", "2023-12-31",
                               constants=CONSTANTS)
    assert (one["sizes"]["50"]["tier1_requests_per_day"]
            == two["sizes"]["50"]["tier1_requests_per_day"])


def test_summary_reports_percentiles_never_a_mean():
    """Filing arrival is clustered ~29x between the median day and the peak
    (measured 7/day vs 201/day), so a mean describes no day that ever
    happens. The summary is median / p90 / max, and an empty series is None,
    not zero."""
    s = cost._summary([1.0] * 9 + [100.0])
    assert s["median"] == 1.0
    assert s["max"] == 100.0
    assert s["p90"] in (1.0, 100.0)  # nearest-rank on the boundary
    assert cost._summary([])["median"] is None
    # a fabricated mean of 10.9 would sit between the two real regimes
    assert s["median"] != pytest.approx(10.9)
