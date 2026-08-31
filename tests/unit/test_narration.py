"""
Narration-tier tests: prose that cannot outrun its numbers.

Each test pins one decision or one specific way this tier could go wrong: the
cost gate (no un-flagged verdict ever reaches a model), the deterministic
verifier (a number that does not trace to the claim's OWN citations is
rejected), the two-attempt cap (repair once, then refuse and ship the
arithmetic without prose), the KILL banner on every rendered status, and the
append-only store keyed on the payload hash. No network, no credentials, no
real client: every test injects a ScriptedClient, which raises IndexError on
an unexpected extra call -- that loud failure is how the cost gate is pinned
rather than assumed.
"""
from __future__ import annotations

import copy
import importlib
import json
import sqlite3

import pytest

import ledgerline.narrate
from ledgerline import edgar, signals_v3, status
from ledgerline.narrate import payload as npayload
from ledgerline.narrate import prompt as nprompt
from ledgerline.narrate import run as nrun
from ledgerline.narrate import schema as nschema
from ledgerline.narrate import verify as nverify
from ledgerline.narrate.client import ScriptedClient
from tests.unit.test_gate import build_filer

# Real evaluate() outputs, built once: hand-written fixtures drift from the
# gate; these cannot.
GATED = signals_v3.evaluate(
    "TEST", "0000000001", as_of="2024-03-01",
    norm=build_filer(quarters=32, shock={"ocf": 0.35}))
QUIET = signals_v3.evaluate(
    "TEST", "0000000001", as_of="2023-12-01", norm=build_filer(quarters=32))
UNSCOREABLE = signals_v3.evaluate(
    "TEST", "0000000001", as_of="2017-06-01", norm=build_filer(quarters=16))

assert GATED["gated_in"] and QUIET["scoreable"] and not QUIET["gated_in"]
assert not UNSCOREABLE["scoreable"]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(edgar.SCHEMA)
    edgar._migrate(c)
    yield c
    c.close()


def gated() -> dict:
    return copy.deepcopy(GATED)


def good_response(verdict: dict | None = None) -> str:
    """A narration that verifies clean against the given verdict's payload."""
    pl = npayload.build(verdict or GATED)
    f = pl["flags"]["ocf_to_revenue"]
    return json.dumps({
        "headline": "Cash generation broke from this company's own pattern",
        "claims": [{
            "text": (f"Cash generated per dollar of sales fell, a "
                     f"{f['z']:.1f}-sigma move against its own trailing "
                     f"median of {f['baseline_median']:.3f}."),
            "diagnostic": "ocf_to_revenue",
            "cites": ["flags.ocf_to_revenue.z",
                      "flags.ocf_to_revenue.baseline_median"],
        }],
        "abstain": False})


BAD_RESPONSE = json.dumps({
    "headline": "Cash generation moved",
    "claims": [{"text": "a 9.9-sigma move in cash generation",
                "diagnostic": "ocf_to_revenue",
                "cites": ["flags.ocf_to_revenue.z"]}],
    "abstain": False})


def mini_payload() -> dict:
    """A hand-held payload for verifier unit tests, with values chosen to sit
    away from half-ulp boundaries (the design's own worked example numbers)."""
    return {
        "as_of": "2024-03-15",
        "period": "2023-12-31",
        "score": 55.0,
        "flags": {"gross_margin": {
            "label": "Gross margin abnormal vs own history",
            "z": 2.4382, "value": 0.4123, "baseline_median": 0.45,
            "baseline_scale": 0.021, "baseline_n": 17, "floored": False,
            "direction": -1, "weight": 0.3818, "detail": "gross_margin ..."}},
        "quiet": {"dso": 0.4},
        "provenance": {},
        "summary": {"n_fired": 1, "n_tracked": 13, "derived_fraction": 0.29},
    }


def narration(text: str, diagnostic: str = "gross_margin",
              cites: list[str] | None = None,
              headline: str = "A label") -> nschema.Narration:
    return nschema.Narration(
        headline=headline,
        claims=[nschema.Claim(text=text, diagnostic=diagnostic,
                              cites=cites if cites is not None
                              else ["flags.gross_margin.z"])],
        abstain=False)


def codes(failures: list[nverify.Failure]) -> list[str]:
    return [f.code for f in failures]


# ------------------------------------------------------------- the cost gate


def test_ungated_verdict_never_reaches_the_model(conn):
    """The cost gate is the tier's economic floor: a scoreable but un-flagged
    verdict is skipped with zero client calls. ScriptedClient([]) raises on
    any call, so a bypass fails loudly."""
    client = ScriptedClient([])
    res = nrun.narrate(copy.deepcopy(QUIET), client=client, conn=conn)
    assert res.status == "skipped"
    assert client.calls == []


