# Ledgerline 1.0 — roadmap

Phases 0 through 7 are built. The detector doesn't work. This file says what
1.0 means given that, and what had to be done before the version number moved.

**Status, 2026-10-03: all four items done.** 502 tests. The one item done in a
weaker form than written: the cold-clone test ran on a fresh clone on the
build machine, not on a second machine.

See `ROADMAP.md` for Phases 0–7 and what each one cut. See `reports/PHASE0.md`
for the test result.

---

## Where 0.1 stands

| | |
|---|---|
| Detector | Failed. Caught 28.7% of deteriorations, needed 60%. False alarms 0.0383 per quiet company-quarter vs 0.0051 for a two-line rule. 51.2% of fine companies flagged at least once. |
| Everything under it | Built and audited twice. 450 tests. Ingestion, point-in-time vintages, provenance to accession, abstention, signal ledger, track record, cost model, filer registry. |
| Surface | CLI, five web pages, JSON contract, CSV import/export. |
| Reserved test set | `r1`. 8,888 company-quarters, hashed 2026-08-30, can be scored from 2028-02-12. |

The measurement machinery works. The thing it measured doesn't. So 1.0 can't
mean "the detector works", and it shouldn't mean "the dashboard looks better".

## What 1.0 means

1.0 is the release where someone else can run a fair test of their own
accounting hypothesis against point-in-time SEC data and get an answer they
can defend.

The product is the apparatus. The detector was the first hypothesis it tested.
It failed, and the apparatus is what proved it failed.

No vendor in `docs/research/competitive-scan.md` does this. They all ship a
score. None ships a way to check one.

### What 1.0 isn't

- A prediction product. No scored feed, no alerts, no recommendations.
- A claim the gate works. The verdict ships on every surface until a reserved
  set says otherwise, and the wording stays "floor to beat", not "grade".
- A hosted service. Loopback only.

---

## Four things to do

### 1.0-a — Make the Phase 0 result reproducible

Right now nobody can re-run it.

- [x] One command rebuilds every published number from the committed split,
      pre-registration and calibration. It has to land on 0.287 / 9mo / 0.0383
      exactly, or fail and say which number moved and by how much.
      **Done: `ledgerline reproduce`.** HEAD can't do this: the audit fixes
      changed 1,291 lines of scoring arithmetic with every constant unchanged.
      So it runs the commit that scored the holdout (`e53912f`) in a throwaway
      worktree. 2026-10-03: all 22 numbers match to the last digit.
- [x] The EDGAR cache is 5 GB and gitignored. A fresh clone can't do anything.
      Fix with either a documented cold path (fetch, takes hours, needs a
      contact address) or a committed fixture subset big enough to reproduce
      the headline numbers.
      **Done: the cold path.** `reproduce` downloads the 387 holdout companies
      itself (about a minute). Those are today's files, carrying filings from
      after Phase 0, so a match also shows point-in-time truncation held.
- [x] CI runs the reproduction against that fixture subset, so an arithmetic
      change breaks the suite instead of waiting for the next audit.
      **Done, differently:** the full reproduction needs the holdout's 387
      companies, too big to commit. CI instead pins the *current* arithmetic:
      ten real companies, 304 KB of trimmed filings, 40 evaluations
      (`tests/unit/test_golden.py`). Outputs that move without a
      `GATE_VERSION` bump fail the suite.

Two audits found 59 reproduced defects. Several changed published numbers. The
`total_debt` double-count predated the whole phase build and nothing caught it.
A reproduction gate catches that class.

### 1.0-b — Let a second hypothesis be registered and tested

The code can only score one gate. It's hard-wired.

- [x] Make a hypothesis a named, versioned object: its diagnostics, its label,
      its pre-registered rule, its reserved set. `signals_v3` becomes the first
      instance instead of the only thing that exists.
      **Done: `ledgerline/hypothesis.py`.** Identified by commit, constants,
      and a hash of its outputs on the golden cases. h0 (the Phase 0 gate)
      and HEAD share every constant and hash differently.
- [x] `ledgerline hypothesis new / register / status`. Register before data is
      drawn. The tool refuses to score anything unregistered.
      **Done.** Registration against r1 closes 2026-11-15, its first
      checkpoint.
- [x] `retest.py` already has an alpha budget. Make it bind across hypotheses,
      not within one. Five hypotheses against `r1` at p<0.05 is one hypothesis
      at p<0.23.
      **Already true** (one global budget); `hypothesis register` is now the
      only way in, and a test pins that registrations draw on it.
- [x] A comparison surface for any two hypotheses on identical terms, with the
      comparability rules enforced by tests. `track.py` already does this for
      live-versus-frozen.
      **Done: `hypothesis compare`.** Results count as comparable only when
      both were scored on the same set, so h0 is scored alongside on every set
      a hypothesis registers against.

This is what makes the thing reusable. It's also the only honest way to get to
a working detector, because the next idea needs a fair test and `r1` can only
absorb one.

