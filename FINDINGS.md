# Ledgerline Signal — pre-build audit

Audit of the uploaded snapshot (`edgar.py`, `signals.py`, `signals_v2.py`,
`backtest.py`, `data/*`). Conclusion up front: **the signal has not validated,
and the reason is upstream of the scoring layer.** Four of the highest-weighted
diagnostics are computed on a corrupted input series, so neither v1 nor v2 has
actually been tested yet.

---

## 1. v2 did not fix the problem it was written to fix

`signals_v2.py` opens by stating v1 failed at a 24% false-positive rate.
`data/backtest_v2.json` reports v2's own FPR as **28.8%** — higher.

| ticker | broke   | v1 fire rate | v2 lead | v2 fire rate |
|--------|---------|--------------|---------|--------------|
| PTON   | 2021-11 | 10/17        | **-12mo** | 4/17       |
| CVNA   | 2022-05 | 15/24        | +12mo   | 8/24         |
| BYND   | 2021-11 | 15/19        | **-18mo** | 3/19       |
| ZM     | 2022-02 | 9/19         | +9mo    | 1/19         |
| DOCU   | 2021-12 | 6/24         | **-8mo**  | 4/24       |
| ROKU   | 2022-02 | 19/24        | +6mo    | 2/24         |
| WBD    | 2022-08 | 22/24        | +42mo   | 14/24        |
| LUMN   | 2022-11 | 15/24        | +45mo   | 3/24         |

Read honestly:

- **3 of 8 fire after the story broke** (PTON, BYND, DOCU). Those are not
  signals, they are confirmations.
- **2 of 8 fire on the first cutoff in the window** (WBD, LUMN, both
  2019-02-15). A +42mo "lead" is censored — the true first-fire is before the
  data starts. WBD fires in 14 of 24 quarters. That is a constant, not an event.
- **3 of 8 genuinely lead** (CVNA +12, ZM +9, ROKU +6).

So: 3 real hits, 2 uninformative, 3 misses, at a ~29% base rate of firing on
the control group. That is not distinguishable from noise, and the write-up in
the v2 docstring claims a fix that the accompanying data contradicts.

Separate methodological problem: `Z_TRIGGER = 2.0`, the weight table, and
`THRESHOLD = 45.0` were all chosen while looking at these same 8 cases. There is
no holdout. Even if the numbers had looked good, they would be in-sample.

## 2. The OCF series is structurally broken — this is the root cause

`edgar.normalize()` keeps a flow fact only if its duration is 80–100 days.
Most filers report operating cash flow **cumulative year-to-date** in 10-Qs, not
per quarter. So only fiscal Q1 survives the filter. Observed in the shipped
`state.db` for PTON (CIK 0001639825):

```
revenue              37 facts   2018-06 … 2026-06   (every quarter)
operating_cash_flow  17 facts   2018-06, 2018-09, 2019-06, 2019-09, 2020-06 …
```

Two OCF points per fiscal year, alternating June/September. `ttm()` then does
`sum(series[-4:])` with no contiguity check, so "TTM operating cash flow" is
actually four non-adjacent quarters spanning ~two years.

Everything downstream of `ocf_ttm` is therefore wrong:

| diagnostic            | weight | status |
|-----------------------|--------|--------|
| `accrual_ratio`       | 2.0    | corrupt (`ni_ttm` real, `ocf_ttm` garbage) |
| `ocf_to_revenue`      | 1.5    | corrupt |
| `net_debt_to_ttm_ocf` | 1.0    | corrupt |
| `cash_conversion_gap` | 2.0    | OK — `yoy()` is month-matched, so it dodges this |

That is 4.5 of the 18.5 total weight in `TRACKED` computed on a series that
does not mean what the code thinks it means. **No conclusion about v1 or v2
survives this.** The fix is to derive quarterly flows by differencing YTD
cumulatives (Q4 = FY − 9M, Q3 = 9M − 6M, …) instead of discarding them.

## 3. Other correctness bugs

**Live/backtest divergence in `signals_v2._history()`.** `backtest.py` correctly
truncates on the XBRL `filed` date and its docstring explains why. `_history()`
truncates on `r["end"] <= cut_end` — period end. Inside the backtest this is
harmless because the snapshot was already filed-filtered. In production
`evaluate()` runs on the full fact set, so baselines are built from *restated*
figures that were not public at the time. Production and backtest compute
different functions. Any validated result would not transfer.

**Autocorrelated baseline, no scale floor.** `_history()` produces overlapping
trailing windows; consecutive TTM-based diagnostics share 3 of 4 quarters. That
understates `pstdev`, which inflates every z. Compounding it, there is no floor
on `sd` — a filer with a stable stretch gets `sd → 0` and then any move is a
5-sigma event. With `MIN_HISTORY = 6` and population sd, this is a
false-positive generator on its own. Use a MAD-based scale with an explicit
floor and a minimum effective sample size.

