#!/usr/bin/env bash
# Ledgerline Signal — infrastructure bootstrap
# Run once from an empty directory. Idempotent where it can be.
set -euo pipefail

PROJECT="ledgerline"
PY_MIN="3.11"

echo "==> Ledgerline Signal bootstrap"

# --------------------------------------------------------------- preflight
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 - <<'EOF' || { echo "Python >= 3.11 required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
echo "    python $PYV ok"

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
cat > ledgerline/data/.gitignore <<'EOF'
*
!.gitignore
EOF

# ---------------------------------------------------------------- packaging
cat > pyproject.toml <<'EOF'
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
api = ["fastapi>=0.111", "uvicorn[standard]>=0.30", "psycopg[binary]>=3.1"]

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
cat > .env.example <<'EOF'
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

cat > .gitignore <<'EOF'
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
EOF

# ------------------------------------------------------------------ venv
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -e ".[dev]"
echo "    venv ready, package installed editable"

# ------------------------------------------------------------- pre-commit
cat > .pre-commit-config.yaml <<'EOF'
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
cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy ledgerline
      # No network in CI. Fixtures under tests/fixtures are recorded EDGAR
      # payloads, so ingestion is testable without hitting SEC.
      - run: pytest -m "not network and not slow" --cov=ledgerline
EOF

# ---------------------------------------------------------------- Makefile
cat > Makefile <<'EOF'
.PHONY: help test lint fmt backfill validate clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "%-12s %s\n",$$1,$$2}'

test:      ## unit tests, no network
	pytest -m "not network and not slow"

lint:      ## ruff + mypy
	ruff check . && mypy ledgerline

fmt:       ## autoformat
	ruff format . && ruff check --fix .

backfill:  ## pull companyfacts for the configured universe
	python -m ledgerline.cli backfill

validate:  ## Phase 0 gate. Prints SHIP or KILL.
	python -m ledgerline.cli validate --split holdout

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
echo "Next:"
echo "  1. edit .env  -> set LEDGERLINE_UA to a real contact address"
echo "  2. gh repo create wpf002/ledgerline --private --source=. --remote=origin"
echo "  3. git add -A && git commit -m 'chore: bootstrap' && git push -u origin main"
echo "  4. make test"
