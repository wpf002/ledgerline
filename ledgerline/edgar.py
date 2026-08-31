"""
Ledgerline Signal -- Tier 0/1 ingestion.

Zero-credit, deterministic SEC EDGAR client:
  Tier 0  change detection via daily-index (one request covers the whole market)
  Tier 1  XBRL companyfacts -> normalized, provenance-tagged metric dictionary

SEC fair-access rules: descriptive User-Agent required, ~10 req/sec ceiling.
Both are enforced here. Violating them causes blocks, blocks cause retries, and
retries are a cost leak -- so this is correctness, not courtesy.

FIXES APPLIED (see FINDINGS.md):
  §2  flow metrics are derived from YTD cumulatives instead of discarded
  §3  total_debt now includes current maturities and short-term borrowings
  §3  deferred_revenue now includes the noncurrent contract liability
  §3  diluted_shares locks to one concept per filer, no basic-shares fallback
  §3  persist_metrics PK no longer includes `form`, so the dedupe survives
      the round trip to sqlite
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

from . import derive, reasons

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
DB_PATH = os.path.join(DATA, "state.db")

USER_AGENT = os.environ.get("LEDGERLINE_UA", "")
MIN_INTERVAL = 0.11  # ~9 req/sec, under SEC's ceiling

os.makedirs(CACHE, exist_ok=True)

_last_call = [0.0]

# Request accounting, read into the job_runs table per run. ROADMAP §10 gates
# scaling on cost-flat-in-universe-size, and that needs a measured number per
# run rather than an asserted one. Two counters and a byte count, no wrapper.
STATS: dict[str, int] = {"requests": 0, "cache_hits": 0, "bytes_fetched": 0}


def stats_reset() -> None:
    for k in STATS:
        STATS[k] = 0


def stats() -> dict[str, int]:
    return dict(STATS)


def _throttle() -> None:
    delta = time.monotonic() - _last_call[0]
    if delta < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - delta)
    _last_call[0] = time.monotonic()


def _require_ua() -> str:
    if not USER_AGENT or "@" not in USER_AGENT:
        raise RuntimeError(
            "The SEC requires every automated reader to identify itself with a "
            "contact address, and blocks those that don't. Set LEDGERLINE_UA in "
            ".env, e.g.\n"
            '  LEDGERLINE_UA="Ledgerline research you@example.com"'
        )
    return USER_AGENT


def fetch(url: str, cache_key: str | None = None, retries: int = 3,
          refresh: bool = False) -> bytes:
    """GET with throttle, gzip, and optional on-disk cache.

    A FACT is immutable once a filing is accepted; a DOCUMENT that aggregates
    facts is not -- companyfacts grows with every filing, and today's daily
    index is still being appended to. `refresh=True` skips the cache READ but
    never the write, so a refetch replaces the stale copy and the next plain
    read is free. Default False, so every existing caller is untouched.
    """
    path = os.path.join(CACHE, cache_key) if cache_key else None
    if path and os.path.exists(path) and not refresh:
        STATS["cache_hits"] += 1
        with open(path, "rb") as fh:
            return fh.read()

    ua = _require_ua()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle()
            # Counted per attempt, not per success: a 403 or a retry loop is
            # still traffic the SEC sees, and the runs table is the place that
            # number has to be honest.
            STATS["requests"] += 1
            req = urllib.request.Request(
                url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
        except urllib.error.HTTPError as exc:
            last_err = exc
            # Any 4xx is a fact about the request, not a transient fault --
            # retrying cannot fix it. This used to re-raise only 404, so SEC's
            # 403 for a not-yet-published daily index (every weekend) looped
            # through the retries and surfaced as a raw traceback that blamed
            # the User-Agent. daily_index() catches HTTPError and treats it as
            # "no list published today", which is the truth.
            if 400 <= exc.code < 500:
                raise
            time.sleep(2**attempt)
            continue
        except Exception as exc:  # transient network
            last_err = exc
            time.sleep(2**attempt)
            continue

        STATS["bytes_fetched"] += len(body)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(body)
        return body

    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def fetch_json(url: str, cache_key: str | None = None, refresh: bool = False) -> dict:
    return json.loads(fetch(url, cache_key, refresh=refresh))


# ---------------------------------------------------------------- state store

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    cik      TEXT PRIMARY KEY,
    ticker   TEXT,
    name     TEXT,
    sic      TEXT
);
CREATE TABLE IF NOT EXISTS filings (
    accession    TEXT PRIMARY KEY,
    cik          TEXT,
    ticker       TEXT,
    form         TEXT,
    filing_date  TEXT,
    period       TEXT,
    primary_doc  TEXT
);
-- FIX (FINDINGS §3): the old PK was (cik, metric, period, form), which let the
-- same quarter land twice -- once from the 10-Q, once from the 10-K -- undoing
-- the dedupe done in normalize(). That is why the shipped state.db held 37
-- revenue rows for ~33 quarters, with 2019-06-30 / 2020-06-30 / 2021-06-30
-- each duplicated. Form now travels as a column, not as identity.
CREATE TABLE IF NOT EXISTS metrics (
    cik      TEXT,
    metric   TEXT,
    end_date TEXT,
    kind     TEXT,
    start_date TEXT,
    value    REAL,
    fy       INTEGER,
    fp       TEXT,
    form     TEXT,
    filed    TEXT,
    concept  TEXT,
    origin   TEXT,
    sources  TEXT,
    PRIMARY KEY (cik, metric, end_date, kind)
);
CREATE TABLE IF NOT EXISTS coverage (
    cik      TEXT,
    metric   TEXT,
    ratio    REAL,
    scoreable INTEGER,
    reason   TEXT,
    computed_at TEXT,
    PRIMARY KEY (cik, metric)
);
-- DEAD. Superseded by job_runs: the run_date PK meant a retry after a failure
-- silently overwrote the record of the failure it was retrying, and `scanned`
-- and `changed` were both written as len(hits), so the market-wide index count
-- -- the number that demonstrates the Tier 0 cost claim -- was never stored.
-- Left in place (0 rows) rather than dropped, so nothing in this shared schema
-- string is destructive; drop it in a cleanup that touches nothing else.
CREATE TABLE IF NOT EXISTS runs (
    run_date    TEXT PRIMARY KEY,
    scanned     INTEGER,
    changed     INTEGER,
    scored      INTEGER,
    gated_in    INTEGER,
    started_at  TEXT,
    finished_at TEXT
);
-- One row per job EXECUTION, not per day. requests/cache_hits/bytes_fetched
-- exist because ROADMAP §10 gates scaling on cost-flat-in-universe-size, and
-- that needs a measurement rather than an assertion. index_rows is the
-- market-wide daily-index count; universe_hits the filtered one. A quiet day
-- is a row with index_rows > 0 and universe_hits = 0 -- the old scan returned
-- before writing anything, so the day the cost architecture is loudest about
-- was the one day it left no evidence.
CREATE TABLE IF NOT EXISTS job_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job           TEXT NOT NULL,
    as_of         TEXT,
    status        TEXT NOT NULL,          -- running | ok | failed
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    requests      INTEGER DEFAULT 0,
    cache_hits    INTEGER DEFAULT 0,
    bytes_fetched INTEGER DEFAULT 0,
    index_rows    INTEGER DEFAULT 0,
    universe_hits INTEGER DEFAULT 0,
    filers_done   INTEGER DEFAULT 0,
    filers_failed INTEGER DEFAULT 0,
    restatements  INTEGER DEFAULT 0,
    scored        INTEGER DEFAULT 0,
    gated_in      INTEGER DEFAULT 0,
    error         TEXT
);
-- Replaces scripts/backfill_state.json, which lived outside the database, so
-- `check` could not distinguish a filer never pulled from one pulled and
-- holding no XBRL. facts_filed_max is the staleness key: a filer whose daily-
-- index filing date is newer than the newest `filed` in its cached facts needs
-- a refetch, which is how scan decides what to refresh rather than refreshing
-- the universe.
CREATE TABLE IF NOT EXISTS ingest_state (
    cik             TEXT PRIMARY KEY,
    status          TEXT NOT NULL,        -- ok | no_facts | error
    last_run_id     INTEGER,
    facts_filed_max TEXT,
    rows            INTEGER,
    metrics         INTEGER,
    low_coverage    TEXT,
    error           TEXT,
    updated_at      TEXT
);
-- normalize() has carried the full vintage list on every row since FINDINGS §5
-- and persist_metrics wrote only the newest, so nothing downstream of sqlite
-- could tell a first publication from a revision. Measured cost: 1.08x the
-- top-level row count -- the whole revision history for 8% more rows.
CREATE TABLE IF NOT EXISTS vintages (
    cik      TEXT,
    metric   TEXT,
    end_date TEXT,
    kind     TEXT,
    filed    TEXT,
    value    REAL,
    form     TEXT,
    concept  TEXT,
    origin   TEXT,
    sources  TEXT,
    PRIMARY KEY (cik, metric, end_date, kind, filed)
);
CREATE INDEX IF NOT EXISTS vintages_by_filed ON vintages (filed);
-- The revision event, emitted rather than applied: the vintage it supersedes
-- stays in `vintages` untouched. Detection is vintage-list GROWTH, not
-- form = '/A' -- measured, only 6 of 624 revisions (0.96%) arrived on an
-- amended form, so on_amendment is a flag on the event and not the trigger.
-- `material` is a column and not a filter: 42.5% of measured revisions fall
-- under 1% relative, and dropping them at write time destroys the denominator
-- needed to say what fraction of restatements matter. The PK is the
-- superseding vintage, which makes re-ingest idempotent for free.
CREATE TABLE IF NOT EXISTS restatements (
    cik          TEXT,
    metric       TEXT,
    end_date     TEXT,
    kind         TEXT,
    filed        TEXT,
    prior_filed  TEXT,
    prior_value  REAL,
    value        REAL,
    rel_change   REAL,
    form         TEXT,
    on_amendment INTEGER,
    material     INTEGER,
    detected_run INTEGER,
    detected_at  TEXT,
    PRIMARY KEY (cik, metric, end_date, kind, filed)
);
CREATE INDEX IF NOT EXISTS restatements_by_cik ON restatements (cik, end_date);
-- Point-in-time coverage, superseding `coverage` (0 rows, dead: its PK
-- (cik, metric) has no as_of, and coverage depends on the cutoff --
-- universe.admit has always judged it on an as_of snapshot, so the old table
-- never could hold what was actually computed). `expected` and `achieved`
-- exist to make the structural-ceiling finding auditable: AVERAGED_FLOWS
-- correctly refuses to difference a weighted-average share count, so a filer
-- tagging quarterly diluted shares in each 10-Q but only an annual figure in
-- the 10-K cannot exceed 3 of 4 quarters, and the global COVERAGE_MIN of 0.90
-- therefore suppresses dilution_yoy in 92.3% of scoreable filers. Recorded
-- here; deliberately NOT acted on -- unsuppressing a diagnostic in ~92% of
-- the universe would apply a weight fitted on the ~8% where it existed.
CREATE TABLE IF NOT EXISTS coverage_pit (
    cik         TEXT,
    as_of       TEXT,
    metric      TEXT,
    ratio       REAL,
    expected    REAL,
    achieved    REAL,
    n           INTEGER,
    scoreable   INTEGER,
    code        TEXT,
    detail      TEXT,
    computed_at TEXT,
    PRIMARY KEY (cik, as_of, metric)
);
-- The filer-level abstention record: the only place n_evaluated,
-- evaluated_weight and can_reach_threshold are ever written down. The score
-- is a weighted hinge sum over a FIXED divisor, so missing weight compresses
-- the scale -- measured median evaluated weight 1.675 of 1.992, and one filer
-- in a 250-filer sample could not reach THRESHOLD at any z while reporting
-- score 0.0. `abstentions` is a JSON dict of diagnostic -> reason code,
-- matching how metrics.sources already stores JSON rather than a join table.
CREATE TABLE IF NOT EXISTS scoreability (
    cik                 TEXT,
    as_of               TEXT,
    ticker              TEXT,
    scoreable           INTEGER,
    code                TEXT,
    detail              TEXT,
    n_evaluated         INTEGER,
    n_tracked           INTEGER,
    evaluated_weight    REAL,
    weight_total        REAL,
    can_reach_threshold INTEGER,
    abstentions         TEXT,
    derived_fraction    REAL,
    fiscal_calendar     TEXT,
    peer_level          INTEGER,
    peer_n              INTEGER,
    computed_at         TEXT,
    PRIMARY KEY (cik, as_of)
);
-- The signal store (Phase 3). EVERY evaluation is persisted -- fires, quiet
-- scoreable quarters, and unscoreable filers with their reason. A fires-only
-- store has no denominator: it can measure precision and can never measure
-- recall, and recall (0.287 against a required 0.60) is what Phase 0 failed
-- on. signal_id is content-addressed (sha256 over cik, as_of, gate_version,
-- score, gated_in, scoreable, reason, flags, z), so a replay is idempotent
-- while the same (cik, as_of) under a CHANGED gate writes a SECOND row beside
-- the first -- that coexistence is the entire mechanism by which a revised
-- gate is ever compared to the one that returned KILL. score is NULL when
-- scoreable = 0, never 0.0: FINDINGS §3's defect was that 0.0 is
-- indistinguishable from "assessed, looks clean", and that mistake must not
-- be re-made at the delivery boundary. accessions is NOT NULL because the
-- README invariant is "a score traces back to accessions or it does not
-- ship", and the persisted record outlives the fact cache, so this is the
-- last place it can be enforced. Writer: ledgerline/emit.py, nothing else.
CREATE TABLE IF NOT EXISTS signals (
    signal_id        TEXT PRIMARY KEY,
    seq              INTEGER NOT NULL,
    schema_version   TEXT NOT NULL,
    cik              TEXT NOT NULL,
    ticker           TEXT,
    as_of            TEXT NOT NULL,
    period           TEXT,
    emitted_at       TEXT NOT NULL,
    run_id           TEXT,
    source           TEXT NOT NULL,       -- scan | score | emit | replay
    split            TEXT,
    score            REAL,                -- NULL when scoreable = 0
    gated_in         INTEGER NOT NULL,
    scoreable        INTEGER NOT NULL,
    reason           TEXT,
    reason_code      TEXT,
    n_flags          INTEGER NOT NULL DEFAULT 0,
    flags            TEXT NOT NULL,
    z                TEXT NOT NULL,
    abstentions      TEXT NOT NULL,
    evaluated_weight REAL,
    weight_total     REAL,
    accessions       TEXT NOT NULL,
    coverage_failed  TEXT NOT NULL,
    derived_fraction REAL,
    gate_version     TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    record           TEXT NOT NULL        -- full payload + embedded run block
);
CREATE UNIQUE INDEX IF NOT EXISTS signals_seq ON signals (seq);
CREATE INDEX IF NOT EXISTS signals_cik_asof ON signals (cik, as_of);
CREATE INDEX IF NOT EXISTS signals_gate ON signals (gate_version, as_of);
CREATE INDEX IF NOT EXISTS signals_run ON signals (run_id);
-- The append-only invariant, enforced rather than stated: prose is not an
-- invariant, RAISE(ABORT) is. These triggers are why the writer uses INSERT
-- OR IGNORE and never OR REPLACE -- OR REPLACE would fire the delete trigger
-- and abort, which is correct.
CREATE TRIGGER IF NOT EXISTS signals_no_update
BEFORE UPDATE ON signals BEGIN
    SELECT RAISE(ABORT, 'signals is append-only: emit a new signal, do not edit');
END;
CREATE TRIGGER IF NOT EXISTS signals_no_delete
BEFORE DELETE ON signals BEGIN
    SELECT RAISE(ABORT, 'signals is append-only: emit a new signal, do not delete');
END;

-- Forward scoring of persisted signals at +1/+2/+4 quarters (track.py).
-- resolved_at is IN the primary key because a resolution is a VINTAGE, not a
-- fact: the deterioration label is computed from later filings and a
-- restatement can flip it -- RESTATEMENT is itself one of the five criteria.
-- Overwriting would silently rewrite history in the one table whose whole job
-- is remembering what was known when (FINDINGS 5's defect in a new place).
-- PENDING is never stored: the absence of a row IS pending, which also keeps
-- the table from filling with daily restatements of ignorance. label_rule
-- carries the horizon, so a +1q row can never be silently compared to the
-- +4q pre-registered rule the holdout hit rate was measured against.
CREATE TABLE IF NOT EXISTS signal_scores (
    signal_id           TEXT NOT NULL,
    horizon_q           INTEGER NOT NULL,
    resolved_at         TEXT NOT NULL,
    outcome             TEXT NOT NULL,    -- DETERIORATED | CLEAN
    event_period        TEXT,
    n_quarters_observed INTEGER NOT NULL,
    criteria            TEXT,             -- json list of tripped criteria
    label_rule          TEXT NOT NULL,
    PRIMARY KEY (signal_id, horizon_q, resolved_at)
);

-- Dated snapshots of the live record. ROADMAP 10 asks whether performance
-- decays; without a time series "decays" has nothing to be measured against.
-- The two false-positive rates sit in separate columns because their
-- denominators differ: _clean counts quarters not followed by deterioration,
-- _control_filer counts quarters of filers with no resolved deterioration at
-- all, and only the latter is comparable to the frozen Phase 0 0.0383. No
-- brier column: the score-to-probability link was fitted on tuning and the
-- only outcomes available today replay that same split (see reliability.py).
CREATE TABLE IF NOT EXISTS track_record (
    gate_version     TEXT NOT NULL,
    horizon_q        INTEGER NOT NULL,
    computed_at      TEXT NOT NULL,
    n_resolved       INTEGER,
    n_fires          INTEGER,
    recall           REAL,
    fpr_per_quarter_control_filer REAL,
    fpr_per_quarter_clean         REAL,
    payload          TEXT,               -- the full JSON, re-readable later
    PRIMARY KEY (gate_version, horizon_q, computed_at)
);
"""

