# Competitive scan: forensic-accounting signal vendors

Run 2026-08-30 across 40 sources, 38 retrieved. Eight parallel readers, three
synthesis passes, one completeness critic. Vendor claims are recorded as
claims; nothing was accessed behind a login, so UI findings describe published
documentation, not observed product behaviour.

## The market in one paragraph

Nobody in this set publishes a holdout, a pre-registration, a confusion
matrix, or a false-positive rate — and nobody appears to lose deals for it.
Five of the six vendors with a score label outcomes on price. Every vendor
contradicts itself across its own pages. Ledgerline has the market's best-
specified epistemology and (pre-KILL) its worst product.

## They do it, we don't

### Read filing narrative text and footnotes

**Who:** Hudson Labs (score is text-only), New Constructs (footnote-derived unusual items), Calcbench (Interactive Disclosures search), Transparently (14 clusters over statements + governance text)

**Should we:** Not before Phase 0 returns a verdict, and probably never as an input to the gate. The right move is a bounded answer to the footnote critique in writing: (a) pull footnote-level XBRL tags where filers provide them, (b) argue that abnormality-of-change partially cancels a constant undetected adjustment, (c) argue derived diagnostics (accruals, cash conversion) catch the consequence of items never parsed. If narrative ever enters, it enters at Tier 4 as context, never as a scoring input.

New Constructs' footnote argument is the single most specific technical attack
on an XBRL-only design in the whole scan. It is not marketing. Having no
written answer is a live liability.

### Ingest the 'says' half — earnings call transcripts, press-release KPIs, guidance, non-GAAP reconciliations

**Who:** Calcbench (earnings release data within minutes of the wire), Hudson Labs (transcripts, presentations, tone signals: 'confidence, stress, deflection'), Transparently (news context in Luca)

**Should we:** Either build a narrow, deterministic slice of it (non-GAAP-to-GAAP reconciliation deltas, which are tabular and partially tagged) or change the positioning. Do not claim say-vs-file divergence detection while comparing XBRL to XBRL.

Ledgerline's one-line description is 'divergence between what a company says
and what its SEC XBRL filings show'. Today it only reads the filings. The
current product is an internal-consistency detector across a filer's own
tagged history — a defensible thing, but not the thing the positioning claims.

### Objective non-financial risk facts: auditor turnover, executive departures, material weaknesses, going-concern warnings, non-timely filings, SEC comment letters, restatement history

**Who:** Hudson Labs (most complete), Calcbench (comment letters, auditor fees & flags dataset), Transparently (Corporate Governance cluster)

**Should we:** Yes, and soon after Phase 0. Item 4.02 (non-reliance) and NT filings are events, not judgments — they fit the deterministic gate cleanly. Restatement is already one of the five deterioration criteria, so the labeling machinery half-exists.

This is the layer Hudson Labs' own guide implies actually convinces sceptical
analysts, and it is cheaper for Ledgerline to build than for a text-model
vendor to make deterministic.

### Governance, ownership concentration, board structure, related-party transactions, insider activity

**Who:** Transparently (Corporate Governance is 1 of 14 clusters), Fidelity (5 of its 6 forensic pillars are non-accounting), Hudson Labs (Governance, Related-Party categories)

**Should we:** No, not as gate inputs. Form 4 insider selling could be a cheap reported-alongside statistic, the way price drawdown already is. Beyond that this is a different product.

Fidelity's six pillars are the honest correction to Ledgerline's language: the
accurate claim is coverage of the accounting pillar, not 'forensic analysis'.

### Coverage breadth — universe size and history depth

**Who:** Transparently (85,000 companies, 100+ countries, early 1990s), Hudson Labs (10,000+ US issuers + 1,400 ADRs, from 2019), Calcbench (12,000+ live filers, 2009-present), New Constructs (~3,000), CFRA (25,000+ quantitative / 140 qualitative), MSCI (37 licensed indexes, global)

**Should we:** Yes on breadth, after Phase 0. Never claim history depth — state the 2011 floor as a hard constraint and stop describing six regimes as if they spanned dotcom or the GFC.

Breadth is the one competitive axis where Ledgerline's cost structure is
genuinely favourable. Depth is permanently lost.

### Financials, banks, insurers and REITs in the covered universe

**Who:** Transparently claims 85,000 listed companies but its FAQ excludes 'Banks and insurers'; Hudson Labs excludes financial services; New Constructs and CFRA do not state an exclusion

