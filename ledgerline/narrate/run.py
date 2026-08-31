"""
Orchestration: cost gate, dedupe, validate-then-repair, abstention, rendering
and persistence. The only module in the tier that touches both a client and
the database.

Why the cost controls live here and not in the gate's selectivity: the Phase 0
per-quarter control false-positive rate (0.0383) makes narration cheap by
arithmetic accident, but the per-FILER rate is 0.512 -- half of all control
filers fire eventually. So the controls that hold regardless of how good the
gate is are: the content-hash dedupe (re-running an unchanged payload returns
the stored row for free), the per-run budget cap, and the CLI's --dry-run.

Failing closed is the contract. One repair pass (MAX_ATTEMPTS = 2), then the
narration is REFUSED: the arithmetic ships without prose rather than with
unverified prose. Abstention is publication, not silence -- the fallback text
is the gate's own deterministic flag sentences, which are already prose
written by the arithmetic. When the model cannot be trusted to describe the
numbers, the numbers describe themselves.

No model output can reach a scoring decision: narrate() takes an
already-scored verdict, mutates nothing in it, and writes to a table no
scorer reads. Pinned by a test that re-evaluates after narrating.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .. import edgar, status
from .. import render as render_mod
from . import payload as payload_mod
from . import prompt, schema, verify
from .client import NarrationClient, build_client

MAX_ATTEMPTS = 2            # one write, one repair, then refuse
MAX_NARRATIONS_PER_RUN = 25  # hard per-run budget on model calls


@dataclass
class NarrationResult:
    cik: str
    ticker: str
    as_of: str
    period: str | None
    # narrated | abstained | skipped | cached -- four states, no nulls, no
    # implicit success.
    status: str
    headline: str | None
    text: str
    claims: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    reason: str | None = None
    attempts: int = 0
    model: str | None = None
    endpoint: str | None = None
    payload: dict = field(default_factory=dict)
    payload_sha: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def should_narrate(verdict: dict, conn: sqlite3.Connection | None = None, *,
                   force: bool = False) -> tuple[bool, str | None]:
    """The cost gate, checked BEFORE any client exists. A verdict that is not
    scoreable, or scoreable but not flagged, is skipped with the gate's own
    reason preserved -- and an unchanged payload already in the store is a
    cached read, not a new model call."""
    if not verdict.get("scoreable"):
        return False, verdict.get("reason") or "could not be assessed"
    if not verdict.get("gated_in"):
        return False, ("not flagged -- narration runs only on flagged "
                       "assessments")
    if conn is not None and not force:
        sha = payload_mod.payload_sha(payload_mod.build(verdict))
        row = conn.execute(
            "SELECT 1 FROM narrations WHERE cik=? AND as_of=? AND "
            "payload_sha=?",
            (verdict.get("cik"), verdict.get("as_of"), sha)).fetchone()
        if row:
            return False, "already narrated: the numbers have not changed"
    return True, None


def fallback_text(payload: dict) -> str:
    """The deterministic narration: the gate's own flag sentences. Already
    prose, already verified by construction -- each detail string was
    formatted from the same numbers the payload carries."""
    lines = [f["detail"] for f in payload.get("flags", {}).values()
             if f.get("detail")]
    return "\n".join(lines)


def _result(verdict: dict, payload: dict, **kw) -> NarrationResult:
    return NarrationResult(
        cik=verdict.get("cik", ""), ticker=verdict.get("ticker", ""),
        as_of=verdict.get("as_of", ""), period=verdict.get("period"),
        payload=payload, payload_sha=payload_mod.payload_sha(payload),
        created_at=datetime.now(UTC).isoformat(), **kw)


def narrate(verdict: dict, *, client: NarrationClient | None = None,
            norm: dict | None = None, conn: sqlite3.Connection | None = None,
            force: bool = False, persist_result: bool = True) -> NarrationResult:
    """Narrate one already-scored verdict, or refuse to.

    The verdict is read, never written: there is no code path from a model
    response back into score, gated_in, or any diagnostic.
    """
    # No committed failure record on this machine, no prose: load() raises on
    # a missing phase0.json, and the stamp check refuses a verdict that did
    # not pass through status.stamp().
    status.load()
    status.assert_stamped(verdict)

    ok, why = should_narrate(verdict)
    if not ok:
        return _result(verdict, payload_mod.build(verdict, norm),
                       status="skipped", headline=None, text="", reason=why)

    payload = payload_mod.build(verdict, norm)
    sha = payload_mod.payload_sha(payload)

    own_conn = conn is None
    if own_conn:
        conn = edgar.db()
    assert conn is not None
    try:
        if not force:
            prior = _load_row(conn, verdict["cik"], verdict["as_of"], sha)
            if prior is not None:
                prior.status = "cached"
                prior.reason = "already narrated: the numbers have not changed"
                return prior

        if client is None:
            client = build_client()
        endpoint = type(client).__name__

        sch = schema.json_schema(sorted(payload["flags"]))
        messages: list[dict] = [
            {"role": "user", "content": prompt.user_prompt(payload)}]
        attempts = 0
        itok = otok = 0
        model: str | None = None
        narration: schema.Narration | None = None
        failures: list[verify.Failure] = []
        raw: str | None = None

        while attempts < MAX_ATTEMPTS:
            attempts += 1
            try:
                resp = client.complete(system=prompt.SYSTEM,
                                       messages=messages, schema=sch)
            except Exception as exc:
                # A transport failure consumes an attempt like any other
                # defect -- two failures abstain, never a retry storm.
                narration, raw = None, None
                failures = [verify.Failure("MALFORMED", -1, "",
                                           f"the model service failed: {exc}")]
                continue
            itok += resp.input_tokens
            otok += resp.output_tokens
            model = resp.model
            raw = resp.text
            try:
                narration = schema.parse(raw)
            except schema.MalformedNarration as exc:
                narration = None
                failures = [verify.Failure("MALFORMED", -1, "",
                                           str(exc)[:300])]
            else:
                failures = verify.verify(narration, payload)
                if narration.abstain or not failures:
                    break
            if attempts < MAX_ATTEMPTS:
                # One repair pass: the model sees its own output and the
                # failure list -- codes and offending tokens, never the
                # corrected values.
                if raw is not None:
                    messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user",
                                 "content": prompt.repair_prompt(failures)})

        if narration is not None and narration.abstain:
            result = _result(
                verdict, payload, status="abstained", headline=None,
                text=fallback_text(payload),
                reason=narration.abstain_reason
                or "the model declined to write claims from this payload",
                attempts=attempts, model=model, endpoint=endpoint,
                input_tokens=itok, output_tokens=otok)
        elif narration is not None and not failures:
            result = _result(
                verdict, payload, status="narrated",
                headline=narration.headline,
                text="\n".join(c.text for c in narration.claims),
                claims=[c.model_dump() for c in narration.claims],
                attempts=attempts, model=model, endpoint=endpoint,
                input_tokens=itok, output_tokens=otok)
        else:
            # Verification failed twice: the arithmetic ships without prose
            # rather than with unverified prose.
            result = _result(
                verdict, payload, status="abstained", headline=None,
                text=fallback_text(payload),
                failures=[asdict(f) for f in failures],
                reason="the draft could not be verified against the "
                       "computed numbers: "
                       + "; ".join(
                           f"{f.code} ({f.token})" if f.token
                           else f"{f.code}: {f.detail}" for f in failures),
                attempts=attempts, model=model, endpoint=endpoint,
                input_tokens=itok, output_tokens=otok)

        if persist_result:
            persist(conn, result)
        return result
    finally:
        if own_conn:
            conn.close()


def narrate_batch(verdicts: list[dict], *,
                  client: NarrationClient | None = None,
                  budget: int = MAX_NARRATIONS_PER_RUN,
                  conn: sqlite3.Connection | None = None) -> list[NarrationResult]:
    """Narrate a day's verdicts under a hard budget of MODEL CALLS -- skipped
    and cached results are free and do not consume it. Every verdict returns
    a result, including the ones past the cap (status 'skipped', budget
    reason): a silently truncated batch is a data-loss bug wearing a cost
    control's clothes."""
    own_conn = conn is None
    if own_conn:
        conn = edgar.db()
    assert conn is not None
    results: list[NarrationResult] = []
    calls_used = 0
    try:
        for v in verdicts:
            ok, _ = should_narrate(v, conn)
            if ok and calls_used >= budget:
                results.append(_result(
                    v, payload_mod.build(v), status="skipped", headline=None,
                    text="", reason="per-run narration budget exhausted"))
                continue
            res = narrate(v, client=client, conn=conn)
            calls_used += res.attempts
            results.append(res)
        return results
    finally:
        if own_conn:
            conn.close()


