# ruff: noqa
# Research scratch for the h1 search (docs/research/h1-search.md). Practice half only.
"""Scratch exploration, practice half only. Not part of the project."""
import json, math, os, sys
from collections import Counter, defaultdict
from datetime import date
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

SP = os.environ.get("H1_DATA", os.path.dirname(os.path.abspath(__file__))); REPO = os.getcwd()
rows = json.load(open(os.path.join(SP, "tuning_f2.json")))["rows"]
split = json.load(open(os.path.join(REPO, "ledgerline/data/split.json"))); cases = split["cases"]
zrows = {(r["ticker"], r["cutoff"]): r["z"] for r in json.load(open(os.path.join(REPO, "ledgerline/data/tuning_dataset.json")))["rows"]}
ZK = ["cash_conversion_gap", "accrual_ratio", "receivables_vs_revenue", "inventory_vs_revenue", "dso", "dio",
      "deferred_vs_revenue_gap", "revenue_accel", "gross_margin", "op_margin", "ocf_to_revenue", "net_debt_to_ttm_ocf", "dilution_yoy"]
NUM = ("revenue_yoy", "revenue_accel", "gross_margin", "gross_margin_delta_yoy", "op_margin", "ocf_yoy", "accrual_ratio",
       "cash_conversion_gap", "ocf_to_revenue", "dso", "dso_delta_yoy", "dio", "dio_delta_yoy", "receivables_vs_revenue",
       "inventory_vs_revenue", "deferred_rev_yoy", "deferred_vs_revenue_gap", "net_debt_to_ttm_ocf", "dilution_yoy",
       "rev_drop", "rev_vol", "rev_qoq", "ocf_decline", "ocf_ttm_neg", "opi_m", "ni_m", "capex_m", "debt_a", "cash_a")
D1 = ("gross_margin", "op_margin", "opi_m", "ocf_to_revenue", "rev_drop", "gross_margin_delta_yoy", "ocf_decline",
      "inventory_vs_revenue", "receivables_vs_revenue", "dso", "dio", "revenue_yoy")
