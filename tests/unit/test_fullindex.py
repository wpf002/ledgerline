"""
Filer-registry tests: the fixed-width parser, the survivorship property, and
the open-quarter cache rule.

Each test pins a decision or a defect. Fixture realism is load-bearing (the
standing FINDINGS §5 lesson): the .idx text includes a company name of four
space-separated tokens, three of them digits ('1 800 FLOWERS COM INC', taken
from the real 2015Q1 file), and a form type containing a space ('SC 13G/A') --
a fixture where every company name is one token would let a rsplit-based
parser pass. No network: fetch_quarter is stubbed above the wire, and the
sqlite under test is the real one in a tmp directory.
"""
from __future__ import annotations

from datetime import date

import pytest

from ledgerline import edgar, fullindex


def _idx_line(name: str, form: str, cik: str, filed: str, fname: str) -> str:
    return f"{name:<62}{form:<12}{cik:<12}{filed:<12}{fname}"


IDX_2015Q1 = "\n".join([
    "Description:           Master Index of EDGAR Dissemination Feed by Company Name",
    "Company Name" + " " * 50 + "Form Type   CIK         Date Filed  File Name",
    "-" * 130,
    _idx_line("1 800 FLOWERS COM INC", "10-Q", "1084869", "2015-01-30",
              "edgar/data/1084869/0001437749-15-001369.txt"),
    _idx_line("1 800 FLOWERS COM INC", "SC 13G/A", "1084869", "2015-02-10",
              "edgar/data/1084869/0000913760-15-000048.txt"),
    _idx_line("ACME STAYS PUT CORP", "10-K", "222", "2015-02-20",
              "edgar/data/222/0000000222-15-000001.txt"),
]).encode("latin-1")


def _isolated_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def _stub_quarters(monkeypatch, per_quarter: dict[str, bytes]) -> None:
    def fake(quarter: str, refresh: bool = False) -> list[dict]:
        rows = fullindex.parse_company_idx(per_quarter[quarter])
        for r in rows:
            r["quarter"] = quarter
        return rows
    monkeypatch.setattr(fullindex, "fetch_quarter", fake)


def test_company_idx_is_parsed_by_column_not_by_split():
    """company.idx puts the company name FIRST and carries form types with
    spaces, so edgar.daily_index()'s rsplit(None, 3) heuristic -- correct on
    form.idx -- silently mangles the form column here. The parser slices fixed
    columns instead; this fixture is the case that catches a splitter."""
    rows = fullindex.parse_company_idx(IDX_2015Q1)
    flowers = [r for r in rows if r["cik"] == edgar.pad("1084869")]
    assert len(flowers) == 1  # the SC 13G/A row is not a periodic filing
    assert flowers[0]["form"] == "10-Q"
    assert flowers[0]["name"] == "1 800 FLOWERS COM INC"
    assert flowers[0]["filed"] == "2015-01-30"
    assert flowers[0]["accession"] == "0001437749-15-001369"


def test_registry_retains_filers_that_stopped_filing(tmp_path, monkeypatch):
    """The survivorship property, and the whole point of the module: a CIK
    present only in an early quarter survives into the registry with its
    last_periodic recorded, instead of vanishing the way it vanishes from a
    scrape of current index membership."""
    _isolated_db(tmp_path, monkeypatch)
    gone = _idx_line("GONE BY 2016 CORP", "10-Q", "111", "2013-02-01",
                     "edgar/data/111/0000000111-13-000001.txt")
    stays_13 = _idx_line("ACME STAYS PUT CORP", "10-Q", "222", "2013-02-05",
                         "edgar/data/222/0000000222-13-000001.txt")
    stays_24 = _idx_line("ACME STAYS PUT CORP", "10-K", "222", "2024-02-05",
                         "edgar/data/222/0000000222-24-000001.txt")
    newcomer = _idx_line("BORN 2020 INC", "10-Q", "333", "2024-02-09",
                         "edgar/data/333/0000000333-24-000001.txt")
    _stub_quarters(monkeypatch, {
        "2013Q1": gone.encode() + b"\n" + stays_13.encode(),
        "2024Q1": stays_24.encode() + b"\n" + newcomer.encode(),
    })
    fullindex.ingest(start="2013Q1", end="2013Q1")
    fullindex.ingest(start="2024Q1", end="2024Q1")

    reg = {r["cik"]: r for r in fullindex.registry()}
    assert set(reg) == {edgar.pad("111"), edgar.pad("222"), edgar.pad("333")}
    assert reg[edgar.pad("111")]["last_periodic"] == "2013-02-01"
    assert reg[edgar.pad("222")]["n_quarters"] == 2


