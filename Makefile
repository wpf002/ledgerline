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
