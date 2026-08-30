"""
Phase 0f -- fit the gate on the TUNING split only.

ROADMAP §4 0f: "Weights by logistic regression against the labeled outcome;
Z_TRIGGER, SCORE_DIVISOR, THRESHOLD chosen on the tuning split. Report the
coefficients."

Three commitments shape how this is written.

  1. NOTHING HERE EVER READS THE HOLDOUT. build_dataset() takes a split name
     and load_split() is the only way in. The holdout is scored once, by
     backtest.run("holdout"), after this has finished and its output is
     committed.

  2. THE FIT IS ON THE GATE'S OWN FUNCTIONAL FORM, not on a parallel model.
     The gate scores sum(weight_i * hinge(z_i)) where
     hinge(z) = min(max(z, 0) / Z_TRIGGER, Z_CAP). Fitting logistic regression
     on exactly those hinge features means the fitted coefficients ARE the
     weights -- there is no translation step in which a model's notion of
     importance gets reinterpreted as a hand-set weight. An auditor can
     recompute a score from the published table and the published z values.

  3. NO NEW DEPENDENCY. Plain-Python IRLS in ~40 lines. scikit-learn would be
     one line, but the point of this layer is that someone can re-derive it,
     and a fit nobody can follow is the same problem as a model nobody can
     follow. L2 regularisation is included because 13 correlated features on a
     few thousand rows will otherwise separate.

The label is per FILER-QUARTER, not per filer: at each scoreable cutoff, did a
fundamental deterioration event follow within the horizon? That is the question
the gate is asked at every filing, so it is the question the weights are fitted
to answer.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date

from . import edgar, signals_v3
from . import label as label_mod
from .validate import harness

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CALIB_PATH = os.path.join(DATA, "calibration.json")
DATASET_PATH = os.path.join(DATA, "tuning_dataset.json")
REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

FEATURES = list(signals_v3.TRACKED)

# Z_TRIGGER is chosen from this grid on tuning, not assumed.
Z_TRIGGER_GRID = (1.5, 2.0, 2.5, 3.0)

# Ridge penalty. 13 correlated diagnostics on a few thousand rows separate
# without one, and a separated fit produces enormous coefficients that read as
# confidence rather than as degeneracy.
L2 = 1.0
MAX_IRLS_ITERS = 50
TOLERANCE = 1e-7


# ------------------------------------------------------------------ dataset


def hinge(z: float | None, z_trigger: float) -> float:
    """The gate's own transform: nothing below the trigger, capped above.

    Identical to the expression inside signals_v3.evaluate, so a coefficient
    fitted here is directly the weight applied there.
    """
    if z is None or z < z_trigger:
        return 0.0
    return min(z / z_trigger, signals_v3.Z_CAP)


def build_dataset(split: str = "tuning", start_year: int = 2005,
                  end_year: int = 2025, progress=None) -> list[dict]:
    """One row per scoreable filer-quarter in `split`.

    Each row carries the raw signed z for every diagnostic (so the Z_TRIGGER
    grid can be searched without rescoring) and the forward-looking label.
    """
    if split == "holdout":
        raise RuntimeError(
            "calibration must never touch the holdout -- it is scored once, "
            "after the weights are fixed and committed"
        )
    cases = harness.load_split(split)
    cutoffs = [f"{y}-{m:02d}-15" for y in range(start_year, end_year + 1)
               for m in (2, 5, 8, 11)]

    rows: list[dict] = []
    for i, case in enumerate(cases, 1):
        norm = edgar.normalize(case.cik)
        if not norm:
            continue
        for c in cutoffs:
            res = signals_v3.evaluate(case.ticker, case.cik, as_of=c, norm=norm)
            if not res["scoreable"] or not res.get("z"):
                continue
            # Forward-looking on purpose: the label is the outcome side of the
            # experiment. label() builds its horizon from quarters first
            # published after the cutoff, so it cannot leak into its own
            # scoring window.
            lab = label_mod.label(case.ticker, case.cik, norm, as_of=c)
            if not lab.n_quarters_observed:
                continue  # no forward window yet -- unlabelled, not negative
            rows.append({
                "ticker": case.ticker,
                "cutoff": c,
                "is_positive_case": case.is_positive,
                "y": int(lab.deteriorated),
                "z": res["z"],
            })
        if progress:
            progress(i, len(cases), len(rows))
    return rows


# --------------------------------------------------------- logistic fit


def _design(rows: list[dict], z_trigger: float) -> tuple[list[list[float]], list[int]]:
    x = [[1.0] + [hinge(r["z"].get(f), z_trigger) for f in FEATURES] for r in rows]
    y = [r["y"] for r in rows]
    return x, y


def fit_logistic(x: list[list[float]], y: list[int], l2: float = L2) -> list[float]:
    """Ridge-penalised logistic regression by IRLS. Intercept is not penalised.

    Newton steps on the penalised log-likelihood, solving the normal equations
    with Gaussian elimination. Deliberately unclever -- it is meant to be read.
    """
    n_feat = len(x[0])
    beta = [0.0] * n_feat

    for _ in range(MAX_IRLS_ITERS):
        # gradient and Hessian of the penalised negative log-likelihood
        grad = [0.0] * n_feat
        hess = [[0.0] * n_feat for _ in range(n_feat)]
        for xi, yi in zip(x, y, strict=True):
            eta = sum(b * v for b, v in zip(beta, xi, strict=True))
            eta = max(-30.0, min(30.0, eta))
            p = 1.0 / (1.0 + math.exp(-eta))
            w = max(p * (1.0 - p), 1e-9)
            resid = yi - p
            for a in range(n_feat):
                if xi[a] == 0.0:
                    continue
                grad[a] += resid * xi[a]
                for b in range(a, n_feat):
                    hess[a][b] += w * xi[a] * xi[b]
        for a in range(n_feat):
            for b in range(a):
                hess[a][b] = hess[b][a]
        for a in range(1, n_feat):  # intercept unpenalised
            grad[a] -= l2 * beta[a]
            hess[a][a] += l2

        step = _solve(hess, grad)
        if step is None:
            break
        beta = [b + s for b, s in zip(beta, step, strict=True)]
        if max(abs(s) for s in step) < TOLERANCE:
            break
    return beta


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / m[col][col]
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


# ------------------------------------------------------- operating point


def choose_threshold(rows: list[dict], weights: dict[str, float],
                     z_trigger: float, max_fpr: float,
                     min_flags: int) -> dict:
    """Pick the raw-score cutoff on TUNING.

    The operating point is the most sensitive cutoff whose per-quarter
    false-positive rate on control filer-quarters still satisfies the
    pre-registered ceiling. "Control filer-quarter" means a quarter of a
    control case -- the same denominator verdict() uses on the holdout, so the
    number chosen here means the same thing there.
    """
    scored = []
    for r in rows:
        hinges = {f: hinge(r["z"].get(f), z_trigger) for f in FEATURES}
        n_flags = sum(1 for v in hinges.values() if v > 0)
        raw = sum(weights[f] * v for f, v in hinges.items())
        scored.append((raw, n_flags, r))

    control_q = [s for s in scored if not s[2]["is_positive_case"]]
    positive_q = [s for s in scored if s[2]["is_positive_case"] and s[2]["y"] == 1]
    if not control_q or not positive_q:
        return {"raw_cutoff": None, "reason": "no control or no positive quarters"}

    candidates = sorted({round(s[0], 3) for s in scored if s[0] > 0})
    best = None
    for cut in candidates:
        def fires(s, cut=cut):
            return s[0] >= cut and s[1] >= min_flags
        fpr = sum(1 for s in control_q if fires(s)) / len(control_q)
        if fpr > max_fpr:
            continue
        recall = sum(1 for s in positive_q if fires(s)) / len(positive_q)
        if best is None or recall > best["tuning_recall_on_deteriorating_quarters"]:
            best = {"raw_cutoff": cut, "tuning_fpr_per_quarter": round(fpr, 5),
                    "tuning_recall_on_deteriorating_quarters": round(recall, 4)}
    if best is None:
        return {"raw_cutoff": None,
                "reason": f"no cutoff satisfies fpr <= {max_fpr}"}
    best["n_control_quarters"] = len(control_q)
    best["n_deteriorating_quarters"] = len(positive_q)
    return best


def run(split: str = "tuning", progress=None) -> dict:
    """Fit everything on tuning and write calibration.json. Never the holdout."""
    rule = harness.load_prereg()
    # The dataset is a pure function of the committed split and the immutable
    # EDGAR cache, so it is rebuilt only when absent. Keyed by the split hash
    # so a different split can never silently reuse it.
    split_sha = harness.verify_split()
    rows = None
    if os.path.exists(DATASET_PATH):
        with open(DATASET_PATH) as fh:
            cached = json.load(fh)
        if cached.get("split_sha256") == split_sha and cached.get("split") == split:
            rows = cached["rows"]
    if rows is None:
        rows = build_dataset(split, progress=progress)
        with open(DATASET_PATH, "w") as fh:
            json.dump({"split": split, "split_sha256": split_sha, "rows": rows}, fh)
    if not rows:
        raise RuntimeError("no scoreable filer-quarters in the tuning split")

    results = []
    for zt in Z_TRIGGER_GRID:
        x, y = _design(rows, zt)
        beta = fit_logistic(x, y)
        weights = dict(zip(FEATURES, beta[1:], strict=True))
        # Negative coefficients mean the diagnostic points the other way from
        # its declared direction on this data. Clamping to zero is honest -- it
        # says "this one contributed nothing" instead of letting the gate be
        # talked out of firing by a diagnostic that was supposed to be evidence.
        clamped = {f: max(0.0, w) for f, w in weights.items()}
        op = choose_threshold(rows, clamped, zt,
                              rule["max_false_positive_rate_per_quarter"],
                              signals_v3.MIN_FLAGS)
        results.append({"z_trigger": zt, "intercept": beta[0],
                        "coefficients": weights, "weights": clamped, **op})

    usable = [r for r in results if r.get("raw_cutoff") is not None]
    chosen = max(usable, key=lambda r: r["tuning_recall_on_deteriorating_quarters"]) \
        if usable else results[0]

    # SCORE_DIVISOR is a presentation constant: it maps the chosen raw cutoff
    # onto the published THRESHOLD so the 0-100 score keeps its meaning.
    divisor = None
    if chosen.get("raw_cutoff"):
        divisor = round(chosen["raw_cutoff"] / (signals_v3.THRESHOLD / 100.0), 4)

    payload = {
        "fitted": date.today().isoformat(),
        "split": split,
        "split_sha256": harness.verify_split(),
        "prereg_sha256": harness.prereg_hash(),
        "n_rows": len(rows),
        "n_positive_rows": sum(r["y"] for r in rows),
        "features": FEATURES,
        "l2": L2,
        "grid": results,
        "chosen": chosen,
        "SCORE_DIVISOR": divisor,
        "THRESHOLD": signals_v3.THRESHOLD,
        "MIN_FLAGS": signals_v3.MIN_FLAGS,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(CALIB_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "calibration.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def load() -> dict | None:
    if not os.path.exists(CALIB_PATH):
        return None
    with open(CALIB_PATH) as fh:
        return json.load(fh)