# Migrations, ordered and carried on PRAGMA user_version. CREATE TABLE IF NOT
# EXISTS is a silent no-op against an existing table, so a column added inside
# the SCHEMA string above would be read by the code and never created in the
# live state.db on disk -- an ALTER here is the ONLY way a column is ever added.
# Later phases append to MIGRATIONS; renumbering an existing step would make a
# db migrated under one order silently skip another's step, so steps are
# append-only too.
SCHEMA_VERSION: int = 1


def _migration_1(conn: sqlite3.Connection) -> None:
    """filings.first_seen_run: which run first saw an accession. Combined with
    detect_changes(record=False) + record_filings() after a filer ingests, this
    fixes the resumability hole where a crash mid-run marked accessions known
    and a rerun skipped them permanently."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(filings)")}
    if "first_seen_run" not in cols:
        conn.execute("ALTER TABLE filings ADD COLUMN first_seen_run INTEGER")


MIGRATIONS: list = [_migration_1]


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply outstanding migrations. Idempotent: user_version records progress."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for step, apply in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            apply(conn)
            conn.execute(f"PRAGMA user_version = {step}")


def db() -> sqlite3.Connection:
    os.makedirs(DATA, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def pad(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


# ------------------------------------------------------------------- universe


def load_ticker_map() -> dict[str, dict]:
    """SEC's canonical ticker -> CIK map. One request, whole market."""
    raw = fetch_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json")
    return {
        row["ticker"].upper(): {
            "cik": pad(row["cik_str"]),
            "name": row["title"],
            "ticker": row["ticker"].upper(),
        }
        for row in raw.values()
    }


