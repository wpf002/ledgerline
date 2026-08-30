"""
Ingestion-hardening tests: cache freshness, run bookkeeping, resumability.

Each test pins a specific defect. The headline one: edgar.fetch() returned a
cached file unconditionally and companyfacts() uses a permanent cache key, so
`scan` detected a new 10-Q via the daily index and then scored the facts file
written at backfill time -- the filing that triggered the scan was not in the
data the scan scored. No network: the wire is a fake urlopen over a mutable
in-memory "SEC", so the caching layer under test is the real one.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from ledgerline import edgar, ingest, signals_v3
from tests.unit.test_ingestion import discrete_facts, facts_doc


class _Resp:
    def __init__(self, body: bytes):
        self.body = body
        self.headers: dict = {}

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _wire(monkeypatch, tmp_path, source: dict) -> None:
    """Real fetch(), fake network: urlopen serves source['body'], the cache
    lives under tmp_path, and the throttle is off."""
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(edgar, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(edgar, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(edgar, "USER_AGENT", "test test@example.com")
    monkeypatch.setattr(edgar.urllib.request, "urlopen",
                        lambda req, timeout=60: _Resp(source["body"]))
    edgar.stats_reset()


def _isolated_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def _watch(monkeypatch, ciks: list[str]) -> None:
    monkeypatch.setattr(
        edgar, "universe",
        lambda: {c: {"cik": c, "ticker": f"T{i}", "name": f"T{i} Inc"}
                 for i, c in enumerate(ciks)})


def _facts_body(quarters: list[float], start_year: int = 2023) -> bytes:
    doc = {"facts": {"us-gaap": facts_doc(
        {"Revenues": discrete_facts("rev", start_year,
                                    len(quarters) // 4, quarters)})}}
    return json.dumps(doc).encode()


def _hit(cik: str, accession: str, form: str = "10-Q") -> dict:
    return {"form": form, "name": "X", "cik": cik,
            "filing_date": "2026-08-28", "file": "edgar/data/x",
            "accession": accession}


# ----------------------------------------------------------- cache freshness


def test_companyfacts_cache_hides_the_filing_that_triggered_the_scan(
        tmp_path, monkeypatch):
    """The confirmed live defect: the cached companyfacts aggregate never
    refreshes, so a scan triggered by a new filing scores data that predates
    the filing. refresh=True is the fix; the default stays blind, which is
    exactly why ingest passes refresh for filers the index says filed."""
    source = {"body": _facts_body([100.0] * 4)}
    _wire(monkeypatch, tmp_path, source)

    v1 = edgar.companyfacts("1")
    assert len(v1["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 4

    # The filer files again: the SEC's copy grows, our cache does not.
    source["body"] = _facts_body([100.0] * 4 + [50.0] * 4)
    stale = edgar.companyfacts("1")
    assert len(stale["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 4, (
        "the cached aggregate hid the new filing -- this assertion documents "
        "the defect the refresh parameter exists to fix")

    fresh = edgar.companyfacts("1", refresh=True)
    assert len(fresh["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 8


def test_refresh_still_writes_the_cache_so_the_next_read_is_free(
        tmp_path, monkeypatch):
    """refresh bypasses the cache READ, never the write."""
    source = {"body": b'{"v": 1}'}
    _wire(monkeypatch, tmp_path, source)
    edgar.fetch_json("http://x", "k.json")
    source["body"] = b'{"v": 2}'
    assert edgar.fetch_json("http://x", "k.json", refresh=True) == {"v": 2}

    requests_before = edgar.stats()["requests"]
    assert edgar.fetch_json("http://x", "k.json") == {"v": 2}
    assert edgar.stats()["requests"] == requests_before, (
        "the refreshed copy was not cached, so the next read paid again")


def test_daily_index_for_today_is_refetched_not_served_from_cache(
        tmp_path, monkeypatch):
    """Today's index is still being appended to: a 10:00 scan that cached a
    partial copy must not pin the evening rerun to the morning's view. Past
    days are final and stay cached."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    seen: list[tuple[str, bool]] = []

    def fake_index(d, refresh=False):
        seen.append((d.isoformat(), refresh))
        return []

    monkeypatch.setattr(edgar, "daily_index", fake_index)
    ingest.scan(days_back=2, refresh=True)
    from datetime import date
    today = date.today().isoformat()
    assert dict(seen)[today] is True
    older = [r for d, r in seen if d != today]
    assert older == [False]