**Should we:** No. But stop treating the exclusion as a neutral scoping decision; it is a coverage hole in the most fraud-dense sector, and it should be disclosed as such rather than justified.

This is a shared industry blind spot, which means it is neither a
differentiator against them nor an excuse.

### Peer and sector-relative context

**Who:** All of them. Transparently (rank percent, rank percent within sector, cluster quantiles), New Constructs (cross-sectional deciles), MSCI (rank against parent index constituents), Sabrient (peer quintiles), CFRA (sector-relative)

**Should we:** Yes, but strictly as an overlay, never as the gate. The stated reason is right: a sector-wide inventory build should not fire on every name in the sector. Deciling would also destroy the core differentiator and impose a fixed alert rate by construction.

Peer context answers a question own-history z cannot: 'is the whole industry
doing this'. That is a suppression signal, not a firing signal.

### Any user interface at all — a web dashboard, a company page, a score with drill-down

**Who:** Transparently (Dashboard), Hudson Labs (4-tab company view, forensic score in the Red Flags tab), Calcbench (Company Detail, Multi-Company grid), New Constructs (Portfolio table, Ratings page), CFRA (Institutional Research Portal), MSCI (index table)

**Should we:** Yes, and the drill-down shape is already determined by the architecture: score → per-flag z with baseline_median, baseline_scale, baseline_n, floored → accession. That is a strictly deeper evidence chain than any competitor shows.

Every competitor's evidence layer stops at a signal name or a text span.
Ledgerline's stops at a filing. That advantage is invisible without a screen.

### Watchlists as the organizing primitive

**Who:** Hudson Labs (first action on signup: CSV upload or manual tickers; alerts, feeds and screens all filter through it), CFRA (custom watchlists in the portal)

**Should we:** Yes. It is the cheapest single thing that converts a universe scanner into something a PM will actually open, and it fits Tier 0 exactly (one index request, filter the result).

Note the structural difference: their watchlist exists because per-company
processing is expensive. Ledgerline's watchlist would be a view, not a cost
gate — which means it can also offer the whole-universe screen they cannot
cheaply offer.

### Screening and ranked screens

**Who:** Hudson Labs (11 named risk screens, $300M market-cap floor, including the composite 'High risk score + price run-up'), CFRA (Hazard List → Biggest Concerns), New Constructs (screening tools), Sabrient (EQR quintile files for universe screening)

**Should we:** Yes, but as a fired-events list with an explicit unscoreable count shown alongside, not as a full-universe ranking. Do not manufacture a rank by treating unscoreable as zero — that was the exact v2 defect (score=0.0 indistinguishable from 'assessed, looks clean').

Honesty about coverage is more differentiating here than completeness.

### Alerting and push delivery

**Who:** Hudson Labs (email alerts on event classes: earnings call, material weakness, executive turnover, non-timely filings, restatements, going concern, SEC comment letters; plus 4-step agent wizard with triggers), Transparently (daily updates), Calcbench (filing alerts via API add-on)

**Should we:** Yes, and lead with the alert nobody offers — 'this filer's diagnostic went abnormal against its own history, here is the z, the baseline and the accession'. That is a score-movement alert with evidence attached.

This is the clearest product-shaped hole in the competitive set, and it
happens to be exactly what a per-filing own-history gate naturally emits.

### Integrations and distribution surfaces — API, Excel add-in, MCP server, index licensing, ETF/UIT packaging

**Who:** Calcbench (Excel add-in, API, Python client), Hudson Labs (MCP server exposing coverage inside Claude), New Constructs (API, Excel add-in, Bloomberg index BCORET:IND), Sabrient (UITs, ETFs), MSCI (licensed indexes)

**Should we:** MCP: yes, cheap and on-strategy. Excel: yes eventually — it is where analysts actually work. Index/ETF: no, and note that route requires price-based performance, which the filing-based labeling decision deliberately forgoes.

The MCP path is disproportionately cheap given the JSON contract is already
the planned boundary.

### LLM narration and conversational assistant over computed diagnostics

**Who:** Transparently (Luca), Hudson Labs (Co-Analyst, continuous feeds with pre-written summaries), CFRA and Sabrient (human-written analyst notes)

**Should we:** Yes, and it is more urgent than the roadmap treats it. This is the layer buyers actually consume; the deterministic layer is the layer they trust.

Every competitor's evidence-under-the-score is prose. Ledgerline has built the
layer nobody sees and deferred the layer everybody buys.

### Interpretation layer — letter grades, bands, prescriptive next steps

