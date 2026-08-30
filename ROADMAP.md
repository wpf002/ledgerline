# Ledgerline Signal — roadmap

Everything from the audit through ship, in order.

- §1 — what the audit found
- §2 — fixes shipped
- §3 — the three open decisions, resolved and implemented
- §4 — Phase 0, the gate
- §5–10 — Phases 1–7, conditional on Phase 0 passing

**Phase 0 is a gate, not a milestone.** If it fails, nothing after it gets
built. The kill condition is committed before the test runs.

> ## PHASE 0 RESULT: KILL (2026-08-30)
>
> The holdout was scored once, against `data/prereg.json` committed at
> `3bdba2fd784dc066` and the split committed at `5c12ce5412c4d7e1…`.
> Two of six pre-registered criteria failed.
>
> | criterion | holdout | required | |
> |---|---|---|---|
> | false-positive rate / control quarter | 0.0383 | ≤ 0.04 | pass |
> | median lead | 9 months | ≥ 6 | pass |
> | **positive hit rate** | **0.287** | **≥ 0.60** | **FAIL** |
> | regimes detected in | 6 of 6 | ≥ 4 | pass |
> | sample size | 178 pos / 209 ctrl | 40 / 200 | pass |
> | **beats naive baseline** | **0.0383 vs 0.0051** | strictly below | **FAIL** |
>
> Per the pre-registration, the deterministic gate is not a viable product
> core and Phases 1–7 are not built. **Do not retune and re-run against this
> holdout.** Full write-up in `reports/PHASE0.md`.
>
> What did NOT go wrong, because it matters for whatever comes next: the
> holdout scored *better* than the tuning split it was fitted on (0.287 vs
> 0.212 hit rate, 9 vs 6 months lead), so there is no overfitting; the gate
> detected in all six regimes including `2017-19-idiosyncratic`, so it is not
> a beta detector; and the ingestion and point-in-time layers held up under
> adversarial audit. The gate finds real deterioration — it just finds too
> little of it, and it is noisier than a two-line rule.

Status: **74 tests passing**, no network required. Fixtures are synthetic
XBRL-shaped payloads; each test reproduces a specific documented bug or
enforces a specific decision.

**Build log, 2026-08-30.** Infrastructure bootstrapped, S&P 1500 universe set
(1503 of 1504 tickers resolved to a CIK), companyfacts backfilled for all of
them -- 1496 with XBRL facts, 2 without, 0 errors.

Two things came out of that pull:

- Phase 0a's headline check passes on real filings. Operating cash flow now
  covers 86-100% of each filer's revenue quarters, with roughly three quarters
  of every OCF series existing only because `derive.py` differences the YTD
  cumulatives. The residual reported-only counts land at 17-19 per filer over
  ~19 years -- exactly the two-per-fiscal-year signature FINDINGS §2 documented.
- A new defect, written up as FINDINGS §5: `normalize()` kept only the most
  recently filed vintage of each period, so `as_of()` hid original disclosures
  and served restated figures. It failed safe, but it delayed first
  scoreability by a median of 56 months and made the three earliest regimes
  structurally unreachable. Fixed before generating any case set, since Phase 0
  numbers computed on it would have been wrong in the pessimistic direction.

---

## 1. What the audit found

Full evidence in `FINDINGS.md`.

**v2 did not fix what it claimed to fix.** Its docstring stated v1 failed at a
24% false-positive rate. `backtest_v2.json` reported v2 at **28.8%** — worse.

| ticker | broke   | v1 fire rate | v2 lead   | v2 fire rate |
|--------|---------|--------------|-----------|--------------|
| PTON   | 2021-11 | 10/17        | **−12mo** | 4/17         |
| CVNA   | 2022-05 | 15/24        | +12mo     | 8/24         |
| BYND   | 2021-11 | 15/19        | **−18mo** | 3/19         |
| ZM     | 2022-02 | 9/19         | +9mo      | 1/19         |
| DOCU   | 2021-12 | 6/24         | **−8mo**  | 4/24         |
| ROKU   | 2022-02 | 19/24        | +6mo      | 2/24         |
| WBD    | 2022-08 | 22/24        | +42mo †   | 14/24        |
| LUMN   | 2022-11 | 15/24        | +45mo †   | 3/24         |

† censored — fired on the first cutoff in the window, so the true first fire is
outside the data. WBD fires in 14 of 24 quarters; that is a constant, not an
event.