### 1.0-c — Measure the three known blind spots

Each is recorded and unacted on. Close them or state the limit in the README
as permanent.

- [x] **Footnotes.** New Constructs argues material unusual items sit in
      narrative footnotes that a structured-data pipeline never reads. It's the
      sharpest attack on this design and there's no written answer. Measure it:
      how much of the deterioration the label catches is visible in tagged
      facts at all. Footnote-level XBRL tags are the cheap partial path.
      **Measured** (`docs/research/footnotes.md`): 92% of company-years tag
      an unusual item, 39% at ≥10% of net income. The gate reads none of
      them. Material items appear before 45% of breaks vs 36% of control
      years. A candidate for h1, not a fix.
- [x] **Survivorship.** 67% thirteen-year attrition (FINDINGS §6e). The missing
      filers skew toward where deterioration ends, so 0.287 is probably biased
      low. `fullindex.py` can build the unbiased universe. Measuring it doesn't
      re-score anything.
      **Measured** (`docs/research/survivorship.md`): 59% of 2014Q1 filers were
      gone by 2024Q1. Sizable leavers deteriorated more (28.6% vs 20.1%) and
      about twice as often severely. Confirms the precondition for the bias,
      not the bias. Also found the registry parser had been reading zero rows.
- [x] **Coverage.** 37.9% hit rate at 11–12 measures vs 20.6% at ≤8
      (`docs/research/recall-hypothesis.md`). Real effect, not big enough to
      explain the failure. It's the obvious shape for a next hypothesis.
      **Measured** before this roadmap was written.

Each one is a reason 0.287 might be wrong in a direction we can name. Shipping
1.0 without measuring them asserts they don't matter.

### 1.0-d — Make it work for someone who isn't me

Everything so far has run on one machine, operated by the person who built it.

- [x] Cold-clone test on a second machine: bootstrap, fetch a small watchlist,
      check, scan, explain, publish, serve. Every step works or the docs are
      wrong.
      **Done on a fresh clone on this machine**, not a second one
      (`scripts/cold_clone_test.sh`). First run found three breaks: bootstrap
      required python3 ≥ 3.11 (macOS ships 3.9), overwrote committed config
      files on every run, and the overview answered 503 on a fresh install.
      All fixed; 18 of 18 now pass. A truly separate machine is still untested.
- [x] `ledgerline doctor`: what's installed, what's missing, what to run next.
      The LaunchAgent and the cron entry are hand-installed and undocumented.
      **Done**, and `ledgerline schedule install` replaces the hand install.
- [x] Move the scheduled scan off cron. cron skips any run where the machine is
      asleep and never catches up. September 2026 lost about half its weekdays
      that way. `launchd` with `StartCalendarInterval` fires on wake.
      **Done**, plus `scan --catch-up`: launchd folds every missed run into
      one, so the scan now reads every daily list since the last completed
      one. The first catch-up recovered 465 revised figures.
- [x] Rewrite the README opening as the honest pitch: what this is, what it
      found, why the failure is worth looking at.

---

## Not in 1.0

| | why |
|---|---|
| Hosting, accounts, billing | It's a loopback tool. |
| Alerts, email, webhooks | Pushing an unvalidated signal distributes a false claim. |
| Russell 3000 | Historical membership is licensed and can't be reconstructed. Scaling a failed gate scales its false positives. `fullindex.py` gives a better universe anyway. |
| Peer suppression | Only removes fires. Recall is what failed. |
| Brier score, reliability diagram | Needs live resolved outcomes. The only data available is the tuning split the probability link was fitted on. |
| Re-scoring the holdout | Spent. Permanently. |

## Test for whether it's 1.0

Can someone who distrusts this project download it, re-derive the failure,
register their own hypothesis, and get a verdict the project couldn't have
rigged?

If yes, it's 1.0.

---

## After 1.0

Two dates matter.

- **2026-11-15: registration against `r1` closes.** Any second hypothesis
  that wants a fair test on `r1` has to be drafted, committed and registered
  before then. The obvious candidates, from 1.0-c: a diagnostic on earnings
  with tagged unusual items removed, and a gate that abstains when too few of
  the thirteen measures are available. After that date a new hypothesis needs
  a new reserved set.
- **Before 2028-02-12: write and commit the `r1` comparison.** Scoring is
  deliberately unwritten (`validate/retest.py`), but it should be fixed in a
  commit before the first outcome resolves, so nobody chooses the analysis
  after seeing the data.

Nothing is scored until 2028-02-12, when `r1` can be. That date isn't a schedule
decision. It's how long it takes for four quarters of filings to resolve an
outcome, times enough of them to have power.

Between now and then: 1.0-a through 1.0-d, plus whatever hypothesis is worth
registering. If the second hypothesis also fails, that's a second finding, and
the apparatus will have produced it the same way it produced the first.
