# Can lowering the flag line reach 60%?

Practice half only. Regenerate with `python scripts/research/threshold_sweep.py`.

## The question

The detector caught 28.7% of deteriorations against a 60% goal. The flag line
sits at a score of 45 with two measures out of line. Lower it and more gets
caught. What does that cost?

## Measured

194 practice-half deteriorations, 9,323 quiet company-quarters.

| flag line | measures needed | caught | false alarms per quarter |
|---|---|---|---|
| 45 (today) | 2 | 29.9% | 4.0% |
| 35 | 2 | 43.8% | 6.5% |
| 25 | 2 | 52.6% | 10.2% |
| 15 | 2 | 65.5% | 14.7% |
| 25 | 1 | 63.4% | 13.3% |
| 15 | 1 | 83.0% | 22.6% |

## Reading

Catching 60% with this detector means about 13 to 15% false alarms per
quarter. The limit in the test was 4%, and the two-line rule it had to beat
sits at 0.5%. Moving the line trades one failure for another.

A pass needs the detector to separate the two groups about three times better
at the same false-alarm rate. That takes information it doesn't read today.
The two things measured so far don't supply it: complete data lifts the catch
rate to about 38% (`recall-hypothesis.md`), and tagged unusual items are only
9 points more common before a break (`footnotes.md`).

## Limits

- The window is simpler than the pre-registered test's, so the catch rate at
  45 reads 29.9% here against the official 21.2%. The false-alarm rate
  matches. The table shows the shape of the trade-off; it isn't a result.
- Practice data. Any change to the line is a new hypothesis and gets judged
  on the reserved set, not here.