def set_universe(tickers: list[str]) -> list[dict]:
    tmap = load_ticker_map()
    rows = [tmap[t.upper()] for t in tickers if t.upper() in tmap]
    missing = [t for t in tickers if t.upper() not in tmap]
    conn = db()
    with conn:
        # ON CONFLICT DO UPDATE on the three columns actually supplied, never
        # INSERT OR REPLACE: REPLACE deletes the whole row, which nulled the
        # sic column on every re-run. With 1,496 of 1,498 universe rows
        # carrying a SIC, one `watch --add` with a larger list would have
        # nulled them all -- after which admission rejects unknown SIC and the
        # next case build silently returns an empty set until 1,500
        # submissions files are re-fetched.
        conn.executemany(
            "INSERT INTO universe (cik, ticker, name) VALUES (?,?,?) "
            "ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker, "
            "name=excluded.name",
            [(r["cik"], r["ticker"], r["name"]) for r in rows],
        )
    conn.close()
    if missing:
        print(f"  [warn] no CIK for: {', '.join(missing)}")
    return rows


def universe() -> dict[str, dict]:
    conn = db()
    rows = conn.execute("SELECT cik, ticker, name FROM universe").fetchall()
    conn.close()
    return {r[0]: {"cik": r[0], "ticker": r[1], "name": r[2]} for r in rows}


