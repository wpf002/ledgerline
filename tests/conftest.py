"""Shared test setup.

No test may write to the published pages. `ledgerline publish` and anything
that calls it write to reports/feed by default, and that directory is what the
local viewer serves. On 2026-10-11 a test that called the scan command as a
plain function published its empty fixture there and blanked every page; the
database was untouched, so one `ledgerline publish` restored them. This points
both default locations at a temporary directory for every test.
"""
from __future__ import annotations

import os

import pytest

from ledgerline.api import contract, views


@pytest.fixture(autouse=True)
def _published_pages_are_off_limits(tmp_path_factory, monkeypatch):
    feed = tmp_path_factory.mktemp("feed")
    monkeypatch.setattr(contract, "FEED_PATH", os.path.join(str(feed), "signals.jsonl"))
    monkeypatch.setattr(views, "FEED_DIR", str(feed))