Honestly read: 3 real leads, 2 uninformative, 3 fired *after* the story broke,
at a ~29% base rate on controls. Not distinguishable from noise.

**The root cause was upstream of scoring.** `edgar.normalize()` kept a flow fact
only when its span was 80–100 days. Most filers report cash-flow items
cumulative year-to-date in 10-Qs, so only fiscal Q1 survived. In the shipped
`state.db`, PTON had 17 operating-cash-flow points against 37 revenue points —
two per fiscal year. `ttm()` then summed four non-adjacent quarters spanning
~two years. That corrupted `accrual_ratio`, `ocf_to_revenue` and
`net_debt_to_ttm_ocf` — 4.5 of the 18.5 total weight in `TRACKED`. **Neither v1
nor v2 had actually been tested**; the backtest was measuring arithmetic on a
broken series.

**Live and backtest computed different functions.** `backtest.as_of()` truncated
on the XBRL `filed` date; `signals_v2._history()` truncated on period `end`.
Inside the backtest this was masked by pre-filtering. In production, baselines
were built from restatements that were not public at the time — so no validated
result would have transferred.

Also: `pstdev` with no scale floor on overlapping TTM windows; `persist_metrics`
PK included `form`, re-duplicating what `normalize()` deduped; `total_debt`
omitted current maturities; `deferred_revenue` was current-only;
`diluted_shares` fell back to basic with no corporate-action guard (BYND flagged
for DILUTION on +673.8% YoY share count); `Z_TRIGGER`, the weight table and
`THRESHOLD=45` all tuned in-sample on the same eight cases, one macro regime, no
holdout.

## 2. Fixes shipped

### 2a. Flow derivation — `derive.py` (new)

YTD cumulatives are differenced rather than discarded: `Q4 = FY − 9M`,
`Q3 = 9M − 6M`, `Q2 = 6M − 3M`. Facts inside one fiscal year share an identical
period `start`, so the join key is exact. Filers tagging genuine standalone
3-month Q2/Q3 facts keep those as `origin="reported"`, taking precedence over
the derived value.

A derived quarter's `filed` date is the later of its two inputs — which makes
post-derivation truncation by `filed` exactly equivalent to pre-derivation
truncation. That property is what lets backtest and production share one path.

`derive.ttm()` refuses non-contiguous windows and returns `None` rather than a
plausible wrong number.

*Verified:* 8 years of YTD-tagged OCF now yields 32 quarters, not 8.

### 2b. Ingestion — `edgar.py` (rewritten)

- `total_debt` sums long-term + current maturities + short-term borrowings.
- `deferred_revenue` sums current + noncurrent contract liability.
- `diluted_shares` locks to the diluted concept; basic fallback removed.
- `metrics` PK is `(cik, metric, end_date, kind)`; form is a column, not identity.
- `edgar.as_of()` is the single truncation primitive, on `filed`.
- `edgar.coverage_report()` gives per-metric coverage with a `scoreable` flag and
  a written reason. Episodic metrics (`capex`, `impairment`) are exempt.
- `fetch()` raises if `LEDGERLINE_UA` lacks a contact address.

### 2c. Diagnostics — `signals.py` (rewritten)

- Contiguity-gated `ttm()`.
- `revenue_accel` indexes the series directly instead of rebuilding a synthetic
  norm dict.
- `dilution_yoy` returns `None` above a 50% YoY move — a split, reverse split,
  IPO or exchange offer, not economic dilution.
- `peer_z` uses median/MAD.
- `derived_fraction` carried on every reading.
- **The v1 absolute-threshold rule set is deleted.** "DSO up >10 days" measures a
  business model, not a change in one.

### 2d. The gate — `signals_v3.py` (new, replaces v2)

- Baselines truncate on `filed` via `edgar.as_of()`.
- Median/MAD with a **per-metric scale floor** in the diagnostic's own units, so
  a quiet stretch cannot turn ordinary noise into a 5-sigma event.
- `MIN_HISTORY` 6 → 12 quarters; 8 non-null baseline observations per diagnostic.
- Coverage gate returns `scoreable=False` with a reason — **not** `score=0.0`,
  which was indistinguishable from "assessed, looks clean."
- Every flag carries `z`, `baseline_median`, `baseline_scale`, `baseline_n`,
  `floored`.
- Weights, `Z_TRIGGER`, `THRESHOLD` labeled **UNCALIBRATED**. They are
  placeholders so the module runs. Fitting numbers on the same eight cases is
  what produced the v2 result; calibration is Phase 0f.

