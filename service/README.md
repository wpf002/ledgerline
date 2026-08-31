# Ledgerline read service — local development only

Run it with:

```
node server.mjs
```

There is no install step. The server uses only Node's built-in modules
(`node:http`, `node:fs`, `node:path`, `node:url`) plus its own `pages.mjs` —
no package.json, no npm dependencies, no node_modules, no CDN link, no build
step. It listens on both loopback literals — `127.0.0.1:8787` and
`[::1]:8787`, never off the machine — and redirects either of them to the one
canonical address, **http://localhost:8787**, so the address bar always reads
the same way. It reads what `ledgerline publish` wrote under `reports/feed/`:
the JSONL signal feed, plus `watchlist.json`, `runs.json` and
`companies/TICKER.json`.

Four pages, and the JSON routes they sit beside:

| Route               | Returns                                                        |
| ------------------- | -------------------------------------------------------------- |
| `/`                 | the latest run: coverage, the chance-alone expectation, fires   |
| `/watchlist`        | every watched company; filter by group, assessability, ticker   |
| `/company`          | the company lookup form; `?ticker=` redirects to the path below |
| `/company/:ticker`  | one company: the plain reading, the thirteen measures, the filings behind each number, revisions, the provenance trail |
| `/activity`         | the run log: when, what it cost, what it found, what it could not assess |
| `/style.css`        | the one stylesheet all four pages link                          |
| `/signals`          | the feed, cursor-paged (`?since_seq=&limit=`)                  |
| `/signals/:ticker`  | one company's records                                          |
| `/validation`       | the validation block alone                                     |
| `/digest`           | the latest run as JSON                                          |

Anything else is a 404 carrying the route list; anything that is not a GET is
a 405. Both answer with the validation block, like every other response.

The pages are rendered on the server. That is not a preference: **the verdict
banner is the first thing in the body of every page**, and a banner painted by
JavaScript after a fetch is a banner that does not exist with scripting off,
on a page that still shows scores. `tests/unit/test_web_pages.py` pins the
ordering. Nothing else on a page requires JavaScript either — there is none.

**The signal is unvalidated.** The detector failed its own pre-registered
test on 2026-08-30 (it caught 28.7% of the deteriorations it was built to
find, against a required 60%). Every response carries the validation block
saying so, the server refuses to serve any feed line or render any page whose
data arrives without one, and a page with nothing to show still carries it.
The service never computes a score — it re-serves what the Python emitted,
including the plain-language sentences; if serving ever seems to require
recomputing a number or renaming a measure, that is a boundary question for
`ledgerline/api/views.py`, not a porting task.

There is deliberately **no deployment or hosting configuration of any
kind** — no Dockerfile, no cloud or process-manager config, no TLS, no
non-loopback bind. This app is under development; running it locally is the
whole scope.
