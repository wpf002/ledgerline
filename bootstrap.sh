#!/usr/bin/env bash
# Ledgerline Signal — infrastructure bootstrap
# Run once from an empty directory. Idempotent where it can be.
set -euo pipefail

PROJECT="ledgerline"
PY_MIN="3.11"

echo "==> Ledgerline Signal bootstrap"

# --------------------------------------------------------------- preflight
# The first Python >= 3.11 we can actually run. `python3` alone isn't enough:
# macOS ships 3.9 as python3, and a pyenv shim like python3.12 exists on PATH
# but refuses to run when that version isn't the selected one. So each
# candidate is executed, not just located.
ok_python() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }
pick_python() {
  for cand in "${PYTHON:-}" python3.13 python3.12 python3.11 python3 python; do
    [ -n "$cand" ] && command -v "$cand" >/dev/null 2>&1 && ok_python "$cand" \
      && { command -v "$cand"; return 0; }
  done
  if [ -d "$HOME/.pyenv/versions" ]; then
    for v in $(ls -1r "$HOME/.pyenv/versions"); do
      p="$HOME/.pyenv/versions/$v/bin/python3"
      [ -x "$p" ] && ok_python "$p" && { echo "$p"; return 0; }
    done
  fi
  return 1
}
PY=$(pick_python) || {
  echo "Python 3.11 or newer is needed and none was found."
  echo "Install 3.12 (for example: pyenv install 3.12) and run this again,"
  echo "or point at one: PYTHON=/path/to/python3.12 ./bootstrap.sh"
  exit 1
}
PYV=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "    python $PYV ok ($PY)"

# On a fresh, empty directory this script writes the project's config files.
# On an existing checkout those files are committed and have moved on from the
# templates below (data/.gitignore, .gitignore and the Makefile all had), so
# they're only ever CREATED, never overwritten.
if [ -f pyproject.toml ]; then MODE=existing; else MODE=fresh; fi
write_new() {
  if [ -e "$1" ]; then
    cat >/dev/null
    echo "    kept existing $1"
  else
    mkdir -p "$(dirname "$1")"
    cat > "$1"
  fi
}

# ---------------------------------------------------------- directory tree
mkdir -p \
  ledgerline/{validate,narrate,api} \
  ledgerline/data \
  tests/{unit,integration,fixtures} \
  scripts \
  reports \
  .github/workflows

touch ledgerline/__init__.py \
      ledgerline/validate/__init__.py \
      ledgerline/narrate/__init__.py \
      ledgerline/api/__init__.py \
      tests/__init__.py

# data/ holds the sqlite state + the immutable EDGAR cache; neither belongs in git
write_new ledgerline/data/.gitignore <<'EOF'
# state.db and the EDGAR cache are derived data and do not belong in git.
*
!.gitignore

# These two DO. The whole Phase 0 methodology rests on the split and the
# decision rule being committed BEFORE the holdout is scored -- a
# pre-registration that lives only on the machine that ran the test is not a
# pre-registration. bootstrap.sh's blanket ignore would have kept both
# untracked, which would have quietly voided the commitment.
!split.json
!prereg.json
!cases.json

# So does the outcome. reports/*.json is ignored at the repo root, so without
# these two the record of the fitted coefficients and of the Phase 0 KILL
# would not survive a fresh clone -- and status.load() deliberately refuses to
# label the gate on a machine that holds no evidence.
!calibration.json
!phase0.json

# And so does the re-test reserve: the hashed future evaluation sets and the
# alpha budget they draw on. A registry that lives only on the machine that
# reserved the set is not a registry -- same argument as prereg.json.
!retests.json
# Registrations are commitments; they live in git.
!hypotheses.json
EOF

# ---------------------------------------------------------------- packaging
write_new pyproject.toml <<'EOF'
[project]
name = "ledgerline"
version = "0.1.0"
description = "Deterministic accounting-signal detection on SEC XBRL filings"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "typer>=0.12",
    "python-dateutil>=2.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
    "pre-commit>=3.7",
]

