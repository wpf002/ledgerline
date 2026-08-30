"""
Plain-language rendering of everything the CLI shows a person.

The rule this module enforces (see docs/VOICE.md): every line about a company
is a sentence a non-accountant can act on. Plain words first, the technical
term in parentheses once. No snake_case, no SHOUTING_CONSTANTS, no bare
percentages without the bar they are judged against. Machine keys stay in
--json output; the terminal gets English.

Why it exists: the flagship command used to dump ~100 lines of raw JSON with
fifty unlabelled identifiers, `coverage` shouted EXCLUDED at companies the gate
assessed perfectly well, and `score` printed 0.0 next to scoreable=false -- a
reader scanning for the number saw "clean" when the truth was "could not
assess". Nothing on any surface said the detector had failed its own test.

Nothing here computes. Every figure is read from the Verdict dict that
signals_v3.evaluate() already produces.
"""
from __future__ import annotations

# One sentence, same wording everywhere. Any output that flags a company -- or
# whose silence could be read as a clean bill of health -- carries it.
CAVEAT = (
    "This detector missed its own test on 2026-08-30: it caught 29% of the "
    "deteriorations it was built to find, against a target of 60%. Not being "
    "flagged is not a clean bill of health."
)

# diagnostic -> (short name for tables, plain description of the BAD direction)
PLAIN: dict[str, tuple[str, str]] = {
    "cash_conversion_gap":     ("cash-vs-sales",
                                "Sales growing faster than the cash coming in"),
    "accrual_ratio":           ("paper-vs-cash profit",
                                "Profit on paper outrunning actual cash"),
    "receivables_vs_revenue":  ("unpaid-bills",
                                "Customers' unpaid bills growing faster than sales"),
    "inventory_vs_revenue":    ("stockpile",
                                "Stock building up faster than sales"),
    "dso":                     ("collection-days",
                                "Taking longer to collect what customers owe"),
    "dio":                     ("shelf-days",
                                "Stock sitting longer before it sells"),
    "deferred_vs_revenue_gap": ("prepaid-orders",
                                "Customers prepaying less, relative to sales"),
    "revenue_accel":           ("growth-brake",
                                "Sales growth slowing hard against its own trend"),
    "gross_margin":            ("product-margin",
                                "Less profit left per dollar of sales"),
    "op_margin":               ("operating-margin",
                                "Less profit left after running costs"),
    "ocf_to_revenue":          ("cash-per-sale",
                                "Less actual cash generated per dollar of sales"),
    "net_debt_to_ttm_ocf":     ("debt-vs-cash",
                                "Debt heavy relative to the cash the business makes"),
    "dilution_yoy":            ("share-creep",
                                "Share count creeping up, spreading profit thinner"),
}

# metric -> what to call the missing data in a "cannot assess" reason
METRIC_PLAIN: dict[str, str] = {
    "revenue": "sales",
    "operating_cash_flow": "cash from operations",
    "net_income": "profit",
    "cost_of_revenue": "cost of sales",
    "gross_profit": "gross profit",
    "operating_income": "operating profit",
    "diluted_shares": "share count",
}


def plain_metric(m: str) -> str:
    return METRIC_PLAIN.get(m, m.replace("_", " "))


def plain_reason(reason: str | None) -> str:
    """Rewrite the machine `reason` strings evaluate() produces into sentences.

    The machine strings stay as they are (tests pin them, --json carries them);
    this is the reading a person gets.
    """
    if not reason:
        return "No reason recorded."
    if reason.startswith("no XBRL facts"):
        return ("This company has no machine-readable filings with the SEC at all. "
                "Nothing is wrong with your setup.")
    if reason.startswith("no revenue facts filed as of cutoff"):
        return ("As of this date the company had not yet published any sales figures "
                "with the SEC -- likely not a public filer yet. Try a later date.")
    if reason.startswith("insufficient own-history"):
        # "insufficient own-history (6q of 12)"
        have = reason.split("(")[-1].split("q")[0]
        return (f"Only {have} quarters of its own filing history by this date; 12 are "
                "needed before the tool knows what is normal for this company.")
    if reason.startswith("cannot reach the flag threshold"):
        return ("Too few of the thirteen measures can be computed for this "
                "company: even if every one of them broke from its pattern at "
                "once, the score could not reach the flag threshold. Scoring "
                "it would look like a clean bill of health and mean nothing.")
    if reason.startswith("insufficient quarterly coverage"):
        detail = reason.split(":", 1)[-1].strip()
        parts = []
        for piece in detail.split(","):
            piece = piece.strip()
            for m, plain in METRIC_PLAIN.items():
                if piece.startswith(m):
                    pct = piece[len(m):].strip()
                    parts.append(f"{plain} reported in only {pct} of quarters")
                    break
            else:
                parts.append(piece)
        return ("Cannot assess: " + "; ".join(parts) +
                " -- 90% is needed. This is a gap in what the company filed, "
                "not something you can re-fetch.")
    return reason


def _sigma_sentence(flag: dict) -> str:
    z = flag["z"]
    base = (f"That is {z:.1f} times this company's own usual quarter-to-quarter "
            f"wobble, measured over its last {flag['baseline_n']} readings.")
    if flag.get("floored"):
        base += (" (This company's figure barely moves, so a minimum wobble was "
                 "used instead of its own -- read the multiple as a ceiling, "
                 "not a measurement.)")
    return base


