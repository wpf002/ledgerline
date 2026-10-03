"""Measure the footnote critique (ROADMAP_V1 1.0-c). Writes JSON to stdout.

New Constructs argues material unusual items sit in narrative footnotes that a
structured-data pipeline never reads. Three questions, tuning split only (the
holdout is spent and this is hypothesis-generating):

  1. How often do filers tag unusual items as standalone XBRL facts that
     companyfacts carries?
  2. When tagged, how big are they against net income?
  3. Do material tagged unusual items show up more often before a
     deterioration than in companies that stayed fine?

Uses each fact's latest vintage, so it measures what is reachable in tagged
data, which is an upper bound on what a point-in-time gate could have used.
It can't measure unusual items that are only in footnote text: those are, by
construction, invisible to anything that reads tagged facts.
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ledgerline import derive, edgar  # noqa: E402
from ledgerline.reliability import wilson  # noqa: E402
from ledgerline.validate import harness  # noqa: E402

# Overlapping concepts are grouped so one item isn't counted twice: within a
# family the largest magnitude wins, and families are summed.
FAMILIES: dict[str, list[str]] = {
    "impairment": ["AssetImpairmentCharges", "GoodwillImpairmentLoss",
                   "ImpairmentOfIntangibleAssetsExcludingGoodwill",
                   "ImpairmentOfLongLivedAssetsHeldForUse",
                   "RestructuringCostsAndAssetImpairmentCharges"],
    "restructuring": ["RestructuringCharges", "RestructuringCosts"],
    "disposals": ["GainLossOnSaleOfBusiness", "GainLossOnDispositionOfAssets",
                  "GainLossOnSaleOfPropertyPlantEquipment"],
    "debt_extinguishment": ["GainsLossesOnExtinguishmentOfDebt"],
    "discontinued_ops": ["IncomeLossFromDiscontinuedOperationsNetOfTax",
                         "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"],
    "litigation": ["LitigationSettlementExpense", "LossContingencyLossInPeriod"],
    "tax_law_change": ["IncomeTaxReconciliationChangeInEnactedTaxRate"],
}
MATERIAL_SHARE_OF_NI = 0.10


def annual(gaap: dict, concept: str) -> dict[str, float]:
    """FY-duration values from 10-Ks, keyed by period end, latest filed wins."""
    best: dict[str, tuple[str, float]] = {}
    units = gaap.get(concept, {}).get("units", {})
    for f in units.get("USD", []):
        if f.get("form") not in ("10-K", "10-K/A") or not f.get("start"):
            continue
        if derive.classify(f["start"], f["end"]) != "FY":
            continue
        prev = best.get(f["end"])
        if prev is None or f["filed"] > prev[0]:
            best[f["end"]] = (f["filed"], float(f["val"]))
    return {end: v for end, (_, v) in best.items()}


def company_years(cik: str) -> list[dict]:
    path = os.path.join(edgar.CACHE, "facts", f"CIK{edgar.pad(cik)}.json")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        gaap = json.load(fh).get("facts", {}).get("us-gaap", {})
    ni = annual(gaap, "NetIncomeLoss") or annual(gaap, "ProfitLoss")
    fam_vals = {fam: [annual(gaap, c) for c in cs] for fam, cs in FAMILIES.items()}
    out = []
    for end, ni_v in ni.items():
        tagged, total = {}, 0.0
        for fam, series in fam_vals.items():
            vals = [abs(s[end]) for s in series if end in s]
            if vals:
                tagged[fam] = max(vals)
                total += max(vals)
        out.append({"end": end, "ni": ni_v, "unusual": total, "families": tagged})
    return out


def material(row: dict) -> bool:
    return abs(row["ni"]) > 0 and row["unusual"] >= MATERIAL_SHARE_OF_NI * abs(row["ni"])


def main() -> None:
    with open(harness.SPLIT_PATH) as fh:
        split = json.load(fh)
    cases = [split["cases"][t] for t in split["tuning"]]
    rng = random.Random(20261003)

    all_years: list[dict] = []
    tagged_any = 0
    fam_count: dict[str, int] = {f: 0 for f in FAMILIES}
    pos_k = pos_n = ctrl_k = ctrl_n = 0
    for c in cases:
        years = company_years(c["cik"])
        if not years:
            continue
        all_years += years
        for y in years:
            if y["families"]:
                tagged_any += 1
            for f in y["families"]:
                fam_count[f] += 1
        if c["is_positive"] and c.get("broke_filed"):
            before = [y for y in years if y["end"] < c["broke_filed"]]
            if before:
                last = max(before, key=lambda y: y["end"])
                pos_n += 1
                pos_k += material(last)
        elif not c["is_positive"]:
            pool = [y for y in years if "2014" <= y["end"][:4] <= "2024"]
            if pool:
                pick = rng.choice(pool)
                ctrl_n += 1
                ctrl_k += material(pick)

    n = len(all_years)
    nonzero = [y for y in all_years if abs(y["ni"]) > 0 and y["unusual"] > 0]
    shares = sorted(y["unusual"] / abs(y["ni"]) for y in nonzero)

    def q(p: float) -> float | None:
        return shares[int(p * (len(shares) - 1))] if shares else None

    print(json.dumps({
        "companies": len(cases),
        "company_years": n,
        "years_with_any_tagged_unusual_item": tagged_any,
        "share_with_any_tagged": tagged_any / n if n else None,
        "family_share": {f: v / n for f, v in fam_count.items()} if n else {},
        "material_years": sum(material(y) for y in all_years),
        "material_share": sum(material(y) for y in all_years) / n if n else None,
        "unusual_over_ni_quantiles": {"p25": q(.25), "p50": q(.5), "p75": q(.75),
                                      "p90": q(.9)},
        "before_break": {"k": pos_k, "n": pos_n, "rate": pos_k / pos_n if pos_n else None,
                         "wilson": wilson(pos_k, pos_n)},
        "controls_random_year": {"k": ctrl_k, "n": ctrl_n,
                                 "rate": ctrl_k / ctrl_n if ctrl_n else None,
                                 "wilson": wilson(ctrl_k, ctrl_n)},
    }, indent=1))


if __name__ == "__main__":
    main()
