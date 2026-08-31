"""
The four web pages, served by service/server.mjs against published files.

Each test pins one decision or defect:

  * the verdict banner is the FIRST thing in the body of every page, ahead of
    the masthead, the navigation and the page's own content -- the single page
    this replaced painted it from JavaScript after a fetch, so with scripting
    off the verdict never appeared at all and the page still showed scores;
  * no page needs JavaScript or the network to say that: no script tag, no CDN,
    no web font, one stylesheet from one route;
  * a page with nothing to show says what happened and which command changes
    it, and still carries the verdict -- an empty screen and a stack trace are
    the two answers this surface is not allowed to give;
  * an unknown group and an empty group are different pieces of news, the same
    distinction `ledgerline watch --group` makes at the terminal;
  * a company nothing was assessed for shows words, never a number, and a quiet
    result is never presented as a clean bill of health;
  * every number on a company page names the filing it came from;
  * the service still only reads, and still answers at one canonical address.

The pages are served by node, so the whole module skips where node is absent.
Isolation idiom of test_signal_store.py: edgar.DATA / edgar.DB_PATH are
redirected to tmp_path and the feed is published into it, so the live state.db
and reports/feed are never touched. No network: the server binds loopback and
the pages link nothing off this machine.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time

import pytest

from ledgerline import edgar, emit, groups
from ledgerline.api import contract, views
from tests.unit.test_signal_store import fired_verdict, unscoreable_verdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "service", "server.mjs")

PAGES = ["/", "/watchlist", "/company/TEST", "/activity"]

# What "before any other content" is measured against: the masthead, the
# navigation, and the first heading of the page's own body.
AFTER_THE_BANNER = ['<h1><a href="/">Ledgerline</a>', '<nav class="pages">', "<h2>"]


class Site:
    """A running read service and the directory it publishes from."""

    def __init__(self, port: int, feed_dir: str):
        self.port = port
        self.feed_dir = feed_dir

    def request(self, path: str, method: str = "GET", host: str | None = None):
        conn = http.client.HTTPConnection("localhost", self.port, timeout=10)
        try:
            conn.request(method, path, headers={"Host": host} if host else {})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read().decode("utf-8")
        finally:
            conn.close()

    def body(self, path: str) -> str:
        return self.request(path)[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def site(tmp_path, monkeypatch):
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the read service is a node program")
    monkeypatch.setattr(edgar, "DATA", str(tmp_path))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "state.db"))

    conn = edgar.db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO universe (cik, ticker, name, sic) "
            "VALUES (?,?,?,?)",
            [("0000000001", "TEST", "T Inc", "3674"),
             ("0000000002", "QUIET", "Q Inc", "3674")])
        # One stored figure, so the company page has a filing timeline: the
        # figures carry the provenance the timeline is built from.
        conn.execute(
            "INSERT INTO metrics (cik, metric, end_date, kind, filed, value, "
            "form, concept, origin, sources) VALUES "
            "('0000000001','revenue','2024-03-31','Q','2024-05-02',1.0,'10-Q',"
            "'Revenues','reported','[\"0000000001-24-000007\"]')")
    conn.close()
    groups.assign("semis", ["0000000001"])
    groups.create("empty-group")
    emit.emit_run([fired_verdict(), unscoreable_verdict()], source="emit",
                  run_id="1", run_date="2026-08-30")

    feed_dir = tmp_path / "feed"
    feed = feed_dir / "signals.jsonl"
    contract.export_jsonl(str(feed))
    views.write_all(str(feed_dir))

    port = _free_port()
    proc = subprocess.Popen(
        ["node", SERVER],
        env={**os.environ, "PORT": str(port), "LEDGERLINE_FEED": str(feed)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    running = Site(port, str(feed_dir))
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"the read service exited: {proc.stdout.read()}")
        try:
            running.request("/style.css")
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("the read service did not start")
    try:
        yield running
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def statement() -> str:
    return contract.validation_block()["statement"]


# ------------------------------------------------------- the verdict, first


@pytest.mark.parametrize("path,expect", [(p, 200) for p in PAGES] + [
    # The pages that render something OTHER than a table of results. Each is a
    # separate branch, and the verdict is not a property of the happy path: a
    # page that has no companies to show is still a page about this detector.
    ("/company", 200),                       # the lookup form, nothing chosen
    ("/company/ZZNOPE", 404),                # a ticker nobody watches
    ("/company/one/two", 404),               # not a company address at all
    ("/watchlist?group=semi", 200),          # a group name nobody created
    ("/watchlist?group=empty-group", 200),   # a group nobody has filled in
    ("/watchlist?q=ZZNOPE", 200),            # a search that matched nothing
])
def test_the_verdict_is_the_first_thing_on_every_page(site, path, expect):
    """The banner renders ahead of the masthead, the navigation and the page's
    own content, on every route. The page this replaced fetched /digest and
    painted the banner from JavaScript: until the fetch returned it read
    "LOADING…", and with scripting off it never said anything at all -- while
    still being a page about scores from a detector that failed its own test."""
    status, _, body = site.request(path)
    assert status == expect
    head, _, rest = body.partition("<body>")
    assert "<body>" not in head
    at = rest.index("failed its own pre-registered test")
    assert statement() in rest
    for marker in AFTER_THE_BANNER:
        assert at < rest.index(marker), f"{marker} renders before the verdict"


@pytest.mark.parametrize("path", PAGES)
def test_no_page_needs_javascript_or_the_network(site, path):
    """Node built-ins on the server, hand-written HTML on the client, and the
    verdict in the markup rather than behind a fetch. A CDN link would put the
    page's legibility on someone else's uptime; a script tag would put the
    verdict behind execution again."""
    body = site.body(path)
    assert "<script" not in body.lower()
    assert "<style" not in body.lower()
    assert 'href="/style.css"' in body
    # The only off-machine addresses are filings on the SEC's own site.
    for url in re.findall(r'https?://[^"\s<]+', body):
        assert url.startswith("https://www.sec.gov/Archives/edgar/data/"), url


def test_one_stylesheet_serves_every_page(site):
    """One route, one copy. Four pages each carrying their own <style> block
    would be four places for the verdict banner's styling to drift apart."""
    status, headers, css = site.request("/style.css")
    assert status == 200
    assert headers["Content-Type"].startswith("text/css")
    assert "--flag" in css and ".banner" in css


