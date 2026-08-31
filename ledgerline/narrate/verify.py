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
  (c) every cited dotted path resolves in the payload AND names a single
      leaf, not a container. A container citation is what made (d) collapse:
      payload.resolve() returns whatever a dotted path names, so `cites:
      ["flags"]` flattened every number in the payload into one claim's
      traceable set, and eight such citations -- inside the schema's own
      maxItems cap -- reproduced the whole-payload number index exactly.
      That is the global-index matching (d) exists to prevent, restored by
      a citation the schema was happy to accept;
  (d) every numeric literal in the prose matches, within the half-ulp of its
      own printed precision, a value at one of THAT CLAIM'S OWN cited paths
      -- not merely somewhere in the payload. This is the anti-gaming check:
      global-index matching would pass an invented figure beside six
      unrelated citations, which is a check in name only;
  (e) the claim states the direction the flag declares, and states no other.
      Both halves are load-bearing. Banning the wrong-direction words alone
      was a closed wordlist and therefore always incomplete: "improved",
      "strengthened", "grows" and "accelerated" each asserted that a value
      ROSE against a flag that fired because it FELL, and each passed. A
      list of forbidden words can never be finished, so the check is
      inverted -- a claim must carry a word from the RIGHT set, and a claim
      carrying none is refused. Being wrong about that costs prose; being
      wrong the other way publishes an inverted relation.

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

def _inflect(*roots: str) -> set[str]:
    """A verb root and its ordinary present/past/participle forms.

    Hand-maintained direction lists drift in ways nobody can see by reading
    them: "grew" and "growing" were banned while "grows" was not, "rose" and
    "rising" while "rise" and "rises" were not, "dropped" while "drops" was
    not. Generating the forms from a root removes that whole class of gap.
    Irregular and consonant-doubled forms are still listed by hand beside
    each call -- English has no rule this function could apply.
    """
    out: set[str] = set()
    for r in roots:
        out.add(r)
        if r.endswith("e"):
            out.update({r + "s", r + "d", r[:-1] + "ing"})
        elif r.endswith(("s", "x", "z", "ch", "sh")):
            out.update({r + "es", r + "ed", r + "ing"})
        else:
            out.update({r + "s", r + "ed", r + "ing"})
    return out


# The value went UP / went DOWN. These describe the raw movement and are
# read against the flag's declared direction: a fired -1 flag means the value
# fell, so UP_WORDS contradict it, and a fired +1 flag means it rose.
UP_WORDS: frozenset[str] = frozenset(
    _inflect("rise", "increase", "climb", "grow", "widen", "expand", "jump",
             "surge", "accelerate", "spike", "escalate", "mount")
    | {"rose", "risen", "grew", "grown", "higher", "up", "upward", "upwards",
       "above"})
DOWN_WORDS: frozenset[str] = frozenset(
    _inflect("fall", "decline", "drop", "decrease", "shrink", "narrow",
             "contract", "collapse", "slip", "slide", "sink", "plunge",
             "tumble", "soften", "compress", "subside")
    | {"fell", "fallen", "dropped", "dropping", "shrank", "shrunk",
       "slipped", "slipping", "lower", "down", "downward", "downwards",
       "below"})

# The metric got BETTER / got WORSE. These are not movement words: whether
# "improved" means up or down depends on the flag's sign, and a fired flag
# ALWAYS means the metric got worse. So BETTER_WORDS contradict any fired
# flag whichever way it points, and WORSE_WORDS satisfy any of them. Missing
# this distinction is what let "improved to 0.167" and "eased to 3.313" pass
# against flags that fired because the company deteriorated.
BETTER_WORDS: frozenset[str] = frozenset(
    _inflect("improve", "strengthen", "recover", "rebound", "ease",
             "normalise", "normalize", "stabilise", "stabilize", "heal",
             "repair")
    | {"better", "healthier", "stronger", "healthy"})
WORSE_WORDS: frozenset[str] = frozenset(
    _inflect("deteriorate", "weaken", "worsen", "erode", "degrade")
    | {"worse", "weaker", "weak"})


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

    The face-value candidate for a percent token is a READING, not a licence:
    which of the two readings is legitimate depends on the units of the value
    being matched, which this function cannot see. matches() decides that.
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