**Who:** Hudson Labs (1-100, >70 high risk, 60-70 medium-high), Transparently (A+ to F rating plus 0-100 score, plus audit procedures and literal CFO questions), New Constructs (Strong Beat / Beat / In line / Miss / Strong Miss)

**Should we:** Not until after calibration. Prescriptive next steps (what to check in the next 10-Q) are a legitimate Tier 4 output because they follow from the fired diagnostic, but no letter grade before a reliability curve exists.

Shipping a band before calibration would be the exact failure mode the roadmap
already diagnosed in v1 and v2.

### Published pricing and a self-serve motion

**Who:** Hudson Labs ($119/month, 14-day trial, cancel anytime), Transparently ($499/month Starter, credit-card activation in minutes), Calcbench ($6,000 and $12,000 per user per year, free Basic tier)

**Should we:** No — this is out of scope for an unvalidated signal. Recorded because the price points bound the commercial ceiling: the entire normalization layer (Tier 1) is worth roughly $6-12k per seat per year as a standalone product, and a scored risk grade sells for $119-499 per month.

Useful as a calibration on what any of this is worth, not as a near-term
action.

## We can, they don't

### Point-in-time vintage discipline enforced as a system invariant — every fact is a sequence of vintages, as_of() selects the newest vintage filed on or before the cutoff, and backtest and production call the same function

Why they can't copy it: Three separate structural locks. (1) Hudson Labs'
score is 'machine learned, not additive' and gets retrained — a historical
score displayed today is today's model re-run over old text, and making it
vintage-correct means throwing away the displayed score history that is their
drill-down. (2) New Constructs' unusual-items dataset comes from 'human-
assisted ML'; a human-assisted extraction cannot be regenerated from source on
demand, so the historical record is whatever the reviewers recorded, and a
later correction silently rewrites it. (3) Commercially, any incumbent that
adopts strict as-of truncation must implicitly concede its existing backtest
was contaminated. That reputational asymmetry is why the gap persists even
though the technique is not secret.

How to use it: Publish the vintage mechanic as a reproducible demonstration:
take one filer whose 2012 quarter was later restated, show the 2012 value the
system serves at a 2012 cutoff versus the restated value companyfacts returns
today, and show what a naive backtest would have concluded. That single worked
example is falsifiable, cheap, and no competitor can produce its equivalent.

### A deterministic gate an auditor can re-derive by hand — robust z (median/MAD with a per-metric scale floor) against the filer's own trailing history, with every flag carrying z, baseline_median, baseline_scale, baseline_n and floored

Why they can't copy it: Their differentiation is the model. Transparently's
score is 'a complex non-linear amalgamation of patterns'; Hudson Labs' is
'machine learned rather than additive. It reflects how multiple risk signals
interact'; New Constructs' formula is disclosed but sits on an undisclosed
human-assisted extraction. Making the gate hand-checkable would (a) make it
trivially copyable, since the diagnostics themselves are textbook, and (b)
remove the 'AI' that is doing the selling. Both Hudson and Transparently also
market explainability while their explanation layer is a post-hoc attribution
from an explicitly non-additive model — nobody discloses how the eight or
fourteen category contributions are derived, and that is a hard problem they
have chosen not to solve.

How to use it: Ship the arithmetic. Publish the full gate — weights,
Z_TRIGGER, THRESHOLD, scale floors — after calibration, and challenge a reader
to reproduce one fired signal in a spreadsheet from the published accessions.
The copyability risk is real and worth accepting: the disclosure is the
product claim.

### Provenance to source concept, form, accession, filed date, and reported/derived/summed on every number

