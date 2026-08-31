"""
The shape of what the model may say, and the parser that holds it to it.

The model does not emit free prose; it emits claims that each name ONE fired
diagnostic and cite the dotted payload paths they draw numbers from. Citation
is not decoration -- it is what makes the deterministic verifier possible: a
number with no cited path has nothing to be checked against, so the schema
makes uncited prose unwritable rather than merely discouraged.

json_schema() builds the `diagnostic` property as an ENUM of the codes that
fired for THIS event, so the provider-side constraint already forbids naming
a diagnostic that did not fire, and CITATION_PATTERN constrains a citation to
the shape of a single payload leaf. The verifier re-checks both anyway: a
constraint you asked the provider to enforce is not a constraint you
verified, and this gate's whole history is of checks that were assumed
rather than run.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

# What a citation is allowed to LOOK like, mirrored into the provider-side
# constraint. It exists because `cites: ["flags"]` was schema-valid: dotted
# paths were capped for length and nothing else, payload.resolve() returned
# the container, and one claim inherited every number in the payload.
#
# The pattern encodes the payload's actual shape -- flags.<name>.<field>,
# provenance.<flag>.<metric>.<field>, quiet/summary/status.<field>, or a
# top-level scalar -- and refuses a bare container name. It is a hint, not
# the check: a regex cannot know that provenance...sources is a list, and a
# provider that ignores `pattern` costs nothing here. verify.py's
# CITATION_NOT_A_LEAF is the authority, because a constraint you asked the
# provider to enforce is not a constraint you verified.
CITATION_PATTERN = (
    r"^(?!(?:flags|provenance|quiet|summary|status)$)"
    r"(?:flags\.[^.]+\.[^.]+"
    r"|provenance\.[^.]+\.[^.]+\.[^.]+"
    r"|(?:quiet|summary|status)\.[^.]+"
    r"|[^.]+)$"
)


class MalformedNarration(ValueError):
    """The response was not the schema. Counts as a failed attempt like any
    verification failure -- garbage does not buy a free retry."""


class Claim(BaseModel):
    text: str
    diagnostic: str
    cites: list[str]


class Narration(BaseModel):
    headline: str
    claims: list[Claim]
    abstain: bool = False
    abstain_reason: str | None = None


def json_schema(fired_codes: list[str]) -> dict:
    """The provider-side constraint for one event. Tight on purpose:
    additionalProperties false throughout, text capped so a claim stays a
    sentence rather than an essay, claims capped so three flags cannot be
    padded into a page."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "claims", "abstain"],
        "properties": {
            "headline": {"type": "string", "maxLength": 120},
            "claims": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "diagnostic", "cites"],
                    "properties": {
                        "text": {"type": "string", "maxLength": 320},
                        "diagnostic": {"type": "string",
                                       "enum": sorted(fired_codes)},
                        "cites": {"type": "array", "maxItems": 8,
                                  "items": {"type": "string",
                                            "maxLength": 120,
                                            "pattern": CITATION_PATTERN}},
                    },
                },
            },
            "abstain": {"type": "boolean"},
            "abstain_reason": {"type": ["string", "null"], "maxLength": 320},
        },
    }


def parse(raw: str) -> Narration:
    """json.loads + model validation, folded into one failure mode."""
    try:
        return Narration.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise MalformedNarration(str(exc)) from exc