def matches(tok: str, target: float) -> bool:
    """Does one printed literal name this payload value?

    Not simply "any candidate within its own half-ulp", because the face
    value of a percent token carries the half-ulp of its PRINTED precision
    rather than a tolerance in the payload's units. For a 0-decimal token
    that half-ulp is 0.5, so "0%" sat within tolerance of ocf_to_revenue's
    0.16652 and verified as a true statement that a company generated no
    cash per dollar of sales when it generated 16.7 cents. Units confusion
    -- 0.287 written as 28.7% -- is the exact class this verifier exists to
    catch, and it was passing at 100x the wrong scale.

    The rule: a percent token may be read at face value only against a value
    already in percent units, and every ratio this gate stores is a fraction.
    So a target of magnitude <= 1 gets the /100 reading only. A payload that
    genuinely held a sub-1 percent-unit value would see the claim refused --
    which costs prose, not correctness, and is the direction this module
    fails in by design.
    """
    # parse_literal puts the face reading first and the /100 fraction, when
    # there is one, second. A percent token against a fraction-scale target
    # keeps only the second.
    readings = parse_literal(tok)
    if tok.endswith("%") and abs(target) <= 1.0:
        readings = readings[1:]
    return any(abs(target - v) <= hu for v, hu in readings)


def _leaf_number(node: object) -> float | None:
    """The number a resolved citation names, or None if it names something
    else (a string, a null, a bool).

    Deliberately NOT recursive, and it must never become recursive again.
    The recursive version flattened every number under a container into the
    claim's traceable set, so `cites: ["flags"]` handed one claim the whole
    payload's numbers and another flag's z could be published as this
    flag's. A citation names one number; verify() rejects a container
    outright (CITATION_NOT_A_LEAF) rather than digging through it.

    bool is checked FIRST -- it is a subclass of int, and True would
    otherwise be citeable as the figure 1 (see payload.number_index).
    """
    if isinstance(node, bool):
        return None
    if isinstance(node, (int, float)):
        return float(node)
    return None


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
            elif isinstance(node, (dict, list)):
                # Check (c)'s second half. Without it `cites: ["flags"]` is
                # a schema-valid citation that hands the claim every number
                # in the payload, and check (d) below degenerates into the
                # global-index matching it exists to prevent.
                failures.append(Failure(
                    "CITATION_NOT_A_LEAF", i, path,
                    "this path names a group of values, not one value; a "
                    "citation that covers everything traces nothing"))
            else:
                leaf = _leaf_number(node)
                if leaf is not None:
                    cited_values.append(leaf)

        # Check (d), the anti-gaming check: each literal must match a value
        # reachable from THIS claim's own citations.
        for tok in literals(claim.text):
            ok = any(matches(tok, target) for target in cited_values)
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
        # it fell, +1 means it rose. Either way the metric got WORSE -- that
        # is what firing means -- so a claim that the company improved
        # contradicts the flag whichever way the flag points.
        if fired and name in signals_v3.TRACKED:
            direction = signals_v3.TRACKED[name][0]
            moved = "fell" if direction < 0 else "rose"
            words = set(re.findall(r"[a-z]+", claim.text.lower()))
            right = (DOWN_WORDS if direction < 0 else UP_WORDS)
            wrong = words & ((UP_WORDS if direction < 0 else DOWN_WORDS)
                             | BETTER_WORDS)
            if wrong:
                failures.append(Failure(
                    "DIRECTION_MISMATCH", i, sorted(wrong)[0],
                    f"the underlying value {moved} and the measure got "
                    "worse; a traceable number does not license an untrue "
                    "relation"))
            elif not (words & (right | WORSE_WORDS)):
                # The inversion. A closed list of forbidden words can never
                # be finished -- "improved", "strengthened", "grows" and
                # "accelerated" all asserted a rise against a flag that
                # fired on a fall, and all passed -- so the claim must say
                # which way the value went rather than merely avoid saying
                # it wrongly. An incomplete RIGHT list only costs prose.
                failures.append(Failure(
                    "DIRECTION_UNSTATED", i, "",
                    f"the underlying value {moved}; say so in the claim -- a "
                    "figure with no stated direction leaves the reader to "
                    "supply one"))

    return failures