*Verified on synthetic filers:* steady filer scores 0.0 and stays silent; OCF
collapse fires `CASH_CONVERSION_GAP` + `ACCRUAL_RATIO` + `OCF_TO_REVENUE`; AR
spike fires `RECEIVABLES_VS_REVENUE` + `DSO`; margin hit fires `GROSS_MARGIN`.

### 2e. Validation — `validate/harness.py`, `backtest.py` (rewritten)

- `backtest.py` calls `signals_v3.evaluate(..., as_of=cutoff)` — the same
  function production calls. No separate backtest path.
- Censoring detection: firing at the first cutoff where a filer was scoreable at
  all is excluded from the median lead, not credited as a 42-month lead.
- `make_split()` writes a stratified split with a SHA-256 over its contents;
  `verify_split()` raises if edited after commit. Pre-commit hook enforces it.
- `write_prereg()` refuses to overwrite.

### 2f. Wiring — `cli.py` (new)

`universe`, `backfill`, `coverage`, `regimes`, `cases`, `scan`, `score`,
`split`, `prereg`, `validate`. `scan` writes to `runs` (previously written by
nothing) and exits after one HTTP request on a quiet day. `validate --split
holdout` exits non-zero on KILL so it can gate CI.

---

## 3. The three decisions

### 3.1 Pre-2009 regimes → **dropped; regime requirement redefined**

The SEC XBRL mandate phased in by filer size: large accelerated filers with >$5B
float for periods ending after 2009-06-15, remaining large accelerated filers
after 2010-06-15, everyone else after 2011-06-15. There is no contemporaneous
XBRL for dotcom or GFC, and coverage is not universal until 2011. Sourcing
pre-2011 fundamentals from a vendor would mean two ingestion paths with
different revision semantics, to validate regimes the product will never
operate in.

**The trap worth naming:** `companyfacts` *does* return pre-2011 figures,
because filers include comparative prior periods in later filings. Those facts
carry the **later** filing's `filed` date, so `edgar.as_of()` correctly hides
them from earlier cutoffs. The system fails safe — pre-2011 cutoffs return
`scoreable=False` rather than a wrong answer. But it means anyone eyeballing
`companyfacts` and concluding "we have 2006 data" is wrong in a way that only
shows up as a silently optimistic backtest.

Replaced "≥3 regimes including dotcom/GFC" with **≥4 regimes drawn from
2011–2025**. `universe.REGIMES` defines six:

| regime | window | why it's in the set |
|---|---|---|
| `2014-16-energy` | 2014-07 – 2016-06 | Oil ~$100 → ~$26. Shale, services, offshore, mining. |
| `2015-18-retail` | 2015-01 – 2018-12 | Bricks-and-mortar and consumer brands losing share. |
| `2017-19-idiosyncratic` | 2017-01 – 2019-12 | **No macro tide.** Single-name breaks only. |
| `2020-covid` | 2020 | Demand shock both directions; violent working-capital moves that then reverted. |
| `2021-22-growth-unwind` | 2021–2022 | The original eight cases. |
| `2023-25-rate-shock` | 2023–2025 | Refi walls, CRE, leveraged names. |

`2017-19-idiosyncratic` is the most informative one. **A gate that only fires
when the whole market is falling is a beta detector**, and the original
eight-case set could not have detected that failure mode because every case came
from a single broad selloff. Regime breadth is now a pass/fail check in
`verdict()`, not a footnote.

**Second admission rule, added:** financials, real estate and REITs are excluded
(SIC 6000–6599, 6700–6799). Every tracked diagnostic — DSO, DIO, inventory,
gross margin, deferred revenue, accruals over assets — assumes an operating
company. A random Russell 3000 draw is roughly 20% financials, which would be
either silently unscoreable or scoreable and meaningless. Unknown SIC is also
inadmissible.

Consequence: first genuinely scoreable cutoffs land ~2013 for large accelerated
filers, later for the tail. `universe.cutoffs_for()` starts each filer's loop at
**its own** scoreable date rather than a fixed calendar year — which is what
makes censoring detection meaningful. A fire on the first cutoff now genuinely
means "as early as this filer could be assessed."

*Implemented:* `universe.py` — `XBRL_FLOOR`, `scoreable_from()`, `regime_for()`,
`sic_excluded()`, `admit()`, `cutoffs_for()`. 10 tests.