def test_unscoreable_verdict_never_reaches_the_model(conn):
    """An unscoreable filer is skipped with the gate's own reason preserved
    verbatim -- 'could not assess' must not silently become 'nothing to
    say'."""
    client = ScriptedClient([])
    res = nrun.narrate(copy.deepcopy(UNSCOREABLE), client=client, conn=conn)
    assert res.status == "skipped"
    assert res.reason == UNSCOREABLE["reason"]
    assert client.calls == []


# ---------------------------------------------------------------- verifier


def test_a_number_absent_from_the_payload_fails_verification():
    """A sentence can name a diagnostic truthfully and still invent the
    number attached to it -- the exact weakness of the ROADMAP's
    'maps to a diagnostic' check."""
    fails = nverify.verify(narration("a 9.9-sigma move"), mini_payload())
    assert codes(fails) == ["UNTRACEABLE_NUMBER"]
    assert fails[0].token == "9.9"


def test_a_rounded_number_within_its_printed_precision_passes():
    """The half-ulp of the token's own printed precision IS the tolerance:
    z 2.4382 written as '2.4' is rounding; '2.5' is invention."""
    assert nverify.verify(narration("a 2.4-sigma move"), mini_payload()) == []
    fails = nverify.verify(narration("a 2.5-sigma move"), mini_payload())
    assert codes(fails) == ["UNTRACEABLE_NUMBER"]


def test_a_percent_literal_matches_a_stored_fraction():
    """gross_margin is stored as the fraction 0.4123 and prose properly says
    '41.2%' -- the dual-candidate parse for '%' makes that legal without
    letting '41.9%' through."""
    cites = ["flags.gross_margin.value"]
    ok = narration("margin came in at 41.2%", cites=cites)
    assert nverify.verify(ok, mini_payload()) == []
    bad = narration("margin came in at 41.9%", cites=cites)
    assert codes(nverify.verify(bad, mini_payload())) == ["UNTRACEABLE_NUMBER"]


def test_a_claim_naming_a_quiet_diagnostic_is_rejected():
    """dso was computed at z=0.4 and stayed normal. Present-but-quiet is not
    fired, exactly as scoreable=False is not score=0.0 -- the gate's own
    coverage lesson, applied to prose."""
    n = narration("collection days moved", diagnostic="dso", cites=[])
    assert "DIAGNOSTIC_NOT_FIRED" in codes(nverify.verify(n, mini_payload()))


def test_a_claim_naming_a_diagnostic_outside_tracked_is_rejected():
    """A diagnostic the gate never computes is a different defect from one
    that stayed quiet, and the two must stay distinguishable."""
    n = narration("EBITDA margin collapsed", diagnostic="ebitda_margin",
                  cites=[])
    assert "UNKNOWN_DIAGNOSTIC" in codes(nverify.verify(n, mini_payload()))


def test_a_citation_path_that_does_not_resolve_is_rejected():
    """An invented path is an invented source."""
    n = narration("gross margin moved", cites=["flags.dso.z"])
    assert "BAD_CITATION" in codes(nverify.verify(n, mini_payload()))


def test_a_literal_must_match_a_value_at_its_own_cited_paths():
    """THE anti-gaming check: 17 exists in the payload (baseline_n), but this
    claim does not cite it -- global-index matching would pass this and the
    whole verifier would be theatre."""
    n = narration("a 2.4-sigma move over its last 17 readings",
                  cites=["flags.gross_margin.z"])
    fails = nverify.verify(n, mini_payload())
    assert codes(fails) == ["UNTRACEABLE_NUMBER"]
    assert fails[0].token == "17"


def test_iso_dates_verify_against_the_date_index_not_the_number_scan():
    """'2024-03-31' must not decompose into 2024, 03 and 31 as three
    untraceable numbers -- and a date absent from the payload is its own
    failure, checked against dates only."""
    assert nverify.literals("as of 2024-03-31") == []
    ok = narration("figures filed by 2024-03-15", cites=[])
    assert nverify.verify(ok, mini_payload()) == []
    bad = narration("figures filed by 2024-06-30", cites=[])
    assert codes(nverify.verify(bad, mini_payload())) == ["UNTRACEABLE_DATE"]