**`persist_metrics` undoes the dedupe.** `normalize()` merges on `(end, kind)`;
the `metrics` table PK is `(cik, metric, period, form)`. Same quarter reported
in both a 10-K and a 10-Q writes two rows — that is why the DB shows 37 revenue
rows for ~33 quarters, with `2019-06-30`, `2020-06-30`, `2021-06-30` each
duplicated. Nothing reads from the table today, so it is latent, but every
`back=4` offset breaks the moment something does.

**`total_debt` omits current maturities.** Candidates are `LongTermDebt`,
`LongTermDebtNoncurrent`, `DebtLongtermAndShorttermCombinedAmount`. Revolver
draws and current portion are invisible, so `net_debt` is understated for
exactly the leveraged names the LEVERAGE flag exists to catch (CVNA).

**`deferred_revenue` is current-only.** A reclass between current and noncurrent
contract liability reads as a demand break. DOCU's `DEFERRED_VS_REVENUE_GAP`
fire is suspect for this reason.

**`diluted_shares` falls back to basic, with no corporate-action guard.** The
shipped `eval.json` has BYND flagged for `DILUTION` on **+673.8% YoY diluted
shares**. That is a split, reverse split, or concept switch — not issuance. PTON
shows the same shape in the DB: 22.9M (2019-06) → 279.9M (2019-12) across the
IPO. The rule cannot tell a corporate action from dilution.

**`eval.json` is stale.** NVDA's entry has `period: 2020-01-26` — the dead-series
symptom the `normalize()` comment says was fixed by merging candidate concepts.
The artifact predates the fix. Delete it rather than reason from it.

## 4. What this means for the build

The ingestion layer (`edgar.py`) is good work — the daily-index change detector
is the right cost architecture, the throttle and permanent cache are correct,
and the concept-merge fix was the right call. Keep it.

The scoring layer is untested, not disproven. It cannot be tested until the
flow-derivation bug is fixed, because right now the backtest is measuring
arithmetic on a broken series.

So the sequence is: fix derivation → rebuild the validation harness with a real
control group and a pre-registered decision rule → *then* decide whether there
is a product. See `ROADMAP.md`. Phase 0 has a documented kill condition.

---

## 5. Found during the build: point-in-time was inverted by the vintage collapse

Added 2026-08-30, after standing the project up and backfilling the S&P 1500.
This is a NEW defect, not part of the original audit, and it sat underneath
the §3 fix rather than being addressed by it.

`normalize()` collapsed each period end to a single fact. `derive._keep_prior`
resolved ties by concept rank, then by most recent `filed` -- "restatements
supersede originals." Combined with truncation on `filed`, that inverts the
invariant the README opens with.

ABT's Q1 2012, straight out of `companyfacts`:

```
filed 2012-05-08  10-Q  $9,456,633,000   <- what the market actually saw
filed 2013-05-08  10-Q  $5,283,685,000   <- restated, AbbVie spin-off
filed 2014-02-21  10-K  $5,284,000,000   <- the row that survived
```

The surviving row carried `filed=2014-02-21`. So:

1. **Original disclosures disappeared.** `as_of("2012-08-15")` returned nothing
   for that quarter, though it had been public since May 2012. ABT's 73 revenue
   quarters collapsed to 33 distinct filing dates, which is why
   `signals_v3._history()` reported "6q of 12" for a filer that has filed every
   quarter since 2011.
2. **Baselines used restated figures.** §3 diagnosed exactly this and fixed the
   truncation *key* -- `filed` rather than period `end`. But the row that
   survived the dedupe was still the restatement, so the fix was incomplete in
   a way the tests could not see, because they never fed the same period twice.

Measured over 150 backfilled filers:

| | before | after |
|---|---|---|
| gap between `universe.scoreable_from()` and the first cutoff `evaluate()` can score | median **+56 months** | −12 months |
| scoreable cutoffs per filer | 21–41 of 84 | median **50** of 84 |
| filers reaching `2014-16-energy` | 28% | 87% |
| filers reaching `2017-19-idiosyncratic` | 51% | 93% |

The failure direction matters: it **hid** data rather than leaking it, so no
published number was ever wrong and there was no lookahead. But `admit()` used
`scoreable_from()` while scoring used `evaluate()`, and those disagreed by
nearly five years -- so admission was letting in positives whose break date the
gate provably could not reach, and the three earliest regimes were structurally
unreachable. A Phase 0 run on this would have produced a bad verdict for a
reason that has nothing to do with whether the signal exists.

**Fix.** A fact is a sequence of vintages, not a value. Every vintage is
retained; the row exposes the latest (labels may look forward, and restatement
is itself one of the five deterioration criteria); `edgar.as_of()` rewinds each
row to the newest vintage filed on or before the cutoff. `derive.newest_at()`
is the selection primitive. A derived quarter gets a vintage wherever either of
its two YTD inputs moved. `tests/unit/test_vintages.py` pins the behaviour in
both directions -- no restated value leaks backwards, and no quarter stays
hidden after its original filing.

**Standing lesson.** The §3 finding was correct and the fix was applied to the
right function. What made it incomplete is that the defect had two halves in
two modules, and the test fixtures never reported the same period twice, so a
half-fix looked total. Fixture realism is load-bearing here.

---