# ------------------------------------------- Tier 0: daily-index change detect

TRACKED_FORMS = {"10-K", "10-Q", "8-K", "20-F", "10-K/A", "10-Q/A"}
AMENDED_FORMS = {"10-K/A", "10-Q/A"}

# Forms that can move a number. TRACKED_FORMS stays broader for change
# detection (an 8-K is still a filing worth recording), but 8-Ks carry no XBRL
# fundamentals and were 74% of two years' tracked-form hits -- once the
# companyfacts cache is correctly invalidated, letting an 8-K trigger a ~3.4MB
# refetch that cannot change any diagnostic is pure cost. This set gates the
# refetch-and-assess path.
PERIODIC_FORMS = {"10-K", "10-Q", "20-F", "10-K/A", "10-Q/A"}


def _qtr(d: date) -> str:
    return f"QTR{(d.month - 1) // 3 + 1}"


def daily_index(d: date, refresh: bool = False) -> list[dict]:
    """Every filing SEC accepted on date `d`, across all filers. ONE request.

    The core cost optimization: replaces N per-company polls with a single
    flat-file read, regardless of universe size.

    `refresh` matters for TODAY's index, which is still being appended to as
    the SEC accepts filings: a 10:00 scan that cached a partial copy would
    otherwise pin the evening rerun to the morning's view. Past days' indexes
    are complete and the cache is sound.
    """
    url = (
        f"https://www.sec.gov/Archives/edgar/daily-index/"
        f"{d.year}/{_qtr(d)}/form.{d.strftime('%Y%m%d')}.idx"
    )
    try:
        raw = fetch(url, f"idx/form.{d.strftime('%Y%m%d')}.idx", refresh=refresh)
    except urllib.error.HTTPError:
        return []  # weekend / holiday / not yet published

    out = []
    for line in raw.decode("latin-1").splitlines():
        # Layout: Form Type | Company Name | CIK | Date Filed | File Name.
        # Company names contain spaces, so split from the right: the last three
        # whitespace-delimited tokens are always cik, date, filename.
        if "edgar/data" not in line:
            continue
        parts = line.rsplit(None, 3)
        if len(parts) != 4:
            continue
        head, cik, filed, fname = parts
        if not cik.isdigit() or len(filed) != 8 or not filed.isdigit():
            continue
        out.append(
            {
                "form": head[:12].strip(),
                "name": head[12:].strip(),
                "cik": pad(cik),
                "filing_date": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
                "file": fname,
                "accession": fname.split("/")[-1].replace(".txt", ""),
            }
        )
    return out