def explain(res: dict, name: str | None = None) -> str:
    """One company, in plain words. Input is signals_v3.evaluate() output."""
    lines: list[str] = []
    title = res["ticker"] + (f"  {name}" if name else "")
    lines.append(title)

    if not res.get("scoreable"):
        lines.append("")
        lines.append("CANNOT ASSESS.  " + plain_reason(res.get("reason")))
        lines.append("")
        lines.append("(No score is shown because there is no score. A 0 here would "
                     "look like a clean bill of health and mean the opposite.)")
        return "\n".join(lines)

    lines.append(f"Quarter ending {res.get('period')}, using only figures filed "
                 f"by {res.get('as_of')}.")
    lines.append("")

    flags = res.get("flags", [])
    score = res.get("score")
    if res.get("gated_in"):
        lines.append(f"FLAGGED.  Concern score {score:g} of 100 (a company is "
                     f"flagged at 45 with at least 2 measures out of line). "
                     f"{len(flags)} measure{'s' if len(flags) != 1 else ''} broke "
                     "from this company's own pattern:")
    else:
        lines.append(f"NOT FLAGGED.  Concern score {score:g} of 100 (a company is "
                     "flagged at 45 with at least 2 measures out of line).")
    lines.append("")

    for f in flags:
        key = f["code"].lower()
        short, desc = PLAIN.get(key, (key, f.get("label", key)))
        lines.append(f"  {short}: {desc}.")
        lines.append(f"    {_sigma_sentence(f)}")
        lines.append(f"    (technical: {key} {f['value']:.3f} vs its median "
                     f"{f['baseline_median']:.3f}, spread {f['baseline_scale']:.3f}, "
                     f"z {f['z']:.2f})")
        lines.append("")

    computed = res.get("z", {})
    missing = [k for k in PLAIN if k not in computed]
    lines.append(f"{len(computed)} of {len(PLAIN)} measures were computed."
                 + ("" if not missing else
                    " Not computable here: "
                    + ", ".join(PLAIN[m][0] for m in missing) + "."))

    df = res.get("derived_fraction")
    if df:
        lines.append(f"{df:.0%} of the quarterly figures behind this reading were "
                     "worked out by subtracting one year-to-date report from "
                     "another, rather than read directly from a filing.")
    lines.append("")
    lines.append(CAVEAT)
    return "\n".join(lines)


def check_line(ticker: str, ok: bool, reason: str | None,
               soft_gaps: list[str]) -> str:
    """One line of `ledgerline check`.

    READY / CANNOT ASSESS is decided by the same rule the gate uses
    (signals_v3.REQUIRED_COVERAGE), not by every metric under 90% -- the old
    `coverage` command shouted EXCLUDED at companies `score` assessed happily,
    and skipped metrics with zero data entirely, so a company with no usable
    figures could print as fine.
    """
    if not ok:
        return f"  {ticker:6} CANNOT ASSESS -- {plain_reason(reason)}"
    if soft_gaps:
        gaps = ", ".join(plain_metric(m) for m in soft_gaps)
        return (f"  {ticker:6} READY -- but some measures will be unavailable "
                f"({gaps} below 90% of quarters)")
    return f"  {ticker:6} READY"


VERDICT_ROWS: dict[str, tuple[str, str]] = {
    # check key -> (plain label, how to speak the limit)
    "false_positive_rate_per_quarter": ("false alarms per company-quarter", "at most"),
    "median_lead_months": ("months of warning (typical)", "at least"),
    "positive_hit_rate": ("bad turns caught in time", "at least"),
    "regime_coverage": ("market eras it worked in", "at least"),
    "sample_size": ("cases measured", "at least"),
    "beats_naive_baseline": ("better than the obvious two-line check", "below"),
}


def verdict_text(v: dict) -> str:
    """The run-test result, with every number carrying its direction and bar."""
    lines = []
    for key, c in v["checks"].items():
        label, direction = VERDICT_ROWS.get(key, (key.replace("_", " "), ""))
        mark = "PASS" if c["pass"] else "FAIL"
        val, lim = c["value"], c["limit"]
        if key in ("false_positive_rate_per_quarter", "positive_hit_rate") \
                and isinstance(val, (int, float)):
            val = f"{val:.1%}"
        if key in ("false_positive_rate_per_quarter", "positive_hit_rate") \
                and isinstance(lim, (int, float)):
            lim = f"{lim:.0%}"
        lines.append(f"  {mark}  {label}: {val}  ({direction} {lim})")

    lines.append("")
    pf = v.get("false_positive_rate_per_filer")
    if pf is not None:
        lines.append(f"  Also reported, not part of the pass mark: {pf:.0%} of the "
                     "companies that were fine got flagged at least once.")
    nc = v.get("n_censored_positives")
    if nc:
        lines.append(f"  {nc} companies fired on the very first date they could be "
                     "assessed, so the true first warning is off the front of the "
                     "record; they are excluded from both scores.")
    lines.append("")
    if v["verdict"] == "KILL":
        lines.append("MISSED THE BAR.")
        lines.append("  The pre-registered answer is no: do not retune against this "
                     "test set. A new idea needs a new sealed test.")
    else:
        lines.append("PASSED every pre-registered criterion.")
    return "\n".join(lines)
