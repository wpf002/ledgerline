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

run-test:  ## score the sealed half against the committed pass mark
	python -m ledgerline.cli run-test --split holdout

cost:      ## measure the run-cost curve; prints the flat and non-flat parts
	python -m ledgerline.cli cost

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
