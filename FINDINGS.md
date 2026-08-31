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

## 6. Measured during the instrumentation build (2026-08-30, post-KILL)

Added after the Phase 0 KILL, while building the measurement and honesty
infrastructure. Every number here is a **finding, not a fix** — and none of
them, singly or together, explains the KILL. Each is a hypothesis the new
instrumentation makes measurable for the first time; the only thing entitled
to test any of them is a fresh pre-registration on data that did not exist on
2026-08-30.

### 6a. Diagnostic-level silent abstention

The filer-level coverage gate was the right fix applied to the right function,
and it looked total because no test ever asked how many diagnostics a
*scoreable* filer actually evaluated. Measured at 2024-05-15 on a 250-filer
sample: 67.6% scoreable, and of those 169, exactly **1** had all 13
diagnostics evaluated — median **10 of 13**, minimum 2. `gross_margin`, the
heaviest weight at 0.3818, was absent in 24.3% of scoreable filers.

The worst case was structural: one filer's computable diagnostics carried so
little weight that no z-value whatsoever could reach `THRESHOLD` — and it
reported `score 0.0 / gated_in False / scoreable True`. That is the exact
defect the coverage gate was written to prevent ("not score=0.0, which was
indistinguishable from assessed, looks clean"), resurfacing one level down.
Fixed at the delivery boundary in gate 3.1.0: below `MIN_SCOREABLE_WEIGHT`
the verdict is `scoreable=False` with reason `CANNOT_REACH_THRESHOLD`, and an
unscoreable filer's score is **null in the contract, never 0.0**.

"Half the universe is scored on a fraction of the diagnostic set" reads like
an explanation for the 0.287 recall. It is not one. Recorded, measurable,
untested.

### 6b. The diluted_shares structural ceiling

`AVERAGED_FLOWS` correctly refuses to difference a weighted-average share
count (differencing produced 266 negative share counts before the rule
existed). A filer tagging quarterly diluted shares in each 10-Q but only an
annual figure in the 10-K therefore structurally cannot exceed 3 of 4
quarters — a **0.75 coverage ceiling** — while the global `COVERAGE_MIN` of
0.90 judges it against 1.0. Result: `dilution_yoy` is suppressed in **92.3%**
of scoreable filers, and its calibrated weight (0.0949) was fitted on the ~8%
of tuning rows where it existed.

Measured, not acted on. Unsuppressing it would apply that weight to a
population it was never fitted on — an uninterpretable score change that
would inevitably be read as an improvement. The coverage dashboard reports
`expected` (0.75) beside `achieved`; acting waits for a re-measurement under
a new pre-registration.

### 6c. The /A-form restatement criterion misses 99% of revisions

`label.py`'s RESTATEMENT criterion scans for a form ending in `/A`. Measured
across 12 cached filers: **6 of 624 revisions (0.96%)** arrive that way — the
other 99% arrive as revised comparatives inside ordinary 10-Ks and 10-Qs.
`restate.py` therefore detects on vintage-list growth, carrying
`on_amendment` as a labeled subset rather than the trigger.

`label.py` itself is deliberately **not** changed: it is a criterion of the
Phase 0 label set, and editing a labeling criterion after the holdout was
scored means the Phase 0 label set is no longer reproducible from the code.
Whether the label should use vintage-growth events is a future
re-measurement question.

### 6d. 52/53-week label contamination, ~1.1% of trips

A 14-week quarter compared YoY against a 13-week quarter measures calendar,
not business: 17–20% of filers run a 52/53-week calendar, detected long
quarters carry a median +7.4% revenue lift over their neighbours, and median
|revenue_accel| is 62% higher in quarters whose YoY chain touches one.
On the *label* side, **25 of 2,216** revenue-decel trips (~1.1%) touch a
14-week comparison, of which roughly half are marginal enough for the extra
week to account for the trip — call it ~0.5% of the label.

The gate side is guarded (`yoy_at` refuses non-comparable spans, shipped with
a `gate_version` bump because it changes scores). The label side is left
alone for the same reason as 6c: scores are versioned, labels are the frozen
half of the one clean measurement this project has.

### 6e. 67% thirteen-year attrition the universe cannot see

The current universe is a scrape of *today's* index membership, so every
filer delisted, acquired or bankrupted before today is absent. Measured from
the SEC quarterly full-index, which is point-in-time by construction:
**8,021** CIKs filed a periodic report in 2011Q1, **6,190** in 2024Q1, and
only **2,629** appear in both — 67% attrition over thirteen years.

The direction is one-way and it is the most tempting sentence in this file:
the missing two-thirds are disproportionately where deterioration actually
*ends*, so the generated positive set is enriched in mild recoverable cases
and the measured 0.287 hit rate is **plausibly biased downward**. Recorded,
not acted on. It does not license a re-run: `prereg.json` says do not retune
and re-run against this holdout, and a survivorship-free case set is a
*different* case set requiring a new split and a new pre-registration
committed before it is scored. `fullindex.survivorship_gap()` measures;
nothing rebuilds cases from it.

### 6f. The companyfacts cache was permanent for a document that grows

`edgar.py` cached companyfacts permanently on the reasoning "a fact is
immutable once filed." True of a FACT, false of the DOCUMENT — a per-company
aggregate that grows with every filing. The live consequence: `scan` detected
a new 10-Q via the daily index, then scored the facts file written at
backfill time — **the filing that triggered the scan was not in the data the
scan scored**. Fixed: callers that just learned the filer filed pass
`refresh=True`, which skips the cache read but never the write, and
`ingest_state.facts_filed_max` is the staleness key that decides who needs a
refetch.

### 6g. Provisional constants — sample-derived, labeled as such

Several shipped constants were set from samples, not the universe, and each
says so in its own docstring (`PROVISIONAL`) until the first full-universe
run replaces it:

| constant | set from | module |
|---|---|---|
| `MATERIAL_REL` (1% materiality; 42.5% of revisions below it) | 12 filers, 624 revisions | `restate.py` |
| `DERIVED_FRACTION_HIGH` (0.50 tripwire; observed max 0.457) | 34 filers | `provenance.py` |
| 52/53-week detection constants | 91 filers | `fiscal.py` |
| coverage/abstention numbers (§6a) | 250 filers at one date | `coverage.py` |
| label contamination (§6d) | 2,216 trips | — |

A constant that looks derived but was set from 12 filers is a number that
implies more measurement than happened. The label is the fix available today;
the re-derivation is the fix that needs the full universe.

---

## 7. Adversarial review of the post-KILL code (2026-08-31)

Thirty-one defects, found by reading the code written *after* the KILL against
the invariants it was written to hold. Every one was reproduced against the
live repository before it was touched — a command run, a page fetched, a
figure recomputed from the cache — and every one now has a regression test
that fails without its fix and names the defect in its docstring. **None was
dismissed, and none turned out not to hold.** The suite went from 370 tests to
450 across six commits.

Nothing here re-scores the sealed half, and nothing here is a correction to
the Phase 0 result. §7b says what the fixes do and do not move.

### 7a. What was wrong, by class

| Class | Count | What the defects had in common |
|---|---|---|
| Point-in-time and the arithmetic | 7 | A number that is not what the filings said at the cutoff |
| The numbers a surface prints | 7 | A figure printed where nothing had been measured |
| The database, and the files that leave it | 7 | A write that did not survive an interrupt, or went somewhere else |
| The read service and its pages | 5 | Caller input that ended the process, or a wrong body that looks right |
| The verdict on every surface | 5 | A score reaching a person without the failed test attached |

What each class looked like in practice:

- Debt counted twice; a balance sheet paired with a flow from a different
  year; an outcome graded with knowledge that arrived a year after the window
  shut; a fiscal calendar read off the untruncated snapshot and stamped with
  a cutoff it had not reached.
- A 0.0 hit rate on an empty denominator, printed beside the frozen 0.287
  under the same "per-case" label; a 0.0 derived fraction for filers where it
  was never computed; a clean bill of health, with a provenance stamp on it,
  for a company where nothing was read; a verifier that let generated prose
  cite a container and inherit every number underneath.
- Two transactions where one was needed, so an interrupt destroyed a filer's
  revision history for good; migrations with no rollback; append-only triggers
  that INSERT OR REPLACE walked straight past; a CSV import that could hand a
  watched symbol to a different company.
- A malformed percent-escape, a wrong-shaped published file, and a record
  admitted without the numbers its own sentence is derived from — each an
  uncaught throw, which is to say an exit, taking every other route with it;
  and paging parameters nobody validated, so `limit=-1` presented 39,563 of
  39,564 records as a complete page.
- `explain`, `narrations` and `run-test` each emitted a score or model-written
  prose about a flagged company with no verdict on either stream; the digest
  and the web Overview presented a replay over the threshold-fitting half as
  "Latest run".

By severity: 11 broke a stated invariant, 11 produced a wrong number or a
wrong sentence, 9 were smaller — a message, a label, a spreadsheet cell.

### 7b. What a green suite did not catch

The suite was green at 370 tests on the commit before this review, and it had
been green through the whole phase build. It did not catch:

- **A holdout that could be scored a second time.** `ledgerline run-test
  --split holdout` had no guard of any kind. `replay` refused the sealed half
  and `calibrate` refused it; the one command whose entire job is to score a
  split did not, and its report would have overwritten
  `reports/backtest_holdout.json` — which is not in git and is the only full
  record of the 2026-08-30 failure. One flag, and the project's single
  remaining measurement would have been spent quietly, into a file.
- **A path traversal in `publish`.** Company files were built as
  `companies/<ticker>.json` from a ticker nothing had validated, and the
  ticker column of an imported watchlist CSV is text from another tool by
  design. A row spelled `../../../pwned` wrote a file three directories above
  the feed while `publish` reported full success. The read side had matched
  its ticker rather than trusted it since it was written; the write side
  never did.
- **`total_debt` counting the current maturities twice.** `us-gaap:LongTermDebt`
  is the all-in figure and sat in the group that resolves the *noncurrent*
  component, so any filing that tagged it without the noncurrent split had the
  current portion added to a number that already held it. That grouping
  entered in `c33ee74`, the repository's first commit — **it predates the
  case set, the split, the pre-registration and the test.** Every reading of
  net debt the project has ever published on an affected filer was overstated;
  Jefferies at 2012-12-31 read 1,799,264,000 against a true 1,358,695,000.

All three are the same shape: correct-looking code with no test asking the
question. A green suite is evidence about the questions someone thought to
ask.

### 7c. Effect on the Phase 0 numbers — a finding, not a correction

Two of the fixes change what the detector computes: `total_debt` is no longer
double-counted, and `deferred_vs_revenue_gap` and `net_debt_to_ttm_ocf` now
abstain rather than pair a stale balance with a current flow. Both of those
diagnostics carry weight **0.0000**, so no score moves — but the gate needs
two distinct measures out of line (`MIN_FLAGS = 2`), and that count does not
look at weight. A retiring flag can therefore flip a company from flagged to
quiet. Bounded, without scoring anything:

- **The saved record.** Of 39,564 saved assessments, 2,610 carry one of the
  two diagnostics and 338 of those were flagged. Not one of the 338 is left
  below two measures if **both** diagnostics retire entirely. **No saved
  verdict flips.**
- **The frozen holdout report**, read and not re-run. 245 of its 387 companies
  fired. Not one of them falls below two measures if both diagnostics retire.
  So no company that fired stops firing, and the 51.2% of companies that
  stayed fine and were flagged at least once — 107 of 209 controls —
  **cannot fall.**
- **The 28.7% caught** (47 of 164 positives with a qualifying lead) is not
  bounded in both directions, and saying so matters more than a tidy answer.
  The abstention fix can only take flags away, and taking one away can only
  delay the quarter a company first fires, never advance it — a hit has to
  land inside the pre-registered lead window, so on that fix alone recall can
  only fall or stay. The debt fix is different: it moves the one measure that
  reads `total_debt` (`net_debt` feeds `net_debt_to_ttm_ocf` and nothing
  else), and a z-score can move either way, so where the two balances do share
  a moment it could add a flag rather than remove one. What is certain is that
  no company that fired stops firing.
- **The 3.83% of quiet company-quarters flagged cannot be bounded at all**
  from the frozen record: it stores each company's flags as a union across its
  quarters, not per quarter. Measuring it means scoring the sealed half a
  second time, which the tool now refuses and which nothing in this review
  licenses.

No bound here points toward a passing verdict, and the one quantity that
could move upward — recall, by at most the handful of quarters where a
corrected debt figure newly clears the trigger — would have to close a gap of
31.3 percentage points to reach the 60% floor. The KILL stands, and this is
the second time it is worth writing down (§6e was the first): a defect that would have made the detector look *worse* is not a
reason to re-run the test, and a defect that would have made it look better
would not be either. A corrected detector is a different detector, and the
reserved company-quarters in `ledgerline/data/retests.json` are the only thing
entitled to test it.

### 7d. Nothing was skipped

Every one of the 31 was reproduced and fixed. Two things were deliberately
*not* done, each with a test that pins the decision:

- A CSV import does not cross-check a supplied CIK against the SEC ticker map.
  Import fetches nothing over the network, and that is a promise the code
  keeps on purpose; a row whose symbol already belongs to another company is
  refused instead.
- `ShortTermBorrowings` is not treated as already inside `LongTermDebt`.
  Commercial paper and revolver draws are not maturities of long-term debt.

The sweep that closed the review found three more, all in the same class as
the ones above and all now fixed: `calibrate --split holdout` refused by
raising out of its own dataset builder, so the person who typed it got a
traceback instead of a written reason; `replay`'s refusal carried a
hand-typed copy of "2026-08-30" that nothing bound to the frozen record — the
same second-copy defect that had just been fixed in the terminal caveat, one
command over; and a split named neither half reached the loader and came back
as a `ValueError` traceback. All three sealed-half refusals now read the same
words from `ledgerline/data/phase0.json`, and moving that file moves all of
them.
