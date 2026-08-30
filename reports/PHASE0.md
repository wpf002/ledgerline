# Phase 0 result: KILL (2026-08-30)

The sealed test half was scored exactly once, against the decision rule in
`ledgerline/data/prereg.json` (committed 2026-08-30, sha256 `3bdba2fd784dc066…`)
and the split in `ledgerline/data/split.json` (sha256 `5c12ce5412c4d7e1…`).
The rule said any single failed criterion is a kill. Two failed.

This file is the write-up `ROADMAP.md` promised. The machine-readable record is
`ledgerline/data/phase0.json`, written once by `ledgerline phase0-freeze` and
committed — `reports/*.json` is gitignored, so `backtest_holdout.json` itself
does not survive a fresh clone. Every command that shows a score reads the
frozen record through `ledgerline/status.py` and refuses to run without it.

## The result

| # | criterion | holdout | required | |
|---|---|---|---|---|
| 1 | false-positive rate / control quarter | 0.0383 | at most 0.04 | pass |
| 2 | median lead | 9 months | at least 6 | pass |
| 3 | **positive hit rate** | **0.287** | **at least 0.60** | **FAIL** |
| 4 | regimes detected in | 6 of 6 | at least 4 | pass |
| 5 | sample size | 178 positives / 209 controls | 40 / 200 | pass |
| 6 | **beats naive baseline** | **0.0383 vs 0.0051** | strictly below | **FAIL** |

The gate caught 28.7% of the deteriorations it was built to warn about, and it
raised false alarms at seven times the rate of the two-line rule it had to beat
(TTM operating cash flow negative while net debt is positive — 0.0051 per
control quarter over 7,988 control filer-quarters).

Also measured, reported but never part of the pass mark: 14 positives fired on
the very first date they could be assessed, so their true lead is off the front
of the record; they are excluded from both scores (164 assessable positives
remain of 178).

## The per-filer number: 0.512

The per-quarter false-positive rate passed its criterion with 0.0017 of
headroom. The same fires, counted per company instead of per quarter, read
very differently: **51.2% of the control filers — companies that never
deteriorated — were flagged at least once** across their scoreable history
(7,657 control filer-quarters). The pre-registration required this number to
be reported, not gated on, and that choice looks generous in hindsight: one
false alarm per company is what actually spends a reader's trust, and this
gate spends it on half the quiet companies it watches. Any future
pre-registration should consider gating on the per-filer rate directly.

Two denominators, one warning: 0.0383 counts fires among quarters of filers
that *never* deteriorated. A live rate computed over "quarters not yet
followed by deterioration" includes the quiet quarters of filers that break
later, and is not comparable to this number.

## What did NOT go wrong

Worth recording, because it constrains what a revision should change:

- **No overfitting.** The holdout scored *better* than the tuning split the
  weights were fitted on: 0.287 vs 0.212 hit rate, 9 vs 6 months median lead.
  The gate generalizes; what it generalizes is not good enough.
- **Not a beta detector.** It fired with positive lead in all six market eras,
  including `2017-19-idiosyncratic` — the regime a falling-market detector
  cannot fake.
- **The measurement layer held.** Point-in-time truncation by `filed` date,
  generated (not curated) cases, a committed split and rule, and one code path
  shared by backtest and production all survived adversarial audit. The 0.287
  is a clean number about a bad gate, not a dirty number about an unknown one.
- **The lead is real.** When the gate did fire ahead of a deterioration, the
  median warning was 9 months — early enough to matter, if only it fired often
  enough to trust.

The failure is recall. The gate finds real deterioration; it finds too little
of it, and it is noisier than a two-line rule.

## A specification defect in criterion 5 of the label

The deterioration label (the outcome side of the test — `ledgerline/label.py`)
trips when at least 2 of 5 criteria occur within four quarters. Its fifth
criterion, `RESTATEMENT`, is specified as "a 10-K/A or 10-Q/A touching
revenue, OCF or net income" — it keys on *amended forms*.

Measured during the Phase 1 design pass, on the full vintage histories of 12
randomly sampled cached filers: of 624 value revisions actually observed,
**6 arrived on an /A form — 0.96%**. The other 99% land silently as revised
comparatives inside ordinary 10-Ks and 10-Qs. So the criterion as specified
detects roughly one restatement in a hundred, and the label set is thinner on
restatement-driven deteriorations than its own definition intends. (12 filers
is a sample, not the universe; the number is provisional until a full-universe
measurement replaces it.)

**This is deliberately not fixed.** The holdout was scored against the label
as written. Editing a labeling criterion after the fact would mean the Phase 0
numbers above could no longer be reproduced from the code — corrupting the one
clean measurement the project has, to flatter or punish a gate that already
has its verdict. The defect is recorded here; a corrected
`RESTATEMENT` criterion (detection by vintage growth rather than form suffix)
belongs to a future re-measurement under a new pre-registration, on data this
test never touched.

## What follows from a KILL

Per the pre-registration: the deterministic gate is not a viable product core,
and the product phases conditioned on it are not built as designed. **Do not
retune and re-run against this holdout** — it is spent; a scored sealed half
cannot be sealed again. What continues is the part whose value never depended
on the gate: the point-in-time fact store, provenance, restatement detection,
and the measurement machinery that would be needed to test any revised gate
against a new reservation. Every surface that shows a score carries this
verdict, stamped from the frozen record, until some future gate passes a test
this one failed.