def render(result: NarrationResult) -> str:
    """The full printed form: banner FIRST on every status, then the computed
    numbers, then -- and only then -- any machine-written prose, labelled as
    such. The KILL is structural, not a footer someone can drop."""
    lines = [status.banner(), ""]
    p = result.payload
    score = p.get("score")
    head = f"{result.ticker}  quarter ending {result.period or '?'}, " \
           f"using only figures filed by {result.as_of}."
    lines.append(head)

    if result.status == "skipped":
        lines.append(f"Not narrated: {result.reason}")
        return "\n".join(lines)

    n = len(p.get("flags", {}))
    if score is not None:
        lines.append(
            f"FLAGGED.  Concern score {score:g} of 100 (a company is flagged "
            f"at 45 with at least 2 measures out of line). "
            f"{n} measure{'s' if n != 1 else ''} broke from this company's "
            "own pattern:")
    lines.append("")
    for name, f in p.get("flags", {}).items():
        short, desc = render_mod.PLAIN.get(name, (name, f.get("label", name)))
        lines.append(f"  {short}: {desc}.")
        if f.get("detail"):
            lines.append(f"    (technical: {f['detail']})")
    lines.append("")

    if result.status in ("narrated", "cached"):
        lines.append("Machine-written summary -- drafted by a language model "
                     "from the numbers above; every figure in it was checked "
                     "against them by a program before printing:")
        if result.headline:
            lines.append(f"  {result.headline}")
        for t in result.text.splitlines():
            lines.append(f"  {t}")
    else:
        lines.append("No machine-written summary is shown: "
                     f"{result.reason}. The computed sentences above are the "
                     "narration -- they were written by the arithmetic "
                     "itself.")
    lines.append("")

    accs: list[str] = []
    filed: list[str] = []
    for per_metric in p.get("provenance", {}).values():
        for t in per_metric.values():
            accs += [s for s in (t.get("sources") or []) if s]
            if t.get("filed"):
                filed.append(t["filed"])
    if accs:
        lines.append(
            "Sources: SEC filings "
            + ", ".join(sorted(set(accs)))
            + (f" (filed {min(filed)} to {max(filed)})" if filed else ""))
    df = (p.get("summary") or {}).get("derived_fraction")
    if df:
        lines.append(f"{df:.0%} of the quarterly figures behind this reading "
                     "were worked out by subtracting one year-to-date report "
                     "from another.")
    lines.append("")
    lines.append(render_mod.CAVEAT)
    return "\n".join(lines)