D4 = ("op_margin", "opi_m", "ni_m", "ocf_to_revenue", "debt_a", "cash_a", "capex_m")
def months(a, b): return (date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44
nan = float("nan")
g = lambda r, k: r[k] if r is not None and r.get(k) is not None else nan
def soft(r, a, b, c):
    return int(g(r, "rev_drop") >= a) + int(-g(r, "gross_margin_delta_yoy") >= b) + int(g(r, "ocf_decline") >= c or g(r, "ocf_ttm_neg") == 1)

by = defaultdict(list)
for r in rows: by[r["t"]].append(r)
for rs in by.values(): rs.sort(key=lambda r: r["a"])
# stress, by cutoff: share of companies with two soft signs; and with any full sign
st = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
for r in rows:
    sec = (cases[r["t"]].get("sector") or "00")[:2]
    for key in (sec, "ALL"):
        d = st[r["a"]][key]; d[0] += 1; d[1] += soft(r, 0.08, 0.02, 0.25) >= 2; d[2] += bool(r["crit"])
cutoffs = sorted(st)
mk = {a: st[a]["ALL"][1] / st[a]["ALL"][0] for a in cutoffs}
mk_full = {a: st[a]["ALL"][2] / st[a]["ALL"][0] for a in cutoffs}
def trailing(a, src, n=8):
    i = cutoffs.index(a); prev = [src[c] for c in cutoffs[max(0, i - n):i]]
    return sum(prev) / len(prev) if prev else nan

agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
for r in rows:
    sec = (cases[r["t"]].get("sector") or "00")[:2]
    for key in (sec, "ALL"):
        for k in ("revenue_yoy", "rev_drop", "gross_margin_delta_yoy", "ocf_decline", "op_margin"):
            v = r.get(k)
            if v is not None: agg[r["a"]][key][k].append(v)
def med(a, sec, k):
    v = agg[a][sec][k]
    if len(v) < 5: v = agg[a]["ALL"][k]
    return float(np.median(v)) if v else nan
f3 = json.load(open(os.path.join(SP, "tuning_f3.json")))
XK = sorted({k for v in f3.values() for k in v if k != "dur"})
names = None; X = []; meta = []
for t, rs in by.items():
    c = cases[t]; sec = (c.get("sector") or "00")[:2]
    for i, r in enumerate(rs):
        if "err" in r: continue
        p1 = rs[i - 1] if i >= 1 else None; p4 = rs[i - 4] if i >= 4 else None
        z = zrows.get((t, r["a"]), {})
        f = {k: g(r, k) for k in NUM}
        f.update({"d1_" + k: g(r, k) - g(p1, k) for k in D1})
        f.update({"d4_" + k: g(r, k) - g(p4, k) for k in D4})
        f["qoq_sa"] = g(r, "rev_qoq") - g(r, "rev_qoq_ly")
        f["lag"] = g(r, "lag"); f["lag_x"] = g(r, "lag") - g(r, "lag_norm")
        f["log_rev"] = math.log10(r["rev"]) if r.get("rev") and r["rev"] > 0 else nan
        f["score"] = r["score"] if r["score"] is not None else nan; f["nf"] = r["nf"]
        f["ncrit"] = len(r["crit"])
        for code in ("REVENUE_DECEL", "MARGIN_COLLAPSE", "OCF_HALVED", "OCF_NEGATIVE"): f["c_" + code] = int(code in r["crit"])
        f["soft_a"] = soft(r, 0.10, 0.03, 0.35); f["soft_b"] = soft(r, 0.05, 0.015, 0.2)
        f["soft_b_p1"] = soft(p1, 0.05, 0.015, 0.2) if p1 else nan
        for k in ZK: f["z_" + k] = z.get(k) if z.get(k) is not None else nan
        f["nz2"] = sum(1 for k in ZK if (z.get(k) or 0) >= 2)
        d = st[r["a"]]
        f["mk"] = mk[r["a"]]; f["mk_x"] = mk[r["a"]] - trailing(r["a"], mk); f["mk_full"] = mk_full[r["a"]]
        f["sec"] = d[sec][1] / d[sec][0] if d[sec][0] >= 5 else mk[r["a"]]
        for k in ("revenue_yoy", "rev_drop", "gross_margin_delta_yoy", "ocf_decline", "op_margin"):
            f["sm_" + k] = med(r["a"], sec, k); f["mm_" + k] = med(r["a"], "ALL", k)
            f["rel_" + k] = g(r, k) - f["sm_" + k]
        x3 = f3.get(f"{t}|{r['a']}", {})
        for k in XK: f["x_" + k] = x3.get(k, nan)
        pa = cutoffs[cutoffs.index(r["a"]) - 1] if cutoffs.index(r["a"]) else None
        f["sm_d1_rev"] = f["sm_revenue_yoy"] - med(pa, sec, "revenue_yoy") if pa else nan
        f["mm_d1_rev"] = f["mm_revenue_yoy"] - med(pa, "ALL", "revenue_yoy") if pa else nan
        if names is None: names = list(f)
        if c["is_positive"] and c["broke_filed"]:
            m = months(r["a"], c["broke_filed"])
            kind = 1 if 0 < m <= 24 else 2 if m > 24 else -1
        else: kind = 0 if not c["is_positive"] else -1
        if kind < 0: continue
        X.append([f[k] for k in names]); meta.append((t, r["a"], kind, c.get("broke_filed"), c.get("regime")))
X = np.array(X, dtype=float); kind = np.array([m[2] for m in meta]); grp = np.array([m[0] for m in meta])
y = (kind == 1).astype(int)
print("rows", X.shape, "window rows", y.sum(), "controls", (kind == 0).sum())


mwin = np.array([months(m[1], m[3]) if m[2] == 1 else np.nan for m in meta])
tick = sorted(set(grp))
def oof_scores(cols, seed, y_train, depth=3, iters=200, lr=0.05, bag=1):
    ci = [names.index(c) for c in cols]
    rng = np.random.RandomState(seed); order = rng.permutation(len(tick)); fold_of = {tick[j]: i % 5 for i, j in enumerate(order)}
    fold = np.array([fold_of[t] for t in grp]); oof = np.zeros(len(y))
    for k in range(5):
        tr = fold != k; te = ~tr
        for b in range(bag):
            m = HistGradientBoostingClassifier(max_iter=iters, learning_rate=lr, max_depth=depth, min_samples_leaf=40,
                                               l2_regularization=1.0, class_weight="balanced", random_state=seed * 10 + b)
            keep = tr & (y_train >= 0)
            m.fit(X[keep][:, ci], y_train[keep]); oof[te] += m.predict_proba(X[te][:, ci])[:, 1] / bag
    return oof

def report(label, oof, targets=(0.02, 0.04, 0.06)):
    out = []
    for tgt in targets:
        thr = np.quantile(oof[kind == 0], 1 - tgt); f = oof >= thr
        pos = defaultdict(list)
        for (t, a, k, bf, reg), fi, mw in zip(meta, f, mwin):
            if k == 1: pos[t].append((mw, fi, reg))
        caught = [max(m for m, fi, _ in v if fi) for v in pos.values() if any(fi for _, fi, _ in v)]
        fa = f[np.isin(kind, (0, 2))].mean()
        out.append((len(caught) / len(pos), fa, float(np.median(caught)) if caught else 0.0))
        if tgt == 0.04:
            reg_t = Counter(v[0][2] for v in pos.values()); reg_h = Counter(v[0][2] for v in pos.values() if any(fi for _, fi, _ in v))
            regs = " ".join(f"{r.split('-')[-1][:6]}:{reg_h[r]}/{reg_t[r]}" for r in sorted(reg_t))
    return out, regs

def trial(label, cols, y_train=None, seeds=(1, 2, 3), **kw):
    y_train = y if y_train is None else y_train
    res = []; regs = ""
    for sd in seeds:
        o, regs = report(label, oof_scores(cols, sd, y_train, **kw)); res.append(o)
    avg = np.mean(np.array(res), axis=0)
    print(f"{label:38}" + " | ".join(f"@{t:.0%}: {c:5.1%} (all-quiet {fa:4.1%}, lead {ld:4.1f})" for t, (c, fa, ld) in zip((0.02, 0.04, 0.06), avg)), flush=True)
    print(" " * 38 + regs, flush=True)

allc = list(names)
noz = [c for c in allc if not c.startswith("z_") and c not in ("score", "nf", "nz2")]
EXTRA = [c for c in noz if c.startswith("x_")]
core = [c for c in noz if c not in EXTRA]
y12 = np.where(kind == 1, np.where(mwin <= 12, 1, -1), 0)
kw = dict(depth=3, iters=120, lr=0.04)
SEEDS = (1, 2, 3, 4, 5)
def caught_at(oof, fa_target):
    thr = np.quantile(oof[kind == 0], 1 - fa_target); f = oof >= thr
    pos = defaultdict(bool)
    for (t, a, k, bf, reg), fi in zip(meta, f):
        if k == 1: pos[t] = pos[t] or bool(fi)
    return sum(pos.values()) / len(pos)
grid = (0.005, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08)
res = np.array([[caught_at(oof_scores(core, sd, y12, **kw), g) for g in grid] for sd in SEEDS])
print("new model, caught at each false-alarm rate (mean of 5 runs, min-max):")
for g, col in zip(grid, res.T): print(f"  {g:5.1%}: {col.mean():5.1%}  ({col.min():.1%}-{col.max():.1%})")
# the two-line rule on the same rows: cash from operations negative over the year, and more debt than cash
ci = {n: names.index(n) for n in ("ocf_ttm_neg", "debt_a", "cash_a")}
base = (X[:, ci["ocf_ttm_neg"]] == 1) & (X[:, ci["debt_a"]] > X[:, ci["cash_a"]])
pos = defaultdict(bool)
for (t, a, k, bf, reg), fi in zip(meta, base):
    if k == 1: pos[t] = pos[t] or bool(fi)
print(f"two-line rule: caught {sum(pos.values())/len(pos):.1%} at false alarms {base[kind == 0].mean():.2%}")
h0 = (X[:, names.index("score")] >= 45) & (X[:, names.index("nf")] >= 2)
pos = defaultdict(bool)
for (t, a, k, bf, reg), fi in zip(meta, h0):
    if k == 1: pos[t] = pos[t] or bool(fi)
print(f"current detector: caught {sum(pos.values())/len(pos):.1%} at false alarms {h0[kind == 0].mean():.2%}")