[project.scripts]
ledgerline = "ledgerline.cli:app"

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["ledgerline*"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
warn_unused_ignores = true
disallow_untyped_defs = false

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
markers = [
    "network: hits SEC EDGAR; excluded from CI",
    "slow: full backtest, minutes not seconds",
]
EOF

# ------------------------------------------------------------------ env
write_new .env.example <<'EOF'
# SEC fair-access requires a descriptive User-Agent with a real contact address.
# Requests without one get blocked, and blocks cause retries, and retries cost money.
LEDGERLINE_UA="Ledgerline Signal research you@example.com"

# Local dev uses the sqlite file in ledgerline/data. Postgres is Phase 5.
DATABASE_URL="sqlite:///ledgerline/data/state.db"

# Phase 4 only. Narration runs on gated-in events, nothing else.
ANTHROPIC_API_KEY=""
TRIDENT_ENDPOINT=""
EOF
[ -f .env ] || cp .env.example .env

write_new .gitignore <<'EOF'
__pycache__/
*.py[cod]
.venv/
venv/
.env
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/
reports/*.json
reports/*.html
# projections of the append-only signal log; regenerable, and committing them
# would create a second source of truth for an immutable history
reports/feed/
reports/digest/

# universe snapshot is committed (reproducibility); run state is not
scripts/backfill_state.json
reports/*.log
scripts/sic_state.json
reports/*.err
EOF

# ------------------------------------------------------------------ venv
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -e ".[dev]"
echo "    venv ready, package installed editable"

# ------------------------------------------------------------- pre-commit
write_new .pre-commit-config.yaml <<'EOF'
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
  - repo: local
    hooks:
      # The holdout is only worth something if nobody edits it after commit.
      - id: split-integrity
        name: validation split integrity
        entry: python -c "from ledgerline.validate.harness import verify_split; verify_split()"
        language: system
        files: 'ledgerline/data/split\.json'
        pass_filenames: false
EOF
pre-commit install >/dev/null 2>&1 || true

# ------------------------------------------------------------------- CI
write_new .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          # the reproduction tests check the Phase 0 commit is in history
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy ledgerline
      # No network in CI. Fixtures under tests/fixtures are recorded EDGAR
      # payloads, so ingestion is testable without hitting SEC.
      - run: pytest -m "not network and not slow" --cov=ledgerline
EOF

# ---------------------------------------------------------------- Makefile
write_new Makefile <<'EOF'
.PHONY: help test lint fmt fetch run-test cost clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "%-12s %s\n",$$1,$$2}'

test:      ## unit tests, no network
	pytest -m "not network and not slow"

lint:      ## ruff + mypy
	ruff check . && mypy ledgerline

fmt:       ## autoformat
	ruff format . && ruff check --fix .

fetch:     ## download filing history for the watchlist
	python -m ledgerline.cli fetch

run-test:  ## score the practice half against the committed pass mark
	python -m ledgerline.cli run-test --split tuning

cost:      ## measure the run-cost curve; prints the flat and non-flat parts
	python -m ledgerline.cli cost

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
EOF

# ------------------------------------------------------------------- git
if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi

echo ""
echo "==> Done. Structure:"
find . -type d \
  -not -path './.git/*' -not -path './.venv/*' -not -path '*/__pycache__*' \
  | sort | sed 's|^\./||'
echo ""
if [ "$MODE" = existing ]; then
  echo "Next:"
  echo "  1. edit .env  -> set LEDGERLINE_UA to a real contact address"
  echo "  2. source .venv/bin/activate && ledgerline doctor"
else
  echo "Next:"
  echo "  1. edit .env  -> set LEDGERLINE_UA to a real contact address"
  echo "  2. git add -A && git commit -m 'chore: bootstrap'"
  echo "  3. make test"
fi
