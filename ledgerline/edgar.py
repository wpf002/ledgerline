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

from . import derive

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
DB_PATH = os.path.join(DATA, "state.db")

USER_AGENT = os.environ.get("LEDGERLINE_UA", "")
MIN_INTERVAL = 0.11  # ~9 req/sec, under SEC's ceiling

os.makedirs(CACHE, exist_ok=True)

_last_call = [0.0]


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


def fetch(url: str, cache_key: str | None = None, retries: int = 3) -> bytes:
    """GET with throttle, gzip, and optional on-disk cache.

    XBRL facts and archived filings are immutable once a filing is accepted, so
    caching them is free correctness rather than a staleness risk.
    """
    path = os.path.join(CACHE, cache_key) if cache_key else None
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()

    ua = _require_ua()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle()
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

        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(body)
        return body

    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def fetch_json(url: str, cache_key: str | None = None) -> dict:
    return json.loads(fetch(url, cache_key))


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
CREATE TABLE IF NOT EXISTS runs (
    run_date    TEXT PRIMARY KEY,
    scanned     INTEGER,
    changed     INTEGER,
    scored      INTEGER,
    gated_in    INTEGER,
    started_at  TEXT,
    finished_at TEXT
);
"""


def db() -> sqlite3.Connection:
    os.makedirs(DATA, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
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
        conn.executemany(
            "INSERT OR REPLACE INTO universe (cik, ticker, name) VALUES (?,?,?)",
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


def _qtr(d: date) -> str:
    return f"QTR{(d.month - 1) // 3 + 1}"


def daily_index(d: date) -> list[dict]:
    """Every filing SEC accepted on date `d`, across all filers. ONE request.

    The core cost optimization: replaces N per-company polls with a single
    flat-file read, regardless of universe size.
    """
    url = (
        f"https://www.sec.gov/Archives/edgar/daily-index/"
        f"{d.year}/{_qtr(d)}/form.{d.strftime('%Y%m%d')}.idx"
    )
    try:
        raw = fetch(url, f"idx/form.{d.strftime('%Y%m%d')}.idx")
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


def detect_changes(days_back: int = 1, as_of: date | None = None) -> list[dict]:
    """Filings that are (a) in our universe, (b) a tracked form, (c) not already
    recorded. On a quiet day this returns [] and the caller exits before
    anything downstream runs -- a near-zero-cost day."""
    uni = universe()
    if not uni:
        return []
    conn = db()
    known = {r[0] for r in conn.execute("SELECT accession FROM filings")}

    as_of = as_of or date.today()
    hits, scanned = [], 0
    for i in range(days_back):
        for row in daily_index(as_of - timedelta(days=i)):
            scanned += 1
            if row["cik"] not in uni or row["form"] not in TRACKED_FORMS:
                continue
            if row["accession"] in known:
                continue
            row["ticker"] = uni[row["cik"]]["ticker"]
            row["is_amendment"] = row["form"] in AMENDED_FORMS
            hits.append(row)

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO filings "
            "(accession, cik, ticker, form, filing_date, period, primary_doc) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (h["accession"], h["cik"], h["ticker"], h["form"], h["filing_date"],
                 None, h["file"])
                for h in hits
            ],
        )
    conn.close()
    print(f"  scanned {scanned} market-wide filings -> {len(hits)} in universe")
    return hits


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


def companyfacts(cik: str) -> dict:
    """Immutable per accepted filing -> cache permanently."""
    return fetch_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{pad(cik)}.json",
        f"facts/CIK{pad(cik)}.json",
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
        report[metric] = {
            "ratio": round(ratio, 3),
            "n": len(rows),
            "scoreable": ratio >= derive.COVERAGE_MIN,
            "reason": None if ratio >= derive.COVERAGE_MIN
            else f"coverage {ratio:.0%} < {derive.COVERAGE_MIN:.0%}",
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
