# The survivorship gap, measured

ROADMAP_V1 1.0-c. Regenerate with `python scripts/research/survivorship.py`
(needs the filer registry for 2014Q1 and 2024Q1 and roughly 700 SEC requests).

## The claim on record

The case set was generated from today's S&P 1500, so every company in it lasted
until 2026. FINDINGS §6e claimed the companies that left skew toward
deterioration, so the case set is short of the severe cases and 0.287 is
probably biased low. Until now that was an argument, not a number.

## Attrition

7,317 companies filed a 10-K or 10-Q in 2014Q1. 6,208 did in 2024Q1. Only
3,021 filed in both: **59% of the 2014 filers were gone ten years later.**
(The registry parser was returning zero rows until this measurement found it;
see commit `1b86e76`.)

## Do the leavers deteriorate more?

Both groups: filed in 2014Q1, total assets of at least $1B at 2013-12-31 (one
SEC frames request gives this for every filer), not a bank, insurer, REIT or
unknown sector. Leavers stopped filing before 2024Q1. Survivors still filed and
are on the current watchlist, because that's the population the case set came
from. Outcome: a deterioration event by `label.py`'s 2-of-5 rule at any
quarter ending 2014–2019.

| | n | deteriorated | 95% interval | 3+ criteria at once | 95% interval |
|---|---|---|---|---|---|
| Left by 2024 | 269 | **28.6%** | 23.6% – 34.3% | 6.3% | 4.0% – 9.9% |
| Still here | 299 | **20.1%** | 15.9% – 25.0% | 3.3% | 1.8% – 6.0% |

Leavers deteriorated about 8.5 points more often, and roughly twice as often
on three or more criteria at once. Both intervals overlap slightly, so the size
of the gap is uncertain. The direction isn't.

## What it does and doesn't show

It confirms the precondition: the case set is short of deterioration, and more
short of severe deterioration, than the population of companies that existed
at the time.

It doesn't show 0.287 is biased low. That follows only if the gate catches
severe cases at a higher rate than mild ones, and nothing has measured that.
Measuring it means scoring the gate on leavers, which is a new case set. Per
`prereg.json` that needs a new split and a new pre-registration first. The
holdout result stands as published.

## Limits

- Leavers include healthy companies that were acquired. They dilute the
  leaver rate, so the gap between distressed leavers and survivors is larger
  than shown.
- Survivors on the S&P 1500 watchlist skew larger than the leaver pool. Size
  and survival aren't separated here.
- 31 of the 300 sampled leavers had no usable revenue history and are left
  out.
- Each fact's latest vintage is used. This measures outcomes, which may look
  forward.
