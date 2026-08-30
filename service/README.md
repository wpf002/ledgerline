# Ledgerline read service — local development only

Run it with:

```
node server.mjs
```

There is no install step. The server uses only Node's built-in modules
(`node:http`, `node:fs`) — no package.json, no npm dependencies, no
node_modules. It binds `127.0.0.1:8787` (loopback only) and serves the
JSONL feed written by `ledgerline publish`:

| Route               | Returns                                                        |
| ------------------- | -------------------------------------------------------------- |
| `/signals`          | the feed, cursor-paged (`?since_seq=&limit=`)                  |
| `/signals/:ticker`  | one company's records                                          |
| `/validation`       | the validation block alone                                     |
| `/digest`           | the latest run: coverage, the chance-alone expectation, fires  |

**The signal is unvalidated.** The detector failed its own pre-registered
test on 2026-08-30 (it caught 28.7% of the deteriorations it was built to
find, against a required 60%). Every response carries the validation block
saying so, and the server refuses to serve any record that arrives without
one. It never computes a score — it re-serves what the Python emitted, and
if serving ever seems to require recomputing a number, that is a boundary
question, not a porting task.

There is deliberately **no deployment or hosting configuration of any
kind** — no Dockerfile, no cloud or process-manager config, no TLS, no
non-loopback bind. This app is under development; running it locally is the
whole scope.
