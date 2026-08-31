// Local read service for the Ledgerline signal feed, and the small viewer
// over it. Node built-ins only -- no package.json, no npm install, no
// node_modules, no CDN. Run: node server.mjs
//
// Why it exists at all: other programs on this machine can read the feed over
// HTTP instead of parsing the JSONL themselves, and a person can read the same
// thing without a terminal. Why it does so little: the detector is
// UNVALIDATED -- it failed its own pre-registered test on 2026-08-30 -- so
// this service never computes a score, never pushes anything anywhere, and
// refuses to serve a record that arrives without its validation block. Every
// response body carries that block, and every page renders the verdict before
// anything else in the document. It binds 127.0.0.1 only: an app under
// development, serving an unvalidated signal, should not be reachable off the
// machine by default.
//
// What it reads, and nothing else: `ledgerline publish` writes signals.jsonl
// plus watchlist.json, runs.json and companies/<TICKER>.json beside it. This
// file joins nothing, derives nothing, and renders no sentence about a company
// that the Python did not already write -- see ledgerline/api/views.py. If
// serving ever requires recomputing a number the Python already computed, that
// is the signal to revisit the boundary, not to port the arithmetic.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as pages from "./pages.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FEED = process.env.LEDGERLINE_FEED ??
  path.join(HERE, "..", "reports", "feed", "signals.jsonl");
// The published pages sit beside the feed, because `publish` writes them
// there: one location, whether the feed is the default one or a fixture.
const PAGES_DIR = path.dirname(FEED);
const PORT = Number(process.env.PORT ?? 8787);
const HOST = "localhost"; // the canonical name shown in the address bar.
// Listening happens on BOTH loopback literals below: binding the name binds
// only whichever family resolves first, and binding "::" would listen on
// every interface. Two explicit loopback listeners keep it local-only with
// both spellings answering.

// A ticker is a path segment here, so it is matched rather than trusted --
// ".." is a ticker-shaped string too, and companies/ is a directory of files.
const TICKER = /^[A-Z0-9][A-Z0-9.\-]{0,11}$/;

let cache = { mtimeMs: -1, size: -1, records: [] };

// The feed is append-only, so a cheap stat per request keeps the cache fresh
// without a watcher. A line that fails to parse, or arrives without its
// validation block, fails the WHOLE load: a feed with silently skipped lines
// would serve wrong denominators, which is the one thing this contract
// exists to prevent.
function loadFeed() {
  const stat = fs.statSync(FEED);
  if (stat.mtimeMs === cache.mtimeMs && stat.size === cache.size) {
    return cache.records;
  }
  const lines = fs.readFileSync(FEED, "utf8").split("\n").filter(Boolean);
  const records = lines.map((line, i) => {
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      throw new Error(`feed line ${i + 1} is not valid JSON`);
    }
    if (!rec.validation || typeof rec.validation.statement !== "string" ||
        rec.validation.statement.length === 0) {
      throw new Error(
        `feed line ${i + 1} has no validation block -- refusing to serve a ` +
        "score without the fact that the detector failed its own test");
    }
    return rec;
  });
  cache = { mtimeMs: stat.mtimeMs, size: stat.size, records };
  return records;
}

// The published view files, cached the same way and checked the same way: a
// page file without its validation block is refused exactly as a feed line
// without one is. There is no way to render a company from this service
// without also holding the fact that the detector failed its test.
const views = new Map();

function readView(rel) {
  const file = path.join(PAGES_DIR, rel);
  let stat;
  try {
    stat = fs.statSync(file);
  } catch {
    return { ok: false, missing: true, file };
  }
  const hit = views.get(file);
  if (hit && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) {
    return { ok: true, data: hit.data };
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    return { ok: false, file, error: String(err.message ?? err) };
  }
  if (!data.validation || !data.validation.statement) {
    return { ok: false, file, error:
      `${rel} has no validation block -- refusing to render a page without ` +
      "the fact that the detector failed its own test" };
  }
  views.set(file, { mtimeMs: stat.mtimeMs, size: stat.size, data });
  return { ok: true, data };
}

