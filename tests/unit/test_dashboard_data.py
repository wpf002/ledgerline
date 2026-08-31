"""
Groups, bulk CSV in and out, and the JSON pages a viewer reads.

Each test pins one decision or defect:

  * an empty filtered view always says WHY it is empty -- an unknown group and
    an empty group are different things, and a silent empty list reads as "none
    of your companies qualified" for both;
  * deleting a label never deletes the thing it labels;
  * a bulk import reports every row, fetches nothing, and never overwrites a
    company already on the watchlist;
  * every file that leaves this machine -- CSV or JSON -- carries the record of
    the failed 2026-08-30 test, and cannot be written on a machine that holds
    no evidence of it;
  * publishing reads what was saved and assesses nothing, and the browser is
    handed the same plain-language text the CLI prints rather than a second
    implementation of it.

Same isolation idiom as test_signal_store.py: edgar.DATA / edgar.DB_PATH are
redirected to tmp_path, so the live state.db is never touched. No network.
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from ledgerline import cli, csvio, edgar, emit, groups, render, signals_v3, status
from ledgerline.api import views
from tests.unit.test_signal_store import fired_verdict, unscoreable_verdict

runner = CliRunner()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))


def watch(rows: list[tuple[str, str, str, str | None]]) -> None:
    """Put companies on the watchlist directly: (cik, ticker, name, sic)."""
    conn = edgar.db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO universe (cik, ticker, name, sic) "
            "VALUES (?,?,?,?)", rows)
    conn.close()


def write_csv(path, text: str) -> str:
    path.write_text(text)
    return str(path)


TMAP = {
    "AAPL": {"cik": "0000320193", "ticker": "AAPL", "name": "Apple Inc."},
    "MSFT": {"cik": "0000789019", "ticker": "MSFT", "name": "Microsoft Corp"},
}


# --------------------------------------------------------------------- groups


def test_an_unknown_group_is_not_an_empty_group(isolated_db):
    """The distinction the whole module exists for. Both produce no companies,
    and a caller that could not tell them apart would print the same empty
    list for a typo and for a group nobody has filled in yet."""
    groups.create("semis")
    assert groups.members("semis") == []
    assert groups.members("semi") is None


def test_deleting_a_group_keeps_every_company_it_held(isolated_db):
    """A group is a label over the watchlist; the watchlist and the filing
    histories fetched against it are the expensive thing. There is no path
    from deleting a label to deleting the companies under it."""
    watch([("0000000001", "AAA", "A Inc", "3674"),
           ("0000000002", "BBB", "B Inc", "3674")])
    groups.assign("semis", ["0000000001", "0000000002"])
    assert groups.delete("semis") == 2
    assert groups.members("semis") is None
    assert sorted(edgar.universe()) == ["0000000001", "0000000002"]


def test_one_group_however_a_person_spells_it(isolated_db):
    """`Semis` typed today and `semis` typed tomorrow are one group, and the
    spelling first chosen is the one shown back -- case is the person's, the
    key ignores it."""
    watch([("0000000001", "AAA", "A Inc", None)])
    groups.create("Semis")
    groups.assign("semis", ["0000000001"])
    listed = groups.listing()
    assert len(listed) == 1
    assert listed[0]["name"] == "Semis" and listed[0]["n"] == 1
    assert groups.members("SEMIS") == ["0000000001"]


def test_a_group_filter_never_returns_nothing_without_saying_why(isolated_db):
    """Three situations produce zero companies -- unknown name, empty group,
    and a group whose companies are not watched -- and each gets its own
    sentence and a non-zero exit. A silent empty watchlist reads as 'none of
    these filed', which is a different and untrue statement."""
    watch([("0000000001", "AAA", "A Inc", None)])
    groups.create("semis")

    unknown = runner.invoke(cli.app, ["watch", "--group", "semi"])
    assert unknown.exit_code == 1
    assert 'no group called "semi"' in unknown.stdout
    assert "semis" in unknown.stdout  # the groups that do exist

    empty = runner.invoke(cli.app, ["watch", "--group", "semis"])
    assert empty.exit_code == 1
    assert "no watched companies in it" in empty.stdout


def test_groups_listing_shows_an_empty_group_and_its_count(isolated_db):
    """An empty group is exactly the row a person needs to see: it is what
    explains an empty filtered view. A count-only inner join would hide it."""
    watch([("0000000001", "AAA", "A Inc", None)])
    groups.assign("owned", ["0000000001"])
    groups.create("watchlist-b")
    out = runner.invoke(cli.app, ["groups"]).stdout
    assert "owned" in out and "1 company" in out
    assert "watchlist-b" in out and "empty" in out


