# ruff: noqa
# Research scratch for the h1 search (docs/research/h1-search.md). Practice half only.
"""Practice half only. Signals the filings carry that the detector never
reads, as they stood at each cutoff: buybacks, dividends, overhead, interest,
restructuring, credit-line draws, payables, backlog, short-term debt."""
import json, os, sys, time
from datetime import date
sys.path.insert(0, os.getcwd())
from ledgerline import edgar, status

OUT = sys.argv[1]; WHO = sys.argv[2] if len(sys.argv) > 2 else "tuning"
split = json.load(open(os.path.join(status.DATA, "split.json")))
conn = edgar.db(); cut = {}
for t, a in conn.execute("select distinct ticker, as_of from signals where source='replay'"):
    cut.setdefault(t, []).append(a)
conn.close()
FLOW = {
 "rev": edgar.METRIC_MAP["revenue"], "cogs": edgar.METRIC_MAP["cost_of_revenue"],
 "opi": ["OperatingIncomeLoss"],
 "buyback": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"],
 "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "PaymentsOfOrdinaryDividends"],
 "sga": ["SellingGeneralAndAdministrativeExpense"], "rd": ["ResearchAndDevelopmentExpense"],
 "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
 "restructuring": ["RestructuringCharges", "RestructuringAndRelatedCostIncurredCost"],
 "gw_impair": ["GoodwillImpairmentLoss"],
 "asset_impair": ["AssetImpairmentCharges", "ImpairmentOfLongLivedAssetsHeldForUse"],
 "revolver": ["ProceedsFromLinesOfCredit", "ProceedsFromLongTermLinesOfCredit"],
 "debt_issue": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt"],
 "sbc": ["ShareBasedCompensation"], "inv_wd": ["InventoryWriteDown"],
}
STOCK = {
 "payables": ["AccountsPayableCurrent"], "rpo": ["RevenueRemainingPerformanceObligation"],
 "debt_cur": ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
 "cur_assets": ["AssetsCurrent"], "cur_liab": ["LiabilitiesCurrent"],
 "equity": ["StockholdersEquity"], "retained": ["RetainedEarningsAccumulatedDeficit"],
 "allowance": ["AllowanceForDoubtfulAccountsReceivableCurrent"],
}
def d(s): return date.fromisoformat(s)
def gather(gaap, aliases):
    out = []
    for c in aliases:
        for f in (gaap.get(c, {}).get("units", {}).get("USD") or []):
            if f.get("filed") and f.get("end") and f.get("val") is not None:
                out.append((f["filed"], f.get("start") or "", f["end"], float(f["val"])))
    out.sort()
    return out
def near(table, start, end, tol=12):
    """The value for the period closest to (start, end), within tolerance."""
    best = None
    for (s, e), v in table.items():
        if abs((d(e) - end).days) <= tol and (start is None or (s and abs((d(s) - start).days) <= tol)):
            return v
    return best
def shift(dt): 
    try: return dt.replace(year=dt.year - 1)
    except ValueError: return dt.replace(year=dt.year - 1, day=28)

