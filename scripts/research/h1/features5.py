# ruff: noqa
"""Price features at each cutoff, from closes on or before the cutoff only.
Scratch, practice half only. Writes tuning_f5.json: "T|cutoff" -> features."""
import bisect, json, math, os, sys
from statistics import pstdev
SP = os.environ.get("H1_DATA", os.path.dirname(os.path.abspath(__file__)))
rows = json.load(open(os.path.join(SP, "tuning_features.json")))["rows"]

def load(t):
    p = os.path.join(SP, "prices", f"{t}.json")
    if not os.path.exists(p): return None
    d = json.load(open(p))
    if not isinstance(d, list) or not d: return None
    d = [(x["date"][:10], x["adjClose"]) for x in d if x.get("adjClose")]
    return ([a for a, _ in d], [b for _, b in d])

spy = load("SPY")
def at(series, day, back_days=0):
    """Close on the last trading day on or before `day` minus back_days calendar days."""
    from datetime import date, timedelta
    target = (date.fromisoformat(day) - timedelta(days=back_days)).isoformat()
    i = bisect.bisect_right(series[0], target) - 1
    if i < 0: return None, None
    # stale if the last close is more than 10 days before the target (delisted / gap)
    if (date.fromisoformat(target) - date.fromisoformat(series[0][i])).days > 10: return None, None
    return i, series[1][i]

out = {}
cache = {}
for r in rows:
    t, a = r["t"], r["a"]
    if t not in cache: cache[t] = load(t)
    s = cache[t]
    if s is None: continue
    i0, p0 = at(s, a)
    if p0 is None: continue
    f = {}
    for name, days in (("r1", 30), ("r3", 91), ("r6", 182), ("r12", 365)):
        _, pb = at(s, a, days); _, m0 = at(spy, a); _, mb = at(spy, a, days)
        if pb and m0 and mb:
            f[name] = math.log(p0 / pb); f[name + "_x"] = f[name] - math.log(m0 / mb)
    j = bisect.bisect_left(s[0], s[0][max(0, i0 - 252)])
    window = s[1][j:i0 + 1]
    if len(window) > 120:
        f["dd52"] = p0 / max(window) - 1
        rets = [math.log(window[k] / window[k - 1]) for k in range(1, len(window))]
        v3 = pstdev(rets[-63:]); v12 = pstdev(rets)
        f["vol3"] = v3 * math.sqrt(252); f["vol_ratio"] = v3 / v12 if v12 else None
        f["worst_day"] = min(rets[-63:])
    out[f"{t}|{a}"] = {k: v for k, v in f.items() if v is not None}
json.dump(out, open(os.path.join(SP, "tuning_f5.json"), "w"))
print(len(out), "rows with prices of", len(rows))
