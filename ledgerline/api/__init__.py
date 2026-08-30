"""
The delivery boundary: how an assessment leaves this repo.

Python is the compute worker and the sole author of signal records; anything
that serves them (service/server.mjs, or any future reader) reads and
re-serves, never recomputes. If a delivery surface ever needs to recompute a
number, that is the trigger to revisit this boundary -- not a reason to
duplicate arithmetic in a second language, which is how two surfaces come to
disagree about the same company.

Three modules, no logic here:

  contract  the versioned envelope (validation block required, score null
            when unassessed) and the JSONL export the read service consumes
  schema    one SPEC, projected to a JSON Schema for other languages and a
            validator for this one
  digest    one run as a text file -- banner, coverage, the computed
            expectation line, and only then a company name. No send step.
"""
from __future__ import annotations

from .contract import MEDIA_TYPE, SCHEMA_VERSION, envelope, export_jsonl, validation_block
from .schema import json_schema, validate

__all__ = ["MEDIA_TYPE", "SCHEMA_VERSION", "envelope", "export_jsonl",
           "validation_block", "json_schema", "validate"]
