# ruff: noqa
# Research scratch for the h1 search (docs/research/h1-search.md). Practice half only.
"""Practice half only. The market value each company reports in its annual
report (public float), as known at each cutoff."""
import json, os, sys
from datetime import date
sys.path.insert(0, os.getcwd())
from ledgerline import edgar, status
OUT = sys.argv[1]; WHO = sys.argv[2] if len(sys.argv) > 2 else "tuning"
split = json.load(open(os.path.join(status.DATA, "split.json")))
conn = edgar.db(); cut = {}
for t, a in conn.execute("select distinct ticker, as_of from signals where source='replay'"):
    cut.setdefault(t, []).append(a)
conn.close()
rows = {}
for ticker in sorted(split[WHO]):
    cik = split["cases"][ticker]["cik"]
    try: dei = edgar.companyfacts(cik).get("facts", {}).get("dei", {})
    except Exception: continue
    facts = sorted((f["filed"], f["end"], float(f["val"])) for f in dei.get("EntityPublicFloat", {}).get("units", {}).get("USD", [])
                   if f.get("filed") and f.get("end") and f.get("val"))
    for a in sorted(cut.get(ticker, [])):
        known = {}
        for filed, end, val in facts:
            if filed <= a: known[end] = val
        if not known: continue
        ends = sorted(known)
        e = ends[-1]; cur = known[e]
        f = {"float": cur, "float_age": round((date.fromisoformat(a) - date.fromisoformat(e)).days / 30.44, 1), "float_end": e}
        prev = [x for x in ends[:-1] if 300 <= (date.fromisoformat(e) - date.fromisoformat(x)).days <= 430]
        if prev and known[prev[-1]] > 0: f["float_yoy"] = round(max(-0.95, min(4.0, cur / known[prev[-1]] - 1)), 4)
        prev2 = [x for x in ends[:-1] if 660 <= (date.fromisoformat(e) - date.fromisoformat(x)).days <= 800]
        if prev2 and known[prev2[-1]] > 0: f["float_2y"] = round(max(-0.95, min(6.0, cur / known[prev2[-1]] - 1)), 4)
        rows[f"{ticker}|{a}"] = f
json.dump(rows, open(OUT, "w")); print("rows", len(rows), "with yoy", sum(1 for r in rows.values() if "float_yoy" in r))
