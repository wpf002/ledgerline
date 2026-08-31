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

## Groups, and lists that arrive as spreadsheets

```bash
ledgerline watch --import my-list.csv   # add a whole spreadsheet of companies
ledgerline groups                       # your own groupings, with counts
ledgerline groups --assign semis --tickers NVDA,AMD,INTC
ledgerline watch --group semis          # and check --group / scan --group
```

The import file needs a first line naming its columns; only `ticker` is
required, and `name`, `sector`, `cik`, `group` and `status` are understood in
any order. Every line is reported back — added, already watched, or not
recognised by the SEC — and one bad line does not stop the rest. Nothing is
downloaded for the new companies; run `ledgerline fetch` next.

A group is a label over the watchlist. A company can be in as many as you
like, and deleting a group never removes a company or anything downloaded for
it. Filtering by a group you have not created, or one nobody is in yet, says
so rather than showing an empty list.

## Taking the data elsewhere

```bash
ledgerline export watchlist --out watchlist.csv   # or signals, or runs
ledgerline publish                                # the files the local viewer reads
```

Every exported file leads with a comment line carrying the result of the
failed 2026-08-30 test, so a spreadsheet that leaves this machine still says
the detector missed its own bar. `publish` writes the assessment feed plus
`watchlist.json`, `runs.json` and one file per company under `reports/feed/`;
each of those carries the same record. Publishing reads what has already been
saved and assesses nothing.

## Reading it in a browser

```bash
ledgerline publish        # write the files the viewer reads
node service/server.mjs   # then open http://localhost:8787
```

Four pages: the latest run, the whole watchlist (filter by group or by whether
a company can be assessed, search by ticker), one page per company (the plain
reading, the thirteen measures, the filings every number came from, anything
later revised), and the run log. Nothing is installed and nothing is
downloaded — it reads the published files and stays on loopback. Every page
leads with the result of the failed test, and a page with nothing to show says
which command would fill it.

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