// The newest record's validation block. Served on every response; never
// synthesised here -- if the feed holds no records, there is no evidence to
// serve and the endpoints say so instead of defaulting.
function validationOf(records) {
  return records[records.length - 1].validation;
}

// The verdict for a page whose own data file could not be read. Cheapest
// evidence first: the small view files, then the feed only if it is already
// parsed in memory -- a banner is not worth re-reading 78 MB for. Returns
// null when this machine holds no evidence at all, and pages.banner() then
// says so rather than inventing a verdict.
function anyValidation() {
  for (const rel of ["watchlist.json", "runs.json"]) {
    const got = readView(rel);
    if (got.ok) return got.data.validation;
  }
  return cache.records.length ? validationOf(cache.records) : null;
}

// The most recent run's records: the newest record's embedded run block,
// then every record sharing its run_id (or, for runs emitted without an id,
// its emitted_at -- rows of one emit share their timestamp).
function latestRun(records) {
  const last = records[records.length - 1];
  const same = records.filter((r) =>
    last.run_id != null || last.run.run_id != null
      ? r.run.run_id === last.run.run_id
      : r.emitted_at === last.emitted_at);
  return { run: last.run, records: same };
}

function digestOf(records) {
  const validation = validationOf(records);
  const { run, records: rows } = latestRun(records);
  const scored = run.scoreable ?? 0;
  const fpr = validation.measured.fpr_per_control_quarter;
  return {
    // Same order of concerns as `ledgerline digest`: the verdict, the
    // coverage, the chance-alone expectation, then -- last -- the names.
    validation,
    run,
    // Computed from the record's own frozen numbers, so this surface and the
    // Python one cannot disagree about it.
    expected_false_positives_if_nothing_wrong: scored * fpr,
    fires: rows
      .filter((r) => r.assessment.state === "fired")
      .map((r) => ({
        ticker: r.filer.ticker,
        score: r.assessment.score,
        flags: r.flags.map((f) => f.code),
      })),
  };
}

function send(res, status, body) {
  const text = JSON.stringify(body, null, 1);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(text);
}

function sendHtml(res, status, html) {
  res.writeHead(status, { "Content-Type": "text/html; charset=utf-8" });
  res.end(html);
}

// One page for a missing view file. It names the file, says what writes it,
// and still carries the verdict if any evidence at all can be read.
function notPublished(res, { title, current, rel }) {
  sendHtml(res, 503, pages.message({
    title, current, validation: anyValidation(),
    heading: "This page has not been written yet",
    paragraphs: [
      `The viewer reads <code>${pages.esc(rel)}</code>, and there is no such ` +
      `file under <code>${pages.esc(PAGES_DIR)}</code>.`,
      "Write it: <code>ledgerline publish</code>. That reads what has already " +
      "been saved and assesses nothing.",
    ],
  }));
}

function serveOverview(res) {
  let records;
  try {
    records = loadFeed();
  } catch (err) {
    sendHtml(res, 503, pages.message({
      title: "Overview", current: "/", validation: anyValidation(),
      heading: "The assessment feed cannot be read",
      paragraphs: [pages.esc(String(err.message ?? err)) + ".",
        "Write it: <code>ledgerline publish</code>."],
    }));
    return;
  }
  if (records.length === 0) {
    sendHtml(res, 503, pages.message({
      title: "Overview", current: "/", validation: anyValidation(),
      heading: "The assessment feed is empty",
      paragraphs: [
        "There are no saved assessments to show, and no validation evidence " +
        "to serve with them.",
        "Assess something first: <code>ledgerline scan --score</code>, then " +
        "<code>ledgerline publish</code>.",
      ],
    }));
    return;
  }
  sendHtml(res, 200, pages.overview(digestOf(records)));
}

function serveWatchlist(res, url) {
  const got = readView("watchlist.json");
  if (!got.ok) {
    if (got.missing) {
      notPublished(res, { title: "Watchlist", current: "/watchlist",
        rel: "watchlist.json" });
      return;
    }
    sendHtml(res, 503, pages.message({
      title: "Watchlist", current: "/watchlist", validation: anyValidation(),
      heading: "The watchlist file cannot be read",
      paragraphs: [pages.esc(got.error) + ".",
        "Rewrite it: <code>ledgerline publish</code>."],
    }));
    return;
  }
  const q = url.searchParams;
  sendHtml(res, 200, pages.watchlist(got.data, {
    q: q.get("q") ?? "",
    group: q.get("group") ?? "",
    assessable: ["yes", "no", "unknown"].includes(q.get("assessable"))
      ? q.get("assessable") : "",
    page: q.get("page") ?? "1",
  }));
}