@pytest.mark.parametrize("text,family", [
    ("revenue will decline further", "prediction"),
    ("we recommend caution here", "advice"),
    ("this is evidence of fraud", "accusation"),
    ("the detector is validated on this case", "endorsement"),
])
def test_predictive_and_advisory_language_is_rejected(text, family):
    """The banned lexicon makes the four families mechanically unwritable;
    each failure names the family that tripped."""
    fails = nverify.verify(narration(text, cites=[]), mini_payload())
    banned = [f for f in fails if f.code == "BANNED_TERM"]
    assert banned and family in banned[0].detail


def test_the_banned_lexicon_does_not_trip_on_legitimate_accounting_prose():
    """\\b anchoring is deliberate: 'selling, general and administrative' and
    'shortfall' are ordinary accounting prose, not advice. Pinned so nobody
    'fixes' the patterns into substring matches."""
    for text in ("selling, general and administrative expense moved",
                 "a shortfall against the trailing median"):
        fails = nverify.verify(narration(text, cites=[]), mini_payload())
        assert "BANNED_TERM" not in codes(fails), text


def test_a_direction_word_contradicting_the_flag_direction_is_rejected():
    """gross_margin declares direction -1 (unusually LOW is bad), so a fired
    flag means the margin FELL; 'rose' is a traceable number attached to an
    untrue relation."""
    n = narration("gross margin rose sharply, a 2.4-sigma move")
    assert "DIRECTION_MISMATCH" in codes(nverify.verify(n, mini_payload()))


def test_a_headline_containing_a_number_is_rejected():
    """A headline is a label, not an assertion: it carries no citations, so
    it may carry no figures."""
    n = narration("margin moved", headline="A 2.4-sigma break")
    assert "NUMBER_IN_HEADLINE" in codes(nverify.verify(n, mini_payload()))


def test_two_claims_on_the_same_diagnostic_are_rejected():
    """Padding one flag into two sentences reads as more evidence than
    exists."""
    n = nschema.Narration(
        headline="A label",
        claims=[nschema.Claim(text="margin fell", diagnostic="gross_margin",
                              cites=[]),
                nschema.Claim(text="margin dropped", diagnostic="gross_margin",
                              cites=[])],
        abstain=False)
    assert "DUPLICATE_DIAGNOSTIC" in codes(nverify.verify(n, mini_payload()))


# ----------------------------------------------- validate-then-repair loop


def test_failed_verification_triggers_exactly_one_repair_pass(conn):
    """Bad then good: the second attempt is a repair, not a regeneration --
    and there is never a third."""
    client = ScriptedClient([BAD_RESPONSE, good_response()])
    res = nrun.narrate(gated(), client=client, conn=conn)
    assert res.status == "narrated"
    assert res.attempts == 2
    assert len(client.calls) == 2
    # The repair turn carries the model's own draft and the failure list.
    assert client.calls[1]["messages"][1]["role"] == "assistant"
    assert "UNTRACEABLE_NUMBER" in client.calls[1]["messages"][2]["content"]


def test_two_failures_abstain_rather_than_publish(conn):
    """Failing closed is the contract: the arithmetic ships without prose
    rather than with unverified prose, and the fallback IS the arithmetic's
    own sentences."""
    client = ScriptedClient([BAD_RESPONSE, BAD_RESPONSE])
    res = nrun.narrate(gated(), client=client, conn=conn)
    assert res.status == "abstained"
    assert res.attempts == 2
    assert len(client.calls) == 2
    assert res.text == nrun.fallback_text(res.payload)
    assert res.failures and res.failures[0]["code"] == "UNTRACEABLE_NUMBER"


def test_malformed_json_abstains_after_exactly_two_calls(conn):
    """Garbage does not buy extra attempts: malformed output consumes an
    attempt like any verification failure."""
    client = ScriptedClient(["not json", '{"headline":'])
    res = nrun.narrate(gated(), client=client, conn=conn)
    assert res.status == "abstained"
    assert len(client.calls) == 2
    assert any(f["code"] == "MALFORMED" for f in res.failures)


def test_a_transport_error_abstains_without_a_retry_storm(conn):
    """A failing service consumes the same two attempts as a failing draft --
    no backoff loop, no third call, and the error lands in the reason."""
    client = ScriptedClient([RuntimeError("502"), RuntimeError("502")])
    res = nrun.narrate(gated(), client=client, conn=conn)
    assert res.status == "abstained"
    assert len(client.calls) == 2
    assert "502" in (res.reason or "")


def test_a_model_abstention_publishes_the_fallback(conn):
    """The model may decline; the numbers still describe themselves."""
    client = ScriptedClient([json.dumps(
        {"headline": "", "claims": [], "abstain": True,
         "abstain_reason": "cannot state a claim from this payload"})])
    res = nrun.narrate(gated(), client=client, conn=conn)
    assert res.status == "abstained"
    assert len(client.calls) == 1
    assert res.text == nrun.fallback_text(res.payload)