def known_accessions() -> set[str]:
    conn = db()
    known = {r[0] for r in conn.execute("SELECT accession FROM filings")}
    conn.close()
    return known


def match_universe(rows: list[dict], uni: dict[str, dict],
                   known: set[str]) -> list[dict]:
    """The daily-index rows that are (a) in our universe, (b) a tracked form,
    (c) not already recorded. One filter shared by detect_changes and
    ingest.scan, so the two cannot drift on what counts as a hit."""
    hits = []
    for row in rows:
        if row["cik"] not in uni or row["form"] not in TRACKED_FORMS:
            continue
        if row["accession"] in known:
            continue
        row["ticker"] = uni[row["cik"]]["ticker"]
        row["is_amendment"] = row["form"] in AMENDED_FORMS
        hits.append(row)
    return hits


def detect_changes(days_back: int = 1, as_of: date | None = None,
                   record: bool = True) -> list[dict]:
    """Filings that are (a) in our universe, (b) a tracked form, (c) not already
    recorded. On a quiet day this returns [] and the caller exits before
    anything downstream runs -- a near-zero-cost day.

    `record=False` detects without writing to `filings`. The old behaviour
    wrote every hit before any work happened, so a crash mid-run marked
    accessions known and a rerun skipped them permanently; ingest.scan records
    a filing only after its filer's ingest succeeds, via record_filings().
    """
    uni = universe()
    if not uni:
        return []
    known = known_accessions()

    as_of = as_of or date.today()
    hits, scanned = [], 0
    for i in range(days_back):
        rows = daily_index(as_of - timedelta(days=i))
        scanned += len(rows)
        hits += match_universe(rows, uni, known)

    if record:
        record_filings(hits)
    print(f"  scanned {scanned} market-wide filings -> {len(hits)} in universe")
    return hits


def record_filings(hits: list[dict], run_id: int | None = None) -> int:
    """Mark accessions as known. Called per filer AFTER that filer ingests, so
    an accession is never marked done ahead of the work it triggers."""
    if not hits:
        return 0
    conn = db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO filings "
            "(accession, cik, ticker, form, filing_date, period, primary_doc, "
            " first_seen_run) VALUES (?,?,?,?,?,?,?,?)",
            [
                (h["accession"], h["cik"], h.get("ticker"), h["form"],
                 h["filing_date"], None, h.get("file"), run_id)
                for h in hits
            ],
        )
    conn.close()
    return len(hits)


