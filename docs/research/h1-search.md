# Searching for a detector that catches 60%

Practice half only (471 companies). The sealed test half wasn't read. Every
number below is cross-validated by company: the model never sees a company it
is scored on. Five runs with different fold assignments; the range is shown.

## Where things stand

| | caught | false alarms per quarter |
|---|---|---|
| Two-line rule (the baseline) | 5.6% | 0.5% |
| Current detector (h0) | 27.0% | 3.2% |
| New model | 20.4% | 0.5% |
| New model | 44.2% | 2% |
| New model | **56.7%** (53.5–60.9) | **4%** |
| New model | 60.3% (57.7–63.3) | 5% |
| New model | 63.9% (60.0–66.5) | 6% |

"Caught" means flagged at least once in the 24 months before the
deterioration became public. False alarms are counted on companies that never
deteriorated, as in the Phase 0 test.

The new model roughly doubles the catch rate at the same false-alarm rate. It
does not reach 60% at 4%. It reaches 60% at about 5%.

## What the new model reads

All of it comes from the same filings, as they stood at each date.

- The label's own warning signs before they cross their thresholds: how far
  sales growth has fallen below its recent norm, the year-over-year change in
  gross margin, the decline in trailing operating cash flow.
- Levels, not only changes: margin, leverage, cash, capital spending, size,
  and how volatile the company's sales growth has been.
- What the rest of the market and the company's sector look like at that
  date: the median of each sign across companies, and the share showing two.
- How late the latest report was filed against the company's own habit.

Predicting a break within 12 months worked better than 24.

## What didn't help

- h0's thirteen z-scores. Dropping them changed nothing.
- Buybacks, dividends, overhead, interest cover, restructuring and impairment
  charges, credit-line draws, payables, backlog, short-term debt, read
  straight from the tagged facts: 55.2% at 4%, inside the noise of 56.7%.
- A deeper or bagged model.
- The market value each company states in its annual report (public float):
  its year-over-year change, its change against other companies, and its
  ratio to sales. 55.9% at 4%. It's one reading a year, published about eight
  months after it's measured.

Daily stock prices are untested. Stooq, the source tried, serves a bot check
to scripted downloads, so the file has to be fetched by hand.

## Where it misses

By era, at 4%: pandemic 2020 about 55% (it was 20% before market-wide context
was added), energy 2014–16 about 40%, retail 2015–18 about 50%, growth unwind
2021–22 about 80%.

## What this is not

- Not a test. Dozens of variants were tried on the same data, so the best
  number flatters. Expect a few points less on companies it has never seen.
- Not in the app. The detector that assesses the watchlist is still h0.

## How it was run

`scripts/research/h1/features2.py` and `features3.py` extract the features
with the project's own code. `explore.py` fits the models and needs numpy and
scikit-learn, which are not project dependencies; it was run in a separate
environment. A detector built from this would be reimplemented without them.

## What a real pass needs

The model has to be fixed and registered first, then scored once on companies
never used for anything: SEC filers outside the S&P 1500, whose filings
haven't been downloaded. That set can only be used once.