# ------------------------------------------------------------- persistence


def _load_row(conn: sqlite3.Connection, cik: str, as_of: str,
              sha: str) -> NarrationResult | None:
    row = conn.execute(
        "SELECT cik, ticker, as_of, period, status, headline, text, claims, "
        "failures, reason, attempts, model, endpoint, payload, payload_sha, "
        "input_tokens, output_tokens, created_at FROM narrations "
        "WHERE cik=? AND as_of=? AND payload_sha=?",
        (cik, as_of, sha)).fetchone()
    return _from_row(row) if row else None


def _from_row(row: tuple) -> NarrationResult:
    (cik, ticker, as_of, period, st, headline, text, claims, failures,
     reason, attempts, model, endpoint, payload, sha, itok, otok,
     created) = row
    return NarrationResult(
        cik=cik, ticker=ticker, as_of=as_of, period=period, status=st,
        headline=headline, text=text or "",
        claims=json.loads(claims or "[]"),
        failures=json.loads(failures or "[]"), reason=reason,
        attempts=attempts or 0, model=model, endpoint=endpoint,
        payload=json.loads(payload or "{}"), payload_sha=sha,
        input_tokens=itok or 0, output_tokens=otok or 0,
        created_at=created or "")


def persist(conn: sqlite3.Connection, result: NarrationResult) -> None:
    """INSERT OR IGNORE, never REPLACE: a published narration is never
    edited. A changed payload has a different sha and becomes a second row
    beside the first -- both stay readable, which is what makes the table an
    audit trail rather than a cache."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO narrations (cik, ticker, as_of, period, "
            "payload_sha, status, model, endpoint, attempts, headline, text, "
            "claims, failures, reason, payload, input_tokens, output_tokens, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (result.cik, result.ticker, result.as_of, result.period,
             result.payload_sha, result.status, result.model, result.endpoint,
             result.attempts, result.headline, result.text,
             json.dumps(result.claims), json.dumps(result.failures),
             result.reason, json.dumps(result.payload),
             result.input_tokens, result.output_tokens, result.created_at))


def load(conn: sqlite3.Connection, cik: str,
         as_of: str) -> NarrationResult | None:
    """The most recent stored narration for one (cik, as_of)."""
    row = conn.execute(
        "SELECT cik, ticker, as_of, period, status, headline, text, claims, "
        "failures, reason, attempts, model, endpoint, payload, payload_sha, "
        "input_tokens, output_tokens, created_at FROM narrations "
        "WHERE cik=? AND as_of=? ORDER BY created_at DESC LIMIT 1",
        (cik, as_of)).fetchone()
    return _from_row(row) if row else None
