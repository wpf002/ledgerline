"""
The closed taxonomy of abstention reasons.

Why this module exists: every refusal in the codebase used to be a free-text
f-string built at the point of refusal ("insufficient quarterly coverage:
operating_cash_flow 33%"). Prose cannot be counted, grouped, or compared
across runs, so "how often do we refuse, and why" -- the question the coverage
dashboard answers -- was unanswerable. Measured at 2024-05-15 on a 250-filer
sample, exactly 1 of 169 scoreable filers had all 13 diagnostics evaluated and
nothing recorded why the rest were missing. A code is for counting; the detail
sentence that travels beside it is what a reader is owed. Neither replaces the
other.

Codes are module-level string constants, not an Enum: they are persisted to
sqlite and JSON, and a bare string round-trips without a serializer.

UNEXPLAINED is a real member, not an oversight. diagnose() records its own
reasons at every None-return branch, so UNEXPLAINED should never occur -- but a
taxonomy that silently absorbs what it does not understand is worse than no
taxonomy, because it reads as complete. The dashboard publishes the count so a
new None-branch that forgets to record shows up as a rising number, not as a
silently wrong attribution.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------- filer level
# evaluate() produced no score at all.

NO_XBRL_FACTS = "NO_XBRL_FACTS"
NO_REVENUE_AT_CUTOFF = "NO_REVENUE_AT_CUTOFF"
REQUIRED_COVERAGE_LOW = "REQUIRED_COVERAGE_LOW"
SHORT_HISTORY = "SHORT_HISTORY"
# The structural abstention: the diagnostics computable for this filer carry
# too little weight to reach THRESHOLD at ANY z. Before this code existed such
# a filer reported score 0.0 / gated_in False / scoreable True -- an abstention
# wearing the costume of a clean assessment, the exact failure the filer-level
# coverage gate was written to prevent.
CANNOT_REACH_THRESHOLD = "CANNOT_REACH_THRESHOLD"
UNTRACED = "UNTRACED"

# ----------------------------------------------------------- admission level
# universe.admit() refused the case before scoring was ever attempted.

EXCLUDED_SECTOR = "EXCLUDED_SECTOR"
UNKNOWN_SECTOR = "UNKNOWN_SECTOR"
PRE_MANDATE_HISTORY = "PRE_MANDATE_HISTORY"
BREAK_BEFORE_SCOREABLE = "BREAK_BEFORE_SCOREABLE"
BREAK_OUTSIDE_REGIMES = "BREAK_OUTSIDE_REGIMES"

# ---------------------------------------------------------- diagnostic level
# The filer scored, but this one diagnostic did not evaluate.

INPUT_METRIC_ABSENT = "INPUT_METRIC_ABSENT"
INPUT_COVERAGE_LOW = "INPUT_COVERAGE_LOW"
TTM_NONCONTIGUOUS = "TTM_NONCONTIGUOUS"
PERIOD_MISALIGNED = "PERIOD_MISALIGNED"
BASELINE_TOO_THIN = "BASELINE_TOO_THIN"
NO_YEAR_AGO_QUARTER = "NO_YEAR_AGO_QUARTER"
FISCAL_SPAN_MISMATCH = "FISCAL_SPAN_MISMATCH"
CORPORATE_ACTION = "CORPORATE_ACTION"
NONPOSITIVE_DENOMINATOR = "NONPOSITIVE_DENOMINATOR"
NO_PEER_SET = "NO_PEER_SET"
UNEXPLAINED = "UNEXPLAINED"

FILER_LEVEL: tuple[str, ...] = (
    NO_XBRL_FACTS, NO_REVENUE_AT_CUTOFF, REQUIRED_COVERAGE_LOW, SHORT_HISTORY,
    CANNOT_REACH_THRESHOLD, UNTRACED,
)
ADMISSION_LEVEL: tuple[str, ...] = (
    EXCLUDED_SECTOR, UNKNOWN_SECTOR, PRE_MANDATE_HISTORY,
    BREAK_BEFORE_SCOREABLE, BREAK_OUTSIDE_REGIMES,
)
DIAGNOSTIC_LEVEL: tuple[str, ...] = (
    INPUT_METRIC_ABSENT, INPUT_COVERAGE_LOW, TTM_NONCONTIGUOUS,
    PERIOD_MISALIGNED, BASELINE_TOO_THIN, NO_YEAR_AGO_QUARTER,
    FISCAL_SPAN_MISMATCH, CORPORATE_ACTION, NONPOSITIVE_DENOMINATOR,
    NO_PEER_SET, UNEXPLAINED,
)
ALL: tuple[str, ...] = FILER_LEVEL + ADMISSION_LEVEL + DIAGNOSTIC_LEVEL

# One human sentence per code, in the register docs/VOICE.md requires: what
# happened and whether it is the company's filing pattern or the tool refusing.
TEXT: dict[str, str] = {
    NO_XBRL_FACTS: "This company has no machine-readable filings with the SEC.",
    NO_REVENUE_AT_CUTOFF: "No sales figures had been filed by this date.",
    REQUIRED_COVERAGE_LOW: (
        "Sales, cash from operations or profit is missing from too many "
        "quarters to assess this company at all."
    ),
    SHORT_HISTORY: (
        "Too few quarters of this company's own history to know what is "
        "normal for it."
    ),
    CANNOT_REACH_THRESHOLD: (
        "The measures computable for this company carry too little weight to "
        "ever reach the flag threshold, so scoring it would be theatre."
    ),
    UNTRACED: (
        "A flagged measure could not be traced back to the SEC filings it "
        "came from, so the reading is refused rather than shown unsourced."
    ),
    EXCLUDED_SECTOR: (
        "Banks, insurers and property trusts are excluded: every measure here "
        "assumes an operating company."
    ),
    UNKNOWN_SECTOR: "The SEC's record does not say what industry this company is in.",
    PRE_MANDATE_HISTORY: (
        "Not enough machine-readable history: the SEC only mandated the "
        "format from 2011."
    ),
    BREAK_BEFORE_SCOREABLE: (
        "The company's bad turn came before it had enough history to be "
        "assessed, so it cannot be a fair test case."
    ),
    BREAK_OUTSIDE_REGIMES: "The company's bad turn falls outside the tested market eras.",
    INPUT_METRIC_ABSENT: "A figure this measure needs is not in the company's filings.",
    INPUT_COVERAGE_LOW: (
        "A figure this measure needs is missing from too many quarters to "
        "trust."
    ),
    TTM_NONCONTIGUOUS: (
        "A trailing-twelve-month total needs four consecutive quarters, and "
        "this company's series has a gap there."
    ),
    PERIOD_MISALIGNED: (
        "A balance-sheet figure and the flow it would be divided by describe "
        "different moments in time."
    ),
    BASELINE_TOO_THIN: (
        "Too few past readings of this measure to know what is normal for "
        "this company."
    ),
    NO_YEAR_AGO_QUARTER: "No quarter from roughly a year earlier to compare against.",
    FISCAL_SPAN_MISMATCH: (
        "The quarter and its year-ago comparison cover different numbers of "
        "weeks (a 53-week fiscal year), so comparing them would manufacture a "
        "change that is calendar, not business."
    ),
    CORPORATE_ACTION: (
        "The share count moved so much that this is a split, listing or "
        "buyout -- a corporate action, not gradual dilution."
    ),
    NONPOSITIVE_DENOMINATOR: (
        "The figure this measure would divide by is zero or negative, so the "
        "ratio has no meaning."
    ),
    NO_PEER_SET: "Too few comparable companies in the same industry to form a peer group.",
    UNEXPLAINED: (
        "The measure could not be computed and the pipeline did not record "
        "why -- a gap in the taxonomy itself, counted so it gets fixed."
    ),
}


@dataclass(frozen=True)
class Abstention:
    """One refusal: the countable code, the sentence a reader is owed, and the
    metric or diagnostic it is about."""

    code: str
    detail: str
    subject: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "detail": self.detail, "subject": self.subject}


def is_valid(code: str) -> bool:
    return code in TEXT