def submissions(cik: str) -> dict:
    """Per-company filing history. Backfill only -- never poll this.

    Cached, unlike the daily index. Unlike companyfacts this file IS mutable
    (new filings append to it), so the cache is only sound for the static
    company metadata read off it -- SIC, name, fiscal year end. Tier 0 change
    detection deliberately does not read this file; it reads the daily index,
    which is the whole point of the cost architecture.
    """
    return fetch_json(
        f"https://data.sec.gov/submissions/CIK{pad(cik)}.json",
        f"submissions/CIK{pad(cik)}.json",
    )


def set_sic(rows: list[tuple[str, str | None]]) -> int:
    """Persist (cik, sic) into the universe table.

    The column existed from the first schema but nothing ever wrote it, so
    every `cases` run re-fetched 1500 submissions files to recover a value that
    does not change. Written once here, read back by universe.fetch_sic.
    """
    conn = db()
    with conn:
        conn.executemany("UPDATE universe SET sic = ? WHERE cik = ?",
                         [(sic, cik) for cik, sic in rows])
    conn.close()
    return len(rows)


def sic_map() -> dict[str, str | None]:
    conn = db()
    rows = conn.execute("SELECT cik, sic FROM universe").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------ Tier 1: XBRL metric layer

# metric -> ordered candidate us-gaap concepts. Lower index = higher priority.
# All candidates are merged rather than first-hit-wins: filers migrate concepts
# over time (NVDA moved off RevenueFromContractWithCustomer*), and locking to
# the first available one silently freezes a dead series.
METRIC_MAP: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    # Used by the outcome labeler, not by the gate. A large writedown is one of
    # the deterioration criteria in label.py.
    "impairment": [
        "AssetImpairmentCharges",
        "GoodwillImpairmentLoss",
        "ImpairmentOfIntangibleAssetsExcludingGoodwill",
        "TangibleAssetImpairmentCharges",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "total_assets": ["Assets"],
    "inventory": ["InventoryNet"],
    "receivables": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
        "AccountsReceivableGrossCurrent",
    ],
    # FIX (FINDINGS §3): basic-shares fallback removed. Mixing basic and diluted
    # across periods manufactures dilution that never happened.
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}

# FIX (FINDINGS §3): these two are SUMMED across components rather than
# resolved first-hit, because the old single-concept version was structurally
# blind.
#   total_debt: LongTermDebt alone misses revolver draws and current
#     maturities, so net_debt was understated for exactly the leveraged names
#     the LEVERAGE flag exists to catch (CVNA).
#   deferred_revenue: current-only meant a reclass between current and
#     noncurrent contract liability read as a demand break. DOCU's
#     DEFERRED_VS_REVENUE_GAP fire is suspect for this reason.
SUMMED_METRICS: dict[str, list[list[str]]] = {
    "total_debt": [
        ["LongTermDebtNoncurrent", "LongTermDebt"],
        ["LongTermDebtCurrent", "DebtCurrent"],
        ["ShortTermBorrowings", "OtherShortTermBorrowings"],
    ],
    "deferred_revenue": [
        ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"],
        ["ContractWithCustomerLiabilityNoncurrent", "DeferredRevenueNoncurrent"],
    ],
}

FLOW_METRICS = {
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capex",
    "impairment",
}

# Quantities that cannot be negative. If differencing two cumulatives produces
# a negative one, the inputs disagreed and the result is refused rather than
# emitted -- see derive.same_basis and the "no wrong numbers" invariant.
NON_NEGATIVE_FLOWS = {"revenue", "cost_of_revenue"}

# A weighted-average share count is NOT additive across periods: Q4 is not
# FY minus 9M. Differencing it produced 266 negative share counts across 40
# cached filers (GPK Q4-2012 = -1,300,000 against ~397M reported), and
# dilution_yoy then read a ratio of two garbage numbers as a plausible 23%
# buyback that the corporate-action guard could not catch. Weighted averages
# take the duration fact closest to a single quarter and nothing else.
AVERAGED_FLOWS = {"diluted_shares"}

ACCEPTED_FORMS = ("10-K", "10-Q", "20-F", "10-K/A", "10-Q/A")


def companyfacts(cik: str, refresh: bool = False) -> dict:
    """One filer's XBRL facts. Cached, but the cache is only sound until the
    filer files again.

    The old docstring said "immutable per accepted filing -> cache
    permanently". True of a FACT, false of this DOCUMENT, which is a
    per-company aggregate that grows with every filing. The consequence was a
    live defect: scan detected a new 10-Q via the daily index, then scored the
    facts file written at backfill time -- the filing that triggered the scan
    was not in the data the scan scored. Callers that just learned the filer
    filed (ingest.scan, backfill --refresh) pass refresh=True; everything else
    reads the cache for free.
    """
    return fetch_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{pad(cik)}.json",
        f"facts/CIK{pad(cik)}.json",
        refresh=refresh,
    )


def _pick_units(concept: dict) -> list[dict]:
    units = concept.get("units", {})
    for key in ("USD", "shares", "USD/shares"):
        if key in units:
            return units[key]
    return next(iter(units.values()), [])