// The Company tab with no ticker, or with one nobody is watching. Neither is
// an error page: one is a search box, the other says which of the two things
// went wrong -- not watched at all, or watched and not yet published -- and
// which command fixes that one.
function serveCompanyIndex(res, note) {
  const got = readView("watchlist.json");
  if (!got.ok) {
    sendHtml(res, 503, pages.message({
      title: "Company", current: "/company", validation: anyValidation(),
      heading: note ? "That company has no page here" : "Open a company",
      paragraphs: [
        note ?? "Name a company to open: <code>/company/FMC</code>.",
        "The pages a viewer reads are written by <code>ledgerline publish</code>.",
      ],
    }));
    return;
  }
  sendHtml(res, note ? 404 : 200, pages.companyIndex(got.data, note));
}

function serveCompany(res, raw) {
  const ticker = decodeURIComponent(raw).toUpperCase();
  if (!TICKER.test(ticker)) {
    serveCompanyIndex(res, `“${pages.esc(raw)}” is not a ticker symbol. ` +
      "Try one like <code>/company/FMC</code>.");
    return;
  }
  const got = readView(path.join("companies", `${ticker}.json`));
  if (got.ok) {
    sendHtml(res, 200, pages.company(got.data));
    return;
  }
  if (!got.missing) {
    sendHtml(res, 503, pages.message({
      title: ticker, current: "/company", validation: anyValidation(),
      heading: `${pages.esc(ticker)}'s page cannot be read`,
      paragraphs: [pages.esc(got.error) + ".",
        "Rewrite it: <code>ledgerline publish</code>."],
    }));
    return;
  }
  // Missing file, and the watchlist says which of two things happened. When
  // the watchlist itself is missing, neither sentence is known to be true, so
  // neither is printed -- "not on your watchlist" is a claim about the
  // person's setup, and this service would be guessing at it.
  const wl = readView("watchlist.json");
  if (!wl.ok) {
    serveCompanyIndex(res, `No page has been written for ${pages.esc(ticker)}, ` +
      "and neither has the watchlist, so this service cannot say whether it " +
      "is being watched. Run <code>ledgerline publish</code>.");
    return;
  }
  const watched = (wl.data.companies || []).some(
    (c) => (c.ticker || "").toUpperCase() === ticker);
  serveCompanyIndex(res, watched
    ? `${pages.esc(ticker)} is on your watchlist, but no page has been ` +
      "written for it yet. Run <code>ledgerline publish</code>."
    : `${pages.esc(ticker)} is not on your watchlist, so nothing has been ` +
      `read about it. Add it: <code>ledgerline watch --add ${pages.esc(ticker)}` +
      "</code>, then <code>ledgerline fetch</code>.");
}

function serveActivity(res) {
  const got = readView("runs.json");
  if (!got.ok) {
    if (got.missing) {
      notPublished(res, { title: "Activity", current: "/activity",
        rel: "runs.json" });
      return;
    }
    sendHtml(res, 503, pages.message({
      title: "Activity", current: "/activity", validation: anyValidation(),
      heading: "The run log cannot be read",
      paragraphs: [pages.esc(got.error) + ".",
        "Rewrite it: <code>ledgerline publish</code>."],
    }));
    return;
  }
  sendHtml(res, 200, pages.activity(got.data));
}

// One stylesheet, one route: four pages carrying four copies of it would be
// four places for the verdict banner's styling to drift apart.
function serveStylesheet(res) {
  let css;
  try {
    css = fs.readFileSync(path.join(HERE, "style.css"));
  } catch {
    send(res, 404, { error: "style.css missing beside server.mjs" });
    return;
  }
  res.writeHead(200, { "Content-Type": "text/css; charset=utf-8" });
  res.end(css);
}

const HTML_ROUTES = new Set(["", "watchlist", "company", "activity"]);

