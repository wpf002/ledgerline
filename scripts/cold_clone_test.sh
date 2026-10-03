#!/usr/bin/env bash
# Cold-clone test (ROADMAP_V1 1.0-d): does a fresh clone work for someone who
# isn't the author?
#
# Clones the committed HEAD into a temp directory, bootstraps it with a new
# venv and an empty filing cache, and runs the daily-use path end to end on
# three companies. Every step reports pass or fail; the run continues past a
# failure so one run shows every break.
#
# Honest limit: this is the same machine. The clone gets its own venv, cache,
# database and service port, but shares ~/.pyenv and this network. It never
# runs `schedule install`, which would repoint this machine's LaunchAgents at
# the throwaway clone.
#
#   LEDGERLINE_UA="Name you@example.com" scripts/cold_clone_test.sh [workdir]
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-$(mktemp -d -t ledgerline-cold)}"
PORT=8799
UA="${LEDGERLINE_UA:-$(grep -E '^LEDGERLINE_UA=' "$SRC/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')}"
PASS=0; FAIL=0; RESULTS=()

step() {
  local name="$1"; shift
  local log="$WORK/step-$(( PASS + FAIL + 1 )).log"
  if "$@" >"$log" 2>&1; then
    PASS=$((PASS + 1)); RESULTS+=("  pass  $name")
  else
    FAIL=$((FAIL + 1)); RESULTS+=("  FAIL  $name  (log: $log)")
  fi
}

[ -n "$UA" ] || { echo "Set LEDGERLINE_UA (the SEC needs a contact address)."; exit 2; }
echo "Cold clone of $(git -C "$SRC" rev-parse --short HEAD) into $WORK"

CLONE="$WORK/ledgerline"
step "clone committed HEAD"        git clone -q "$SRC" "$CLONE"
cd "$CLONE" || exit 1
step "bootstrap (venv, install)"   ./bootstrap.sh
step "bootstrap left tracked files alone" git diff --quiet
printf 'LEDGERLINE_UA="%s"\n' "$UA" > .env

L="$CLONE/.venv/bin/ledgerline"
step "doctor runs on an empty clone" bash -c "$L doctor; [ \$? -le 1 ]"
step "watch three companies"       "$L" watch --add AAPL,MSFT,FMC
step "fetch their filing history"  "$L" fetch
step "check assessability"         "$L" check
step "scan with catch-up"          "$L" scan --catch-up
step "explain FMC"                 bash -c "$L explain FMC | grep -q 'FMC'"
step "explain leads with the failed test" bash -c "$L explain FMC | head -3 | grep -qi 'test'"
step "reproduce refuses nothing it shouldn't (dry: hypothesis status)" "$L" hypothesis status
step "publish"                     "$L" publish
step "golden + unit tests pass in the clone" "$CLONE/.venv/bin/pytest" -q

PORT=$PORT node service/server.mjs >"$WORK/service.log" 2>&1 &
SVC=$!
for _ in $(seq 1 20); do curl -s -o /dev/null "http://localhost:$PORT/" && break; sleep 0.5; done
for path in / /watchlist /company/FMC /activity; do
  step "service GET $path" bash -c "curl -sf http://localhost:$PORT$path | grep -q 'UNVALIDATED-KILL'"
done
kill $SVC 2>/dev/null; wait $SVC 2>/dev/null

step "schedule status (read-only)" "$L" schedule status

echo
printf '%s\n' "${RESULTS[@]}"
echo
echo "$PASS passed, $FAIL failed. Clone left at $CLONE for inspection."
[ "$FAIL" -eq 0 ]
