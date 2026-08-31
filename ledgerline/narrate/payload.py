"""
The deterministic payload the model is shown -- and the ONLY thing it sees.

Why this module exists: the narration tier lets a language model write prose,
and prose is the layer where a reader stops seeing arithmetic and starts
seeing a conclusion. The defence is structural: anything absent from this
payload is by construction unciteable, and the verifier (verify.py) rejects
any number in the prose that does not match a value at a cited path here. So
build() COPIES values signals_v3.evaluate() already computed -- it never
derives a new one. A derived value would be a number the model could cite
that no scorer ever produced.

Point-in-time discipline travels through unchanged: the verdict's provenance
block was built from the edgar.as_of() snapshot, whose rows already carry the
vintage that was public at the cutoff (FINDINGS §5). provenance_for() exists
for callers holding only a normalized dict, and reads row["vintages"] through
derive.newest_at() -- never the top-level filed date, which belongs to the
LATEST vintage and would make a 2012-cutoff narration cite a 2014 filing.

payload_sha() is the dedupe key and part of the narrations primary key. A
restatement changes a vintage, which changes provenance, which changes the
sha, which makes the re-narration a NEW row rather than an edit -- append-only
by construction. The sha is therefore sensitive to payload SHAPE, not just
content: SCHEMA_VERSION is inside the hashed payload, and bumping it is a
deliberate, budgeted re-narration of history, never a silent one.
"""
from __future__ import annotations

import hashlib
import json
import re

from .. import derive, signals_v3, status

# Payload shape version, hashed into payload_sha. Bump only on purpose: every
# stored narration keys on the sha, so a shape change re-narrates everything.
SCHEMA_VERSION = "1"

# Cap so a pathological filer cannot balloon the prompt; summary.n_fired
# reports the true count so the model can say "3 of 13" without the payload
# holding all 13.
MAX_FLAGS_IN_PAYLOAD = 8

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Which fields of a fired flag the model may read. `detail` is the gate's own
# deterministic sentence -- carried for the abstention fallback, and harmless
# to show the model because a string leaf never enters the number index.
_FLAG_FIELDS = ("label", "z", "value", "baseline_median", "baseline_scale",
                "baseline_n", "floored", "detail")


def provenance_for(norm: dict, metric: str, cutoff: str) -> dict | None:
    """The vintage of `metric`'s latest row that was public at `cutoff`.

    Reads row["vintages"] through derive.newest_at(), never the top-level
    filed date -- the top-level row carries the LATEST vintage, so a 2012
    narration would otherwise cite a filing from 2014 (FINDINGS §5: a
    citation of a filing that did not yet exist is a lookahead claim in
    prose). None when nothing was public by the cutoff -- never a guess.
    """
    best: tuple[str, dict] | None = None
    for row in norm.get(metric, []):
        vints = row.get("vintages") or [row]
        hit = derive.newest_at(vints, cutoff)
        if hit is None:
            continue
        end = row.get("end") or ""
        if best is None or end > best[0]:
            best = (end, {**row, **hit})
    if best is None:
        return None
    row = best[1]
    return {
        "end": row.get("end"),
        "concept": row.get("concept"),
        "form": row.get("form"),
        "filed": row.get("filed"),
        "origin": row.get("origin"),
        "sources": [s for s in row.get("sources", []) if s],
    }


