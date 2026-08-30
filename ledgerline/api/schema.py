"""
One declarative SPEC for the published record shape, and two projections of
it: a JSON Schema (draft 2020-12) for consumers in other languages, and a
purpose-built validator for the Python side. Two projections, one definition
-- a contract that can disagree with itself is not a contract.

Why not pydantic: it is declared in pyproject and deliberately unused, the
same way edgar.py uses urllib and not the declared httpx. The register here is
stdlib throughout, and the drift risk pydantic would have closed is closed by
tests instead (the golden comparison against the committed schema file, and
validate() running on every exported line).

Why validate() is hand-rolled rather than a JSON Schema engine: the shape is
closed and one level deep, so required keys, scalar types, enums, nullability
and one nesting level are the whole job. A general validator would be a new
dependency for no additional coverage.

Two rules of this schema exist because of measured defects:

  * `validation` is REQUIRED at the same depth as `assessment`, and
    additionalProperties is false at the root and on `validation`. A consumer
    cannot deserialise a score without deserialising the fact that the gate
    failed its 2026-08-30 test, and a producer cannot add or rename a
    validation field without the schema version moving.
  * `score` must be null exactly when `assessment.state` is "unscoreable".
    FINDINGS section 3's defect was score 0.0 beside scoreable=false --
    "assessed, looks clean" -- and that mistake must be rejected, not merely
    discouraged, at the delivery boundary.

Version policy: an additive optional field is a patch; a new required field
or a changed enum is a major, and contract.SCHEMA_VERSION moves with it. The
committed service/signal.schema.json is golden-compared by a test, so any
shape change fails the suite until someone consciously bumps the version and
regenerates the file with `ledgerline contract-schema`.
"""
from __future__ import annotations

import hashlib
import json
import os

# $id is stable and deliberately non-resolvable: no network fetch, ever, from
# either side of the contract.
SCHEMA_ID = "https://ledgerline.invalid/schema/signal/1.0.0.json"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA_OUT = os.path.join(ROOT, "service", "signal.schema.json")

# field -> {type, required, nullable?, enum?, fields? (one nesting level),
#           closed? (additionalProperties false)}. `object` with no `fields`
# is an open dict -- used only for payloads the gate owns (flags, run,
# measured), whose shape is pinned by the gate's own tests.
SPEC: dict[str, dict] = {
    "schema_version": {"type": "string", "required": True},
    "media_type": {"type": "string", "required": True},
    "signal_id": {"type": "string", "required": True},
    "seq": {"type": "integer", "required": True},
    "emitted_at": {"type": "string", "required": True},
    "source": {"type": "string", "required": True,
               "enum": ["scan", "score", "emit", "replay"]},
    "as_of": {"type": "string", "required": True},
    "period": {"type": "string", "required": True, "nullable": True},
    "filer": {
        "type": "object", "required": True, "closed": True,
        "fields": {
            "cik": {"type": "string", "required": True},
            "ticker": {"type": "string", "required": True, "nullable": True},
        },
    },
    "assessment": {
        "type": "object", "required": True, "closed": True,
        "fields": {
            "state": {"type": "string", "required": True,
                      "enum": ["fired", "quiet", "unscoreable"]},
            "score": {"type": "number", "required": True, "nullable": True},
            "reason": {"type": "string", "required": True, "nullable": True},
            "reason_code": {"type": "string", "required": True,
                            "nullable": True},
            "n_flags": {"type": "integer", "required": True},
        },
    },
    "flags": {"type": "array", "required": True},
    "gate": {
        "type": "object", "required": True, "closed": True,
        "fields": {
            "version": {"type": "string", "required": True},
            "validation_status": {"type": "string", "required": True},
        },
    },
    "provenance": {
        "type": "object", "required": True, "closed": True,
        "fields": {
            "accessions": {"type": "array", "required": True},
            "derived_fraction": {"type": "number", "required": True,
                                 "nullable": True},
        },
    },
    "run": {"type": "object", "required": True},
    "validation": {
        "type": "object", "required": True, "closed": True,
        "fields": {
            "status": {"type": "string", "required": True},
            "verdict": {"type": "string", "required": True},
            "scored_on": {"type": "string", "required": True},
            "measured": {"type": "object", "required": True},
            "statement": {"type": "string", "required": True},
            "writeup": {"type": "string", "required": True},
        },
    },
}

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "array": (list,),
    "object": (dict,),
}


