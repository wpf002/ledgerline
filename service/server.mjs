// Local read service for the Ledgerline signal feed. Node built-ins only --
// no package.json, no npm install, no node_modules. Run: node server.mjs
//
// Why it exists at all: other programs on this machine can read the feed over
// HTTP instead of parsing the JSONL themselves. Why it does so little: the
// detector is UNVALIDATED -- it failed its own pre-registered test on
// 2026-08-30 -- so this service never computes a score, never pushes anything
// anywhere, and refuses to serve a record that arrives without its validation
// block. Every response body carries that block. It binds 127.0.0.1 only: an
// app under development, serving an unvalidated signal, should not be
// reachable off the machine by default.
//
// The feed is written by `ledgerline publish`. This file reads it, full stop.
// If serving ever requires recomputing a number the Python already computed,
// that is the signal to revisit the boundary, not to port the arithmetic.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FEED = process.env.LEDGERLINE_FEED ??
  path.join(HERE, "..", "reports", "feed", "signals.jsonl");
const PORT = Number(process.env.PORT ?? 8787);
const HOST = "127.0.0.1"; // loopback only, deliberately not configurable

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

// The newest record's validation block. Served on every response; never
// synthesised here -- if the feed holds no records, there is no evidence to
// serve and the endpoints say so instead of defaulting.
function validationOf(records) {
  return records[records.length - 1].validation;
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

const server = http.createServer((req, res) => {
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
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);

  if (req.method !== "GET") {
    // Read-only by construction: the append-only invariant cannot be
    // enforced from here, so this surface is not given the vocabulary to
    // violate it.
    send(res, 405, { error: "this service only reads", validation });
    return;
  }

  if (parts.length === 0) {
    // The front door. A person landing on the root gets a page, not an
    // "unknown path" error -- the JSON routes stay exactly as they were.
    let page;
    try {
      page = fs.readFileSync(path.join(HERE, "index.html"));
    } catch {
      send(res, 404, { error: "index.html missing beside server.mjs", validation });
      return;
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(page);
    return;
  }

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
      routes: ["/signals", "/signals/:ticker", "/validation", "/digest"],
      validation,
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`ledgerline read service on http://${HOST}:${PORT}`);
  console.log(`feed: ${FEED}`);
  console.log("local development only; the signal is unvalidated " +
              "(failed its own test, 2026-08-30)");
});