rows = {}; t0 = time.time()
for k, ticker in enumerate(sorted(split[WHO])):
    cik = split["cases"][ticker]["cik"]
    try: gaap = edgar.companyfacts(cik).get("facts", {}).get("us-gaap", {})
    except Exception: continue
    flows = {n: gather(gaap, a) for n, a in FLOW.items()}; stocks = {n: gather(gaap, a) for n, a in STOCK.items()}
    ptr = {n: 0 for n in list(FLOW) + list(STOCK)}
    ftab = {n: {} for n in FLOW}; stab = {n: {} for n in STOCK}
    for a in sorted(cut.get(ticker, [])):
        for n, facts in flows.items():
            i = ptr[n]
            while i < len(facts) and facts[i][0] <= a:
                _, s, e, v = facts[i]
                if s: ftab[n][(s, e)] = v
                i += 1
            ptr[n] = i
        for n, facts in stocks.items():
            i = ptr[n]
            while i < len(facts) and facts[i][0] <= a:
                _, s, e, v = facts[i]
                if not s: stab[n][(None, e)] = v
                i += 1
            ptr[n] = i
        # anchor: the latest revenue period on file, longest duration (year to date)
        rv = [(e, (d(e) - d(s)).days, s) for (s, e) in ftab["rev"] if (d(e) - d(s)).days <= 380]
        if not rv: continue
        e_max = max(x[0] for x in rv)
        if (d(a) - d(e_max)).days > 200: continue
        dur, s0 = max((x[1], x[2]) for x in rv if x[0] == e_max)
        S, E = d(s0), d(e_max); PS, PE = shift(S), shift(E)
        cur = {n: near(ftab[n], S, E) for n in FLOW}; pri = {n: near(ftab[n], PS, PE) for n in FLOW}
        scur = {n: near(stab[n], None, E) for n in STOCK}; spri = {n: near(stab[n], None, PE) for n in STOCK}
        f = {"dur": dur}
        R, RP = cur["rev"], pri["rev"]
        def ratio(n): return (cur[n] / R if cur[n] is not None and R else None, pri[n] / RP if pri[n] is not None and RP else None)
        for n in ("sga", "rd", "interest", "sbc", "revolver", "debt_issue", "restructuring", "buyback", "dividends"):
            c_, p_ = ratio(n)
            if c_ is not None: f[n + "_r"] = round(c_, 5)
            if c_ is not None and p_ is not None: f[n + "_d"] = round(c_ - p_, 5)
        for n in ("buyback", "dividends"):
            if pri[n] and pri[n] > 0:
                c_ = cur[n] or 0.0
                f[n + "_yoy"] = round(max(-1.0, min(3.0, c_ / pri[n] - 1)), 4)
        for n in ("restructuring", "gw_impair", "asset_impair", "inv_wd"):
            f[n + "_on"] = int(bool(cur[n] and cur[n] > 0)); 
            f[n + "_new"] = int(bool(cur[n] and cur[n] > 0) and not (pri[n] and pri[n] > 0))
        if cur["opi"] is not None and cur["interest"]:
            f["cover"] = round(max(-20, min(60, cur["opi"] / cur["interest"])), 3)
            if pri["opi"] is not None and pri["interest"]:
                f["cover_d"] = round(f["cover"] - max(-20, min(60, pri["opi"] / pri["interest"])), 3)
        if scur["payables"] and cur["cogs"] and dur:
            f["dpo"] = round(scur["payables"] / (cur["cogs"] / dur), 2)
            if spri["payables"] and pri["cogs"]: f["dpo_d"] = round(f["dpo"] - spri["payables"] / (pri["cogs"] / dur), 2)
        if scur["rpo"] and spri["rpo"]: f["rpo_yoy"] = round(max(-1, min(3, scur["rpo"] / spri["rpo"] - 1)), 4)
        if scur["cur_assets"] and scur["cur_liab"]:
            f["cur_ratio"] = round(min(10, scur["cur_assets"] / scur["cur_liab"]), 3)
            if spri["cur_assets"] and spri["cur_liab"]: f["cur_ratio_d"] = round(f["cur_ratio"] - min(10, spri["cur_assets"] / spri["cur_liab"]), 3)
        if scur["debt_cur"] is not None and scur["cur_liab"]: f["debt_cur_r"] = round(scur["debt_cur"] / scur["cur_liab"], 4)
        if scur["equity"] is not None: f["neg_equity"] = int(scur["equity"] < 0)
        if scur["retained"] is not None: f["deficit"] = int(scur["retained"] < 0)
        if scur["allowance"] and spri["allowance"]: f["allow_yoy"] = round(max(-1, min(3, scur["allowance"] / spri["allowance"] - 1)), 4)
        rows[f"{ticker}|{a}"] = f
    if k % 60 == 0: print(k, len(rows), round(time.time() - t0), flush=True)
json.dump(rows, open(OUT, "w"))
print("done", len(rows), round(time.time() - t0))
from collections import Counter
c = Counter(k for f in rows.values() for k in f); print({k: round(v / len(rows), 2) for k, v in sorted(c.items())})