def _check(value: object, spec: dict, path: str, errors: list[str]) -> None:
    if value is None:
        if not spec.get("nullable"):
            errors.append(f"{path}: must not be null")
        return
    want = _PY_TYPES[spec["type"]]
    # bool is an int subclass; a score of True would pass an isinstance check
    # while meaning nothing, so it is refused by name.
    if isinstance(value, bool) or not isinstance(value, want):
        errors.append(f"{path}: expected {spec['type']}, "
                      f"got {type(value).__name__}")
        return
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path}: {value!r} not one of {spec['enum']}")
    if spec["type"] == "object" and "fields" in spec:
        assert isinstance(value, dict)
        for name, sub in spec["fields"].items():
            if name not in value:
                if sub.get("required"):
                    errors.append(f"{path}.{name}: required field is missing")
                continue
            _check(value[name], sub, f"{path}.{name}", errors)
        if spec.get("closed"):
            for extra in sorted(set(value) - set(spec["fields"])):
                errors.append(f"{path}.{extra}: field is not in the contract "
                              "(additionalProperties is false)")


def validate(record: dict) -> list[str]:
    """Errors as strings; [] means the record conforms.

    Beyond the shape, one cross-field rule: score is null exactly when the
    state is "unscoreable". Both directions matter -- a numeric score on an
    unassessed company is the "assessed, looks clean" costume, and a null
    score on an assessed one hides an assessment that happened.
    """
    if not isinstance(record, dict):
        return ["record: expected object"]
    errors: list[str] = []
    for name, spec in SPEC.items():
        if name not in record:
            if spec.get("required"):
                errors.append(f"{name}: required field is missing")
            continue
        _check(record[name], spec, name, errors)
    for extra in sorted(set(record) - set(SPEC)):
        errors.append(f"{extra}: field is not in the contract "
                      "(additionalProperties is false)")
    assessment = record.get("assessment")
    if isinstance(assessment, dict):
        state, score = assessment.get("state"), assessment.get("score")
        if state == "unscoreable" and score is not None:
            errors.append("assessment.score: must be null when the state is "
                          "'unscoreable' -- 0.0 reads as 'assessed, looks "
                          "clean', which is the defect this rule exists for")
        if state in ("fired", "quiet") and score is None:
            errors.append(f"assessment.score: must carry the number when the "
                          f"state is '{state}'")
    validation = record.get("validation")
    if isinstance(validation, dict) and validation.get("statement") == "":
        errors.append("validation.statement: must be a non-empty sentence")
    return errors


def _project(spec: dict) -> dict:
    out: dict = {}
    if spec.get("nullable"):
        out["type"] = [spec["type"], "null"]
    else:
        out["type"] = spec["type"]
    if "enum" in spec:
        out["enum"] = spec["enum"]
    if spec["type"] == "object" and "fields" in spec:
        out["properties"] = {n: _project(s) for n, s in spec["fields"].items()}
        out["required"] = [n for n, s in spec["fields"].items()
                           if s.get("required")]
        if spec.get("closed"):
            out["additionalProperties"] = False
    return out


def json_schema() -> dict:
    """The SPEC as a draft 2020-12 JSON Schema, for consumers that are not
    this module. Generated, never hand-written -- a second hand-maintained
    copy of the shape is a copy that drifts."""
    from . import contract  # local import: contract imports this module

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Ledgerline signal record",
        "description": (
            "Generated by `ledgerline contract-schema`; do not edit by hand. "
            f"Version {contract.SCHEMA_VERSION}. The validation block is a "
            "required field: a consumer cannot receive a score without "
            "receiving the fact that the detector failed its own "
            "pre-registered test on 2026-08-30."
        ),
        "type": "object",
        "properties": {n: _project(s) for n, s in SPEC.items()},
        "required": [n for n, s in SPEC.items() if s.get("required")],
        "additionalProperties": False,
    }


def write(path: str = SCHEMA_OUT) -> str:
    """Write the generated schema and return its sha256, so the commit that
    changes the shape carries a checkable identity for what it changed to."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(json_schema(), indent=2) + "\n"
    with open(path, "w") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode()).hexdigest()