# --------------------------------------------------------- run bookkeeping


def test_quiet_day_still_writes_a_run_row(tmp_path, monkeypatch):
    """The old cli.scan returned before writing anything when nothing in the
    universe filed -- so the day the one-request cost architecture is loudest
    about was the one day it left no evidence."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    monkeypatch.setattr(
        edgar, "daily_index",
        lambda d, refresh=False: [_hit("999", "a"), _hit("999", "b"),
                                  _hit("999", "c")])
    out = ingest.scan(days_back=1, refresh=False)
    assert out["filers"] == []
    rows = ingest.run_log(job="scan")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["index_rows"] == 3
    assert rows[0]["universe_hits"] == 0


def test_run_row_separates_market_wide_index_rows_from_universe_hits(
        tmp_path, monkeypatch):
    """cli.scan wrote len(hits) into BOTH the scanned and changed columns, so
    the schema never held the market-wide count that demonstrates the Tier 0
    cost claim."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    monkeypatch.setattr(
        edgar, "daily_index",
        lambda d, refresh=False: [_hit("999", "a"), _hit("998", "b"),
                                  _hit("1", "mine")])
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False: {"cik": cik,
                                                      "status": "ok",
                                                      "restatements": 0})
    ingest.scan(days_back=1, refresh=False)
    row = ingest.run_log(job="scan")[0]
    assert row["index_rows"] == 3
    assert row["universe_hits"] == 1


def test_two_runs_on_one_day_are_two_rows(tmp_path, monkeypatch):
    """The old runs table was keyed on run_date, so the second run of a day
    erased the first."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    monkeypatch.setattr(edgar, "daily_index", lambda d, refresh=False: [])
    ingest.scan(days_back=1, refresh=False)
    ingest.scan(days_back=1, refresh=False)
    assert len(ingest.run_log(job="scan")) == 2


def test_failed_run_is_recorded_as_failed_not_overwritten_by_the_retry(
        tmp_path, monkeypatch):
    """INSERT OR REPLACE on run_date meant a retry silently overwrote the
    record of the failure it was retrying; a crash left a row stuck (or
    absent). The contextmanager writes status='failed' with the error, and
    the retry is its own row beside it."""
    _isolated_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="boom"), ingest.run("scan"):
        raise ValueError("boom")
    with ingest.run("scan"):
        pass
    rows = ingest.run_log(job="scan")
    assert [r["status"] for r in rows] == ["ok", "failed"]
    assert "ValueError: boom" in rows[1]["error"]


def test_run_counts_requests_and_cache_hits_separately(tmp_path, monkeypatch):
    """ROADMAP §10 gates scaling on cost-flat-in-universe-size; that needs the
    requests number measured per run, not asserted. A fully cached backfill
    reports requests=0, cache_hits=N."""
    source = {"body": _facts_body([100.0] * 4)}
    _wire(monkeypatch, tmp_path, source)
    _watch(monkeypatch, ["1", "2"])
    edgar.companyfacts("1")
    edgar.companyfacts("2")

    ingest.backfill(resume=False)
    row = ingest.run_log(job="backfill")[0]
    assert row["requests"] == 0
    assert row["cache_hits"] == 2


# ------------------------------------------------------------- resumability


def test_filing_is_only_recorded_after_its_filer_ingests(tmp_path, monkeypatch):
    """detect_changes used to write every hit to `filings` before any work
    happened, so a crash mid-run marked accessions known and a rerun skipped
    them permanently. Now record_filings runs per filer, after that filer's
    ingest -- a crash leaves the unprocessed accessions retryable."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1", "2", "3"])
    monkeypatch.setattr(
        edgar, "daily_index",
        lambda d, refresh=False: [_hit("1", "acc-1"), _hit("2", "acc-2"),
                                  _hit("3", "acc-3")])

    def crashing_ingest(cik, run_id, counters, refresh=False):
        if cik == "2":
            raise ValueError("mid-run crash")
        return {"cik": cik, "status": "ok", "restatements": 0}

    monkeypatch.setattr(ingest, "ingest_filer", crashing_ingest)
    with pytest.raises(ValueError):
        ingest.scan(days_back=1, refresh=False)

    conn = edgar.db()
    known = {r[0] for r in conn.execute("SELECT accession FROM filings")}
    conn.close()
    assert known == {"acc-1"}, "only the ingested filer's accession is known"
    assert ingest.run_log(job="scan")[0]["status"] == "failed"