# ------------------------------------------------------------- bulk CSV import


def test_import_reports_every_row_and_one_bad_row_does_not_abort_it(
        isolated_db, tmp_path):
    """A bulk operation that prints one number cannot be checked. Every line
    gets an outcome, a malformed line is reported with its line number and
    skipped, and the rows after it still land."""
    path = write_csv(tmp_path / "w.csv",
                     "ticker,name,cik\n"
                     "AAA,A Inc,0000000001\n"
                     ",no ticker here,0000000009\n"
                     "BBB,B Inc,not-a-number\n"
                     "CCC,C Inc,0000000003\n")
    out = csvio.import_watchlist(path, tmap={})
    outcomes = {r.ticker: r.outcome for r in out["rows"]}
    assert outcomes == {"AAA": "added", None: "malformed",
                        "BBB": "malformed", "CCC": "added"}
    bad = [r for r in out["rows"] if r.outcome == "malformed"]
    assert {r.line for r in bad} == {3, 4}
    assert out["counts"]["added"] == 2
    assert sorted(v["ticker"] for v in edgar.universe().values()) == ["AAA", "CCC"]


def test_import_never_overwrites_a_company_already_watched(isolated_db, tmp_path):
    """The row on disk carries a name and an industry code that came from the
    SEC. A spreadsheet is not a better source for those, and set_universe
    already carries the scar from an update that nulled 1,496 industry codes
    in one command."""
    watch([("0000000001", "AAA", "Alpha Industries", "3674")])
    path = write_csv(tmp_path / "w.csv",
                     "ticker,name,cik,sector\nAAA,WRONG NAME,0000000001,Tech\n")
    out = csvio.import_watchlist(path, tmap={})
    assert out["counts"]["already"] == 1 and out["counts"]["added"] == 0
    conn = edgar.db()
    name, sic = conn.execute(
        "SELECT name, sic FROM universe WHERE cik = '0000000001'").fetchone()
    conn.close()
    assert (name, sic) == ("Alpha Industries", "3674")
    assert out["ignored"]["sector"] == 1  # read, reported, not stored


def test_import_downloads_nothing_from_the_sec(isolated_db, tmp_path, monkeypatch):
    """Import writes watchlist rows and group memberships, full stop. Hiding
    one polite SEC request per company inside a command a person thinks reads
    a file is how a quick import becomes an hour of network traffic -- that is
    what `ledgerline fetch` is, and it says so."""
    def refuse(*a, **k):
        raise AssertionError("import must not reach the SEC")

    monkeypatch.setattr(edgar, "fetch", refuse)
    monkeypatch.setattr(edgar, "load_ticker_map", refuse)
    monkeypatch.setattr(edgar, "companyfacts", refuse)
    path = write_csv(tmp_path / "w.csv", "ticker,cik\nAAA,1\n")
    out = csvio.import_watchlist(path)
    assert out["counts"]["added"] == 1


def test_import_resolves_a_missing_cik_and_reports_what_the_sec_does_not_know(
        isolated_db, tmp_path):
    """A file without CIKs is resolved through the SEC's own ticker map, and a
    symbol the map does not carry is reported as unresolved rather than
    silently dropped -- those rows are usually funds or foreign listings, and
    a person needs to see which of their 500 tickers they were."""
    path = write_csv(tmp_path / "w.csv",
                     "ticker\nAAPL\nNOTATICKER\n")
    out = csvio.import_watchlist(path, tmap=TMAP)
    outcomes = {r.ticker: r.outcome for r in out["rows"]}
    assert outcomes == {"AAPL": "added", "NOTATICKER": "unresolved"}
    assert edgar.universe()["0000320193"]["name"] == "Apple Inc."


def test_import_creates_and_fills_the_group_the_file_names(isolated_db, tmp_path):
    """A `group` column is an instruction, and it applies to companies already
    watched as well as new ones: the label is the point, not the row."""
    watch([("0000789019", "MSFT", "Microsoft Corp", None)])
    path = write_csv(tmp_path / "w.csv",
                     "ticker,cik,group\nAAPL,0000320193,big tech\n"
                     "MSFT,0000789019,big tech\n")
    csvio.import_watchlist(path, tmap=TMAP)
    assert groups.members("big tech") == ["0000320193", "0000789019"]


def test_import_refuses_a_file_with_no_ticker_column_and_says_what_to_fix(
        isolated_db, tmp_path):
    """An error says what happened and the exact shape of the fix (VOICE.md)."""
    path = write_csv(tmp_path / "w.csv", "name,cik\nA Inc,1\n")
    with pytest.raises(RuntimeError, match="ticker"):
        csvio.import_watchlist(path, tmap={})