# ------------------------------------------------------- the KILL, rendered


def _result_for(status_name: str, conn) -> nrun.NarrationResult:
    if status_name == "narrated":
        return nrun.narrate(gated(), client=ScriptedClient([good_response()]),
                            conn=conn)
    if status_name == "abstained":
        return nrun.narrate(gated(),
                            client=ScriptedClient([BAD_RESPONSE, BAD_RESPONSE]),
                            conn=conn)
    return nrun.narrate(copy.deepcopy(QUIET), client=ScriptedClient([]),
                        conn=conn)


@pytest.mark.parametrize("status_name", ["narrated", "abstained", "skipped"])
def test_every_rendered_narration_opens_with_the_phase0_banner(status_name,
                                                               conn):
    """The banner comes FIRST on every path -- narrated, abstained and
    skipped alike. The KILL is structural, not a footer someone can drop."""
    text = nrun.render(_result_for(status_name, conn))
    assert text.startswith("NOT VALIDATED")
    assert "28.7%" in text and "60%" in text


def test_generated_prose_is_labelled_and_placed_after_the_numbers(conn):
    """The model's text is marked as machine-written and appears AFTER the
    computed figures -- prose is where a reader stops seeing arithmetic, so
    the arithmetic goes first."""
    res = nrun.narrate(gated(), client=ScriptedClient([good_response()]),
                       conn=conn)
    text = nrun.render(res)
    assert "Machine-written summary" in text
    assert text.index("technical:") < text.index("Machine-written summary")


def test_narration_refuses_to_run_without_the_phase0_record(tmp_path,
                                                            monkeypatch, conn):
    """No committed failure record on this machine, no prose: status.load()
    raises rather than defaulting, and narrate() calls it before anything
    else. A default label would be right today for the wrong reason and
    silently wrong the day a future gate passes."""
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "missing.json"))
    client = ScriptedClient([good_response()])
    with pytest.raises(RuntimeError, match="phase0.json"):
        nrun.narrate(gated(), client=client, conn=conn)
    assert client.calls == []


# ----------------------------------------------------------------- payload


def test_the_payload_reads_vintages_not_the_top_level_filed_date():
    """FINDINGS §5 in the narration layer: the top-level row carries the
    LATEST vintage's filed date, so a 2012-cutoff narration would otherwise
    cite a 2014 filing -- a lookahead claim in prose."""
    norm = {"revenue": [{
        "end": "2012-06-30", "filed": "2014-02-21", "form": "10-K/A",
        "concept": "Revenues", "origin": "reported", "sources": ["acc-2014"],
        "value": 90.0,
        "vintages": [
            {"filed": "2012-08-10", "value": 100.0, "form": "10-Q",
             "concept": "Revenues", "origin": "reported",
             "sources": ["acc-2012"]},
            {"filed": "2014-02-21", "value": 90.0, "form": "10-K/A",
             "concept": "Revenues", "origin": "reported",
             "sources": ["acc-2014"]},
        ]}]}
    trace = npayload.provenance_for(norm, "revenue", "2012-09-01")
    assert trace is not None
    assert trace["filed"] == "2012-08-10"
    assert trace["sources"] == ["acc-2012"]
    # And nothing public by the cutoff is None, never a guess.
    assert npayload.provenance_for(norm, "revenue", "2012-01-01") is None


def test_the_payload_contains_no_number_the_gate_did_not_compute():
    """build() copies; it never derives. Every numeric leaf traces to the
    verdict dict, a gate constant, or a structural count of the verdict's
    own contents."""
    pl = npayload.build(GATED)
    verdict_leaves: list = []
    npayload._walk(GATED, "", verdict_leaves)
    allowed = {float(v) for _, v in verdict_leaves
               if not isinstance(v, bool) and isinstance(v, (int, float))}
    allowed |= {signals_v3.THRESHOLD, signals_v3.Z_TRIGGER,
                float(signals_v3.MIN_FLAGS), float(len(signals_v3.TRACKED)),
                float(len(GATED["flags"])), -1.0, 1.0}
    for path, value in npayload.number_index(pl).items():
        assert value in allowed, f"{path}={value} was not computed by the gate"


def test_quiet_diagnostics_travel_outside_flags():
    """The model can be told what stayed normal without being able to claim
    it fired: quiet z-scores live under 'quiet', never under 'flags'."""
    pl = npayload.build(GATED)
    assert set(pl["flags"]) & set(pl["quiet"]) == set()
    fired = {f["code"].lower() for f in GATED["flags"]}
    assert set(pl["flags"]) == fired