def test_backfill_resumes_and_skips_finished_filers(tmp_path, monkeypatch):
    """Resume reads ingest_state, inside the database -- not a JSON checkpoint
    beside the scripts directory."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1", "2", "3"])
    conn = edgar.db()
    with conn:
        conn.execute("INSERT INTO ingest_state (cik, status) VALUES ('1', 'ok')")
        conn.execute("INSERT INTO ingest_state (cik, status) "
                     "VALUES ('2', 'no_facts')")
    conn.close()
    pulled = []
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False: (
            pulled.append(cik) or {"cik": cik, "status": "ok",
                                   "restatements": 0}))
    ingest.backfill(resume=True)
    assert pulled == ["3"]


def test_backfill_honors_the_cache_and_issues_no_requests_on_a_rerun(
        tmp_path, monkeypatch):
    """The resumability bullet, asserted as requests == 0 rather than
    described: a rerun over cached facts regenerates all state for free."""
    source = {"body": _facts_body([100.0] * 4)}
    _wire(monkeypatch, tmp_path, source)
    _watch(monkeypatch, ["1"])
    ingest.backfill(resume=True)
    first = ingest.run_log(job="backfill")[0]
    assert first["requests"] == 1

    ingest.backfill(resume=True)
    second = ingest.run_log(job="backfill")[0]
    assert second["requests"] == 0


def test_backfill_records_an_error_and_continues(tmp_path, monkeypatch):
    """scripts/backfill.py caught RuntimeError and JSONDecodeError (not just
    HTTPError) per filer and continued -- untested for its whole life. The
    behaviour now lives in the package and this pins it: the error is
    recorded, the rest of the universe completes, the run finishes 'ok' with
    filers_failed=1."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1", "2"])

    def flaky(cik, refresh=False):
        if cik == "1":
            raise RuntimeError("fetch failed: timeout")
        return json.loads(_facts_body([100.0] * 4))

    monkeypatch.setattr(edgar, "companyfacts", flaky)
    out = ingest.backfill(resume=False)
    state = ingest.ingest_state()
    assert state["1"]["status"] == "error"
    assert "timeout" in state["1"]["error"]
    assert state["2"]["status"] == "ok"
    row = ingest.run_log(job="backfill")[0]
    assert row["status"] == "ok"
    assert row["filers_failed"] == 1
    assert row["filers_done"] == 1
    assert len(out["filers"]) == 2


def test_backfill_distinguishes_never_pulled_from_pulled_with_no_facts(
        tmp_path, monkeypatch):
    """The JSON checkpoint lived outside the db, so `check` could not tell a
    filer never pulled from one pulled and holding no XBRL. In ingest_state
    they are different things: a 'no_facts' row versus no row at all."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1", "2"])
    monkeypatch.setattr(edgar, "companyfacts",
                        lambda cik, refresh=False: {"facts": {"us-gaap": {}}})
    ingest.backfill(resume=False, limit=1)
    state = ingest.ingest_state()
    assert state["1"]["status"] == "no_facts"
    assert "2" not in state, "never pulled means absent, not 'no_facts'"


# ---------------------------------------------------------- scoring is opt-in


def test_scan_does_not_score_by_default(tmp_path, monkeypatch):
    """The gate failed its own pre-registered test (28.7% caught vs a 60%
    floor). The scheduled job is an INGESTION job; scoring is opt-in."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    monkeypatch.setattr(edgar, "daily_index",
                        lambda d, refresh=False: [_hit("1", "acc-1")])
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False: {"cik": cik,
                                                      "status": "ok",
                                                      "restatements": 0})

    def explode(*a, **k):
        raise AssertionError("evaluate() must not run without score=True")

    monkeypatch.setattr(signals_v3, "evaluate", explode)
    out = ingest.scan(days_back=1, refresh=False)
    assert out["results"] == []


