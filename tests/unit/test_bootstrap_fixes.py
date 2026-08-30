"""Tests for the build-phase fixes. Same discipline as the rest of the suite:
each test pins a specific defect found while standing the project up.

No network -- edgar.submissions and the sqlite path are both redirected.
"""
from __future__ import annotations

import json
import os

import ledgerline
from ledgerline import edgar, universe

# ------------------------------------------------------- .env was never read


def test_dotenv_loads_ledgerline_ua(tmp_path, monkeypatch):
    """bootstrap.sh writes .env and the README says to put a contact address in
    it, but nothing read the file -- edgar.USER_AGENT resolves from os.environ
    at import, so a correctly filled .env still raised 'must be set'."""
    env = tmp_path / ".env"
    env.write_text('LEDGERLINE_UA="Ledgerline research nobody@example.com"\n')
    monkeypatch.delenv("LEDGERLINE_UA", raising=False)
    ledgerline.load_dotenv(str(env))
    assert os.environ["LEDGERLINE_UA"] == "Ledgerline research nobody@example.com"


def test_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    """CI and cron set real env vars. A stale .env must not win over them."""
    env = tmp_path / ".env"
    env.write_text("LEDGERLINE_UA=from-dotenv\n")
    monkeypatch.setenv("LEDGERLINE_UA", "from-real-env")
    ledgerline.load_dotenv(str(env))
    assert os.environ["LEDGERLINE_UA"] == "from-real-env"


def test_dotenv_tolerates_comments_blanks_and_missing_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nDATABASE_URL='sqlite:///x.db'\nnot_a_pair\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    ledgerline.load_dotenv(str(env))
    assert os.environ["DATABASE_URL"] == "sqlite:///x.db"
    ledgerline.load_dotenv(str(tmp_path / "absent"))  # must not raise


# ------------------------------------------- SIC column existed but was empty


def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(universe, "_SIC_CACHE", None)


def test_set_sic_round_trips_through_the_universe_table(tmp_path, monkeypatch):
    """universe.sic has been in the schema since the first commit and nothing
    ever wrote it, so every admit() call saw None and rejected the filer."""
    _isolated_db(tmp_path, monkeypatch)
    conn = edgar.db()
    with conn:
        conn.execute("INSERT INTO universe (cik, ticker, name) VALUES (?,?,?)",
                     ("0000000001", "AAA", "Alpha"))
    conn.close()

    assert edgar.sic_map() == {"0000000001": None}
    edgar.set_sic([("0000000001", "3674")])
    assert edgar.sic_map() == {"0000000001": "3674"}


def test_fetch_sic_reads_the_table_before_the_network(tmp_path, monkeypatch):
    """build_cases() calls fetch_sic once per filer. Hitting data.sec.gov each
    time is ~1500 requests per run for a code that never changes -- the exact
    per-company-poll pattern Tier 0 exists to remove."""
    _isolated_db(tmp_path, monkeypatch)
    conn = edgar.db()
    with conn:
        conn.execute("INSERT INTO universe (cik, ticker, name, sic) VALUES (?,?,?,?)",
                     ("0000000001", "AAA", "Alpha", "3674"))
    conn.close()

    def explode(cik):
        raise AssertionError("fetch_sic went to the network despite a cached SIC")

    monkeypatch.setattr(edgar, "submissions", explode)
    assert universe.fetch_sic("0000000001") == "3674"


def test_fetch_sic_falls_back_to_network_on_a_miss(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(edgar, "submissions", lambda cik: {"sic": "7372"})
    assert universe.fetch_sic("0000000099") == "7372"


def test_unknown_sic_is_inadmissible(tmp_path, monkeypatch):
    """An unresolved SIC must reject, not silently admit a bank into a control
    group whose every diagnostic assumes an operating company."""
    assert universe.sic_excluded(None)
    assert universe.sic_excluded("")
    assert universe.sic_excluded("6022")   # national commercial bank
    assert universe.sic_excluded("6798")   # REIT
    assert not universe.sic_excluded("3674")


# --------------------------------------------- submissions caching is keyed


def test_submissions_is_cached_by_cik(tmp_path, monkeypatch):
    """Uncached, this file was refetched once per filer per run."""
    seen = []

    def fake_fetch(url, cache_key=None, retries=3):
        seen.append(cache_key)
        return json.dumps({"sic": "3674"}).encode()

    monkeypatch.setattr(edgar, "fetch", fake_fetch)
    edgar.submissions("1234")
    assert seen == ["submissions/CIK0000001234.json"]