# --------------------------------------------- nothing to show is an answer


def test_a_page_with_no_data_says_what_to_run_and_still_shows_the_verdict(site):
    """A missing published file is a state a person can fix, so the page names
    the file, names the command, and keeps the verdict it can still read from
    the other published files. Never a raw error, never a blank screen."""
    os.remove(os.path.join(site.feed_dir, "runs.json"))
    status, _, body = site.request("/activity")
    assert status == 503
    assert "runs.json" in body and "ledgerline publish" in body
    assert statement() in body


def test_an_unknown_ticker_gets_a_page_that_says_which_thing_went_wrong(site):
    """Not watched and not yet published are different problems with different
    fixes, and the watchlist knows which one this is. A 404 with no sentence
    would leave a person retyping the ticker."""
    status, _, body = site.request("/company/NOPE")
    assert status == 404
    assert "not on your watchlist" in body
    assert "ledgerline watch --add NOPE" in body
    assert statement() in body

    # A ticker-shaped path segment is matched, never trusted: companies/ is a
    # directory of files and ".." is a ticker-shaped string.
    escape_status, _, escape_body = site.request("/company/..%2f..%2fetc")
    assert escape_status == 404
    assert "is not a ticker symbol" in escape_body

    # The lookup form can only send a query string; the company's address is a
    # path, and one redirect joins them so the URL stays copyable.
    moved, headers, _ = site.request("/company?ticker=test")
    assert moved == 302
    assert headers["Location"] == "/company/TEST"


