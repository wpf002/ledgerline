# ruff: noqa
# Research scratch for the h1 search (docs/research/h1-search.md). Practice half only.
"""Practice half only. One row per (company, quarterly cutoff): the full set
of diagnostics on the filings public at that date, the label's continuous
precursors, filing lag, and size."""
import json, os, sys, time
from dataclasses import asdict
from datetime import date
from statistics import mean, pstdev
sys.path.insert(0, os.getcwd())
from ledgerline import derive, edgar, label, signals, status

OUT = sys.argv[1]
WHO = sys.argv[2] if len(sys.argv) > 2 else "tuning"
split = json.load(open(os.path.join(status.DATA, "split.json")))
conn = edgar.db()
cut = {}
for t, a, s, n, ok in conn.execute(
        "select ticker, as_of, score, n_flags, scoreable from signals where source='replay'"):
    cut.setdefault(t, []).append((a, s, n, ok))
conn.close()
NUM = ("revenue_yoy", "revenue_yoy_prior", "revenue_accel", "gross_margin", "gross_margin_delta_yoy",
       "op_margin", "ocf_yoy", "accrual_ratio", "cash_conversion_gap", "ocf_to_revenue", "dso",
       "dso_delta_yoy", "dio", "dio_delta_yoy", "receivables_vs_revenue", "inventory_vs_revenue",
       "deferred_rev_yoy", "deferred_vs_revenue_gap", "net_debt_to_ttm_ocf", "dilution_yoy")

def latest(snap, metric, kind="Q"):
    rows = signals.series(snap, metric, kind)
    return rows[-1] if rows else None

def one(snap, period):
    out = {}
    rev = signals.series(snap, "revenue", "Q")
    idx = len(rev) - 1
    yo = [signals.yoy_at(rev, i) for i in range(max(0, idx - 12), idx + 1)]
    yo = [y for y in yo if y is not None]
    if idx >= 8 and len(yo) >= 4:
        cur = yo[-1]; prior = yo[-5:-1]
        if len(prior) >= 3: out["rev_drop"] = round(mean(prior) - cur, 4)
        out["rev_vol"] = round(pstdev(yo), 4) if len(yo) >= 6 else None
    if idx >= 1 and rev[-2]["value"]:
        out["rev_qoq"] = round(rev[-1]["value"] / rev[-2]["value"] - 1, 4)
    if idx >= 5 and rev[-5]["value"] and rev[-6]["value"] if idx >= 5 else False:
        out["rev_qoq_ly"] = round(rev[-5]["value"] / rev[-6]["value"] - 1, 4)
    out["rev"] = rev[-1]["value"]
    # filing lag of the latest quarter, and the company's own usual lag
    lags = []
    for r in rev[-9:]:
        f = r.get("filed")
        if f:
            lags.append((date.fromisoformat(f) - date.fromisoformat(r["end"])).days)
    if lags:
        out["lag"] = lags[-1]
        if len(lags) >= 5: out["lag_norm"] = round(mean(lags[:-1]), 1)
    d = signals.diagnose("", "", label._snapshot_at(snap, period))
    if d.period == period:
        dd = asdict(d)
        for k in NUM:
            v = dd.get(k)
            if v is not None: out[k] = round(v, 5)
    orow = signals.series(snap, "operating_cash_flow", "Q")
    if orow and orow[-1]["end"] == period and len(orow) >= 9:
        cur = derive.ttm(orow); prior = derive.ttm(orow, back=4)
        if cur is not None and prior is not None and prior > 0:
            out["ocf_decline"] = round((prior - cur) / prior, 4)
        if cur is not None: out["ocf_ttm_neg"] = int(cur < 0)
    for m, k in (("operating_income", "opi"), ("net_income", "ni"), ("capex", "capex")):
        r = latest(snap, m)
        if r and r["end"] == period and out.get("rev"): out[k + "_m"] = round(r["value"] / out["rev"], 4)
    ta = latest(snap, "total_assets", "PIT"); td = latest(snap, "total_debt", "PIT"); ca = latest(snap, "cash", "PIT")
    if ta and ta["value"]:
        out["assets"] = ta["value"]
        if td: out["debt_a"] = round(td["value"] / ta["value"], 4)
        if ca: out["cash_a"] = round(ca["value"] / ta["value"], 4)
    return out

rows = []; t0 = time.time()
tickers = sorted(split[WHO])
for k, ticker in enumerate(tickers):
    case = split["cases"][ticker]
    try:
        norm = edgar.normalize(case["cik"])
    except Exception:
        continue
    if not norm: continue
    for as_of, score, n_flags, ok in sorted(cut.get(ticker, [])):
        snap = edgar.as_of(norm, as_of)
        rev = signals.series(snap, "revenue", "Q")
        if len(rev) < 9: continue
        period = rev[-1]["end"]
        crit = []
        for fn in label.CRITERIA:
            try: c = fn(snap, period)
            except Exception: c = None
            if c is not None: crit.append(c.code)
        try: extra = one(snap, period)
        except Exception as e: extra = {"err": str(e)[:60]}
        rows.append({"t": ticker, "a": as_of, "p": period, "crit": crit,
                     "score": score, "nf": n_flags, "ok": ok, **extra})
    if k % 60 == 0: print(k, len(rows), round(time.time() - t0), flush=True)
json.dump({"rows": rows}, open(OUT, "w"))
print("done", len(rows), round(time.time() - t0), sum(1 for r in rows if "err" in r))
