# The footnote critique, measured

ROADMAP_V1 1.0-c. Tuning split only; the holdout is spent. Regenerate with
`python scripts/research/footnotes.py`.

## The critique

New Constructs argues that material unusual items (restructuring charges,
impairments, gains on disposals, litigation) sit in narrative footnotes, and
that a pipeline reading structured data never sees them. It was the sharpest
outside attack on this design and had no written answer.

## What was measured

471 tuning companies, 7,097 company-years of 10-K data, seven families of
unusual-item concepts (impairment, restructuring, disposals, debt
extinguishment, discontinued operations, litigation, tax law changes).
Overlapping concepts within a family count once, at the largest value.

| | |
|---|---|
| Company-years tagging at least one unusual item | **91.6%** |
| Company-years where tagged items are ≥10% of net income | **39.3%** |
| Median tagged items as a share of net income | 9.7% |
| 90th percentile | 127% |

| family | share of company-years tagging it |
|---|---|
| impairment | 65.0% |
| disposals | 47.3% |
| restructuring | 34.3% |
| debt extinguishment | 30.0% |
| discontinued operations | 26.5% |
| tax law change | 15.0% |
| litigation | 5.8% |

## Do they carry signal?

Share of company-years with a material tagged unusual item:

| | rate | 95% interval |
|---|---|---|
| Last fiscal year before a deterioration became public (215 positives) | 44.7% | 38.2% – 51.3% |
| A random year 2014–2024 (249 controls) | 36.1% | 30.4% – 42.3% |

About 9 points higher before a break. The intervals overlap, so it's weak
evidence on this sample.

## Answer to the critique

The premise doesn't hold for this data. These items are tagged as standalone
facts in companyfacts for nine company-years in ten, and they're material in
four in ten. A structured pipeline can see them.

This one doesn't read them. Of the seven families, the gate uses none. The
label uses one (impairment ≥5% of assets).

That's a fixable gap, and a candidate for a second hypothesis: a diagnostic on
earnings with tagged unusual items removed, measured against the company's own
history like the other thirteen. The 9-point difference above says to expect a
modest effect, not a rescue. It would have to be registered against a reserved
set before anyone could claim it helps.

## Limits

- Values are each fact's latest vintage, so this is an upper bound on what a
  point-in-time gate could have used.
- companyfacts drops facts with dimensions. A restructuring charge tagged only
  by segment is invisible here.
- Items that appear only in footnote text, never tagged, can't be measured by
  anything that reads tagged facts. This measures the reachable part.