def test_an_unknown_group_and_an_empty_group_say_different_things(site):
    """The distinction groups.members() exists for, carried onto the page:
    both produce no rows, and one is a typo while the other is a group nobody
    has filled in yet. One empty table would tell three-quarters of a lie."""
    unknown = site.body("/watchlist?group=semi")
    assert "no group called" in unknown
    assert "semis" in unknown  # the groups that do exist

    empty = site.body("/watchlist?group=empty-group")
    assert "exists, and no watched company is in it yet" in empty
    assert "ledgerline groups --assign" in empty

    filled = site.body("/watchlist?group=semis")
    assert "TEST" in filled and "QUIET" not in filled


# ------------------------------------------- what a score is allowed to look like


def test_a_company_nothing_was_assessed_for_shows_words_not_a_number(site):
    """The store's rule at the surface a person reads: a score of 0 next to a
    company nobody assessed reads as a clean bill of health and means the
    opposite. QUIET has no saved assessment, so its row says so and carries
    the chip that says which command would produce one."""
    body = site.body("/watchlist?q=QUIET")
    assert "nothing assessed yet" in body
    assert "no assessment saved" in body
    assert "of 100" not in body


def test_a_score_carries_its_scale_and_a_quiet_result_is_not_a_clean_bill(site):
    """docs/VOICE.md: every number carries its bar, and any surface that could
    be read as the tool working carries the failed test. A fuller dashboard
    must not become a claim that the detector works."""
    body = site.body("/watchlist")
    assert "of 100" in body
    assert "flagged at 45 with at least two measures out of line" in body
    assert "not a clean bill of health" in body


def test_every_number_on_a_company_page_names_the_filing_it_came_from(site):
    """The README invariant on the page: a score traces back to accessions or
    it does not ship. Each accession is a link to that filing on the SEC's own
    site, so a reader can check the number rather than believe it."""
    body = site.body("/company/TEST")
    with open(os.path.join(site.feed_dir, "companies", "TEST.json")) as fh:
        published = json.load(fh)
    for filing in published["filings"]:
        assert filing["accession"] in body
        assert f"/{filing['accession'].replace('-', '')}/" in body
    sources = [s for measure in published["provenance"]["measures"]
               for row in measure["inputs"] for s in row["sources"]]
    assert sources, "the fired verdict published no trail to render"
    for accession in sources:
        assert f'href="https://www.sec.gov/Archives/edgar/data/1/' \
               f'{accession.replace("-", "")}/{accession}-index.htm"' in body
    # Plain names in the trail, not the identifiers it is keyed by. The
    # explain text above it does print `cash_conversion_gap` once, in the
    # parenthetical `ledgerline explain` has always printed -- docs/VOICE.md
    # allows the technical term beside the plain one, not instead of it.
    trail = body.split("Where each number came from")[1]
    assert "cash-vs-sales" in trail
    assert "cash_conversion_gap" not in trail and "operating_cash_flow" not in trail


# ---------------------------------------------------- the service's own rules


def test_the_service_still_only_reads(site):
    """Read-only by construction: the append-only invariant cannot be enforced
    from here, so this surface is not given the vocabulary to violate it. The
    verdict travels with the refusal like every other response."""
    status, _, body = site.request("/watchlist", method="POST")
    assert status == 405
    assert json.loads(body)["validation"]["verdict"] == "KILL"


def test_the_pages_answer_at_one_canonical_address(site):
    """Arriving at a loopback literal is redirected to localhost, pages
    included: two spellings of one address in the bar is two apps as far as a
    person's bookmarks are concerned."""
    status, headers, _ = site.request(
        "/watchlist", host=f"127.0.0.1:{site.port}")
    assert status == 301
    assert headers["Location"] == f"http://localhost:{site.port}/watchlist"


def test_a_filter_that_matched_nothing_restates_itself_as_a_sentence(site):
    """The filters were joined as predicates of "companies are ...", which
    produced "companies are that cannot be assessed" for the assessability
    ones and "are matching X" for the search box. Every combination has to
    read as English, because this paragraph is the only thing on the page
    telling a person which of their filters emptied the table."""
    both = site.body("/watchlist?q=ZZNOPE&assessable=yes&group=semis")
    assert "companies that can be assessed in the group “semis” with " \
           "“ZZNOPE” in the ticker or name" in both
    assert "None of your" in both and " are that" not in both
    assert "are matching" not in site.body("/watchlist?q=ZZNOPE")


