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
//
// Nothing a request contains may end this process. A mistyped address, a
// published file with a key of the wrong type, or a feed record missing the
// numbers under its own verdict were each an uncaught throw inside the request
// handler, which is to say an exit -- and one route's bad file took down every
// other route with it, including the one that only serves the verdict. Every
// failure here is answered instead: pages say what happened and which command
// rewrites the file, JSON routes say the same thing in a body. The handler is
// wrapped for the failures no route thought to catch.

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

// Why this checks more than one key: the gate below used to admit a record on
// `validation.statement` alone and digestOf then read `validation.measured
// .fpr_per_control_quarter` off it, so a record carrying the sentence without
// the numbers the sentence is derived from killed the process instead of being
// refused. A loader whose job is to refuse incomplete evidence has to check
// the evidence it is about to hand on -- every field this file reads, not the
// first one. Mirrors ledgerline/api/schema.py, which declares `validation`
// closed with `measured` required.
function validationFault(v) {
  if (!v || typeof v !== "object") return "has no validation block";
  if (typeof v.statement !== "string" || v.statement.length === 0) {
    return "has no validation block";
  }
  if (!v.measured || typeof v.measured !== "object") {
    return "has a validation block with no measured numbers in it";
  }
  if (typeof v.measured.fpr_per_control_quarter !== "number" ||
      !Number.isFinite(v.measured.fpr_per_control_quarter)) {
    return "has a validation block whose measured false-alarm rate is not a " +
      "number";
  }
  return null;
}

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
    const wrong = validationFault(rec.validation);
    if (wrong) {
      throw new Error(
        `feed line ${i + 1} ${wrong} -- refusing to serve a score without ` +
        "the fact that the detector failed its own test");
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

// Where the published files are allowed to be. `rel` is built from route
// segments, and path.join happily walks "companies/../../.." out of the feed
// directory, so the joined path is resolved and then checked rather than
// trusted -- the same reason the TICKER pattern exists, applied to the result
// instead of the input, because only the resolved path knows where it landed.
// Symlinks are resolved where they exist so a link inside companies/ cannot
// point somewhere this service would not otherwise read.
function insideFeed(file) {
  const resolve = (p) => {
    try {
      return fs.realpathSync(p);
    } catch {
      return path.resolve(p);
    }
  };
  const root = resolve(PAGES_DIR);
  const target = resolve(file);
  return target === root || target.startsWith(root + path.sep);
}

function readView(rel) {
  const file = path.join(PAGES_DIR, rel);
  if (!insideFeed(file)) {
    return { ok: false, file, error:
      `${rel} resolves outside the published feed directory, and this ` +
      "service reads nothing else" };
  }
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

// How many records one /signals page carries, and the ceiling on asking for
// more. Why there is a ceiling at all: `?limit=1000000` served all 39,564
// records as a single 90 MB JSON string, roughly twice the feed materialised
// in memory before a byte was written. Paging past the ceiling is not lossy --
// next_seq advances, so a caller that wants the whole feed gets it in pages.
const PAGE_DEFAULT = 100;
const PAGE_MAX = 1000;

// A query parameter is text a caller typed, and Number() answers NaN for
// "abc", -1 for "-1" and 0 for "": each of those used to produce a 200 with a
// body a consuming program could not tell from a correct one -- an empty page
// that reads as "you are caught up", a page short by exactly one record, a
// cursor that silently rewound to the start. Absent or empty means "use the
// default"; anything else has to be a whole number or the caller hears about
// it. Returns null for "the caller sent something this cannot be".
function wholeNumber(raw, fallback) {
  if (raw === null || raw === undefined || raw.trim() === "") return fallback;
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number(raw.trim());
  return Number.isSafeInteger(n) ? n : null;
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

// A path segment is not necessarily text. `new URL()` leaves an invalid
// percent-escape raw in the pathname, so `/company/%` reaches here as a
// literal "%" and decodeURIComponent throws URIError on it -- which used to
// take the whole process down from one mistyped address. An undecodable
// segment is simply not a ticker, and says so on the page like every other
// thing that is not one.
function decodeOrNull(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

function serveCompany(res, raw) {
  const decoded = decodeOrNull(raw);
  const ticker = (decoded ?? "").toUpperCase();
  if (decoded === null || !TICKER.test(ticker)) {
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

function handle(req, res) {
  // Read-only by construction: the append-only invariant cannot be enforced
  // from here, so this surface is not given the vocabulary to violate it. The
  // verdict travels with the refusal like every other response, from whatever
  // evidence can be read cheaply.
  //
  // Ahead of the canonical-host redirect below, and not after it: a 301 has no
  // body, so a write attempt addressed to 127.0.0.1 was refused without the
  // verdict travelling with it, and a client that follows redirects the
  // ordinary way had its POST turned into a GET and was served the feed
  // instead of being told the surface is read-only.
  if (req.method !== "GET") {
    send(res, 405, { error: "this service only reads",
                     validation: anyValidation() });
    return;
  }

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
    const since = wholeNumber(url.searchParams.get("since_seq"), 0);
    const limit = wholeNumber(url.searchParams.get("limit"), PAGE_DEFAULT);
    // limit=0 is refused rather than clamped up to one: a caller who asked for
    // no records and received one has been answered with something they did
    // not ask for, which is the whole failure mode this route is being fixed
    // for.
    const bad = since === null ? "since_seq"
      : limit === null || limit < 1 ? "limit" : null;
    if (bad) {
      send(res, 400, {
        error: bad === "limit"
          ? `limit must be a whole number, at least 1 and at most ${PAGE_MAX}`
          : "since_seq must be a whole number, zero or more",
        hint: `the cursor form is /signals?since_seq=0&limit=${PAGE_DEFAULT}`,
        validation,
      });
      return;
    }
    const take = Math.min(limit, PAGE_MAX);
    const page = records.filter((r) => r.seq > since).slice(0, take);
    send(res, 200, {
      records: page,
      // Never the caller's own unparsed value: `next_seq` used to fall back to
      // `since`, which was NaN for a non-numeric cursor and serialised as
      // null, so a polling client either wedged on it forever or coerced it to
      // 0 and re-read the whole feed. It is now always a number this service
      // computed.
      next_seq: page.length ? page[page.length - 1].seq : since,
      page_limit: take, // what was actually applied, so a clamp is visible
      validation, // once per page, before a single record is opened
    });
  } else if (parts[0] === "signals" && parts.length === 2) {
    const asked = decodeOrNull(parts[1]);
    if (asked === null) {
      send(res, 400, {
        error: "that path segment is not text -- it holds an incomplete " +
          "percent-escape, so there is no ticker to look up",
        validation,
      });
      return;
    }
    const ticker = asked.toUpperCase();
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
}

// The answer of last resort. Every route above already answers "there is
// nothing to show here" with a sentence and the command that writes it -- but
// that whole design only covers the failures a route thought to catch, and one
// wrong-shaped published file (a string where a list belongs, a missing key)
// threw a TypeError mid-render that no route caught. An unguarded throw inside
// the request handler is an uncaughtException, which killed the process: every
// other route on the service went with it, and the person who asked for one
// page got a dead socket. So the handler is wrapped, and a render-time throw
// becomes the same kind of page as a missing file -- what happened, and what
// to run.
function renderFailure(req, res, err) {
  const detail = String((err && err.message) || err);
  console.error(`could not answer ${req.method} ${req.url}: ${detail}`);
  if (res.headersSent) {
    // Half a page is already on the wire; nothing truthful can be added to it.
    res.destroy();
    return;
  }
  let first = "";
  try {
    first = new URL(req.url, `http://${HOST}:${PORT}`)
      .pathname.split("/").filter(Boolean)[0] ?? "";
  } catch { /* an address this unparseable gets the JSON answer */ }
  try {
    if (HTML_ROUTES.has(first)) {
      let verdict = null;
      try {
        verdict = anyValidation();
      } catch { /* the evidence is what just failed; the page says so below */ }
      sendHtml(res, 503, pages.message({
        title: "Not rendered", current: `/${first}`, validation: verdict,
        heading: "This page could not be rendered",
        paragraphs: [
          `The published file this page reads is not shaped the way the ` +
          `viewer expects: ${pages.esc(detail)}.`,
          "Rewrite the published files: <code>ledgerline publish</code>.",
        ],
      }));
      return;
    }
    send(res, 503, {
      error: detail,
      hint: "the published files are not shaped the way this service expects; " +
        "rewrite them: ledgerline publish",
    });
  } catch {
    // Rendering the failure page failed too. Say so in the plainest form
    // this service has, and stay up.
    res.writeHead(503, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`${detail}\nrewrite the published files: ledgerline publish\n`);
  }
}

const server = http.createServer((req, res) => {
  try {
    handle(req, res);
  } catch (err) {
    renderFailure(req, res, err);
  }
});

// The backstop behind the backstop: a throw from an asynchronous callback
// never passes through the try above. A local read service that exits on a
// bad file is worse than one that logs and keeps answering the other routes,
// so nothing here is allowed to end the process.
process.on("uncaughtException", (err) => {
  console.error("uncaught error, still serving:", err);
});
process.on("unhandledRejection", (err) => {
  console.error("unhandled rejection, still serving:", err);
});

// Failing to listen is not a request failing: there is nobody to answer, and
// a process that stayed up holding no socket would look like a running server
// to anyone reading the terminal. This one exception says why and stops.
server.on("error", (err) => {
  console.error(`cannot listen on ${HOST}:${PORT}: ${err.message}`);
  process.exit(1);
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
