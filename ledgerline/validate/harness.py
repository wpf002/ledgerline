"""
Phase 0 validation harness.

The old backtest asked "did it fire before the break on eight names I already
knew broke." That question cannot fail informatively. This one can.

What was missing and is now here:

  1. A CONTROL GROUP. `backtest_v2.json` reported FPR 0.288 with no
     control-construction code in the repo at all. Cases are now GENERATED
     across the whole admissible universe by `build_cases()`: a filer whose
     filings show a fundamental deterioration event is a positive, one that
     never does is a control. Nothing is hand-picked, so there is no hindsight
     selection and no survivorship step to get wrong.

  2. A TUNING/HOLDOUT SPLIT, written to disk and hashed before any threshold is
     chosen. v2's Z_TRIGGER, weight table and THRESHOLD=45 were all set while
     looking at the same eight cases whose lead times were then reported.

  3. CENSORING DETECTION. WBD and LUMN "fired" at 2019-02-15, the first cutoff
     in the window, and that was reported as +42mo and +45mo of lead. The true
     first fire is before the data starts. Censored cases are excluded from the
     median lead rather than credited with it.

  4. A PRE-REGISTERED DECISION RULE with a written kill condition, committed
     before the run.

Point-in-time discipline is enforced upstream: the scorer passed in here is
`signals_v3.evaluate`, which truncates on the XBRL `filed` date.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import median
from typing import Any

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SPLIT_PATH = os.path.join(DATA, "split.json")
PREREG_PATH = os.path.join(DATA, "prereg.json")
CASES_PATH = os.path.join(DATA, "cases.json")


# ------------------------------------------------------------------- cases


@dataclass
class Case:
    ticker: str
    cik: str
    label: str
    is_positive: bool
    regime: str                 # macro regime, e.g. "2021-22-growth-unwind"
    broke: str | None = None    # YYYY-MM; required for positives
    sector: str = ""
    cap_decile: int | None = None

    @staticmethod
    def from_dict(d: dict) -> Case:
        return Case(**d)


def load_cases() -> list[Case]:
    """Case registry. Positives AND controls live in one file so the split can
    stratify across both."""
    with open(CASES_PATH) as fh:
        return [Case.from_dict(d) for d in json.load(fh)["cases"]]


def load_split(which: str) -> list[Case]:
    if which not in ("tuning", "holdout"):
        raise ValueError("split must be 'tuning' or 'holdout'")
    verify_split()
    with open(SPLIT_PATH) as fh:
        tickers = set(json.load(fh)[which])
    return [c for c in load_cases() if c.ticker in tickers]


# ------------------------------------------------------------------ outcomes


@dataclass
class Outcome:
    ticker: str
    is_positive: bool
    fired: bool
    censored: bool
    first_fire: str | None
    lead_months: int | None
    fire_rate: float
    scoreable_quarters: int
    flags: list[str] = field(default_factory=list)
    regime: str | None = None


def months_between(a: str, b: str) -> int:
    """Signed months from a to b. Positive means a precedes b -- a lead."""
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))


def evaluate_case(case: Case, cutoffs: list[str], scorer, threshold: float) -> Outcome:
    """`scorer(ticker, cik, as_of) -> dict | None`.

    One code path for backtest and production. If they diverge, the backtest
    measures something the product does not do.
    """
    scores: list[float] = []
    first, first_flags, first_scoreable = None, [], None
    for c in cutoffs:
        res = scorer(case.ticker, case.cik, as_of=c)
        if not res or not res.get("scoreable") or res.get("score") is None:
            continue
        if first_scoreable is None:
            first_scoreable = c
        scores.append(res["score"])
        if res["score"] >= threshold and first is None:
            first = c
            first_flags = [f["code"] for f in res.get("flags", [])]

    # Firing on the first cutoff at which the filer was scoreable at all means
    # the true first fire is outside the window. Report it, do not credit it.
    censored = first is not None and first == first_scoreable

    lead = None
    if first and case.broke and not censored:
        lead = months_between(first[:7], case.broke)

    return Outcome(
        ticker=case.ticker,
        is_positive=case.is_positive,
        fired=first is not None,
        censored=censored,
        first_fire=first,
        lead_months=lead,
        fire_rate=(sum(1 for s in scores if s >= threshold) / len(scores)) if scores else 0.0,
        scoreable_quarters=len(scores),
        flags=first_flags,
        regime=case.regime,
    )


# -------------------------------------------------------------------- splits


def make_split(seed: int, tuning_frac: float = 0.6) -> dict:
    """Deterministic split, stratified by regime and by positive/control, then
    written to disk and hashed. Commit the hash before running anything. If the
    hash changes, the holdout is burned."""
    cases = load_cases()
    rng = random.Random(seed)
    strata: dict[tuple[str, bool], list[Case]] = {}
    for c in cases:
        strata.setdefault((c.regime, c.is_positive), []).append(c)

    tuning, holdout = [], []
    for key in sorted(strata, key=lambda k: (k[0], k[1])):
        group = sorted(strata[key], key=lambda c: c.ticker)
        rng.shuffle(group)
        cut = round(len(group) * tuning_frac)
        tuning += group[:cut]
        holdout += group[cut:]

    payload = {
        "seed": seed,
        "created": date.today().isoformat(),
        "tuning": sorted(c.ticker for c in tuning),
        "holdout": sorted(c.ticker for c in holdout),
    }
    payload["sha256"] = _hash(payload)
    os.makedirs(DATA, exist_ok=True)
    with open(SPLIT_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def _hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def verify_split() -> str:
    """Recompute the hash. Raises if the split was edited after commit."""
    with open(SPLIT_PATH) as fh:
        payload = json.load(fh)
    if payload.get("sha256") != _hash(payload):
        raise RuntimeError("split.json modified after commit -- the holdout is burned")
    return payload["sha256"]


# ------------------------------------------- pre-registered decision rule

PREREG: dict[str, Any] = {
    "label": "fundamental_deterioration_2of5_within_4q",
    "max_false_positive_rate": 0.10,
    "min_median_lead_months": 6,
    "min_positive_hit_rate": 0.60,
    "must_beat_baseline": "ttm_ocf_negative_and_net_debt_positive",
    "min_positives": 40,
    "min_controls": 200,
    "min_regimes": 4,
    "regime_window": "2011-2025",
    "excluded_sectors": "SIC 6000-6599, 6700-6799 (financials, real estate, REITs)",
    "kill_if_any_fails": True,
    "notes": (
        "Lead is measured from the gate's fire date to the FILING date of the "
        "quarter that tripped the deterioration label, not to the period end. "
        "A quarter ending 3/31 is not public until it is filed. Price drawdown "
        "is reported but is not part of the rule."
    ),
}


def write_prereg() -> dict:
    """Call once, commit the file, then do not touch it."""
    if os.path.exists(PREREG_PATH):
        raise RuntimeError("prereg.json already exists -- rewriting it voids the test")
    os.makedirs(DATA, exist_ok=True)
    payload = {"committed": date.today().isoformat(), "rule": PREREG}
    with open(PREREG_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def verdict(outcomes: list[Outcome], baseline_fpr: float | None = None) -> dict:
    """Apply the pre-registered rule. Run on the HOLDOUT, exactly once."""
    pos = [o for o in outcomes if o.is_positive]
    neg = [o for o in outcomes if not o.is_positive]

    fpr = (sum(1 for o in neg if o.fired) / len(neg)) if neg else None
    leads = [o.lead_months for o in pos if o.lead_months is not None]
    n_led = sum(1 for o in pos if o.lead_months and o.lead_months > 0)
    hit_rate = (n_led / len(pos)) if pos else 0.0
    med_lead = median(leads) if leads else None

    checks = {
        "false_positive_rate": {
            "value": fpr,
            "limit": PREREG["max_false_positive_rate"],
            "pass": fpr is not None and fpr <= PREREG["max_false_positive_rate"],
        },
        "median_lead_months": {
            "value": med_lead,
            "limit": PREREG["min_median_lead_months"],
            "pass": med_lead is not None and med_lead >= PREREG["min_median_lead_months"],
        },
        "positive_hit_rate": {
            "value": round(hit_rate, 3),
            "limit": PREREG["min_positive_hit_rate"],
            "pass": hit_rate >= PREREG["min_positive_hit_rate"],
        },
    }
    regimes = {o.regime for o in outcomes if o.is_positive and o.regime}
    checks["regime_coverage"] = {
        "value": len(regimes),
        "limit": PREREG["min_regimes"],
        "pass": len(regimes) >= PREREG["min_regimes"],
    }
    checks["sample_size"] = {
        "value": {"positives": len(pos), "controls": len(neg)},
        "limit": {"positives": PREREG["min_positives"], "controls": PREREG["min_controls"]},
        "pass": len(pos) >= PREREG["min_positives"] and len(neg) >= PREREG["min_controls"],
    }
    if baseline_fpr is not None:
        checks["beats_naive_baseline"] = {
            "value": fpr,
            "limit": baseline_fpr,
            "pass": fpr is not None and fpr < baseline_fpr,
        }

    passed = all(c["pass"] for c in checks.values())
    return {
        "checks": checks,
        "n_positive": len(pos),
        "n_control": len(neg),
        "n_censored": sum(1 for o in outcomes if o.censored),
        "regimes": sorted(regimes),
        "verdict": "SHIP" if passed else "KILL",
        "note": (
            "All pre-registered criteria met on holdout."
            if passed
            else "At least one pre-registered criterion failed. Per prereg.json the "
                 "deterministic gate is not a viable product core. Write up the "
                 "finding; do not retune and re-run against this holdout."
        ),
    }


def outcomes_to_dicts(outcomes: list[Outcome]) -> list[dict]:
    return [asdict(o) for o in outcomes]


# ------------------------------------------------------- generated case set


def build_cases(tickers: dict[str, str], sic_lookup=None, normalizer=None) -> dict:
    """Generate the case registry across the admissible universe.

    `tickers` is {ticker: cik}. `sic_lookup(cik) -> str|None` and
    `normalizer(cik) -> dict` are injected so this is testable without network.

    A filer is a POSITIVE if its filings show a fundamental deterioration event
    (label.first_deterioration); a CONTROL if it never does. Nothing is curated.
    That is the whole point -- eight names remembered as blowups cannot produce
    a control group, cannot be split, and carry hindsight selection that no
    amount of downstream statistics repairs.

    Every rejection is recorded with its reason. A silently dropped filer is a
    survivorship bias with extra steps.
    """
    from .. import edgar as _edgar
    from .. import label as _label
    from .. import universe as _universe

    sic_lookup = sic_lookup or _universe.fetch_sic
    normalizer = normalizer or _edgar.normalize

    cases, rejected = [], []
    for ticker, cik in sorted(tickers.items()):
        norm = normalizer(cik)
        sic = sic_lookup(cik)
        broke = _label.first_deterioration(ticker, cik, norm) if norm else None

        ok, reason = _universe.admit(cik, ticker, norm, sic, broke=broke)
        if not ok:
            rejected.append({"ticker": ticker, "cik": cik, "reason": reason})
            continue

        regime = _universe.regime_for(broke) if broke else None
        cases.append(
            Case(
                ticker=ticker,
                cik=cik,
                label=f"{ticker} deterioration {broke}" if broke else f"{ticker} control",
                is_positive=bool(broke),
                broke=broke,
                regime=regime or "control",
                sector=str(sic or ""),
            )
        )

    payload = {
        "generated": date.today().isoformat(),
        "label_rule": PREREG["label"],
        "n_positive": sum(1 for c in cases if c.is_positive),
        "n_control": sum(1 for c in cases if not c.is_positive),
        "regimes": sorted({c.regime for c in cases if c.is_positive}),
        "cases": [asdict(c) for c in cases],
        "rejected": rejected,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(CASES_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def readiness(cases_payload: dict | None = None) -> dict:
    """Is the case set big and broad enough to build a split from?

    Called before make_split so a split is never built on a set that cannot
    satisfy the pre-registered rule anyway.
    """
    if cases_payload is None:
        with open(CASES_PATH) as fh:
            cases_payload = json.load(fh)
    cases = [Case.from_dict(c) for c in cases_payload["cases"]]
    pos = [c for c in cases if c.is_positive]
    neg = [c for c in cases if not c.is_positive]
    regimes = {c.regime for c in pos}
    checks = {
        "positives": {"value": len(pos), "limit": PREREG["min_positives"],
                      "pass": len(pos) >= PREREG["min_positives"]},
        "controls": {"value": len(neg), "limit": PREREG["min_controls"],
                     "pass": len(neg) >= PREREG["min_controls"]},
        "regimes": {"value": sorted(regimes), "limit": PREREG["min_regimes"],
                    "pass": len(regimes) >= PREREG["min_regimes"]},
    }
    return {"ready": all(c["pass"] for c in checks.values()), "checks": checks}