Why they can't copy it: Their inputs foreclose it. Hudson Labs' numeric layer
is licensed from S&P Global Market Intelligence, so the numbers carry vendor-
defined normalization and no filer-level lineage; its score reads narrative
text, where the finest available unit is a text span. New Constructs
attributes at figure level only ('Sources: New Constructs, LLC and company
filings') even on the page enumerating 26 adjustment categories, and sells
traceability as a separate SKU ('Marked-Up SEC Filings') rather than as an
attribute of the score. Transparently's explainability cashes out as a cluster
name in prose. Calcbench has genuine cell-to-filing traceability — but has no
score to attach it to.

How to use it: Make the accession the unit of the evidence panel, not a
footnote. And use the reported/derived/summed label as a first-class
disclosure — derived_fraction on every reading is a form of honesty nobody
else offers, and it is more credible than a citation because it discloses
weakness rather than confidence.

### A pre-registered decision rule with a written KILL condition committed to git before the test runs, and a holdout scored exactly once

Why they can't copy it: A company with customers, revenue and investors cannot
pre-register a test it might fail and then publish the failure. The asymmetry
is total: passing buys marginal credibility, failing destroys the product.
Every incumbent in this scan has already shipped, which means the decision to
pre-register is permanently behind them. This is the one advantage that is
genuinely unavailable to them at any price.

How to use it: Publish the pre-registration and the split hash before Phase
0g, publicly and dated, and publish the result either way. The kill write-up
is a more differentiating artifact than a pass, because no competitor has ever
published one.

### Outcome labels defined on filings, not on prices — at least 2 of 5 criteria (revenue deceleration, margin collapse, OCF negative or halved, impairment ≥5% of assets, restatement) within 4 quarters

Why they can't copy it: Their buyers pay for return prediction, so price is
both the natural label and the natural proof. Bedrock's decile spread,
Transparently's 'shorter-term security returns', New Constructs' Bloomberg
index and MSCI's index levels are all price-based by construction. Switching
to filing-based labels would require the derivation pipeline Ledgerline built
and would produce a result that does not map to a return number they can sell.

How to use it: Use it as the answer to the factor-contamination objection,
which is the strongest attack on every published competitor result. A filing-
based label cannot be a disguised size or momentum trade.

### Abstention — scoreable=False with a written reason, and ttm() returning None rather than summing four non-contiguous quarters

Why they can't copy it: Their product surface requires a value for every row.
A 0-100 score on 85,000 companies, a decile rank on the full universe, an
index that must select constituents at every rebalance, and a screen that must
populate — none tolerates a filer that returns 'cannot assess'. New
Constructs' deciling in particular guarantees exactly 10% of the universe is
Strong Miss every quarter regardless of whether accounting quality is
deteriorating; the alarm rate is an artifact of the ranking method. Abstention
breaks that by construction.

How to use it: Surface the unscoreable count next to every scan result rather
than hiding it. 'We assessed 1,102 of 1,496 filers this quarter and here is
why the other 394 were not scoreable' is a stronger trust claim than any
coverage number in the scan — and it is the direct inverse of Transparently's
'85,000 companies' framing.

### Generated-not-curated case sets across the whole admissible universe — 483 positives, 464 controls, 6 regimes, built by walking every filer

Why they can't copy it: They have no filing-based label, so they cannot
generate cases; they can only select them. Without a computable outcome
definition, the case set is whatever a human remembers as a blowup, which is
hindsight selection by construction. Building the label requires the derived-
metric pipeline, which requires the vintage discipline, which they do not
have.

How to use it: Report the control set as prominently as the positives. The
number nobody in this scan publishes is how often their signal fires on a
company that turns out fine.

### Abnormality against the filer's own trailing history rather than cross-sectional rank

Why they can't copy it: Cross-sectional ranking is load-bearing in their
product architecture, not just their statistics. MSCI must select constituents
from a parent index at each rebalance. New Constructs' five tiers are defined
as decile bands. Transparently's Rating is explicitly 'derived from the Risk
Scores, and how those lie relative to the sector and market'. Switching the
baseline to per-filer history would break the index products, the fixed-size
screens and the sector-relative rating simultaneously. Their own bias post
concedes the learned baseline drifts with accounting regime changes (ASC 606,
IFRS 16/ASC 842) and that peer percentiles do not fix it — because a standards
change moves the whole cross-section at once.

How to use it: State the question precisely and repeatedly: not 'is this
number big' but 'is this abnormal for this filer'. Then show the failure mode
it fixes — a chronically high-distortion filer sits permanently in Strong Miss
under deciling and never generates a change signal.

### Tier 0 change detection at cost flat in universe size — one SEC daily-index request returns every filing accepted market-wide that day

Why they can't copy it: Their pipelines are per-document. Hudson Labs
processes SEC filings 'once every three hours' — a polling cadence, and their
detection latency is bounded by it. New Constructs' human-assisted extraction
has a per-filing marginal cost that is linear in filings, which is almost
certainly why coverage stops near 3,000 names. Transparently 'updates daily'
across 85,000 companies with no cost model described. None of them can scan a
universe for free on a quiet day.

How to use it: Do not sell Tier 0. Spend it — on breadth (Russell 3000), on
whole-universe alerting rather than watchlist-only alerting, and on running
every day rather than quarterly.

## Uncomfortable truths

1. The market does not reward methodological rigour and this scan is direct
   evidence of it. Transparently sells at $499/month with no model family named
   on any page, a headline $1T loss figure it self-labels as 'based on our
   internal calculations', and the only named quote on its entire site coming
   from its own CEO. Hudson Labs sells at $119/month while naming three different
   outcomes for one score across three pages (class action litigation, SEC
   enforcement, 'regulatory scrutiny and significant stock drawdowns') and
   contradicting itself on selectivity (<6% versus <10% of mid-caps above 70).
   Both have paying institutional customers. Nobody in this scan appears to have
   lost a deal for lack of an AUC, a holdout, or a false-positive rate.
   Ledgerline is building the thing the market has demonstrated it does not
   check.

2. Distribution is the actual moat and Ledgerline has none of it. Bedrock reached
   $25B in client AUM within three months of launch on word-of-mouth and Twitter,
   from a founder who was a CPA and ex-KPMG with a name in the space.
   Transparently has Franklin Templeton money and anonymized Big 4, sovereign
   wealth fund and top-10 bank customers. CFRA has an institutional research
   channel and named clients. MSCI licenses indexes to ETF issuers. Every buyer
   segment named in this scan — activist shorts, long-short funds, pension
   compliance desks, auditors, regulators — is reached through a relationship,
   not a landing page. Nothing in the Ledgerline plan addresses this, and no
   amount of validation substitutes for it.

3. The excluded sectors are exactly where the accounting fraud is. Financials and
   REITs are out (SIC 6000-6599, 6700-6799) for a defensible reason — DSO, DIO,
   inventory and gross margin assume an operating company — but that removes
   loan-loss provisioning, reserve releases, CECL model changes, held-to-maturity
   classification and asset-valuation marks, which is most of the fraud surface
   in the sector that produced Wirecard, Credit Suisse, SVB and Archegos.
   Transparently and Hudson Labs exclude financials too, so this is an industry-
   wide blind spot, which means it is neither a differentiator nor an excuse:
   Ledgerline both cannot cover it and gets no credit for the gap being shared.

4. Ledgerline reads only half of its own thesis. The product is described as
   detecting divergence between what a company says and what its XBRL filings
   show. There is no structured source for the 'says' half. Guidance, non-GAAP
   reconciliations, KPI disclosures and management commentary are not in XBRL —
   Calcbench parses press releases within minutes of the wire specifically
   because they are not, and Hudson Labs' entire score is text-only. What
   Ledgerline actually built is an internal-consistency detector across a filer's
   own tagged history. That is a real and defensible thing. It is not the thing
   the positioning claims, and the gap between the two is the same claim-versus-
   evidence gap the scan was designed to catch in competitors.

5. The diagnostics themselves are commodity knowledge and Ledgerline should stop
   implying otherwise. Behind the Balance Sheet teaches the detection workflow in
   a one-hour seminar. Sabrient publishes the exact metric list — total accruals
   as net income minus free cash flow, operating accruals as EBITDAS minus CFO,
   AR-to-sales, net AR-to-12M-sales, AFDA-to-gross-AR, inventory-to-sales and to-
   COGS, accrued-expense-to-OpEx — with worked basis-point examples. The
   appraisers chapter gives CRO, AQI, Dechow-Dichev, Lev-Thiagarajan and
   Piotroski as full formulas in a free PDF. Ledgerline's 13 tracked diagnostics
   are not novel and the ratios are not the moat. Only the normalization base,
   the vintage discipline and the abstention behaviour are.

6. The category sells narrative, and Ledgerline has built the layer nobody sees
   and deferred the layer everybody buys. Every competitor's evidence-under-the-
   score is prose: Luca's 'Why:' line and its literal question to ask a CFO,
   Hudson's cited spans and pre-written feed summaries, CFRA's analyst reports,
   New Constructs' grade sitting directly above human Analyst Notes, Sabrient's
   per-company write-up. Tier 4 is unbuilt and is scheduled after Phases 0-3. A
   buyer opening a Ledgerline signal today sees a z-score, a baseline median and
   an accession — technically superior evidence, and considerably less persuasive
   than a paragraph explaining what it means.

7. New Constructs' footnote critique is a specific, cited, technical attack on an
   XBRL-only design and Ledgerline has no written answer to it. Their claim,
   backed by an HBS/MIT Sloan paper, is that material unusual gains and losses
   are buried in narrative footnotes and that databases reading only structured
   data systematically miss them. Note the internal tension in their own
   materials — the same pipeline is branded 'Robo-Analyst' on one page and
   admitted as 'human-assisted ML' on another, and they publish no extraction
   error rate — but the underlying point stands, and 'we compute from tagged
   facts only' is not a rebuttal.

8. The prior evidence on this signal is discouraging and the pre-registered bar
   is far above it. v1 ran at a 24% false-positive rate; v2, which claimed to fix
   that, came in at 28.8% — worse. Read honestly, the eight-case result was three
   real leads, two uninformative, and three fires after the story had already
   broken, at roughly a 29% base rate on controls. The Phase 0 pre-registration
   requires a false-positive rate at or below 10%, median lead of at least 6
   months, at least 60% of positives firing before the break, and a false-
   positive rate strictly below the naive 'TTM OCF negative and net debt
   positive' baseline. The pipeline that produced the bad numbers was genuinely
   broken and is genuinely fixed, so the old numbers are not predictive — but
   nothing has yet been measured on the fixed pipeline, and the distance between
   where the signal was and where the gate is set is large.

9. Own-history normalization has a structural blind spot precisely where fraud
   concentrates, and it is not a bug. MIN_HISTORY of 12 quarters plus 8 non-null
   baseline observations per diagnostic means a filer needs roughly three years
   of clean XBRL before it can be assessed at all. Recent IPOs, de-SPACs and
   hypergrowth names have no trailing distribution to be abnormal against — and
   Bedrock's own decile disclosure says over 10% of its top risk decile were
   SPACs with zero in the bottom decile. Hudson Labs' flagship anecdote is Super
   Micro; Bedrock's are Lordstown and Canoo. The names the category is famous for
   catching are structurally the hardest names for a self-history z to score, and
   cross-sectional ranking, whatever its flaws, does not have this problem.

10. The regime claim is thinner than it sounds. The XBRL mandate phased in
   2009-2011, so there is no dotcom and no GFC, and the six regimes all sit
   inside 2011-2025 — which is one long low-rate expansion, one pandemic shock,
   one growth de-rating and one rate shock. MSCI validates over Dec 2000 to Mar
   2023 with a split-half decay test; New Constructs cites 1999-2025;
   Transparently claims coverage to the early 1990s. Ledgerline's regime breadth
   is real relative to the eight-case v2 set and weak relative to anyone with
   vendor fundamentals. This constraint is permanent and no amount of engineering
   fixes it.

11. Refusing price-based labels was methodologically right and commercially
   expensive. The only named, enthusiastic, fast-moving buyer in the entire scan
   is Chris Drose of Bleecker Street Capital, an activist short seller — and
   activist shorts and long-short funds buy on price outcomes, not on fundamental
   deterioration events. Sabrient's own analyst concedes that earnings-quality
   metrics are 'more useful for short research' and function on the long side
   only as confirmation of an existing thesis, never as a catalyst. Filing-based
   labels make the result honest and make it harder to sell to the people who buy
   fastest. That trade may be correct, but it should be made with open eyes
   rather than treated as costless.

12. Even a clean Phase 0 pass produces one number, not a track record, and one
   number is weaker evidence than what several competitors already have. MSCI has
   25 years of live, licensed, daily-computed index levels — out-of-sample in the
   only sense that fully counts, because the product was public and investable
   while the returns accrued. Hudson Labs has five years of live scoring. A
   holdout scored once is more rigorous per unit of evidence and much smaller in
   quantity, and Phase 6 (forward-scoring every emitted signal at +1/+2/+4
   quarters, reliability diagram, Brier score) is where a real track record
   starts — which is years away and only begins after Phase 0 passes.

13. Nothing in this scan suggests anyone is losing customers over the problems
   Ledgerline solves. There is no evidence of churn caused by unauditable scores,
   contaminated backtests, look-ahead bias or missing provenance. Buyers appear
   to accept a cluster name, a text span, a letter grade and one named winner as
   sufficient evidence. The gap Ledgerline is filling is real, and demand for
   filling it is entirely unproven — which puts the product in the same epistemic
   position as its signal: well specified, carefully argued, and not yet shown to
   work.

## What the scan itself missed

Per the completeness critic: MSCI ESG AGR (a 1-100 accounting-risk score on
~20,000 companies since 2014 — the most credible competitor, absent from the
URL list), LSEG StarMine Earnings Quality (whose US model has a GAAP-vs-non-
GAAP component, partly occupying the say-vs-file gap), Audit Analytics,
terminal built-ins, and the SEC's own Accounting Quality Model. Two pages
failed to load, leaving funding claims unsourced.