const server = http.createServer((req, res) => {
  // One canonical address. Anyone arriving via 127.0.0.1 or [::1] is sent to
  // http://localhost:8787 so the address bar always reads the same way.
  const host = String(req.headers.host ?? "");
  if (host.startsWith("127.0.0.1") || host.startsWith("[::1]")) {
    res.writeHead(301, { Location: `http://localhost:${PORT}${req.url}` });
    res.end();
    return;
  }

  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);

  if (req.method !== "GET") {
    // Read-only by construction: the append-only invariant cannot be
    // enforced from here, so this surface is not given the vocabulary to
    // violate it. The verdict travels with the refusal like every other
    // response, from whatever evidence can be read cheaply.
    send(res, 405, { error: "this service only reads",
                     validation: anyValidation() });
    return;
  }

  if (parts[0] === "style.css" && parts.length === 1) {
    serveStylesheet(res);
    return;
  }

  // The pages. Each one loads only what it shows, and each one that cannot
  // load it answers with a page saying what happened and what to run.
  if (HTML_ROUTES.has(parts[0] ?? "")) {
    if (parts.length === 0) {
      serveOverview(res);
    } else if (parts[0] === "watchlist" && parts.length === 1) {
      serveWatchlist(res, url);
    } else if (parts[0] === "activity" && parts.length === 1) {
      serveActivity(res);
    } else if (parts[0] === "company" && parts.length === 1) {
      const asked = url.searchParams.get("ticker");
      if (asked && asked.trim()) {
        // The lookup form is a plain GET form, so it can only send a query
        // string; the company's address is a path. One redirect joins them,
        // and the page keeps a URL a person can copy.
        res.writeHead(302, {
          Location: `/company/${encodeURIComponent(asked.trim().toUpperCase())}`,
        });
        res.end();
        return;
      }
      serveCompanyIndex(res, null);
    } else if (parts[0] === "company" && parts.length === 2) {
      serveCompany(res, parts[1]);
    } else {
      serveCompanyIndex(res, "That address does not name a company. Company " +
        "pages look like <code>/company/FMC</code>.");
    }
    return;
  }

  // The JSON routes, unchanged: load the feed, refuse without evidence, and
  // carry the validation block on every body.
  let records;
  try {
    records = loadFeed();
  } catch (err) {
    send(res, 503, {
      error: String(err.message ?? err),
      hint: "write the feed first: ledgerline publish",
    });
    return;
  }
  if (records.length === 0) {
    send(res, 503, {
      error: "the feed is empty -- no validation evidence to serve",
      hint: "ledgerline publish writes it from the saved assessments",
    });
    return;
  }
  const validation = validationOf(records);

  if (parts[0] === "validation" && parts.length === 1) {
    send(res, 200, validation);
  } else if (parts[0] === "digest" && parts.length === 1) {
    send(res, 200, digestOf(records));
  } else if (parts[0] === "signals" && parts.length === 1) {
    const since = Number(url.searchParams.get("since_seq") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 100);
    const page = records.filter((r) => r.seq > since).slice(0, limit);
    send(res, 200, {
      records: page,
      next_seq: page.length ? page[page.length - 1].seq : since,
      validation, // once per page, before a single record is opened
    });
  } else if (parts[0] === "signals" && parts.length === 2) {
    const ticker = decodeURIComponent(parts[1]).toUpperCase();
    send(res, 200, {
      records: records.filter((r) => (r.filer.ticker ?? "") === ticker),
      validation,
    });
  } else {
    send(res, 404, {
      error: "unknown path",
      routes: ["/", "/watchlist", "/company/:ticker", "/activity",
               "/signals", "/signals/:ticker", "/validation", "/digest"],
      validation,
    });
  }
});

const server6 = http.createServer(server.listeners("request")[0]);
server6.on("error", () => { /* no IPv6 loopback on this machine; IPv4 covers it */ });
server6.listen(PORT, "::1");
server.listen(PORT, "127.0.0.1", () => {
  console.log(`ledgerline read service on http://${HOST}:${PORT}`);
  console.log(`feed: ${FEED}`);
  console.log("local development only; the signal is unvalidated " +
              "(failed its own test, 2026-08-30)");
});
