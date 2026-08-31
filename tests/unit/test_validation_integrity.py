"""Integrity of the Phase 0 apparatus itself.

Every test here pins a defect that would have made the one-shot holdout run
meaningless -- returning KILL for a perfect gate, or SHIP on an unenforced
rule. Found by adversarial audit before split.json or prereg.json existed.
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from ledgerline import backtest, calibrate, cli, edgar, signals_v3, status
from ledgerline.validate import harness

REAL_PHASE0 = status.PHASE0_PATH


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "DATA", str(tmp_path))
    monkeypatch.setattr(harness, "SPLIT_PATH", str(tmp_path / "split.json"))
    monkeypatch.setattr(harness, "PREREG_PATH", str(tmp_path / "prereg.json"))
    monkeypatch.setattr(harness, "CASES_PATH", str(tmp_path / "cases.json"))
    return tmp_path


def _cases(n_pos=483, n_ctrl=464):
    regimes = ["2014-16-energy", "2015-18-retail", "2017-19-idiosyncratic",
               "2020-covid", "2021-22-growth-unwind", "2023-25-rate-shock"]
    out = []
    for i in range(n_pos):
        out.append({"ticker": f"P{i:04d}", "cik": f"{i:010d}", "label": "d",
                    "is_positive": True, "broke": "2021-06",
                    "broke_filed": "2021-08-05",
                    "regime": regimes[i % len(regimes)], "sector": "3674",
                    "cap_decile": None})
    for i in range(n_ctrl):
        out.append({"ticker": f"C{i:04d}", "cik": f"{9000 + i:010d}", "label": "c",
                    "is_positive": False, "broke": None, "broke_filed": None,
                    "regime": "control", "sector": "3674", "cap_decile": None})
    return {"cases": out}


def _write_cases(ws, payload):
    (ws / "cases.json").write_text(json.dumps(payload))


# ------------------------------------------------- the guaranteed-KILL split


def test_split_leaves_a_holdout_that_can_satisfy_the_prereg_counts(workspace):
    """At tuning_frac=0.6 the shipped 483/464 case set left 186 holdout
    controls against a floor of 200, so verdict() returned KILL for a provably
    perfect gate -- a failure with nothing to do with the signal."""
    _write_cases(workspace, _cases())
    payload = harness.make_split(seed=1)
    frozen = payload["cases"]
    h_pos = sum(1 for t in payload["holdout"] if frozen[t]["is_positive"])
    h_neg = len(payload["holdout"]) - h_pos
    assert h_neg >= harness.PREREG["min_controls"], f"only {h_neg} holdout controls"
    assert h_pos >= harness.PREREG["min_positives"]


def test_readiness_checks_the_holdout_side_not_just_the_full_set(workspace):
    """readiness() gated `ledgerline split` on the FULL case set, so it happily
    permitted a split that made the rule unsatisfiable."""
    _write_cases(workspace, _cases(n_pos=100, n_ctrl=210))
    ready = harness.readiness()
    assert ready["checks"]["controls"]["pass"], "full set has 210 >= 200"
    assert not ready["checks"]["holdout_controls"]["pass"]
    assert not ready["ready"]


# ------------------------------------------------ the rule must be enforced


def test_verdict_reads_the_committed_file_not_the_module_dict(workspace, monkeypatch):
    """Editing harness.PREREG in place used to turn KILL into SHIP on identical
    outcomes while prereg.json still held the original thresholds."""
    harness.write_prereg()
    monkeypatch.setitem(harness.PREREG, "min_median_lead_months", 1)
    with pytest.raises(RuntimeError, match="disagrees"):
        harness.verdict([], baseline_fpr=0.1)


def test_write_prereg_refuses_to_overwrite(workspace):
    harness.write_prereg()
    with pytest.raises(RuntimeError, match="already exists"):
        harness.write_prereg()


def test_verdict_requires_a_committed_prereg(workspace):
    with pytest.raises(RuntimeError, match="missing"):
        harness.verdict([])


# ------------------------------------------------- the split must be frozen


def test_split_is_frozen_by_value_so_relabelling_cases_cannot_change_it(workspace):
    """The hash covered only the ticker lists while load_split() re-read
    cases.json for is_positive, broke and regime. Flipping 30 holdout positives
    to controls changed the scored set with verify_split() still green."""
    _write_cases(workspace, _cases())
    harness.make_split(seed=1)
    before = harness.load_split("holdout")

    tampered = _cases()
    for c in tampered["cases"][:200]:
        c["is_positive"], c["broke"], c["regime"] = False, None, "control"
    _write_cases(workspace, tampered)

    after = harness.load_split("holdout")
    assert [c.is_positive for c in before] == [c.is_positive for c in after]
    assert sum(c.is_positive for c in after) > 0


def test_split_refuses_to_be_rerolled(workspace):
    _write_cases(workspace, _cases())
    harness.make_split(seed=1)
    with pytest.raises(RuntimeError, match="already exists"):
        harness.make_split(seed=2)


def test_load_split_raises_when_a_ticker_has_no_frozen_record(workspace):
    _write_cases(workspace, _cases())
    harness.make_split(seed=1)
    payload = json.loads((workspace / "split.json").read_text())
    payload["cases"].pop(payload["holdout"][0])
    payload["sha256"] = harness._hash(payload)
    (workspace / "split.json").write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="no frozen case record"):
        harness.load_split("holdout")


# ------------------------------------------------------------ lead measurement


def _outcome(**kw):
    base = dict(ticker="T", is_positive=True, fired=True, censored=False,
                first_fire="2013-05-15", lead_months=None, fire_rate=0.1,
                scoreable_quarters=20, flags=[], regime="2020-covid", n_fires=1)
    base.update(kw)
    return harness.Outcome(**base)


def test_lead_is_capped_at_the_preregistered_horizon():
    """A filer that fired once at its second-ever scoreable cutoff and never
    again scored a 108-month lead and counted as a hit."""
    case = harness.Case(ticker="T", cik="1", label="l", is_positive=True,
                        regime="2020-covid", broke="2022-05",
                        broke_filed="2022-06-10")
    cutoffs = ["2013-02-15", "2013-05-15", "2022-02-15"]

    def scorer(ticker, cik, as_of):
        return {"scoreable": True, "score": 100.0 if as_of == "2013-05-15" else 0.0,
                "flags": []}

    out = harness.evaluate_case(case, cutoffs, scorer, threshold=45.0)
    assert out.fired and not out.censored
    assert out.lead_months is None, "a 108-month 'lead' was credited as detection"


def test_lead_is_measured_to_the_break_filing_date_not_the_period_end():
    """PREREG['notes'] requires the filing date. Measuring to the period made a
    gate that fired one month BEFORE publication read as firing after."""
    case = harness.Case(ticker="T", cik="1", label="l", is_positive=True,
                        regime="2020-covid", broke="2022-01",
                        broke_filed="2022-03-10")
    cutoffs = ["2021-11-15", "2022-02-15"]

    def scorer(ticker, cik, as_of):
        return {"scoreable": True, "score": 100.0 if as_of == "2022-02-15" else 0.0,
                "flags": []}

    out = harness.evaluate_case(case, cutoffs, scorer, threshold=45.0)
    assert out.lead_months == 1, "measured to the period end, not publication"


# ---------------------------------------------------------------- gate breadth


def test_a_single_flag_cannot_gate_in():
    """Breadth must be an explicit condition, not an accident of three
    constants. Before Phase 0f the heaviest weight reached
    2.0 * 2.5 / 8.0 * 100 = 62.5 against THRESHOLD=45, so one extreme print
    fired alone despite Z_CAP existing to prevent exactly that. Calibration
    since changed the arithmetic, which is the point: the guarantee must not
    depend on it, because Phase 6 recalibration will change it again."""
    assert signals_v3.MIN_FLAGS >= 2

    # Behavioural, not arithmetic: even a maximally extreme single flag whose
    # score clears THRESHOLD on its own must not gate in.
    heaviest = max(w for _, w, _ in signals_v3.TRACKED.values())
    one_flag_score = heaviest * signals_v3.Z_CAP / signals_v3.SCORE_DIVISOR * 100
    gated = one_flag_score >= signals_v3.THRESHOLD and signals_v3.MIN_FLAGS <= 1
    assert not gated, "a single flag gates in"


# ------------------------------------------------------------ holdout hygiene


def test_calibration_refuses_the_holdout_outright():
    """The holdout was scored once, 2026-08-30, and prereg.json says do not
    retune against it. replay already refuses it at the persistence layer;
    this pins the same refusal at the FITTING layer, where touching it would
    be worse -- a dataset built from holdout rows is a retune in progress, not
    a report. The guard must fire before any file or split is read, so it
    holds even on a machine that has the data."""
    from ledgerline import calibrate

    with pytest.raises(RuntimeError, match="never touch the holdout"):
        calibrate.build_dataset(split="holdout")


def test_no_entry_point_can_score_the_spent_holdout(tmp_path, monkeypatch):
    """Invariant 5, enforced at every door rather than at most of them.

    calibrate.build_dataset refused the sealed half and cli.replay refused it,
    but backtest.run() -- the function both the CLI and any future caller go
    through to score a split -- did not, so `run-test --split holdout` rescored
    it and overwrote reports/backtest_holdout.json. Every scoring path is
    walked here with the scorer replaced by a tripwire, so a regression fails
    this test instead of spending the one measurement the project has left.
    """
    def tripwire(*a, **k):
        raise AssertionError("the sealed half reached the scorer")

    monkeypatch.setattr(edgar, "normalize", tripwire)
    monkeypatch.setattr(signals_v3, "evaluate", tripwire)
    monkeypatch.setattr(backtest, "REPORTS", str(tmp_path / "reports"))
    monkeypatch.setattr(calibrate, "DATASET_PATH", str(tmp_path / "dataset.json"))

    with pytest.raises(RuntimeError, match="retests.json"):
        backtest.run(split="holdout")
    with pytest.raises(RuntimeError, match="never touch the holdout"):
        calibrate.build_dataset(split="holdout")
    with pytest.raises(RuntimeError, match="never touch the holdout"):
        calibrate.run(split="holdout")
    for argv, expect in (
            (["run-test", "--split", "holdout"], "retests.json"),
            (["replay", "--split", "holdout"], "no override flag"),
            (["calibrate", "--split", "holdout"], "no override flag")):
        result = CliRunner().invoke(cli.app, argv)
        assert result.exit_code != 0, f"{argv[0]} scored the sealed half"
        # The tripwire firing means the command reached the scorer before it
        # refused, which is the defect wearing a non-zero exit code.
        assert not isinstance(result.exception, AssertionError), \
            f"{argv[0]} reached the scorer"
        assert expect in result.output + str(result.exception)
    assert not os.path.exists(tmp_path / "reports")
    assert not os.path.exists(tmp_path / "dataset.json")


def test_the_holdout_guard_is_keyed_on_the_frozen_record(tmp_path, monkeypatch):
    """The refusal is keyed on ledgerline/data/phase0.json existing, because
    that file IS the evidence the shot was taken -- not on a constant that
    would also have refused the original 2026-08-30 run. With no frozen
    record there is no verdict to print either, so nothing can show a score
    on such a machine regardless."""
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    assert status.holdout_is_spent() is False
    status.refuse_spent_holdout("holdout")  # the machine that has not scored it
    monkeypatch.setattr(status, "PHASE0_PATH", REAL_PHASE0)
    assert status.holdout_is_spent() is True
    with pytest.raises(RuntimeError, match="no override flag"):
        status.refuse_spent_holdout("holdout")
    status.refuse_spent_holdout("tuning")  # the practice half is never refused


def test_every_sealed_half_refusal_is_a_sentence_not_a_traceback(monkeypatch,
                                                                 tmp_path):
    """`calibrate --split holdout` refused by raising out of build_dataset, so
    the person who typed it got a rich traceback instead of a written reason.

    The refusal held -- nothing was ever scored -- but a traceback is a
    refusal only to a programmer, and it is the one door of the three that did
    not say what to do instead. replay and run-test both printed a sentence
    and exited 2; calibrate now does too.
    """
    monkeypatch.setattr(calibrate, "DATASET_PATH", str(tmp_path / "ds.json"))
    for argv in (["calibrate", "--split", "holdout"],
                 ["replay", "--split", "holdout"],
                 ["run-test", "--split", "holdout"]):
        result = CliRunner().invoke(cli.app, argv)
        assert result.exit_code == 2, argv
        # SystemExit is the clean refusal; anything else is a crash wearing an
        # exit code.
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Traceback" not in result.output
        assert "retests.json" in result.output, argv
    assert not os.path.exists(tmp_path / "ds.json")


def test_no_refusal_hand_types_the_frozen_date(monkeypatch):
    """replay's refusal carried its own typed copy of "2026-08-30".

    It happened to match the frozen record, which is what makes that kind of
    copy dangerous: nothing bound it, and the suite passed either way. Same
    second-copy-that-drifts defect render.CAVEAT was fixed for, one command
    over. Every sealed-half refusal now reads its date from phase0.json, so
    moving the frozen record moves all of them.
    """
    real = status.load()
    monkeypatch.setattr(status, "load",
                        lambda: {**real, "scored_on": "2099-01-01"})
    for argv in (["replay", "--split", "holdout"],
                 ["calibrate", "--split", "holdout"],
                 ["run-test", "--split", "holdout"]):
        out = CliRunner().invoke(cli.app, argv).output
        assert "2099-01-01" in out, argv
        assert "2026-08-30" not in out, argv


def test_a_split_that_is_neither_half_is_named_not_stack_traced():
    """`run-test --split banana` reached harness.load_split and came back as a
    ValueError traceback, and replay told the same typo that the SEALED half
    was spent -- an answer to a question nobody asked. A name that is neither
    half is now its own message, on all three commands."""
    for argv in (["run-test", "--split", "banana"],
                 ["replay", "--split", "banana"],
                 ["calibrate", "--split", "banana"]):
        result = CliRunner().invoke(cli.app, argv)
        assert result.exit_code == 2, argv
        assert "no split named 'banana'" in result.output, argv
        assert "scored exactly once" not in result.output, argv
        assert "Traceback" not in result.output, argv