### 3.2 The false-positive label → **fundamental deterioration, not price**

The placeholder was "≥30% drawdown versus sector within four quarters."
Rejected for three reasons:

1. **It tests the wrong claim.** The product's claim is that a filer's
   accounting is breaking from its own pattern ahead of visible deterioration.
   Labeling on price makes this a return-prediction model, which has to clear a
   much higher bar — factor exposure, transaction costs, capacity — and which
   these diagnostics were never designed for. It also changes what the product
   *is*, and what it would have to be sold as.
2. **Its base rate is unstable across time.** A large fraction of the market fell
   30% versus sector during 2022. The same threshold means something entirely
   different in 2017, which corrupts exactly the cross-regime comparison §3.1
   exists to enable.
3. **It cannot produce a control group.** Hand-picking eight names remembered as
   blowups is hindsight selection, full stop.

**Replacement:** a **fundamental deterioration event** — within the following
four quarters, **at least two of five** criteria trip:

| criterion | threshold |
|---|---|
| `REVENUE_DECEL` | YoY growth drops ≥15pp below the filer's own trailing-4Q norm |
| `MARGIN_COLLAPSE` | gross margin falls ≥5pp YoY |
| `OCF_NEGATIVE` / `OCF_HALVED` | TTM operating cash flow turns negative, or halves YoY |
| `IMPAIRMENT` | impairment charge ≥5% of total assets |
| `RESTATEMENT` | 10-K/A or 10-Q/A touching revenue, OCF or net income |

Requiring two means the filer is breaking in more than one place at once. One
soft quarter is noise.

Why this is better: computable from the same pipeline; no price or sector-return
series; regime-stable; and it directly tests the actual claim. Critically, it
lets the positive and control sets be **generated across the whole admissible
universe** instead of curated — `harness.build_cases()` walks every filer, and
`label.first_deterioration()` derives the `broke` date from filings rather than
from memory. Hindsight selection and survivorship both disappear in one move.

Two details that matter:

- **Labels may look forward. Only the scorer is point-in-time.** An outcome that
  hasn't happened yet is not an outcome. `label.label()` builds its horizon from
  quarters *filed after* the cutoff, so the label can't leak into its own
  scoring window.
- **Lead is measured to the filing date, not the period end.** A quarter ending
  3/31 isn't public until it's filed. `label.broke_date_filed()` handles this;
  measuring to period end would have inflated every lead by 4–6 weeks.

Price drawdown stays as a **reported secondary statistic** — `price_drawdown()`
exists and its result appears alongside the verdict. It is not in the rule, and
a test asserts it never enters any criterion key or threshold.

*Implemented:* `label.py` — 5 criteria, `label()`, `first_deterioration()`,
`broke_date_filed()`. `impairment` added to `METRIC_MAP`. 11 tests.

### 3.3 Python vs TypeScript → **Python through Phase 4; no port**

Staying Python. Three reasons: the work is numerical and statistical, where the
library ecosystem is decisive; the whole system is a batch job on a daily cron,
not a request-response service, so it never sits in a latency path; and the
ingestion layer already works — a port before validation is spending days to
find out whether a thing you haven't tested is worth having.

At Phase 5 the boundary is a **JSON contract, not a rewrite**. Python stays the
compute worker and emits signal records; a Fastify + Prisma service reads and
serves them. That keeps the house stack at the edge where the rest of the
portfolio integrates, without moving the statistics into a language that makes
them harder to write. Revisit only if the delivery layer starts needing to
recompute rather than read.

---

## 4. Phase 0 — find out whether the signal exists

The code is fixed. The signal is still unproven. Nothing below is worth an hour
until this returns a verdict.

**0a. Backfill and re-measure.** `ledgerline backfill && ledgerline coverage`
across the existing 19-name universe. Confirm the OCF series is dense where it
was two-points-per-year. Delete `data/eval.json`, `data/backtest.json`,
`data/backtest_v2.json` — all pre-fix artifacts.

**0b. Expand the universe.** The case set is generated, so this is a universe
problem rather than a curation problem: pull S&P 1500 constituents, run
`ledgerline cases`. Admission rejections are printed with reasons — read them,
because a systematic rejection pattern is itself a finding.

**0c. Check readiness.** `ledgerline cases` prints PASS/FAIL against ≥40
positives, ≥200 controls, ≥4 regimes. `ledgerline split` refuses to run until
all three pass, so a threshold can never be fit to a set that couldn't have
satisfied the rule anyway.

