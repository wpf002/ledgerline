"""
Delivery-boundary tests: the JSON contract, the JSONL export, the run digest,
and the local read service's constraints.

Each test pins one decision or defect. The through-line is the honesty rule
the phase exists for: no surface this repo controls may show a score without
the fact that the detector failed its own 2026-08-30 test, and there is no
code path from a missing evidence file to an exported record. Same isolation
idiom as test_signal_store.py: edgar.DATA / edgar.DB_PATH are redirected to
tmp_path so the live state.db is never touched. No network.
"""
from __future__ import annotations

import json
import os
import re

import pytest

from ledgerline import edgar, emit, signals_v3, status
from ledgerline.api import contract, digest, schema
from tests.unit.test_gate import build_filer
from tests.unit.test_signal_store import (
    fired_verdict,
    quiet_verdict,
    unscoreable_verdict,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def emit_mixed_run(run_id: str = "2026-08-14") -> None:
    """One run holding a fire, a quiet quarter and an unscoreable filer --
    the three states a delivered record can be in."""
    emit.emit_run([fired_verdict(), quiet_verdict(), unscoreable_verdict()],
                  source="emit", run_id=run_id, run_date="2026-08-14")


def exported(tmp_path) -> list[dict]:
    path = str(tmp_path / "signals.jsonl")
    contract.export_jsonl(path)
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------ the validation block


def test_validation_block_is_required_and_says_kill(isolated_db, tmp_path):
    """Every exported record carries validation as a structural field, and
    removing it fails schema validation. The KILL is not a footer a consumer
    can trim; it is a field a conforming reader cannot skip."""
    emit_mixed_run()
    records = exported(tmp_path)
    assert records
    for rec in records:
        v = rec["validation"]
        assert v["verdict"] == "KILL"
        assert v["status"] == status.GATE_STATUS
        assert "failed its own pre-registered test" in v["statement"]
        assert schema.validate(rec) == []
        stripped = {k: val for k, val in rec.items() if k != "validation"}
        assert any("validation" in e for e in schema.validate(stripped))


def test_contract_refuses_to_build_without_validation_evidence(
        isolated_db, tmp_path, monkeypatch):
    """With phase0.json absent, validation_block() raises instead of
    defaulting to 'unvalidated', and the export writes NOTHING. A default
    would produce the right label on a machine with no evidence -- and would
    silently mislabel a future gate that actually passed. There is no path
    from a missing evidence file to an exported record."""
    emit_mixed_run()
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    with pytest.raises(RuntimeError, match="phase0.json is missing"):
        contract.validation_block()
    out = tmp_path / "feed.jsonl"
    with pytest.raises(RuntimeError):
        contract.export_jsonl(str(out))
    assert not out.exists()


def test_validation_statement_is_computed_from_the_frozen_numbers():
    """Change a measured number, the sentence changes with it. Pins that the
    statement tracks its evidence rather than being a string literal that
    would survive the numbers changing."""
    base = dict(status.PHASE0)
    a = contract.statement(base)
    assert "28.7%" in a and "60%" in a and "3.83%" in a and "51.2%" in a
    changed = dict(base, positive_hit_rate=0.912)
    b = contract.statement(changed)
    assert "91.2%" in b and "28.7%" not in b


# --------------------------------------------------------------- the envelope


def test_unscoreable_record_has_null_score_not_zero(isolated_db, tmp_path):
    """FINDINGS section 3's defect restated at the delivery boundary: an
    unassessed company exports score null, never 0.0, and the schema rejects
    a record where the two disagree in either direction."""
    emit_mixed_run()
    un = [r for r in exported(tmp_path)
          if r["assessment"]["state"] == "unscoreable"]
    assert un and all(r["assessment"]["score"] is None for r in un)
    costume = json.loads(json.dumps(un[0]))
    costume["assessment"]["score"] = 0.0
    assert any("must be null" in e for e in schema.validate(costume))
    quiet = [r for r in exported(tmp_path)
             if r["assessment"]["state"] == "quiet"][0]
    hidden = json.loads(json.dumps(quiet))
    hidden["assessment"]["score"] = None
    assert any("must carry the number" in e for e in schema.validate(hidden))


def test_every_record_carries_the_run_denominator(isolated_db, tmp_path):
    """The run block is embedded in each record, not referenced by id: a
    fired record read in isolation still says one of three filers could not
    be assessed that day, so the denominator cannot be dropped in transit."""
    emit_mixed_run()
    for rec in exported(tmp_path):
        run = rec["run"]
        assert run["evaluated"] == 3
        assert run["unscoreable"] == 1
        assert sum(run["unscoreable_reasons"].values()) == 1


def test_schema_rejects_an_extra_top_level_field(isolated_db, tmp_path):
    """additionalProperties is false at the root: a producer that grows the
    shape without a version bump fails validation instead of silently
    diverging from the committed schema."""
    emit_mixed_run()
    rec = exported(tmp_path)[0]
    rec["confidence"] = 0.99
    assert any("not in the contract" in e for e in schema.validate(rec))


def test_schema_file_is_pinned_to_the_code():
    """Golden comparison against the committed service/signal.schema.json:
    any shape change fails here until someone consciously regenerates the
    file (`ledgerline contract-schema`) and, per the version policy in
    schema.py's docstring, bumps SCHEMA_VERSION."""
    with open(os.path.join(REPO, "service", "signal.schema.json")) as fh:
        committed = json.load(fh)
    assert committed == schema.json_schema()
    assert committed["additionalProperties"] is False
    assert "validation" in committed["required"]
    assert committed["properties"]["validation"]["additionalProperties"] is False


# ------------------------------------------------------------ the JSONL feed


def test_feed_export_is_ordered_and_resumable(isolated_db, tmp_path):
    """Two windowed exports concatenate into exactly the full feed, and seq
    is strictly increasing. This cursor is the pull replacement for the
    roadmap's webhook: resumption is exact, and there is no push."""
    emit_mixed_run("run-a")
    full_path = str(tmp_path / "full.jsonl")
    n_full, max_seq = contract.export_jsonl(full_path)
    assert n_full == 3 and max_seq == 3

    part = str(tmp_path / "part.jsonl")
    n1, cursor = contract.export_jsonl(part, since_seq=0)
    # a second run lands (a later as_of, so it is a new evaluation rather
    # than an idempotent re-emit); the resumed export appends only what is new
    later = signals_v3.evaluate("TEST", "0000000001", as_of="2024-06-01",
                                norm=build_filer(quarters=32,
                                                 shock={"ocf": 0.35}))
    emit.emit_run([later], source="emit", run_id="run-b",
                  run_date="2026-08-15")
    n2, cursor2 = contract.export_jsonl(part, since_seq=cursor)
    assert (n1, n2) == (3, 1) and cursor2 == 4

    contract.export_jsonl(full_path)  # rewrite from 0 with both runs
    with open(full_path) as fh:
        full = fh.read()
    with open(part) as fh:
        stitched = fh.read()
    assert stitched == full
    seqs = [json.loads(line)["seq"] for line in full.splitlines()]
    assert seqs == sorted(seqs) == [1, 2, 3, 4]


def test_every_exported_line_conforms_to_the_schema(isolated_db, tmp_path):
    """The export validates before it writes; what lands on disk parses and
    conforms line for line. A feed with a silently skipped or malformed line
    would hand a consumer wrong denominators."""
    emit_mixed_run()
    for rec in exported(tmp_path):
        assert schema.validate(rec) == []


# ---------------------------------------------------------------- the digest


def test_digest_expectation_line_precedes_the_first_ticker(isolated_db):
    """The pinned ordering, by byte offset: banner, then the computed
    chance-alone expectation, and only THEN a company name. A digest that
    leads with fires and buries the expectation is an alert with a
    disclaimer."""
    emit_mixed_run()
    text = digest.render_text(digest.build("2026-08-14"))
    banner_at = text.index("failed its own test")
    expect_at = text.index("Even if nothing were wrong")
    ticker_at = text.index("TEST")  # the fixture filer's ticker
    assert banner_at < expect_at < ticker_at
    # and the coverage counts sit between banner and expectation
    coverage_at = text.index("could not be assessed")
    assert banner_at < coverage_at < expect_at


def test_expected_false_positives_tracks_the_measured_rate(isolated_db):
    """The expectation is arithmetic on the frozen numbers, never a literal:
    33 * 0.0383 = 1.26..., and changing either input changes the line."""
    assert digest.expected_false_positives(33, 0.0383) == pytest.approx(1.2639)
    line = digest.expectation_line(33, 0.0383)
    assert "1.3" in line and "33" in line and "3.83%" in line
    assert "2.5" in digest.expectation_line(66, 0.0383)
    assert "6.6" in digest.expectation_line(33, 0.2)
    # and the built digest uses the frozen rate against THIS run's count
    emit_mixed_run()
    d = digest.build("2026-08-14")
    fpr = status.PHASE0["fpr_per_control_quarter"]
    assert d["expected_false_positives"] == pytest.approx(2 * fpr)


def test_digest_defaults_to_the_most_recent_run(isolated_db):
    """Without --run-id the digest reports the newest run only -- yesterday's
    fires must not pad today's report."""
    emit.emit_run([quiet_verdict()], source="emit", run_id="old",
                  run_date="2026-08-13")
    emit.emit_run([fired_verdict(), unscoreable_verdict()], source="emit",
                  run_id="new", run_date="2026-08-14")
    d = digest.build()
    assert d["run_id"] == "new"
    assert d["run"]["evaluated"] == 2 and len(d["fires"]) == 1


def test_digest_refuses_without_the_evidence_file(isolated_db, tmp_path,
                                                  monkeypatch):
    """The digest is a scored surface like any other: no phase0.json, no
    digest. The banner cannot be generated without the committed numbers,
    and generating it from anything else would be a paraphrase."""
    emit_mixed_run()
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    with pytest.raises(RuntimeError, match="phase0.json is missing"):
        digest.build("2026-08-14")


# ------------------------------------------------------ the local read service


def test_service_uses_only_node_builtins():
    """The user-ordered deviation's constraint, made executable: the service
    imports nothing but node: built-ins and its own files -- zero npm
    dependencies, nothing to install, no supply chain. A bare specifier fails
    here before it fails a reviewer.

    Every .mjs in service/ is checked, not only server.mjs: the viewer grew a
    second module when it grew a second page, and a constraint that covered
    one file would have stopped covering the program."""
    service = os.path.join(REPO, "service")
    for name in sorted(f for f in os.listdir(service) if f.endswith(".mjs")):
        with open(os.path.join(service, name)) as fh:
            src = fh.read()
        imports = re.findall(r'^\s*import\s.*?from\s+"([^"]+)"', src, re.M)
        for spec in imports:
            # A relative path is a file in this directory; anything else is a
            # package, which is the thing there is none of.
            assert spec.startswith("node:") or spec.startswith("./"), \
                f"{name} imports {spec}"
            if spec.startswith("./"):
                assert os.path.exists(os.path.join(service, spec[2:]))
        assert "require(" not in src
    assert not os.path.exists(os.path.join(REPO, "service", "package.json"))
    assert not os.path.exists(os.path.join(REPO, "service", "node_modules"))


def test_service_is_loopback_only_and_says_unvalidated():
    """The service binds 127.0.0.1 (an unvalidated signal should not be
    reachable off the machine by default) and its README leads with local-
    only scope and the failed test -- not with what the tool can do."""
    with open(os.path.join(REPO, "service", "server.mjs")) as fh:
        src = fh.read()
    assert '"127.0.0.1"' in src and "0.0.0.0" not in src
    with open(os.path.join(REPO, "service", "README.md")) as fh:
        readme = fh.read()
    assert "local development only" in readme.lower()
    assert "unvalidated" in readme.lower()
    assert "2026-08-30" in readme
    # deployment artifacts are named only to say they are deliberately absent
    assert "no dockerfile" in readme.lower()
    assert "no deployment or hosting configuration" in readme.lower()