# --------------------------------------------------------------- CSV exports


def emit_one_of_each() -> None:
    emit.emit_run([fired_verdict(), unscoreable_verdict()], source="emit",
                  run_id="2026-08-30", run_date="2026-08-30")


@pytest.mark.parametrize("what", ["watchlist", "signals", "runs"])
def test_every_export_leads_with_the_failed_test(isolated_db, tmp_path, what):
    """A CSV is the format that leaves this machine. Inside the repo the stamp
    is enforced at the scoring path; a spreadsheet mailed to somebody else
    carries whatever its first line says. The line is computed from the frozen
    record, so it cannot drift from the committed numbers."""
    watch([("0000000001", "TEST", "T Inc", "3674")])
    emit_one_of_each()
    out = str(tmp_path / f"{what}.csv")
    csvio.export(what, out)
    with open(out) as fh:
        first = fh.readline()
    assert first.startswith("# ")
    assert "failed its own pre-registered test" in first
    assert "28.7%" in first and "60%" in first


def test_no_csv_is_written_on_a_machine_without_the_frozen_record(
        isolated_db, tmp_path, monkeypatch):
    """There is no path from a missing evidence file to an exported file.
    Refusing before the first byte, rather than after, is what keeps a
    half-written spreadsheet from existing at all."""
    watch([("0000000001", "TEST", "T Inc", None)])
    monkeypatch.setattr(status, "PHASE0_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(status, "_cache", {})
    out = str(tmp_path / "watchlist.csv")
    with pytest.raises(RuntimeError, match="deliberately no default"):
        csvio.export("watchlist", out)
    assert not os.path.exists(out)


def test_exported_watchlist_never_calls_an_unchecked_company_assessable(
        isolated_db, tmp_path):
    """Unknown is a third answer, not a no. A company nobody has checked yet
    says so and says which command answers it -- the same rule that keeps a
    score of 0.0 off an unassessed company."""
    watch([("0000000001", "TEST", "T Inc", "3674")])
    out = str(tmp_path / "w.csv")
    csvio.export("watchlist", out)
    with open(out) as fh:
        lines = [ln for ln in fh.read().splitlines() if not ln.startswith("#")]
    assert "not checked yet" in lines[1]
    assert "ledgerline check" in lines[1]


def test_exported_signals_keep_the_rows_that_could_not_be_assessed(
        isolated_db, tmp_path):
    """An export of fires alone can show precision and can never show recall,
    which is the criterion the detector failed. The unassessable rows are the
    denominator, and their score column is empty rather than 0."""
    watch([("0000000001", "TEST", "T Inc", None)])
    emit_one_of_each()
    out = str(tmp_path / "s.csv")
    csvio.export("signals", out)
    with open(out) as fh:
        rows = [ln.split(",") for ln in fh.read().splitlines()[2:] if ln]
    scoreable = {r[3]: r[4] for r in rows}
    assert scoreable["no"] == ""            # nothing assessed, no number
    assert float(scoreable["yes"]) > 0


# ------------------------------------------------------- the published pages


def published(tmp_path) -> dict:
    views.write_all(str(tmp_path / "feed"))
    out = {}
    for name in ("watchlist", "runs"):
        with open(tmp_path / "feed" / f"{name}.json") as fh:
            out[name] = json.load(fh)
    with open(tmp_path / "feed" / "companies" / "TEST.json") as fh:
        out["company"] = json.load(fh)
    return out


def test_every_published_page_carries_the_validation_block(isolated_db, tmp_path):
    """The signal feed's rule, applied to the pages built beside it: a viewer
    cannot render a watchlist, a run log or a company without also holding the
    fact that the detector failed its own pre-registered test."""
    watch([("0000000001", "TEST", "T Inc", "3674")])
    emit_one_of_each()
    pages = published(tmp_path)
    for page in pages.values():
        assert page["validation"]["verdict"] == "KILL"
        assert "failed its own pre-registered test" in page["validation"]["statement"]


def test_watchlist_page_carries_groups_and_the_latest_saved_assessment(
        isolated_db, tmp_path):
    """The joins happen here, once, in the language that owns the data. A page
    that joined the watchlist to the signal store itself would be a second
    implementation of the same question, and the visible one when they
    disagreed."""
    watch([("0000000001", "TEST", "T Inc", "3674")])
    groups.assign("semis", ["0000000001"])
    emit_one_of_each()
    company = published(tmp_path)["watchlist"]["companies"][0]
    assert company["groups"] == ["semis"]
    assert company["latest"]["flagged"] is True
    assert company["latest"]["score"] > 0
    # Never checked, so assessability is unknown -- not false.
    assert company["assessable"] is None
    assert "ledgerline check" in company["assessable_reason"]


def test_company_page_carries_the_same_words_the_terminal_prints(
        isolated_db, tmp_path):
    """render.explain runs here so the browser never re-implements it. Two
    renderings of one assessment is two things that can disagree about a
    company in public -- and the plain-language rules in docs/VOICE.md would
    have to be re-derived in the second language."""
    watch([("0000000001", "TEST", "T Inc", "3674")])
    emit_one_of_each()
    page = published(tmp_path)["company"]
    expected = render.explain(page["latest"]["verdict"], name="T Inc")
    assert page["explain"] == expected
    assert render.CAVEAT in page["explain"]


def test_company_page_timeline_names_the_filings_the_numbers_came_from(
        isolated_db, tmp_path):
    """The timeline is built from the provenance the stored figures already
    carry: `metrics` for the newest figure per period and `vintages` for the
    ones later revised away, so a filing whose numbers were superseded still
    appears. The live-run `filings` log cannot be the only source -- it holds
    only what a scan happened to see and records no period -- and cannot be
    dropped either, since an 8-K exists nowhere else."""
    watch([("0000000001", "TEST", "T Inc", None)])
    conn = edgar.db()
    with conn:
        conn.execute(
            "INSERT INTO metrics (cik, metric, end_date, kind, filed, value, "
            "form, concept, origin, sources) VALUES "
            "('0000000001','revenue','2024-03-31','Q','2024-05-02',1.0,'10-Q',"
            "'Revenues','reported','[\"0000000001-24-000007\"]')")
        # The NEXT quarter is worked out by subtracting this filing's
        # year-to-date figure from the following one, so it cites this filing
        # too -- a period that had not ended when this filing went in.
        conn.execute(
            "INSERT INTO metrics (cik, metric, end_date, kind, filed, value, "
            "form, concept, origin, sources) VALUES "
            "('0000000001','revenue','2024-06-30','Q','2024-08-01',1.0,'10-Q',"
            "'Revenues','derived','[\"0000000001-24-000007\","
            "\"0000000001-24-000019\"]')")
        # Superseded by a later filing, and still a filing this company made.
        conn.execute(
            "INSERT INTO vintages (cik, metric, end_date, kind, filed, value, "
            "form, concept, origin, sources) VALUES "
            "('0000000001','revenue','2023-12-31','Q','2024-02-06',1.0,'10-Q',"
            "'Revenues','reported','[\"0000000001-24-000002\"]')")
        conn.execute(
            "INSERT INTO filings (accession, cik, ticker, form, filing_date, "
            "period, primary_doc) VALUES ('0000000001-24-000012','0000000001',"
            "'TEST','8-K','2024-06-11',NULL,'x')")
    conn.close()
    page = published(tmp_path)["company"]
    by_acc = {f["accession"]: f for f in page["filings"]}
    assert by_acc["0000000001-24-000007"]["period"] == "2024-03-31"
    assert by_acc["0000000001-24-000007"]["form"] == "10-Q"
    assert by_acc["0000000001-24-000002"]["period"] == "2023-12-31"
    # Cited by two quarters, one of which had not ended when it was filed:
    # the timeline gives the filing its own period, not the later one.
    assert by_acc["0000000001-24-000007"]["n_periods"] == 2
    # An 8-K carries no XBRL fundamentals, so it exists only in the run log --
    # and still belongs on a timeline of what this company filed.
    assert by_acc["0000000001-24-000012"]["form"] == "8-K"
    assert [f["filed"] for f in page["filings"]] == sorted(
        (f["filed"] for f in page["filings"]), reverse=True)  # newest first


def test_publishing_assesses_nothing(isolated_db, tmp_path, monkeypatch):
    """Publishing reads what has already been saved. A publish that quietly
    re-scored the watchlist would be the most expensive command in the tool
    wearing the costume of a file write, and would put scores on disk that the
    append-only store never received."""
    watch([("0000000001", "TEST", "T Inc", None)])
    emit_one_of_each()

    def refuse(*a, **k):
        raise AssertionError("publish must not evaluate anything")

    monkeypatch.setattr(signals_v3, "evaluate", refuse)
    monkeypatch.setattr(edgar, "fetch", refuse)
    pages = published(tmp_path)
    assert pages["watchlist"]["n_companies"] == 1
