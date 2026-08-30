# Running Ledgerline

## First time

```bash
./bootstrap.sh          # creates the venv, installs the package, sets up git hooks
```

Then open `.env` and put a real contact address in `LEDGERLINE_UA`. The SEC
requires every automated reader to identify itself and blocks the ones that
don't.

## Daily use

```bash
ledgerline watch --add AAPL,MSFT,NVDA   # choose companies (US SEC filers)
ledgerline fetch                        # download their filing history (slow once, cached after)
ledgerline check                        # which can be assessed, and what's missing for the rest
ledgerline scan                         # read today's SEC filings, flag anything unusual
ledgerline explain AAPL                 # one company, in plain words
ledgerline status                       # what's set up + what the last test said
```

`ledgerline explain AAPL --as-of 2020-05-15` answers as of a past date, using
only figures that had been filed by then — the tool never peeks at later data.

The first `fetch` over a large watchlist takes a while (the SEC allows ~9
requests/second). Accepted filings never change, so everything is cached
permanently and reruns are fast.

## What the output means

- **FLAGGED** — at least two measures broke from this company's own historical
  pattern and the combined score crossed 45 of 100. It is a prompt to look,
  not a verdict; the detector caught only 29% of real deteriorations in its
  own test.
- **NOT FLAGGED** — nothing unusual against its own past. Not a clean bill of
  health, for the same reason.
- **CANNOT ASSESS** — the company's filings are missing too much data, or its
  history is too short to know what "normal" is for it. This is stated rather
  than papered over: no number is shown because no honest number exists.

## The research commands

`build-cases`, `periods`, `split`, `commit-rule`, `calibrate`, `run-test`
re-run the validation experiment. Most are one-shot by design: the split and
the pass mark refuse to be rewritten, and the sealed test half has already
been scored (once — it missed). Re-testing a revised detector needs a new
sealed set; see `ROADMAP.md`.