def test_booleans_never_enter_the_number_index():
    """bool is a subclass of int; without the explicit check, floored=True
    would index as 1.0 and let the model write '1' with a citation to a
    boolean."""
    idx = npayload.number_index(npayload.build(GATED))
    assert not any(path.endswith(".floored") for path in idx)


# ------------------------------------------------------- store and dedupe


def test_an_unchanged_payload_is_not_re_narrated(conn):
    """The real cost control -- per-filer FPR is 0.512, so half of all
    control filers fire eventually; re-running an unchanged payload must be
    free."""
    first = nrun.narrate(gated(), client=ScriptedClient([good_response()]),
                         conn=conn)
    again = nrun.narrate(gated(), client=ScriptedClient([]), conn=conn)
    assert first.status == "narrated"
    assert again.status == "cached"
    assert again.text == first.text


def test_a_restated_payload_is_a_new_row_not_an_edit(conn):
    """A restatement changes a vintage, which changes the sha, which makes
    the re-narration a second row beside the first -- both readable,
    append-only one phase ahead of the delivery contract's rule."""
    nrun.narrate(gated(), client=ScriptedClient([good_response()]), conn=conn)
    restated = gated()
    for f in restated["flags"]:
        f["value"] = f["value"] * 1.01
    nrun.narrate(restated, client=ScriptedClient([good_response(restated)]),
                 conn=conn)
    rows = conn.execute(
        "SELECT DISTINCT payload_sha FROM narrations WHERE status='narrated'"
    ).fetchall()
    assert len(rows) == 2


def test_the_per_run_budget_caps_model_calls_and_reports_the_remainder(conn):
    """Five gated verdicts, budget 2: two model calls, five results, the
    last three skipped WITH the budget reason. A silently truncated batch is
    a data-loss bug wearing a cost control's clothes."""
    verdicts = []
    for i in range(5):
        v = gated()
        v["cik"] = f"000000000{i}"
        verdicts.append(v)
    client = ScriptedClient([good_response(), good_response()])
    results = nrun.narrate_batch(verdicts, client=client, budget=2, conn=conn)
    assert len(client.calls) == 2
    assert len(results) == 5
    assert [r.status for r in results] == ["narrated", "narrated",
                                           "skipped", "skipped", "skipped"]
    assert all("budget" in (r.reason or "") for r in results[2:])


def test_the_narrations_table_is_created_by_the_migration_helper(conn):
    """Schema changes go through PRAGMA user_version, additive only: a fresh
    database reaches version 2 and holds the table."""
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(narrations)")}
    assert {"cik", "as_of", "payload_sha", "status", "payload"} <= cols


# ------------------------------------------------------------- boundaries


def test_the_repair_prompt_introduces_no_new_numbers():
    """Handing the model the right number is dictation, not repair, and
    would make the second attempt untestable. The repair prompt may repeat
    the model's own bad token, never the payload's correct value."""
    pl = npayload.build(GATED)
    bad = nschema.parse(BAD_RESPONSE)
    failures = nverify.verify(bad, pl)
    rp = nprompt.repair_prompt(failures)
    assert "9.9" in rp  # the model's own token, named so it can be fixed
    true_z = pl["flags"]["ocf_to_revenue"]["z"]
    assert f"{true_z:g}" not in rp
    assert f"{true_z:.1f}" not in rp


def test_no_client_is_constructed_at_import_or_without_credentials(
        monkeypatch, conn):
    """Importing the package and narrating with an injected client must work
    with no credentials configured anywhere -- build_client() is reached
    only when no client was passed."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TRIDENT_ENDPOINT", raising=False)
    importlib.reload(ledgerline.narrate)
    res = ledgerline.narrate.narrate(
        gated(), client=ScriptedClient([good_response()]), conn=conn)
    assert res.status == "narrated"


def test_no_model_output_can_reach_a_scoring_decision(conn):
    """The README invariant, asserted rather than assumed: narrate() mutates
    nothing in the verdict, and re-running the gate afterwards returns the
    identical answer."""
    v = gated()
    before = copy.deepcopy(v)
    nrun.narrate(v, client=ScriptedClient([good_response()]), conn=conn)
    assert v == before
    again = signals_v3.evaluate(
        "TEST", "0000000001", as_of="2024-03-01",
        norm=build_filer(quarters=32, shock={"ocf": 0.35}))
    assert again["score"] == GATED["score"]
    assert again["gated_in"] == GATED["gated_in"]
    assert again["flags"] == GATED["flags"]