def test_open_quarter_is_never_served_from_cache(monkeypatch):
    """A closed quarter's index is immutable; the current quarter is still
    accumulating. Caching a partial quarter would freeze a truncated filer
    list and silently shrink the registry -- so fetch_quarter forces
    refresh=True for the open quarter regardless of what the caller passed."""
    today = date(2026, 8, 30)
    assert fullindex.quarter_is_closed("2025Q4", today=today) is True
    assert fullindex.quarter_is_closed("2026Q3", today=today) is False

    seen = {}

    def spy(url, cache_key=None, retries=3, refresh=False):
        seen[cache_key] = refresh
        return IDX_2015Q1

    monkeypatch.setattr(edgar, "fetch", spy)
    monkeypatch.setattr(fullindex, "current_quarter", lambda t=None: "2026Q3")
    fullindex.fetch_quarter("2026Q3", refresh=False)
    fullindex.fetch_quarter("2025Q4", refresh=False)
    assert seen["fullidx/2026Q3-company.idx"] is True
    assert seen["fullidx/2025Q4-company.idx"] is False


def test_reingesting_a_quarter_replaces_rather_than_accumulates(tmp_path, monkeypatch):
    """A quarter is replaced wholesale on re-ingest (and a closed quarter
    already present is skipped without a fetch), so a re-opened or re-run
    quarter can never half-update or duplicate."""
    _isolated_db(tmp_path, monkeypatch)
    _stub_quarters(monkeypatch, {"2015Q1": IDX_2015Q1})
    first = fullindex.ingest(start="2015Q1", end="2015Q1")
    again = fullindex.ingest(start="2015Q1", end="2015Q1")
    forced = fullindex.ingest(start="2015Q1", end="2015Q1", refresh=True)

    assert first["rows"] == 2 and first["skipped"] == 0
    assert again["skipped"] == 1  # closed and present: no fetch, no rewrite
    assert forced["rows"] == 2
    conn = edgar.db()
    n = conn.execute("SELECT COUNT(*) FROM filer_registry").fetchone()[0]
    conn.close()
    assert n == 2


def test_quarters_are_generated_in_order_and_inclusive():
    """The span arithmetic the ingest loop rests on: both endpoints included,
    year boundaries crossed in calendar order."""
    assert fullindex.quarters("2011Q3", "2012Q2") == \
        ["2011Q3", "2011Q4", "2012Q1", "2012Q2"]


def test_survivorship_gap_counts_the_missing_and_when_they_last_filed(
        tmp_path, monkeypatch):
    """The gap is measured against the current watchlist, with the missing
    filers' last-filing years -- what makes a delisted filer legible as a
    casualty instead of a data error. The do-not-re-run note travels in the
    payload, because the 67% attrition finding is the most misusable sentence
    in the project."""
    _isolated_db(tmp_path, monkeypatch)
    gone = _idx_line("GONE BY 2016 CORP", "10-Q", "111", "2013-02-01",
                     "edgar/data/111/0000000111-13-000001.txt")
    stays = _idx_line("ACME STAYS PUT CORP", "10-K", "222", "2024-02-05",
                      "edgar/data/222/0000000222-24-000001.txt")
    _stub_quarters(monkeypatch, {"2013Q1": gone.encode(), "2024Q1": stays.encode()})
    fullindex.ingest(start="2013Q1", end="2013Q1")
    fullindex.ingest(start="2024Q1", end="2024Q1")

    gap = fullindex.survivorship_gap(current_ciks={edgar.pad("222")})
    assert gap["registry_filers"] == 2
    assert gap["missing_from_watchlist"] == 1
    assert gap["missing_share"] == pytest.approx(0.5)
    assert gap["missing_by_last_filing_year"] == {"2013": 1}
    assert "pre-registration" in gap["note"]


def test_arrivals_reads_only_sqlite(tmp_path, monkeypatch):
    """arrivals() is the cost model's input and must be deterministic and
    offline -- it counts DISTINCT filers per day (a 10-K and its same-day
    amendment are one refetch), and it never touches the network."""
    _isolated_db(tmp_path, monkeypatch)
    lines = [
        _idx_line("ACME STAYS PUT CORP", "10-K", "222", "2015-02-20",
                  "edgar/data/222/0000000222-15-000001.txt"),
        _idx_line("ACME STAYS PUT CORP", "10-K/A", "222", "2015-02-20",
                  "edgar/data/222/0000000222-15-000002.txt"),
        _idx_line("1 800 FLOWERS COM INC", "10-Q", "1084869", "2015-01-30",
                  "edgar/data/1084869/0001437749-15-001369.txt"),
    ]
    _stub_quarters(monkeypatch, {"2015Q1": "\n".join(lines).encode()})
    fullindex.ingest(start="2015Q1", end="2015Q1")

    def no_network(*a, **k):
        raise AssertionError("arrivals() touched the network")

    monkeypatch.setattr(edgar, "fetch", no_network)
    got = fullindex.arrivals({edgar.pad("222"), edgar.pad("1084869")},
                             "2015-01-01", "2015-03-31")
    assert got == {"2015-01-30": 1, "2015-02-20": 1}