def test_an_empty_assessability_filter_never_claims_the_opposite_state(site):
    """Nothing here has been checked, so "cannot be assessed" matches nothing.
    Reporting that as "none of your companies cannot be assessed" states that
    they all can -- the inverse of what is known -- and saying "every watched
    company has been checked" beside a search box that matched nothing draws a
    claim about the whole watchlist from a result about one query."""
    body = site.body("/watchlist?assessable=no")
    assert "You asked for companies that cannot be assessed." in body
    assert "None of your 2 watched companies fit." in body
    # The reading that would be wrong: the empty table is about the filter,
    # not a finding that every watched company can be assessed.
    assert "companies cannot be assessed" not in body
    assert "until that has run a company is neither" in body

    narrowed = site.body("/watchlist?assessable=unknown&q=ZZNOPE")
    assert "Every watched company has been checked" not in narrowed


# --------------------------------------------------- where the numbers are from


PAGES_MJS = os.path.join(ROOT, "service", "pages.mjs")


def render_page(fn: str, *args) -> str:
    """Render one page function out of process, with no server.

    pages.mjs imports nothing and computes nothing -- pure functions from
    published JSON to markup -- which is what lets a test hand it a run the
    live database does not currently hold.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the read service is a node program")
    script = ("import('file://' + process.argv[1]).then((p) => "
              "process.stdout.write(p[process.argv[2]]"
              "(...JSON.parse(process.argv[3]))))")
    out = subprocess.run(
        ["node", "-e", script, "--", PAGES_MJS, fn, json.dumps(list(args))],
        capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _digest(run: dict) -> dict:
    return {"run": run, "validation": contract.validation_block(),
            "expected_false_positives_if_nothing_wrong": 1.2, "fires": []}


def test_the_overview_says_a_replay_is_a_replay():
    """Every assessment on record today is a replay over the practice half --
    the split the thresholds were fitted on -- and the front page rendered
    those six companies under "Latest run" with `source` and `split` arriving
    in the same payload and being dropped. The banner caveats the method; this
    caveats these particular numbers."""
    body = render_page("overview", _digest(
        {"run_date": "2025-11-15", "source": "replay", "split": "tuning",
         "scoreable": 453, "unscoreable": 18, "gated_in": 6}))
    assert "a replay over the practice half" in body
    assert "the companies the thresholds were fitted on" in body
    assert "not a live run" in body
    assert ">source</dt><dd>replay<" in body.replace("\n", "")
    assert ">split</dt><dd>tuning<" in body.replace("\n", "")
    # A live run is not described as a replay.
    live = render_page("overview", _digest(
        {"run_date": "2026-08-30", "source": "scan", "scoreable": 1}))
    assert "a live run on 2026-08-30" in live and "replay" not in live


def test_a_replayed_assessment_says_so_in_the_watchlist_cell():
    """The same omission one table cell down: `views._latest_assessments`
    selected no source column, so a 2019 practice-half replay rendered as the
    company's current verdict with nothing on the row saying otherwise."""
    company = {"ticker": "TEST", "name": "T Inc", "groups": [], "quality": [],
               "assessable": True,
               "latest": {"as_of": "2019-05-15", "period": "2019-03-31",
                          "score": 61.2, "flagged": True, "scoreable": True,
                          "reason": None, "flags": [], "source": "replay",
                          "split": "tuning"}}
    body = render_page("watchlist", {"companies": [company], "groups": [],
                                     "n_companies": 1, "n_assessable": 1,
                                     "validation": contract.validation_block()},
                       {"q": "", "group": "", "assessable": "", "page": 1})
    assert "replayed over the practice half" in body


def test_the_published_watchlist_row_carries_its_own_provenance(site):
    """The Python half of the same fix: the field has to reach the page for
    the page to be able to print it."""
    with open(os.path.join(site.feed_dir, "watchlist.json")) as fh:
        data = json.load(fh)
    latest = next(c["latest"] for c in data["companies"]
                  if c["ticker"] == "TEST")
    assert latest["source"] == "emit"
    assert "split" in latest
