# Why did the gate miss 71%? The coverage hypothesis, measured

**Tuning data only.** The holdout is spent and the retest set (`r1`) is sealed
until 2028-02-12. Everything below is hypothesis-generating for a future
revision, and nothing here licenses re-scoring anything.

## The hypothesis

FINDINGS §6a: half the universe is scored on ~10 of 13 measures, and
gross_margin — the heaviest fitted weight (0.382) — is absent for 24% of
filers. Plausibly the gate misses because it is often blind, not because the
signal is absent.

## The measurement

For each of 194 tuning positives with a scoreable window before its break
became public: how many measures were evaluated at the gate's last chance to
warn, and did it ever fire in time (up to 8 quarters of chances)?

| measures evaluated | fired in time |
|---|---|
| 13 (all)  | 0/3 (n too small to read) |
| 11–12     | 36/95 = **37.9%** |
| 9–10      | 15/62 = 24.2% |
| ≤8        | 7/34 = 20.6% |

| gross_margin | fired in time |
|---|---|
| available | 49/155 = **31.6%** |
| absent    | 9/39 = 23.1% |

Of the 136 misses, **52 were near-misses** (best pre-break score ≥ 30 against
the threshold of 45).

## The honest reading

- **The effect is real.** Fuller coverage roughly doubles the hit rate against
  the thinnest bucket, and gross_margin availability is worth ~8 points.
- **It is not the answer.** Even at 11–12 measures the hit rate is 37.9% —
  nowhere near the 60% bar. Repairing coverage alone cannot make this gate
  pass. Whatever a revision does, it needs more than plugging gaps.
- **The near-miss count is a trap.** 52 misses scoring ≥30 makes "lower the
  threshold" look attractive; that is fitting to tuning by another name, and
  the FPR ceiling was already binding at 0.0399 of 0.04. Any threshold change
  is a revision, and a revision is judged only against retest set r1.

Raw rows: `reports/recall_hypothesis_tuning.json` (gitignored; regenerate with
the script in the git history of this file).
