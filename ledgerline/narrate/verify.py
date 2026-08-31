"""
The deterministic claim-to-diagnostic verifier. No model, no network, no
randomness: the same narration against the same payload always yields the
same failure list. This is the part of the narration tier that has to be
correct, because it is the only thing standing between "the model wrote it"
and "the numbers support it".

ROADMAP §8 asked that every claim "map to a diagnostic in the payload". That
is too weak: a sentence can name gross_margin truthfully and still invent the
number attached to it. Five checks per claim instead, all deterministic:

  (a) the named diagnostic is in signals_v3.TRACKED at all;
  (b) it actually FIRED -- present in payload["flags"], not merely present in
      payload["quiet"] below the trigger. The gate's own coverage lesson:
      present-but-quiet is not fired, exactly as scoreable=False is not
      score=0.0;
  (c) every cited dotted path resolves in the payload;
  (d) every numeric literal in the prose matches, within the half-ulp of its
      own printed precision, a value at one of THAT CLAIM'S OWN cited paths
      -- not merely somewhere in the payload. This is the anti-gaming check:
      global-index matching would pass an invented figure beside six
      unrelated citations, which is a check in name only;
  (e) direction words agree with the flag's declared direction -- traceable
      numbers do not imply a true relation.

Plus the banned lexicon, which makes "validated", "predict", "will",
"recommend" mechanically unwritable. It fails closed: the cost of a false
rejection is a machine-prose fallback; the cost of a false acceptance is a
prose surface claiming a killed gate predicts something.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import signals_v3
from . import payload as payload_mod
from .schema import Narration

ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Thousands-separated alternative FIRST, so "1,234" is one token rather than
# two. Magnitude words (million, bn, x) are deliberately not parsed: the
# payload pre-renders human-scale values, so a word-suffixed magnitude in
# prose is an untraceable literal by design.
NUMBER_RE = re.compile(
    r"[-+]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|[-+]?\$?\d+(?:\.\d+)?%?"
)

# (family, pattern). \b-anchored on purpose so "selling, general and
# administrative" and "shortfall" pass -- do NOT "fix" this into a substring
# match; test_narration pins the two known legitimate collisions.
BANNED: tuple[tuple[str, re.Pattern], ...] = (
    ("prediction", re.compile(
        r"\b(will|expect\w*|forecast\w*|predict\w*|likely|probab\w*|"
        r"imminent|anticipat\w*)\b", re.IGNORECASE)),
    ("advice", re.compile(
        r"\b(recommend\w*|buy|sell|short|overvalued|undervalued|"
        r"target price)\b", re.IGNORECASE)),
    ("accusation", re.compile(r"\b(fraud\w*|manipulat\w*)\b", re.IGNORECASE)),
    # The KILL family: endorsement of a gate that failed its own test.
    ("endorsement", re.compile(
        r"\b(validated|proven|reliable|accurate|guarantee\w*|confirms)\b",
        re.IGNORECASE)),
)

UP_WORDS: frozenset[str] = frozenset({
    "rose", "rising", "risen", "increase", "increased", "increasing",
    "climbed", "climbing", "grew", "growing", "widened", "widening",
    "expanded", "expanding", "jumped", "surged", "higher", "up",
})
DOWN_WORDS: frozenset[str] = frozenset({
    "fell", "falling", "fallen", "decline", "declined", "declining",
    "dropped", "dropping", "decrease", "decreased", "decreasing",
    "shrank", "shrinking", "narrowed", "narrowing", "contracted",
    "contracting", "collapsed", "lower", "down",
})


@dataclass(frozen=True)
class Failure:
    code: str
    claim: int  # -1 for headline / narration-level failures
    token: str
    detail: str


def dates(text: str) -> list[str]:
    return ISO_DATE_RE.findall(text)


def literals(text: str) -> list[str]:
    """Numeric literals with ISO dates MASKED OUT first -- otherwise
    '2024-03-31' contributes 2024, 03 and 31 as three untraceable numbers
    and every date-bearing sentence fails."""
    masked = ISO_DATE_RE.sub(lambda m: " " * len(m.group()), text)
    return NUMBER_RE.findall(masked)


def parse_literal(tok: str) -> list[tuple[float, float]]:
    """[(value, half_ulp)] candidates for one printed literal.

    The half-ulp of the token's own printed precision IS the tolerance:
    "2.4" matches a payload value of 2.4382 (rounding is legal) and does not
    match 2.5 (invention is not). A percent token returns BOTH the face value
    and the /100 fraction, because gross_margin is stored as 0.4123 and prose
    properly writes "41.2%". A bare integer carries half_ulp 0.5 -- looser
    than a decimal, deliberately uniform, and pinned by a test rather than
    accidental.
    """
    is_pct = tok.endswith("%")
    core = tok.rstrip("%").lstrip("+").replace("$", "").replace(",", "")
    decimals = len(core.split(".")[1]) if "." in core else 0
    v = float(core)
    hu = 0.5 * 10 ** -decimals
    out = [(v, hu)]
    if is_pct:
        out.append((v / 100.0, hu / 100.0))
    return out


def _numeric_leaves(node: object) -> list[float]:
    """Numeric values reachable from one resolved citation. bool first --
    it is a subclass of int (see payload.number_index)."""
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [float(node)]
    if isinstance(node, dict):
        return [x for v in node.values() for x in _numeric_leaves(v)]
    if isinstance(node, list):
        return [x for v in node for x in _numeric_leaves(v)]
    return []


def _banned(text: str, claim: int, out: list[Failure]) -> None:
    for family, pat in BANNED:
        m = pat.search(text)
        if m:
            out.append(Failure("BANNED_TERM", claim, m.group(),
                               f"the word belongs to the banned "
                               f"'{family}' family"))


def verify(narration: Narration, payload: dict) -> list[Failure]:
    """Every failure in one pass, so a repair prompt can list them all."""
    failures: list[Failure] = []

    if narration.abstain:
        # A model-initiated abstention carries no claims to check; the
        # caller publishes the deterministic fallback instead.
        return failures

    # A headline is a label, not an assertion: unciteable prose must not
    # carry figures or dates, and the lexicon applies to it too.
    if literals(narration.headline) or dates(narration.headline):
        failures.append(Failure(
            "NUMBER_IN_HEADLINE", -1, narration.headline,
            "the headline is uncited prose and may carry no figure or date"))
    _banned(narration.headline, -1, failures)

    if not narration.claims:
        failures.append(Failure(
            "NO_CLAIMS", -1, "",
            "a narration with no claims narrates nothing; abstain instead"))

    payload_dates = set(payload_mod.date_index(payload).values())
    seen: dict[str, int] = {}

    for i, claim in enumerate(narration.claims):
        name = claim.diagnostic

        if name in seen:
            failures.append(Failure(
                "DUPLICATE_DIAGNOSTIC", i, name,
                f"claim {seen[name]} already covers this diagnostic; padding "
                "one flag into two sentences reads as more evidence than "
                "exists"))
        seen.setdefault(name, i)

        fired = name in payload.get("flags", {})
        if name not in signals_v3.TRACKED:
            failures.append(Failure(
                "UNKNOWN_DIAGNOSTIC", i, name,
                "not a diagnostic this gate computes at all"))
        elif not fired:
            failures.append(Failure(
                "DIAGNOSTIC_NOT_FIRED", i, name,
                "computed but below the trigger -- present-but-quiet is not "
                "fired, exactly as scoreable=False is not score=0.0"))

        cited_values: list[float] = []
        for path in claim.cites:
            node = payload_mod.resolve(payload, path)
            if node is None:
                failures.append(Failure(
                    "BAD_CITATION", i, path,
                    "this path resolves to nothing in the payload; an "
                    "invented path is an invented source"))
            else:
                cited_values.extend(_numeric_leaves(node))

        # Check (d), the anti-gaming check: each literal must match a value
        # reachable from THIS claim's own citations.
        for tok in literals(claim.text):
            ok = any(
                abs(target - v) <= hu
                for target in cited_values
                for v, hu in parse_literal(tok)
            )
            if not ok:
                failures.append(Failure(
                    "UNTRACEABLE_NUMBER", i, tok,
                    "no value at this claim's cited paths matches it within "
                    "its own printed precision"))

        for d in dates(claim.text):
            if d not in payload_dates:
                failures.append(Failure(
                    "UNTRACEABLE_DATE", i, d,
                    "this date appears nowhere in the payload"))

        _banned(claim.text, i, failures)

        # Check (e): the direction the prose asserts must be the direction
        # the flag declares. A fired flag's signed z is positive, so the
        # underlying value moved WITH TRACKED's declared direction: -1 means
        # it fell, +1 means it rose.
        if fired and name in signals_v3.TRACKED:
            direction = signals_v3.TRACKED[name][0]
            words = set(re.findall(r"[a-z]+", claim.text.lower()))
            wrong = words & (UP_WORDS if direction < 0 else DOWN_WORDS)
            if wrong:
                moved = "fell" if direction < 0 else "rose"
                failures.append(Failure(
                    "DIRECTION_MISMATCH", i, sorted(wrong)[0],
                    f"the underlying value {moved}; a traceable number does "
                    "not license an untrue relation"))

    return failures
