"""The frozen Phase 0 record and its stamp. Each test pins a decision:

the record raises when absent instead of defaulting (a default labels the gate
correctly on a machine with no evidence, and mislabels a future gate that
passed); the banner is generated from the file, never typed; freezing happens
exactly once; and no scored payload leaves the CLI unstamped.
"""
from __future__ import annotations

import json
import os

import pytest

from ledgerline import cli, edgar, render, signals_v3, status

# The real holdout verdict, inlined: reports/backtest_holdout.json is
# gitignored, so a test that read it would pass only on the machine that
# scored the holdout -- the exact defect status.py exists to fix.
REPORT = {
    "split": "holdout",
    "threshold": 45.0,
    "z_trigger": 2.0,
    "baseline": {
        "rule": "ttm_ocf_negative_and_net_debt_positive",
        "false_positive_rate_per_quarter": 0.005132699048572859,
        "control_filer_quarters": 7988,
    },
    "verdict": {
        "checks": {
            "false_positive_rate_per_quarter": {"value": 0.0383, "limit": 0.04,
                                                "pass": True},
            "median_lead_months": {"value": 9, "limit": 6, "pass": True},
            "positive_hit_rate": {"value": 0.287, "limit": 0.6, "pass": False},
            "regime_coverage": {"value": 6, "limit": 4, "pass": True},
            "sample_size": {"value": {"positives": 178, "controls": 209},
                            "limit": {"positives": 40, "controls": 200},
                            "pass": True},
            "beats_naive_baseline": {"value": 0.038265639284315006,
                                     "limit": 0.005132699048572859,
                                     "pass": False},
        },
        "false_positive_rate_per_filer": 0.512,
        "control_filer_quarters": 7657,
        "n_positive": 178,
        "n_control": 209,
        "n_censored_positives": 14,
        "n_assessable_positives": 164,
        "regimes_detected": ["2020-covid"],
        "verdict": "KILL",
        "note": "At least one pre-registered criterion failed.",
    },
}

CALIB = {
    "fitted": "2026-08-30",
    "split": "tuning",
    "split_sha256": "aa11",
    "prereg_sha256": "bb22",
    "n_rows": 18480,
    "n_positive_rows": 1699,
    "chosen": {"tuning_fpr_per_quarter": 0.03995,
               "tuning_recall_on_deteriorating_quarters": 0.1396},
}


def _freeze_env(tmp_path, monkeypatch):
    """Point status at a scratch record path with a real-numbered report."""
    report_path = tmp_path / "backtest_holdout.json"
    report_path.write_text(json.dumps(REPORT))
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(CALIB))
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "phase0.json"))
    monkeypatch.setattr(status, "CALIBRATION_PATH", str(calib_path))
    return str(report_path)


# ---------------------------------------------------------------- the record


def test_load_raises_when_the_frozen_record_is_missing(tmp_path, monkeypatch):
    """No default. A machine without phase0.json holds no evidence of the
    2026-08-30 test; defaulting to 'unvalidated' would get the right label for
    the wrong reason today and silently mislabel a future gate that passed."""
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "phase0.json"))
    with pytest.raises(RuntimeError, match="deliberately no default"):
        status.load()


def test_load_raises_when_the_committed_record_is_edited(tmp_path, monkeypatch):
    """Editing the record in place to move the bar must raise, not pass --
    the same discipline harness.load_prereg() applies to prereg.json."""
    report_path = _freeze_env(tmp_path, monkeypatch)
    status.freeze(report_path)
    with open(status.PHASE0_PATH) as fh:
        doctored = json.load(fh)
    doctored["checks"]["positive_hit_rate"]["value"] = 0.65  # a KILL turned SHIP
    with open(status.PHASE0_PATH, "w") as fh:
        json.dump(doctored, fh)
    with pytest.raises(RuntimeError, match="positive_hit_rate"):
        status.load()


def test_banner_cannot_be_produced_without_the_evidence_file(tmp_path, monkeypatch):
    """The banner is generated from the frozen file, never a string literal --
    so on a machine with no evidence there is no banner, only the error."""
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "phase0.json"))
    with pytest.raises(RuntimeError):
        status.banner()


def test_banner_carries_the_numbers_with_their_bars():
    """Every number in the banner comes from the committed record and carries
    its bar (VOICE.md): the hit rate against its floor, the false-alarm rate
    against the baseline it failed to beat, and the per-filer rate."""
    text = status.banner()
    for needed in ("28.7%", "60%", "3.83%", "0.51%", "51.2%", "2026-08-30"):
        assert needed in text
    assert "reports/PHASE0.md" in text


# --------------------------------------------------------------------- freeze


def test_freeze_writes_the_record_and_load_accepts_it(tmp_path, monkeypatch):
    """The verdict, the experiment hashes and the calibration provenance all
    reach the committed file, and load()'s drift check passes on it."""
    report_path = _freeze_env(tmp_path, monkeypatch)
    status.freeze(report_path)
    loaded = status.load()
    assert loaded["verdict"] == "KILL"
    assert loaded["split_sha256"] == "aa11"
    assert loaded["prereg_sha256"] == "bb22"
    assert loaded["calibration"]["split"] == "tuning"
    assert loaded["false_positive_rate_per_filer"] == 0.512