def _raw_rows(facts: dict, metric: str, concepts: list[str]) -> list[dict]:
    """Flatten candidate concepts into one list of comparable fact rows."""
    rows = []
    for rank, concept in enumerate(concepts):
        if concept not in facts:
            continue
        for f in _pick_units(facts[concept]):
            if f.get("form") not in ACCEPTED_FORMS:
                continue
            if f.get("end") is None or f.get("val") is None:
                continue
            rows.append(
                {
                    "metric": metric,
                    "concept": concept,
                    "rank": rank,
                    "start": f.get("start"),
                    "end": f["end"],
                    "value": float(f["val"]),
                    "fy": f.get("fy"),
                    "fp": f.get("fp"),
                    "form": f.get("form"),
                    "filed": f.get("filed"),
                    "accession": f.get("accn"),
                }
            )
    return rows


def _pit_rows(rows: list[dict]) -> list[dict]:
    """Point-in-time (balance sheet) values, one row per period end carrying
    its full vintage history.

    Same fix as derive.derive_quarterly: keeping only the newest vintage and
    then truncating on `filed` hid balances that had been public for years and
    substituted restated figures for the originals.
    """
    seq = derive.collect_vintages(
        [r for r in rows if not r.get("start")], key=lambda r: r["end"]
    )
    out = []
    for end in sorted(seq):
        vints = [
            {**r, "kind": "PIT", "origin": "reported", "sources": [r.get("accession")]}
            for r in seq[end]
        ]
        out.append({**vints[-1], "vintages": vints})
    return out


def _quarter_only_rows(rows: list[dict]) -> list[dict]:
    """Duration facts that are already a single quarter, with vintages.

    For non-additive quantities (weighted-average share counts). A period that
    the filer only ever reported cumulatively simply has no quarterly value,
    and the diagnostic that needs it returns None.
    """
    q = [r for r in rows
         if r.get("start") and derive.classify(r["start"], r["end"]) == "Q"]
    seq = derive.collect_vintages(q, key=lambda r: r["end"])
    out = []
    for end in sorted(seq):
        vints = [{**r, "kind": "Q", "origin": "reported", "sources": [r.get("accession")]}
                 for r in seq[end]]
        out.append({**vints[-1], "vintages": vints})
    return out


def _summed_pit(facts: dict, metric: str, groups: list[list[str]]) -> list[dict]:
    """Sum independent components at each period end.

    A missing component contributes zero rather than voiding the total -- a
    filer with no short-term borrowings simply does not tag the concept. A
    missing FIRST group (the primary component) does void the period, since
    that means the metric genuinely is not reported.
    """
    per_group: list[dict[str, dict]] = []
    for group in groups:
        rows = _pit_rows(_raw_rows(facts, metric, group))
        per_group.append({r["end"]: r for r in rows})

    if not per_group or not per_group[0]:
        return []

    out = []
    for end, primary in per_group[0].items():
        # The sum has a vintage wherever ANY component was filed or restated;
        # at each such date every component contributes its own newest vintage.
        dates = sorted({
            v.get("filed") or ""
            for g in per_group if end in g
            for v in g[end].get("vintages", [g[end]])
        })
        vints = []
        for d in dates:
            head = derive.newest_at(primary.get("vintages", [primary]), d)
            if head is None:
                continue  # the primary component is not public yet -> no total
            total, sources, concepts = 0.0, [], []
            for g in per_group:
                row = g.get(end)
                if row is None:
                    continue
                hit = derive.newest_at(row.get("vintages", [row]), d)
                if hit is None:
                    continue  # component not yet filed -> contributes zero
                total += hit["value"]
                sources += hit.get("sources", [])
                concepts.append(hit["concept"])
            vints.append(
                {
                    "metric": metric,
                    "kind": "PIT",
                    "start": None,
                    "end": end,
                    "value": total,
                    "fy": head.get("fy"),
                    "fp": head.get("fp"),
                    "form": head.get("form"),
                    "filed": d,
                    "concept": "+".join(concepts),
                    "origin": "summed",
                    "sources": [s for s in sources if s],
                }
            )
        if vints:
            out.append({**vints[-1], "vintages": vints})
    return sorted(out, key=lambda r: r["end"])


def normalize(cik: str, facts: dict | None = None) -> dict[str, list[dict]]:
    """Raw XBRL facts -> normalized, provenance-tagged metric dictionary.

    Flow metrics go through derive.derive_quarterly(), which differences YTD
    cumulatives instead of discarding them. Stock metrics take point-in-time
    values. Every row carries concept, form, accession, `filed`, and whether it
    was reported, derived, or summed.

    No point-in-time filtering happens here -- call `as_of()` for that. Because
    a derived row's `filed` is the later of its two inputs, filtering after
    derivation is exactly equivalent to filtering before it.
    """
    if facts is None:
        try:
            facts = companyfacts(cik).get("facts", {}).get("us-gaap", {})
        except urllib.error.HTTPError:
            return {}

    out: dict[str, list[dict]] = {}

    for metric, concepts in METRIC_MAP.items():
        rows = _raw_rows(facts, metric, concepts)
        if not rows:
            continue
        if metric in FLOW_METRICS:
            result = derive.derive_quarterly(
                rows, non_negative=metric in NON_NEGATIVE_FLOWS
            )
        elif metric in AVERAGED_FLOWS:
            result = _quarter_only_rows(rows)
        else:
            result = _pit_rows(rows)
        if result:
            out[metric] = result

    for metric, groups in SUMMED_METRICS.items():
        rows = _summed_pit(facts, metric, groups)
        if rows:
            out[metric] = rows

    return out


