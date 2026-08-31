"""
Every string the model ever sees, in one auditable place.

The system prompt states the rules the verifier enforces, in the verifier's
own terms, so the model is aimed at the check rather than left to guess it.
When a check moves, this text moves with it: the leaf-citation rule and the
stated-direction rule are here because prose that failed them was published,
and a model told only "cite your paths" writes `cites: ["flags"]`.

It carries the KILL as context, not as a formatting instruction:
the reader will see the failure banner above the model's text, rendered by
run.render() from the committed record -- the model is forbidden from writing
the disclosure itself, because a model-authored disclaimer can be softened,
paraphrased or omitted, and a constant cannot.

repair_prompt() emits ONLY failure codes, claim indices, offending tokens and
detail strings. It carries no payload data and no corrected values: handing
the model the right number is dictation, not repair, and would make the
second attempt untestable. Pinned by a test that walks every numeric literal
in the repair text.
"""
from __future__ import annotations

import json

from .verify import Failure

SYSTEM = """\
You describe arithmetic that already happened. A deterministic detector \
compared one company's latest filed figures against that same company's own \
history and flagged the measures listed under "flags" in the payload. You \
write short plain-English claims about those flagged measures, nothing else.

Hard rules -- a deterministic verifier rejects your response if any fails:
1. Name only a diagnostic listed under "flags". The measures under "quiet" \
were computed and stayed normal; they did not fire and you may not say they \
did.
2. Cite, for every claim, the exact dotted payload paths the numbers in your \
sentence come from (e.g. "flags.gross_margin.z"). Each path must name ONE \
value, never a group: "flags.gross_margin.z" is a citation, "flags" and \
"flags.gross_margin" are not. Every number you write must match a value at \
one of YOUR OWN cited paths, at the precision you print it.
3. Write no number and no date you did not read from the payload. Rounding \
to fewer digits is fine; inventing is not. A ratio is stored as a fraction: \
0.4123 is written either as 0.412 or as 41.2%, never as 0.4%.
4. Say which way each measure moved, in the sentence, and say it correctly. \
A measure that fired moved AGAINST the company: if the payload's "direction" \
is -1 the value fell, if it is +1 the value rose, and either way the measure \
got worse. A claim that states no direction is rejected, and so is one that \
calls the move an improvement.
5. Describe what the arithmetic shows -- how far a measure sits from this \
company's own trailing median. Never what it implies about the future: no \
predictions, no advice, no words like "will", "likely", "expect", \
"recommend", and no verdicts on the company.

Context you are writing under: this detector FAILED its pre-registered test \
(the payload's "status" block carries the measured numbers). The reader sees \
that failure banner rendered above your text by the calling program -- do \
not write the disclaimer yourself, and do not write as though a flag is a \
warning or a prediction. It is a description of an unusual number in a \
filing, no more.

The headline is a label: no numbers, no dates. Keep each claim to one or two \
plain sentences a non-accountant can follow, using the "label" field's \
wording for what each measure means.
"""

_PATH_HELP = """\
Citation paths are dotted keys into the JSON payload below, and each one must
land on a single value rather than on a group of them, e.g.
  flags.gross_margin.z          the sigma move of the gross_margin flag
  flags.gross_margin.value      its current value
  summary.n_fired               how many measures fired
  flags                         REJECTED -- a group, not a value
  flags.gross_margin            REJECTED -- a group, not a value
Worked example claim:
  {"text": "Gross margin came in at 31.2%, a 2.6-sigma move below this \
company's own trailing median of 40.8%.",
   "diagnostic": "gross_margin",
   "cites": ["flags.gross_margin.value", "flags.gross_margin.z",
             "flags.gross_margin.baseline_median"]}
"""


def user_prompt(payload: dict) -> str:
    """The payload as sorted-key JSON in a fenced block. Sorted keys keep the
    serialization byte-stable for one payload, which is what makes the
    content-hash dedupe and any future prompt caching honest."""
    return (
        _PATH_HELP
        + "\nThe payload:\n```json\n"
        + json.dumps(payload, sort_keys=True, indent=1)
        + "\n```\n"
    )


def repair_prompt(failures: list[Failure]) -> str:
    """Failure codes and offending tokens only -- no corrected values."""
    lines = ["Your response failed deterministic verification. "
             "Fix ONLY these failures and resend the full JSON:"]
    for f in failures:
        where = "headline" if f.claim < 0 else f"claim {f.claim}"
        tok = f" token '{f.token}'" if f.token else ""
        lines.append(f"- {f.code} at {where}:{tok} -- {f.detail}")
    lines.append("Remember: every number must come from a path you cite. "
                 "If you cannot state a claim from the payload alone, "
                 "abstain.")
    return "\n".join(lines)