def test_scored_output_carries_the_phase0_kill_stamp(tmp_path, monkeypatch):
    """The enforcement point for 'no surface shows a score without the fact
    that it failed its own test': every payload out of scan(score=True) and
    out of signals_v3.evaluate itself passes status.assert_stamped."""
    from ledgerline import status
    from tests.unit.test_gate import build_filer

    norm = build_filer(quarters=32)
    direct = signals_v3.evaluate("T", "0000000001", as_of="2023-12-01",
                                 norm=norm)
    status.assert_stamped(direct)

    unscoreable = signals_v3.evaluate("T", "0000000001", as_of="2023-12-01",
                                      norm={})
    status.assert_stamped(unscoreable)

    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["0000000001"])
    monkeypatch.setattr(edgar, "daily_index",
                        lambda d, refresh=False: [_hit("0000000001", "acc-1")])
    monkeypatch.setattr(
        ingest, "ingest_filer",
        lambda cik, run_id, counters, refresh=False: {"cik": cik,
                                                      "status": "ok",
                                                      "restatements": 0})
    monkeypatch.setattr(signals_v3, "evaluate",
                        lambda t, c, as_of=None, norm=None: direct)
    out = ingest.scan(days_back=1, score=True, refresh=False)
    assert out["results"], "the scored run produced no payloads to check"
    for res in out["results"]:
        status.assert_stamped(res)


def test_scan_records_an_8k_without_refetching_facts(tmp_path, monkeypatch):
    """8-Ks were 74% of two years' tracked-form hits and carry no XBRL
    fundamentals: once the cache is correctly invalidated, letting one trigger
    a ~3.4MB companyfacts refetch is pure cost. The filing is recorded; the
    refetch is gated on PERIODIC_FORMS."""
    _isolated_db(tmp_path, monkeypatch)
    _watch(monkeypatch, ["1"])
    monkeypatch.setattr(edgar, "daily_index",
                        lambda d, refresh=False: [_hit("1", "acc-8k", "8-K")])

    def explode(cik, run_id, counters, refresh=False):
        raise AssertionError("an 8-K must not trigger a facts refetch")

    monkeypatch.setattr(ingest, "ingest_filer", explode)
    out = ingest.scan(days_back=1, refresh=True)
    assert out["filers"][0]["status"] == "recorded"
    conn = edgar.db()
    known = {r[0] for r in conn.execute("SELECT accession FROM filings")}
    conn.close()
    assert known == {"acc-8k"}


# ------------------------------------------------------- schema and universe


def test_migration_is_idempotent_and_leaves_metrics_untouched(
        tmp_path, monkeypatch):
    """A column added inside the SCHEMA string would be read by the code and
    never created in the live db (CREATE TABLE IF NOT EXISTS is a no-op
    against an existing table). The migration helper is the only mechanism
    that adds one, and running it twice must change nothing."""
    _isolated_db(tmp_path, monkeypatch)
    old = sqlite3.connect(str(tmp_path / "state.db"))
    old.execute("CREATE TABLE filings (accession TEXT PRIMARY KEY, cik TEXT, "
                "ticker TEXT, form TEXT, filing_date TEXT, period TEXT, "
                "primary_doc TEXT)")
    old.execute("CREATE TABLE metrics (cik TEXT, metric TEXT, end_date TEXT, "
                "kind TEXT, value REAL, PRIMARY KEY (cik, metric, end_date, "
                "kind))")
    old.execute("INSERT INTO metrics VALUES ('1', 'revenue', '2023-03-31', "
                "'Q', 100.0)")
    old.commit()
    old.close()

    for _ in range(2):
        conn = edgar.db()
        conn.close()

    conn = sqlite3.connect(str(tmp_path / "state.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM metrics").fetchall() == [(100.0,)]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"job_runs", "ingest_state", "vintages", "restatements"} <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(filings)")}
    assert "first_seen_run" in cols
    conn.close()


def test_set_universe_rerun_does_not_null_the_sic_column(
        tmp_path, monkeypatch):
    """INSERT OR REPLACE deleted the whole row, so every universe re-run
    nulled sic -- after which admission rejects unknown SIC and the next case
    build silently returns an empty set until ~1,500 submissions files are
    re-fetched. The upsert touches only the columns actually supplied."""
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        edgar, "load_ticker_map",
        lambda: {"AAPL": {"cik": "0000000001", "name": "Apple",
                          "ticker": "AAPL"}})
    edgar.set_universe(["AAPL"])
    edgar.set_sic([("0000000001", "3571")])
    edgar.set_universe(["AAPL"])  # the re-run that used to null sic
    assert edgar.sic_map()["0000000001"] == "3571"
