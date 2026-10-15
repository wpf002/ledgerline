# ruff: noqa
"""One-time pull of daily prices for the practice companies, plus SPY.
Scratch, research only. Reads TIINGO_API_KEY from the project's .env."""
import json, os, sys, time, urllib.request, urllib.error
REPO, OUT = sys.argv[1], sys.argv[2]
key = next(l.split("=", 1)[1].strip().strip("'\"") for l in open(os.path.join(REPO, ".env"))
           if l.startswith("TIINGO_API_KEY="))
rows = json.load(open(os.path.join(os.path.dirname(OUT), "tuning_features.json")))["rows"]
tickers = sorted({r["t"] for r in rows}) + ["SPY"]
done = fails = 0
for t in tickers:
    path = os.path.join(OUT, f"{t}.json")
    if os.path.exists(path):
        continue
    sym = t.replace(".", "-").lower()
    url = f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate=2008-01-01&endDate=2025-12-31&columns=date,adjClose,close,volume"
    for attempt in range(6):
        req = urllib.request.Request(url, headers={"Authorization": f"Token {key}",
                                                   "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            json.dump(data, open(path, "w")); done += 1
            break
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "replace")
            if e.code == 429 or "limit" in body.lower():
                print(f"{t}: rate limited ({body!r}), waiting 15 min", flush=True); time.sleep(900)
                continue
            print(f"{t}: HTTP {e.code} {body!r}", flush=True)
            json.dump({"error": e.code, "body": body}, open(path, "w")); fails += 1
            break
        except Exception as e:  # network hiccup
            print(f"{t}: {e}", flush=True); time.sleep(30)
    time.sleep(1.5)
    if done % 25 == 0:
        print(f"{done} fetched, {fails} failed", flush=True)
print(f"finished: {done} fetched, {fails} failed", flush=True)