**0d. Split, then commit.** `ledgerline split --seed N`. 60/40, stratified by
regime and by positive/control. Commit `split.json` with its hash in the commit
message before touching any threshold.

**0e. Pre-register.** `ledgerline prereg`, then commit:

> Passes if, on the **holdout**:
> 1. False-positive rate ≤ 10%, and
> 2. median lead ≥ 6 months with ≥60% of positives firing before the break, and
> 3. ≥4 regimes represented among positives, and
> 4. ≥40 positives and ≥200 controls, and
> 5. false-positive rate strictly below the naive baseline "TTM OCF negative and
>    net debt positive."
>
> **Kill condition:** any failure → the deterministic gate is not a viable
> product core and the project stops. A kill is a valid outcome and the finding
> gets written up.

**0f. Fit on tuning only.** Weights by logistic regression against the labeled
outcome; `Z_TRIGGER`, `SCORE_DIVISOR`, `THRESHOLD` chosen on the tuning split.
Report the coefficients. Remove the UNCALIBRATED labels.

**0g. Score the holdout once.** `ledgerline validate --split holdout`. Ship or
kill. Do not retune and re-run against this holdout.

Deliverable: one report.

---

## 5. Phase 1 — ingestion hardened *(only if Phase 0 passes)*

- `scan` promoted to a scheduled job with full run bookkeeping.
- `backfill` resumable across the full universe, honoring the cache.
- Amendment handling: 10-K/A and 10-Q/A trigger recompute of affected periods
  and emit a `restated` event rather than silently overwriting. A restatement
  that would have changed a published signal is itself a signal — and it is
  already one of the five deterioration criteria.
- Provenance surfaced end to end. Every score traces to accessions; a reading
  with high `derived_fraction` gets labeled rather than silently emitted. Same
  discipline as Genesis's CALIBRATED/ESTIMATED/INVENTED gate.

## 6. Phase 2 — metric layer

- Segment-level revenue where filers tag it.
- Fiscal-calendar normalization for 52/53-week filers (AAPL, NVDA, CVNA). The
  ±1-month match in `yoy_at()` is a heuristic that will misfire on 53-week years.
- SIC-based peer sets (`universe.fetch_sic` already pulls the code).
- Coverage dashboard: which filers are scoreable, and why not.

## 7. Phase 3 — scoring

- Peer-relative overlay (`peer_z` is built, unwired) — within-SIC, so a
  sector-wide inventory build does not fire on every name in the sector.
- Persist every emitted signal with its full flag payload for Phase 6 scoring.

## 8. Phase 4 — narration (the only LLM in the system)

- Trident call, cost-gated: narration runs only on gated-in events, which the
  Phase 0 numbers must show is a handful per day across the universe.
- Hard constraint: the model receives computed diagnostics and writes prose
  about them. It never computes a number and never decides whether to fire.
- Schema-constrained output through Flint; validate-then-repair.
- Every claim must map to a diagnostic in the payload. Assertions without a
  backing diagnostic fail the response and trigger a repair pass.

## 9. Phase 5 — delivery

- JSON contract boundary (per §3.3): Python emits signal records, Fastify +
  Prisma + Postgres on Railway reads and serves them. No port.
- Daily digest email; per-filer webhook.
- Signal history append-only. A published signal is never edited — a revised
  view is a new signal referencing the prior one.

## 10. Phase 6 — track record, Phase 7 — scale

- Every emitted signal scored forward automatically at +1/+2/+4 quarters against
  the same deterioration label used in validation, so live performance and
  backtest performance are measured on one definition.
- Reliability diagram and Brier score, same surface as Vantage's calibration
  panel. Track record public inside the product. If live hit rate decays below
  the Phase 0 holdout numbers, the gate is retuned or pulled.
- S&P 1500 → Russell 3000. Cost per run should stay flat in universe size, since
  Tier 0 is one daily-index request regardless. Verify empirically before
  scaling; the 24-month performance gate applies.

---

## Open items

None blocking. Two to revisit at their phase:

1. **Fiscal-calendar handling** (Phase 2) — the ±1-month YoY match is a
   heuristic. 53-week years will misfire. Not blocking Phase 0, since it affects
   positives and controls symmetrically.
2. **Delivery-layer language** (Phase 5) — revisit the Python/TS boundary only if
   the API starts needing to recompute rather than read.
