"""
Pinned outputs for the scoring arithmetic, checked on every CI run.

ROADMAP_V1 1.0-a. The second audit found a total_debt double-count that had
been in the code since before Phase 0, under a green suite of 370 tests. The
tests checked behaviours someone had thought to write down. Nothing checked
that the numbers coming out were the same numbers as last week.

This pins them. Ten real companies' filing histories, trimmed to the concepts
the metric layer reads, are committed under tests/fixtures/golden/. For each
company at four cutoffs, every diagnostic value, every z, the score, the flags
and the abstention reason are recorded in golden.json together with the
GATE_VERSION that produced them.

The test fails in two ways, and they mean different things:

  - the outputs moved but GATE_VERSION didn't: an arithmetic change nobody
    declared. That's the class of bug this exists to catch.
  - GATE_VERSION moved: an intended change. Regenerate with
    `python scripts/golden.py` and say in the commit what moved and why.

The companies cover the cases the audits found bugs in: a restatement that
changed the reporting basis (DLTR), restated originals (ABT), a balance sheet
out of step with its flows (WBD), coverage too thin to assess (AAP), a
weighted-average share count (GPK), amended filings (BTU), a floored scale
(ALGN), genuinely negative reported revenue (CNX), a 52/53-week fiscal year
(AAPL), and a company the gate flags on six measures (FMC).
"""
from __future__ import annotations

import gzip
import json
import os

from .. import edgar, signals_v3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "golden")
GOLDEN_PATH = os.path.join(FIXTURES, "golden.json")

COMPANIES: dict[str, str] = {
    "FMC": "0000037785", "AAPL": "0000320193", "DLTR": "0000935703",
    "ABT": "0000001800", "WBD": "0001437107", "AAP": "0001158449",
    "GPK": "0001408075", "BTU": "0001064728", "ALGN": "0001097149",
    "CNX": "0001070412",
}
CUTOFFS = ("2014-08-15", "2018-11-15", "2021-08-15", "2025-05-15")

# Six decimal places: tight enough that a real arithmetic change shows, loose
# enough that a float summed in a different order doesn't fail the build.
DIGITS = 6


def concepts() -> set[str]:
    out: set[str] = set()
    for names in edgar.METRIC_MAP.values():
        out.update(names)
    for groups in edgar.SUMMED_METRICS.values():
        for g in groups:
            out.update(g)
    return out


def fixture_path(ticker: str) -> str:
    return os.path.join(FIXTURES, f"{ticker}.json.gz")


def build_fixtures() -> list[str]:
    """Trim cached filing histories to the concepts the metric layer reads.
    Run only when adding a company; the committed files are the fixtures."""
    keep = concepts()
    os.makedirs(FIXTURES, exist_ok=True)
    written = []
    for ticker, cik in COMPANIES.items():
        with open(os.path.join(edgar.CACHE, "facts", f"CIK{edgar.pad(cik)}.json")) as fh:
            gaap = json.load(fh).get("facts", {}).get("us-gaap", {})
        trimmed = {"facts": {"us-gaap": {k: v for k, v in gaap.items() if k in keep}}}
        blob = json.dumps(trimmed, separators=(",", ":"), sort_keys=True).encode()
        # mtime=0 so regenerating identical content gives byte-identical files
        with open(fixture_path(ticker), "wb") as fh:
            fh.write(gzip.compress(blob, compresslevel=9, mtime=0))
        written.append(ticker)
    return written


def load_fixture(ticker: str) -> dict:
    with gzip.open(fixture_path(ticker), "rt") as fh:
        return json.load(fh)


def _round(v: object) -> object:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        return round(v, DIGITS)
    if isinstance(v, dict):
        return {k: _round(x) for k, x in sorted(v.items())}
    if isinstance(v, list | tuple):
        return [_round(x) for x in v]
    return v


def summarize(res: dict) -> dict:
    """The parts of a verdict that are arithmetic. Provenance, stamps and
    timestamps are left out: they change for reasons that aren't the math."""
    out = _round({
        "period": res.get("period"),
        "scoreable": res.get("scoreable"),
        "reason_code": res.get("reason_code"),
        "score": res.get("score"),
        "gated_in": res.get("gated_in"),
        "flags": sorted(f["code"] for f in res.get("flags", [])),
        "z": res.get("z") or {},
        "derived_fraction": res.get("derived_fraction"),
        "evaluated_weight": res.get("evaluated_weight"),
        "diagnostics": {k: v for k, v in (res.get("diagnostics") or {}).items()
                        if isinstance(v, int | float | type(None))
                        and not isinstance(v, bool)},
    })
    assert isinstance(out, dict)
    return out


def compute() -> list[dict]:
    out = []
    for ticker, cik in COMPANIES.items():
        facts = load_fixture(ticker)["facts"]["us-gaap"]
        norm = edgar.normalize(cik, facts=facts)
        for cutoff in CUTOFFS:
            res = signals_v3.evaluate(ticker, cik, as_of=cutoff, norm=norm)
            out.append({"ticker": ticker, "cutoff": cutoff, **summarize(res)})
    return out


def write_golden() -> dict:
    payload = {"gate_version": signals_v3.GATE_VERSION, "cases": compute()}
    with open(GOLDEN_PATH, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return payload


def load_golden() -> dict:
    with open(GOLDEN_PATH) as fh:
        return json.load(fh)


def diff(expected: list[dict], actual: list[dict]) -> list[str]:
    """Human-readable differences, one line per moved field."""
    lines = []
    by_key = {(c["ticker"], c["cutoff"]): c for c in actual}
    for e in expected:
        a = by_key.get((e["ticker"], e["cutoff"]))
        if a is None:
            lines.append(f"{e['ticker']} {e['cutoff']}: no longer computed")
            continue
        for k in sorted(set(e) | set(a)):
            if k in ("ticker", "cutoff"):
                continue
            if isinstance(e.get(k), dict) and isinstance(a.get(k), dict):
                for sub in sorted(set(e[k]) | set(a[k])):
                    if e[k].get(sub) != a[k].get(sub):
                        lines.append(f"{e['ticker']} {e['cutoff']} {k}.{sub}: "
                                     f"{e[k].get(sub)} -> {a[k].get(sub)}")
            elif e.get(k) != a.get(k):
                lines.append(f"{e['ticker']} {e['cutoff']} {k}: {e.get(k)} -> {a.get(k)}")
    return lines