def build(verdict: dict, norm: dict | None = None, *,
          cutoff: str | None = None) -> dict:
    """The payload for one gated-in verdict. Copies; never derives.

    The one structural transformation between verdict and payload: flags are
    re-keyed from a LIST into a DICT by lowercase diagnostic name, so citation
    paths are stable and readable ("flags.gross_margin.z") and do not shift
    when flag ordering changes.

    Raises on an unstamped verdict: the Phase 0 KILL travels inside the
    payload (the model writes under the constraint), and a verdict without
    the stamp is a claim the project cannot support.
    """
    status.assert_stamped(verdict)
    cutoff = cutoff or verdict.get("as_of")

    flags_list = sorted(verdict.get("flags", []), key=lambda f: -f.get("z", 0))
    flags: dict[str, dict] = {}
    for f in flags_list[:MAX_FLAGS_IN_PAYLOAD]:
        name = (f.get("code") or "").lower()
        entry = {k: f.get(k) for k in _FLAG_FIELDS}
        entry["direction"] = signals_v3.TRACKED.get(name, (0,))[0]
        entry["weight"] = f.get("weight")
        entry["filed"] = f.get("filed")
        flags[name] = entry

    # Provenance per fired flag, per input metric -- copied from the verdict,
    # whose reading_trace was built from the as_of() snapshot and is therefore
    # vintage-correct already. provenance_for() fills a gap only when a caller
    # supplies norm and the verdict carries no trace.
    prov: dict[str, dict] = {}
    trace_flags = (verdict.get("provenance") or {}).get("flags") or {}
    for name in flags:
        per_metric = trace_flags.get(name)
        if per_metric is None and norm is not None and cutoff:
            per_metric = {
                m: provenance_for(norm, m, cutoff) or {"sources": []}
                for m in signals_v3.DIAGNOSTIC_INPUTS.get(name, ())
            }
        if per_metric:
            prov[name] = {
                m: {k: t.get(k) for k in
                    ("end", "concept", "form", "filed", "origin", "sources")}
                for m, t in per_metric.items()
            }

    # Signed z of diagnostics that did NOT fire: the model can be told what
    # stayed normal without being able to claim it fired -- the verifier
    # rejects any claim naming a diagnostic outside `flags`.
    quiet = {name: z for name, z in (verdict.get("z") or {}).items()
             if name not in flags}

    return {
        "schema_version": SCHEMA_VERSION,
        # The stamp, verbatim: the KILL is in the model's context, not
        # stapled on after it writes.
        "status": dict(verdict["phase0"]),
        "gate_status": verdict.get("gate_status"),
        "ticker": verdict.get("ticker"),
        "cik": verdict.get("cik"),
        "as_of": cutoff,
        "period": verdict.get("period"),
        "score": verdict.get("score"),
        "threshold": signals_v3.THRESHOLD,
        "min_flags": signals_v3.MIN_FLAGS,
        "z_trigger": signals_v3.Z_TRIGGER,
        "summary": {
            "n_fired": len(verdict.get("flags", [])),
            "n_tracked": len(signals_v3.TRACKED),
            "derived_fraction": verdict.get("derived_fraction"),
        },
        "flags": flags,
        "provenance": prov,
        "quiet": quiet,
    }


def payload_sha(payload: dict) -> str:
    """sha256 over the sorted-key JSON: the dedupe key and part of the
    narrations primary key."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


def _walk(node: object, path: str, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}.{i}", out)
    else:
        out.append((path, node))


def number_index(payload: dict) -> dict[str, float]:
    """Every numeric leaf, keyed by dotted path. bool is checked FIRST --
    it is a subclass of int, and True would otherwise index as 1.0 and let
    the model write '1' with a citation to a boolean."""
    leaves: list = []
    _walk(payload, "", leaves)
    return {p: float(v) for p, v in leaves
            if not isinstance(v, bool) and isinstance(v, (int, float))}


def date_index(payload: dict) -> dict[str, str]:
    """Every ISO-date-shaped string leaf, keyed by dotted path. Dates are
    verified against this index, never the number index, so the digits in
    '2024-03-31' never enter numeric traceability."""
    leaves: list = []
    _walk(payload, "", leaves)
    return {p: v for p, v in leaves
            if isinstance(v, str) and _ISO_DATE.match(v)}


def resolve(payload: dict, path: str) -> object | None:
    """Follow a dotted citation path into the payload. None when any step
    fails -- an invented path is an invented source, and the verifier treats
    None as exactly that."""
    node: object = payload
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node
