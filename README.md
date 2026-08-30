# Ledgerline

Ledgerline reads companies' official filings to the SEC and looks for a company
whose numbers have started behaving unlike that same company's own past. Not
"is this number big" but "is this unusual for *them*". When something looks
off it says so, shows the arithmetic, and links back to the filing the numbers
came from.

**Status: tested and failed — KILL, 2026-08-30.** The detection method was
scored once against a standard written down and committed to git before the
test ran. It caught 28.7% of the deteriorations it was built to warn about;
the bar it had set for itself was 60%. And it raised false alarms on 3.83% of
quiet company-quarters, where the crude two-line rule it had to beat raises
0.51%. Two of six criteria failed, so the pre-registered answer is no. Full
write-up: `reports/PHASE0.md`; the frozen numbers every score-showing command
reads live in `ledgerline/data/phase0.json`.

What *is* solid is everything underneath: point-in-time discipline (the tool
only ever uses figures that had actually been filed by the date it is asked
about), a test set generated from the data rather than hand-picked, and a
sealed test half scored exactly once. Every number is published, so anyone can
check the failure.

**Getting started:** see `docs/RUNNING.md`. The short version:

```bash
./bootstrap.sh                       # once: venv, packaging, git hooks
# put a real contact address in .env  (the SEC requires one)
ledgerline watch --add AAPL,MSFT
ledgerline fetch
ledgerline explain AAPL
```

## Repo setup

```bash
mkdir ledgerline && cd ledgerline
curl -sO <this-bootstrap-url>/bootstrap.sh   # or copy it in
chmod +x bootstrap.sh && ./bootstrap.sh

# edit .env: LEDGERLINE_UA must carry a real contact address (SEC fair access)

gh repo create wpf002/ledgerline --private --source=. --remote=origin
git add -A
git commit -m "chore: bootstrap infrastructure"
git push -u origin main
```

Without `gh`:

```bash
git init && git branch -M main
git remote add origin git@github.com:wpf002/ledgerline.git
git add -A && git commit -m "chore: bootstrap infrastructure"
git push -u origin main
```

## Architecture

Four tiers, ordered by cost. Each one exists to keep the next one from running.

| tier | job | cost |
|------|-----|------|
| **0 — change detection** | One SEC daily-index request returns every filing accepted market-wide that day. Filter to the universe locally. | 1 request/day, flat in universe size |
| **1 — metric extraction** | XBRL `companyfacts` to a normalized metric dict. Immutable per accepted filing, so cached permanently. | 1 request/filer/filing |
| **2 — diagnostics** | Derived ratios: accruals, cash conversion, DSO/DIO, deferred-vs-revenue, leverage. Pure arithmetic. | zero |
| **3 — the gate** | Robust z against the filer's own history. Decides the small number of events worth narrating. | zero |
| **4 — narration** | Trident writes prose about already-computed diagnostics. Never computes a number, never decides whether to fire. | only on gated-in events |

Tier 0 is the cost architecture: it replaces N per-company polls with one
flat-file read regardless of universe size. On a quiet day it returns nothing
and the run exits before anything downstream executes.

## Invariants

- **Point-in-time or nothing.** Every fact is truncated by the XBRL `filed`
  date, never by period end. A quarter ending 3/31 filed 5/10 is invisible on
  4/30. Backtest and production share one code path; if they diverge, the
  backtest measures nothing.
- **The gate is deterministic.** No model output enters a scoring decision.
  Tier 4 receives computed diagnostics and writes about them.
- **Provenance on every number.** Each metric row carries its source concept,
  form, accession, `filed` date, and whether it was reported or derived. A score
  traces back to accessions or it does not ship.
- **No wrong numbers.** A TTM that cannot be computed from four contiguous
  quarters returns `None` and excludes the filer, rather than summing whatever
  four rows happen to be at the end of the list.
- **Thresholds are fit on the tuning split only.** The holdout is scored once.
- **Outcomes are labeled on filings, not prices.** The claim is about accounting
  divergence, so the label is a fundamental deterioration event observable in
  later filings. Price is reported alongside, never gated on.
- **Cases are generated, not curated.** Positives and controls are derived across
  the admissible universe, so there is no hindsight selection step to get wrong.
- **A kill is a valid outcome.** The Phase 0 decision rule is committed to git
  before the test runs.

## Layout

```
ledgerline/
  edgar.py            Tier 0/1 — daily index, throttle, cache, XBRL normalize
  derive.py           quarterly flow derivation from YTD cumulatives
  universe.py         admission rules — XBRL era, regimes, sector exclusion
  label.py            fundamental deterioration labeling (outcome side)
  signals.py          Tier 2 — diagnostics
  signals_v3.py       Tier 3 — robust z gate
  backtest.py         validation driver (single code path with production)
  cli.py              universe / backfill / coverage / scan / score / validate
  validate/
    harness.py        generated cases, splits, pre-registered decision rule
  narrate/            Tier 4 — Trident/Flint, Phase 4 only
  api/                Fastify-side contract, Phase 5
  data/               sqlite state + EDGAR cache (gitignored)
tests/
  fixtures/           recorded EDGAR payloads so CI runs without network
reports/              backtest + validation output
```

## SEC fair access

`LEDGERLINE_UA` must identify the operator with a working contact address, and
requests are throttled to ~9/sec. Both are enforced in `edgar.fetch()`. Getting
blocked means retries, and retries are a cost leak, so this is a correctness
concern rather than a courtesy.

## What this is not

Not investment advice, and not a system that decides anything on its own.
It surfaces where a filer's numbers break from that filer's own established
pattern, with the arithmetic shown. What that means is the reader's call.