def test_freeze_refuses_to_overwrite(tmp_path, monkeypatch):
    """The record is frozen exactly once, like write_prereg(): rewriting it is
    how a failed test gets relabeled."""
    report_path = _freeze_env(tmp_path, monkeypatch)
    status.freeze(report_path)
    with pytest.raises(RuntimeError, match="frozen exactly once"):
        status.freeze(report_path)


def test_freeze_refuses_a_report_without_a_verdict_block(tmp_path, monkeypatch):
    """A practice-half report has no verdict; freezing one would present a
    tuning run as the one-shot result."""
    report_path = _freeze_env(tmp_path, monkeypatch)
    with open(report_path, "w") as fh:
        json.dump({"split": "tuning", "outcomes": []}, fh)
    with pytest.raises(RuntimeError, match="no verdict block"):
        status.freeze(report_path)


# ---------------------------------------------------------------------- stamp


def test_stamp_mutates_in_place_and_assert_stamped_accepts_it():
    """stamp() mutates rather than wraps, so every dict-returning path
    (Verdict.as_dict, backtest outcomes) stays shape-compatible."""
    payload = {"ticker": "T", "score": 50.0}
    out = status.stamp(payload)
    assert out is payload
    assert payload["gate_status"] == status.GATE_STATUS
    status.assert_stamped(payload)


def test_assert_stamped_rejects_unstamped_and_tampered_payloads():
    """Key presence is not enough: a phase0 block with the wrong numbers is a
    paraphrase, and a paraphrase is where the verdict softens."""
    with pytest.raises(AssertionError, match="gate_status"):
        status.assert_stamped({"ticker": "T", "score": 50.0})
    tampered = status.stamp({"ticker": "T"})
    tampered["phase0"]["positive_hit_rate"] = 0.65
    with pytest.raises(AssertionError, match="positive_hit_rate"):
        status.assert_stamped(tampered)


# --------------------------------------------- the CLI emission points


def _verdict(score=50.0, gated=True):
    return {"ticker": "T", "cik": "0000000001", "as_of": "2026-08-30",
            "period": "2026-06-30", "score": score, "gated_in": gated,
            "scoreable": True, "reason": None, "flags": [], "coverage": {},
            "derived_fraction": 0.0, "diagnostics": {}, "z": {}}


def _watch_one(monkeypatch):
    monkeypatch.setattr(edgar, "universe",
                        lambda: {"0000000001": {"ticker": "T", "name": "T Inc"}})


def test_score_command_stamps_the_json_and_keeps_stdout_pipeable(
        capsys, monkeypatch):
    """The banner goes to stderr so stdout stays parseable JSON, and the stamp
    travels inside the JSON so a piped consumer cannot lose the verdict."""
    _watch_one(monkeypatch)
    monkeypatch.setattr(signals_v3, "evaluate", lambda *a, **k: _verdict())
    cli.score("T", as_of=None)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    status.assert_stamped(payload)
    assert "NOT VALIDATED" in captured.err
    assert "NOT VALIDATED" not in captured.out


def test_scan_prints_the_banner_before_the_first_result_line(
        capsys, monkeypatch, tmp_path):
    """A feed that leads with flags and buries the failed test is an alert
    with a disclaimer. With --score the verdict prints first, and the payload
    behind every printed line is stamped (stamp mutates, so the reference
    proves it)."""
    from ledgerline import ingest

    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    res = _verdict()
    _watch_one(monkeypatch)
    monkeypatch.setattr(
        edgar, "daily_index",
        lambda d, refresh=False: [{"form": "10-Q", "name": "T Inc",
                                   "cik": "0000000001",
                                   "filing_date": "2026-08-28", "file": "x",
                                   "accession": "acc-1"}])
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False: {
            "cik": cik, "status": "ok", "rows": 1, "metrics": 1,
            "restatements": 0, "low_coverage": []})
    monkeypatch.setattr(signals_v3, "evaluate",
                        lambda *a, **k: status.stamp(res))
    cli.scan(days_back=1, as_of=None, score=True, refresh=False)
    out = capsys.readouterr().out
    assert out.index("NOT VALIDATED") < out.index("FLAGGED")
    status.assert_stamped(res)


def test_explain_payload_is_stamped(capsys, monkeypatch):
    """The plain-words surface reads the same stamped payload as the JSON one;
    there is no unstamped path from evaluate() to a person."""
    res = _verdict(score=0.0, gated=False)
    _watch_one(monkeypatch)
    monkeypatch.setattr(signals_v3, "evaluate", lambda *a, **k: res)
    seen = {}
    monkeypatch.setattr(render, "explain",
                        lambda r, name=None: seen.setdefault("res", r) and "" or "")
    cli.explain("T", as_of=None)
    status.assert_stamped(seen["res"])


def test_committed_record_matches_the_module_pins():
    """The file this repo actually ships parses and agrees with status.PHASE0
    -- if someone edits ledgerline/data/phase0.json, CI fails here."""
    assert os.path.exists(status.PHASE0_PATH)
    loaded = status.load()
    assert loaded["verdict"] == "KILL"
    assert loaded["writeup"] == "reports/PHASE0.md"