def as_of(norm: dict, cutoff: str) -> dict:
    """Drop every fact FILED after `cutoff`.

    Uses the XBRL `filed` date, never period end, so there is no lookahead: a
    quarter ending 3/31 filed 5/10 is invisible on 4/30. This is the ONLY
    truncation primitive in the codebase -- production and backtest both call
    it, so they cannot diverge.
    """
    out = {}
    for metric, rows in norm.items():
        keep = []
        for r in rows:
            vints = r.get("vintages")
            if not vints:
                if (r.get("filed") or "9999-12-31") <= cutoff:
                    keep.append(r)
                continue
            # Not "was this row filed by the cutoff" but "which version of it
            # had been published by then". A quarter restated in 2014 was still
            # public, at its original value, in 2012.
            hit = derive.newest_at(vints, cutoff)
            if hit is not None:
                keep.append({**r, **hit, "vintages": vints})
        if keep:
            out[metric] = keep
    return out


# ------------------------------------------------------------------- coverage


def coverage_report(norm: dict) -> dict[str, dict]:
    """Per-metric coverage against the revenue series, with a scoreable flag.

    Phase 0 rule: a filer below derive.COVERAGE_MIN on a flow metric is excluded
    from scoring on that metric, with the reason logged. Silently scoring a
    gappy filer is how the original OCF bug stayed invisible.
    """
    ref = norm.get("revenue", [])
    report = {}
    # capex and impairment are episodic by nature -- a filer with no writedowns
    # correctly has no impairment facts. Coverage is only meaningful for metrics
    # that should appear every quarter.
    for metric in (FLOW_METRICS | AVERAGED_FLOWS) - {"capex", "impairment"}:
        rows = norm.get(metric, [])
        ratio = derive.coverage(rows, ref)
        ok = ratio >= derive.COVERAGE_MIN
        report[metric] = {
            "ratio": round(ratio, 3),
            "n": len(rows),
            "scoreable": ok,
            "reason": None if ok
            else f"coverage {ratio:.0%} < {derive.COVERAGE_MIN:.0%}",
            # The countable code beside the sentence. The sentence stays
            # exactly as it was -- existing readers of ratio/n/scoreable/
            # reason are untouched, and the code is what a dashboard can
            # aggregate without parsing prose.
            "code": None if ok else reasons.INPUT_COVERAGE_LOW,
        }
    return report


def persist_metrics(cik: str, norm: dict[str, list[dict]]) -> int:
    conn = db()
    payload = [
        (
            cik, m, r["end"], r["kind"], r.get("start"), r["value"], r.get("fy"),
            r.get("fp"), r.get("form"), r.get("filed"), r.get("concept"),
            r.get("origin"), json.dumps(r.get("sources", [])),
        )
        for m, rows in norm.items()
        for r in rows
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO metrics "
            "(cik, metric, end_date, kind, start_date, value, fy, fp, form, filed, "
            " concept, origin, sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            payload,
        )
    conn.close()
    return len(payload)


def persist_coverage(cik: str, as_of: str, report: dict[str, dict]) -> int:
    """Write one filer's point-in-time coverage report to coverage_pit.

    INSERT OR REPLACE keyed on (cik, as_of, metric): re-running a date
    corrects it instead of accumulating -- the as_of in the key is exactly
    what the dead `coverage` table lacked.
    """
    now = date.today().isoformat()
    conn = db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO coverage_pit "
            "(cik, as_of, metric, ratio, expected, achieved, n, scoreable, "
            " code, detail, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (cik, as_of, m, c.get("ratio"), c.get("expected"),
                 c.get("achieved"), c.get("n"), int(bool(c.get("scoreable"))),
                 c.get("code"), c.get("reason"), now)
                for m, c in report.items()
            ],
        )
    conn.close()
    return len(report)


def persist_scoreability(rows: list[dict]) -> int:
    """Write filer-level scoreability records, one per (cik, as_of)."""
    now = date.today().isoformat()
    conn = db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO scoreability "
            "(cik, as_of, ticker, scoreable, code, detail, n_evaluated, "
            " n_tracked, evaluated_weight, weight_total, can_reach_threshold, "
            " abstentions, derived_fraction, fiscal_calendar, peer_level, "
            " peer_n, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (r["cik"], r["as_of"], r.get("ticker"),
                 int(bool(r.get("scoreable"))), r.get("code"), r.get("detail"),
                 r.get("n_evaluated"), r.get("n_tracked"),
                 r.get("evaluated_weight"), r.get("weight_total"),
                 None if r.get("can_reach_threshold") is None
                 else int(bool(r.get("can_reach_threshold"))),
                 json.dumps(r.get("abstentions", {})),
                 r.get("derived_fraction"), r.get("fiscal_calendar"),
                 r.get("peer_level"), r.get("peer_n"), now)
                for r in rows
            ],
        )
    conn.close()
    return len(rows)
