// Every page this service serves, as HTML strings. Node built-ins only; the
// one import is ./svg.mjs, the icons and charts, which follows the same rules.
// These are pure functions from published JSON to markup, which is what lets
// a test hold one against the other.
//
// Why the pages are rendered here and not in the browser: the verdict banner.
// The single page this replaced fetched /digest and painted the banner from
// JavaScript, so for one frame -- and forever, with scripting off -- the page
// read "LOADING…" where the failed test belongs. A person who cannot see the
// verdict is a person being shown an unvalidated score as though it were a
// number. Rendering on the server makes the banner the first thing in the
// document body on every route, before the sidebar, before the navigation,
// with no execution required to reveal it. tests/unit/test_web_pages.py pins
// that ordering.
//
// Why the pages compute nothing: the same boundary ledgerline/api/__init__
// states. Plain names for measures, the reason a company cannot be assessed,
// the explain text, the quality chips, the wording of the test's six checks --
// all of it arrives already written from ledgerline/api/views.py. A second
// implementation of "can this company be assessed" in JavaScript would be a
// second answer, and the one on screen. What follows is layout, escaping,
// scaling published numbers to pixels, and arithmetic no larger than a
// percentage of two published counts.

import {
  filingTimeline, icon, logo, monthDay, runHistory, scoreHistory,
} from "./svg.mjs";

const NAV = [
  ["/", "Overview", "overview"],
  ["/watchlist", "Watchlist", "list"],
  ["/company", "Companies", "building"],
  ["/activity", "Activity", "pulse"],
  ["/verdict", "The test", "flask"],
];

const TAGLINE = "Reads companies' SEC filings and flags numbers that break " +
  "from that company's own past.";

// The concern score at which a company with at least two measures out of line
// is flagged. Every sentence on these pages already says 45; the charts draw
// the same line.
const FLAG_LINE = 45;

// Plain names mirror ledgerline/render.py PLAIN, and are used in exactly one
// place: the overview, whose data comes from /digest -- a JSON route with an
// agreed shape that carries the machine code. Every other page reads a
// published view file where the Python already wrote the plain name, and none
// of them look at this map. Display-only duplication of thirteen words;
// docs/VOICE.md forbids showing a person CASH_CONVERSION_GAP, and the
// alternative is changing a contract shape other programs read.
const PLAIN = {
  CASH_CONVERSION_GAP: "cash-vs-sales", ACCRUAL_RATIO: "paper-vs-cash profit",
  RECEIVABLES_VS_REVENUE: "unpaid-bills", INVENTORY_VS_REVENUE: "stockpile",
  DSO: "collection-days", DIO: "shelf-days",
  DEFERRED_VS_REVENUE_GAP: "prepaid-orders", REVENUE_ACCEL: "growth-brake",
  GROSS_MARGIN: "product-margin", OP_MARGIN: "operating-margin",
  OCF_TO_REVENUE: "cash-per-sale", NET_DEBT_TO_TTM_OCF: "debt-vs-cash",
  DILUTION_YOY: "share-creep",
};

// Every list on a page comes out of a published file, and `x || []` only
// defends against the file not having the key -- a file that has it and holds
// a string instead sailed through and threw on `.map`, which took the whole
// process down rather than rendering a page saying so. A published file whose
// shape is wrong is a file to rewrite, not markup to attempt, so the reader
// gets the empty-state sentence the page already has for "nothing here yet".
function list(x) {
  return Array.isArray(x) ? x : [];
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// A filing's page on the SEC's own site. The accession is the document's
// identity; a number on this page that cannot be walked back to the filing it
// came from is the thing this project exists not to publish.
export function secUrl(cik, accession) {
  const bare = String(accession).replace(/-/g, "");
  const num = String(cik ?? "").replace(/^0+/, "");
  return `https://www.sec.gov/Archives/edgar/data/${num}/${bare}/` +
    `${accession}-index.htm`;
}

// Every filing a company has made, as the SEC's archive lists them.
function companyArchive(cik) {
  const num = String(cik ?? "").replace(/^0+/, "");
  return num ? `https://www.sec.gov/Archives/edgar/data/${num}/` : "";
}

function num(v, digits) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: digits ?? 0, maximumFractionDigits: digits ?? 0,
  });
}

// A fraction as a percentage, without a trailing ".0": 0.6 is "60%".
function pct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return `${Number((Number(v) * 100).toFixed(digits))}%`;
}

// A reading keeps the precision it needs and no more: 235.7 collection-days
// and 0.238 of a dollar of margin are both readable, 235.696 is not.
function reading(v) {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  if (a >= 100) return num(v, 0);
  if (a >= 10) return num(v, 1);
  return Number(v).toFixed(3);
}

function bytes(n) {
  if (!n) return "0 KB";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function when(iso) {
  if (!iso) return "—";
  return String(iso).replace("T", " ").replace(/(\.\d+)?(Z|[+-]\d\d:\d\d)$/, "");
}

function plural(n, one, many) {
  return `${num(n)} ${n === 1 ? one : many}`;
}

function clamp100(v) {
  return Math.max(0, Math.min(100, Number(v) || 0));
}

export function query(params) {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

// --------------------------------------------------------- building blocks

function pageHead({ title, sub = "", aside = "", eyebrow = "" }) {
  return '<header class="page-head"><div class="page-title">' +
    (eyebrow ? `<p class="eyebrow">${eyebrow}</p>` : "") +
    `<h2>${title}</h2>${sub ? `<p class="page-sub">${sub}</p>` : ""}</div>` +
    (aside ? `<div class="page-aside">${aside}</div>` : "") + "</header>";
}

function card({ id = "", title = "", sub = "", aside = "", body = "",
  flush = false, cls = "" }) {
  const head = title
    ? `<div class="card-head"><div><h3>${title}</h3>` +
      `${sub ? `<p class="card-sub">${sub}</p>` : ""}</div>` +
      `${aside ? `<div class="card-aside">${aside}</div>` : ""}</div>`
    : "";
  return `<section class="card${flush ? " flush" : ""}${cls ? ` ${cls}` : ""}"` +
    `${id ? ` id="${id}"` : ""}>${head}<div class="card-body">${body}</div></section>`;
}

function kpi({ label, value, sub = "", tone = "", ic = "", extra = "" }) {
  return `<div class="kpi${tone ? ` ${tone}` : ""}">` +
    `<div class="kpi-label">${ic ? icon(ic) : ""}<span>${label}</span></div>` +
    `<div class="kpi-value">${value}</div>` +
    `${sub ? `<div class="kpi-sub">${sub}</div>` : ""}${extra}</div>`;
}

function pill(text, tone = "quiet") {
  return `<span class="pill ${tone}">${text}</span>`;
}

function legend(items) {
  return '<div class="legend">' + items.map(([cls, label]) =>
    `<span><i class="${cls}"></i>${label}</span>`).join("") + "</div>";
}

// A score in a table cell: the number, its scale, and a bar with the flag
// line drawn on it, so 44 and 46 look as close as they are.
function scoreCell(score, flagged) {
  return '<span class="score"><span class="score-bar">' +
    `<span class="score-fill${flagged ? " flag" : ""}" ` +
    `style="width:${clamp100(score).toFixed(1)}%"></span></span>` +
    `<span class="score-num">${esc(score)}<span class="score-of"> of 100</span>` +
    "</span></span>";
}

// How many of the thirteen measures a company's filings support, one tick
// each. `states` is per tick: "flag", "on" (measured) or "" (not measured).
function segments(states, label) {
  return `<span class="segs" role="img" aria-label="${esc(label)}" ` +
    `title="${esc(label)}">${states.map((s) =>
      `<i${s ? ` class="${s}"` : ""}></i>`).join("")}</span>`;
}

// A value against the bar it was judged by. The fill is red when the check
// it belongs to failed; the tick is the bar.
function meter({ value, limit, max }, { failed = false, limitLabel = "" } = {}) {
  const at = (v) => Math.max(0, Math.min(100, (Number(v) / Number(max)) * 100));
  let mark = "";
  if (limit !== null && limit !== undefined) {
    const where = at(limit);
    const side = where < 14 ? " start" : where > 86 ? " end" : "";
    mark = `<span class="meter-limit${side}" style="left:${where.toFixed(1)}%">` +
      `${limitLabel ? `<span class="meter-limit-label">${limitLabel}</span>` : ""}</span>`;
  }
  return `<div class="meter${failed ? " fail" : ""}${limitLabel ? " labelled" : ""}">` +
    `<span class="meter-fill" style="width:${at(value).toFixed(1)}%"></span>` +
    `${mark}</div>`;
}

// ------------------------------------------------------------- the banner

// The verdict, first, from the validation block the Python wrote. It is never
// composed here: the sentence is computed in ledgerline/api/contract.py from
// the frozen numbers in ledgerline/data/phase0.json, so a page cannot carry a
// paraphrase that drifts from the committed result.
export function banner(validation) {
  if (!validation || !validation.statement) {
    // No evidence loaded is not permission to say nothing. The slot keeps its
    // place and says what is missing and how to restore it.
    return '<div class="banner" role="note"><div class="banner-in">' +
      `<span class="banner-tag">${icon("alert")}No verdict</span>` +
      "<p><b>VERDICT NOT AVAILABLE</b> This page cannot read the record of " +
      "the detector's own test, so it cannot show you the verdict. Nothing " +
      "here is a claim that the detector works. Check that " +
      "<code>ledgerline/data/phase0.json</code> is present, then run " +
      "<code>ledgerline publish</code>.</p></div></div>";
  }
  const failed = validation.verdict === "KILL";
  return '<div class="banner" role="note"><div class="banner-in">' +
    `<span class="banner-tag">${icon(failed ? "x" : "info")}` +
    `${failed ? "Failed its own test" : esc(validation.status)}</span>` +
    `<p><b>${esc(validation.status)} · tested ${esc(validation.scored_on)}</b>` +
    `${esc(validation.statement)} <a href="/verdict">See the test</a></p>` +
    "</div></div>";
}

function nav(current) {
  return '<nav class="pages">' + NAV.map(([href, label, ic]) =>
    `<a href="${href}"${href === current ? ' aria-current="page"' : ""}>` +
    `${icon(ic)}<span>${label}</span></a>`).join("") + "</nav>";
}

export function layout({ title, current, validation, body, generated }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>${esc(title)} · Ledgerline</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
${banner(validation)}
<div class="shell">
<aside class="side">
  <div class="brand">${logo()}<h1><a href="/">Ledgerline</a></h1></div>
  ${nav(current)}
  <div class="side-foot">
    <p class="side-note">${icon("lock")}<span>Runs on this machine only. It
      reads what <code>ledgerline publish</code> wrote and changes nothing.</span></p>
    ${generated ? `<p class="side-note">${icon("clock")}<span>Published ${esc(generated)}</span></p>` : ""}
  </div>
</aside>
<main class="main">
  <div class="topbar">
    <form class="search" method="get" action="/company" role="search">
      ${icon("search")}
      <input type="text" name="ticker" placeholder="Open a company by ticker, e.g. FMC"
        aria-label="Ticker" autocomplete="off" spellcheck="false">
      <kbd>Enter</kbd>
    </form>
  </div>
  <div class="content">
${body}
  </div>
  <footer class="foot">
    Local read service. It re-serves what the Python emitted and computes
    nothing${generated ? `; these pages were written by <code>ledgerline publish</code> on ${esc(generated)}` : ""}.
    Raw feeds: <a href="/digest">/digest</a> · <a href="/signals">/signals</a> ·
    <a href="/validation">/validation</a>. In a terminal:
    <code>ledgerline explain TICKER</code>.
  </footer>
</main>
</div>
</body>
</html>
`;
}

// One page for every "there is nothing to show here": what happened, and the
// command that changes it. An empty screen and a stack trace are the two
// answers this file is not allowed to give.
export function message({ title, current, validation, heading, paragraphs }) {
  const body = `<div class="empty-state"><div class="empty-icon">${icon("info")}</div>` +
    `<h2>${esc(heading)}</h2>` + paragraphs.map((p) => `<p>${p}</p>`).join("") +
    "</div>";
  return layout({ title, current, validation, body });
}

// ------------------------------------------------------------------ overview

// Where the numbers on this page came from, in the same words
// ledgerline/api/digest.py provenance_line() uses. Duplicated in English for
// the same reason the expectation sentence is: /digest carries the machine
// fields (source, split) and no sentence, and this page had been dropping
// both -- so a replay over the practice half, the split the thresholds were
// fitted on, rendered as "Latest run" with nothing saying otherwise.
const REPLAY_SPLIT = {
  tuning: " over the practice half",
  holdout: " over the sealed test half",
};
const REPLAY_NOTE = {
  tuning: "The practice half is the companies the thresholds were fitted on. ",
};
const LIVE_SOURCES = ["scan", "score", "emit"];
const BACKFILL_SOURCES = ["replay"];

export function provenance(run) {
  const source = run.source || "unrecorded";
  const when = run.run_date || "an unrecorded date";
  if (BACKFILL_SOURCES.includes(source)) {
    return "These numbers come from a replay" +
      (REPLAY_SPLIT[run.split] || "") + ` at the ${when} checkpoint. It's ` +
      "not a live run. " + (REPLAY_NOTE[run.split] || "") + "Those quarters " +
      "were already available when the thresholds were chosen, so nothing " +
      "here is evidence about companies the detector hasn't seen.";
  }
  if (LIVE_SOURCES.includes(source)) {
    return `These numbers come from a live run on ${when}, recorded as ` +
      `“${source}”.`;
  }
  return `These numbers come from a run recorded as “${source}”` +
    (run.split ? `, over the ${run.split} half` : "") + `, dated ${when}.`;
}

// The test's three headline numbers, drawn against the bars they were
// judged by. All of them are in the validation block every page carries.
function detectorCard(validation) {
  const m = (validation || {}).measured;
  if (!m) return "";
  const failed = validation.verdict === "KILL";
  const times = m.naive_baseline_fpr
    ? (m.fpr_per_control_quarter / m.naive_baseline_fpr).toFixed(1) : null;
  const row = (label, value, note, bar) =>
    `<div class="stat"><div class="stat-top"><span>${label}</span>` +
    `<b>${value}</b></div>${bar}<div class="stat-note">${note}</div></div>`;
  return card({
    title: "How the detector did on its own test",
    sub: `Scored once, on ${esc(validation.scored_on)}, on companies it had ` +
      "never seen.",
    cls: "detector",
    body: row("Deteriorations caught", pct(m.positive_hit_rate),
      `It needed at least ${pct(m.positive_hit_rate_floor)}.`,
      meter({ value: m.positive_hit_rate, limit: m.positive_hit_rate_floor, max: 1 },
        { failed, limitLabel: "needed" })) +
      row("False alarms per quiet company-quarter",
        pct(m.fpr_per_control_quarter, 2),
        times ? `${times} times the ${pct(m.naive_baseline_fpr, 2)} of a ` +
          "two-line rule it had to beat." : "",
        meter({ value: m.fpr_per_control_quarter, limit: m.naive_baseline_fpr,
          max: 0.06 }, { failed, limitLabel: "rule" })) +
      row("Fine companies flagged at least once", pct(m.fpr_per_filer),
        "Of the companies that never deteriorated.",
        meter({ value: m.fpr_per_filer, limit: null, max: 1 }, { failed })) +
      '<a class="card-link" href="/verdict">See all six checks' +
      `${icon("arrow")}</a>`,
  });
}

export function overview(digest, extra = {}) {
  const run = digest.run || {};
  const expected = Number(digest.expected_false_positives_if_nothing_wrong ?? 0);
  const fires = list(digest.fires);
  const wl = extra.watchlist;
  const last = extra.lastRun;
  const scored = Number(run.scoreable) || 0;
  const unscored = Number(run.unscoreable) || 0;
  const flagged = Number(run.gated_in) || 0;
  const total = scored + unscored;

  const split = total
    ? `<div class="split" aria-hidden="true">` +
      `<span class="flag" style="width:${((flagged / total) * 100).toFixed(2)}%"></span>` +
      `<span style="width:${(((scored - flagged) / total) * 100).toFixed(2)}%"></span>` +
      `<span class="na" style="width:${((unscored / total) * 100).toFixed(2)}%"></span></div>`
    : "";
  const kpis = [
    kpi({ ic: "layers", label: "Assessed in the latest run", value: num(run.scoreable),
      sub: `${plural(run.unscoreable, "company", "companies")} could not be assessed`,
      extra: split }),
    kpi({ ic: "alert", label: "Flagged", value: num(run.gated_in),
      tone: flagged ? "flag" : "",
      sub: `About ${expected.toFixed(1)} would be flagged even if nothing were wrong` }),
    wl ? kpi({ ic: "list", label: "Watched companies", value: num(wl.n_companies),
      sub: `${num(wl.n_assessable)} can be assessed` }) : "",
    last ? kpi({ ic: "pulse", label: "Last scan",
      value: esc(monthDay(String(last.started_at || "").slice(0, 10))),
      sub: `${plural(last.universe_hits, "filing", "filings")} from watched companies` })
      : "",
  ].join("");

  const meta = [["run", run.run_date ?? "—"], ["source", run.source ?? "—"],
    ["split", run.split ?? "—"]];
  const where = `<section class="callout info">${icon("info")}<div>` +
    `<dl class="runmeta">${meta.map(([k, v]) =>
      `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` +
    `<p>${esc(provenance(run))}</p></div></section>`;
  const chance = `<section class="callout caution">${icon("alert")}<p>At the ` +
    `measured false-alarm rate, about ${expected.toFixed(1)} of the ` +
    `${num(run.scoreable)} assessed companies would be expected to be flagged ` +
    "even if nothing were wrong. Read the list below with that in mind.</p></section>";

  const flaggedBody = fires.length === 0
    ? '<p class="card-empty">Nothing flagged in this run. That isn\'t a clean ' +
      "bill of health. This detector caught fewer than three in ten of the " +
      "deteriorations it was built to find.</p>"
    : `<div class="table-wrap"><table>
      <thead><tr><th>Company</th><th>Concern score</th><th>Measures out of line</th></tr></thead>
      <tbody>${fires.map((f) => `<tr>
        <td class="tick"><a href="/company/${encodeURIComponent(f.ticker)}">${esc(f.ticker)}</a></td>
        <td>${scoreCell(f.score, true)}</td>
        <td><div class="chips">${list(f.flags).map((x) =>
          `<span class="chip flag">${esc(PLAIN[x] || x)}</span>`).join("")}</div></td>
      </tr>`).join("")}</tbody></table></div>
      <p class="card-note">A company is flagged at 45 of 100 with at least two
        measures out of line. The score only means something against this
        company's own past. It can't be compared between companies, and it
        isn't a probability of anything.</p>`;

  const body = pageHead({
    title: "Overview", sub: TAGLINE,
    aside: run.run_date
      ? `<span class="tag">${icon("calendar")}Latest run ${esc(run.run_date)}</span>` : "",
  }) +
    `<div class="kpis">${kpis}</div>${where}${chance}` +
    '<div class="grid-2-1">' +
    card({ title: "Flagged in the latest run",
      sub: fires.length ? "Each one links to its page, with the filings behind " +
        "every number." : "",
      flush: fires.length > 0, body: flaggedBody }) +
    detectorCard(digest.validation) + "</div>";
  return layout({ title: "Overview", current: "/", validation: digest.validation, body });
}

// ----------------------------------------------------------------- watchlist

function chips(items) {
  if (!items || !items.length) return "";
  return '<div class="chips">' + items.map((c) => {
    const warn = /cannot assess|failed|no filings/.test(c.label) ? " warn" : "";
    return `<span class="chip${warn}" title="${esc(c.detail)}">${esc(c.label)}</span>`;
  }).join("") + "</div>";
}

// The one cell where a score can appear, and the rules that follow it here:
// an unassessed company gets words, never a number, and a quiet company is
// never described as clean.
function lastAssessment(c) {
  const l = c.latest;
  if (!l) return pill("nothing assessed yet", "none");
  if (!l.scoreable) {
    return pill("could not assess", "none") +
      `<div class="cell-sub">${esc(l.reason || "No reason recorded.")}</div>`;
  }
  // Provenance on the cell, not just on the page: every assessment on record
  // today is a replay over the practice half, and this cell read as a current
  // verdict on the company because the row's own source and split were not
  // shown.
  const from = l.source === "replay"
    ? `, replayed${l.split === "tuning" ? " over the practice half" : ""}`
    : "";
  return '<div class="assess">' +
    pill(l.flagged ? "flagged" : "not flagged", l.flagged ? "flag" : "quiet") +
    scoreCell(l.score, l.flagged) + "</div>" +
    `<div class="cell-sub">Quarter ending ${esc(l.period)}, from figures filed ` +
    `by ${esc(l.as_of)}${esc(from)}${list(l.flags).length
      ? `. Out of line: ${list(l.flags).map(esc).join(", ")}` : ""}</div>`;
}

function coverage(c) {
  const have = Number(c.measures_available);
  const all = Number(c.measures_total);
  if (!Number.isFinite(have) || !Number.isFinite(all) || all <= 0) {
    return '<span class="muted">—</span>';
  }
  const states = Array.from({ length: all }, (_, i) => (i < have ? "on" : ""));
  return segments(states, `${have} of ${all} measures can be computed`) +
    `<span class="segs-label">${num(have)}/${num(all)}</span>`;
}

const ASSESSABLE_OPTIONS = [
  ["", "All"],
  ["yes", "Can be assessed"],
  ["no", "Cannot be assessed"],
  ["unknown", "Not checked yet"],
];

// The same three states said as modifiers of "companies", for the sentence
// that restates a filter which matched nothing. The filter labels answer
// "which ones?"; these have to survive being read mid-sentence.
const ASSESSABLE_PHRASE = {
  yes: "that can be assessed",
  no: "that cannot be assessed",
  unknown: "not checked yet",
};

function filters(data, f) {
  const groupOpts = ['<option value="">Every group</option>'].concat(
    list(data.groups).map((g) =>
      `<option value="${esc(g.name)}"${g.name === f.group ? " selected" : ""}>` +
      `${esc(g.name)} (${num(g.n)})</option>`)).join("");
  const states = ASSESSABLE_OPTIONS.map(([v, label]) => {
    const on = v === (f.assessable || "");
    return `<a href="/watchlist${query({ q: f.q, group: f.group, assessable: v })}"` +
      `${on ? ' class="on" aria-current="true"' : ""}>${label}</a>`;
  }).join("");
  return `<div class="toolbar">
  <form class="filters" method="get" action="/watchlist">
    <label class="field-search">${icon("search")}<input type="text" name="q"
      value="${esc(f.q)}" placeholder="Search by ticker or name"
      aria-label="Search by ticker or name"></label>
    <select name="group" aria-label="Group">${groupOpts}</select>
    ${f.assessable ? `<input type="hidden" name="assessable" value="${esc(f.assessable)}">` : ""}
    <button type="submit" class="btn">Show</button>
    ${f.q || f.group || f.assessable ? '<a class="clear" href="/watchlist">Clear</a>' : ""}
  </form>
  <nav class="seg" aria-label="Filter by whether a company can be assessed">${states}</nav>
</div>`;
}

// Why an empty result is never just an empty table: the four ways to get here
// are four different pieces of news, and one blank screen says the wrong one
// three times out of four. This is the same distinction `ledgerline watch
// --group` makes at the terminal -- an unknown group is a typo, an empty
// group is a group nobody has filled in.
function emptyExplanation(data, f) {
  const known = list(data.groups).map((g) => g.name);
  if (!list(data.companies).length) {
    return ["No companies are being watched yet.",
      "Add some: <code>ledgerline watch --add AAPL,MSFT,NVDA</code>, or " +
      "import a spreadsheet with <code>ledgerline watch --import list.csv</code>."];
  }
  if (f.group && !known.some((n) => n.toLowerCase() === f.group.toLowerCase())) {
    return [`There is no group called “${esc(f.group)}”.`,
      known.length
        ? `The groups you have: ${known.map(esc).join(", ")}.`
        : "You have not created any groups yet.",
      "Create one: <code>ledgerline groups --assign semis --tickers NVDA,AMD,INTC</code>."];
  }
  const grp = list(data.groups).find(
    (g) => g.name.toLowerCase() === (f.group || "").toLowerCase());
  if (grp && grp.n === 0) {
    return [`The group “${esc(grp.name)}” exists, and no watched company is ` +
      "in it yet. This is not the same as none of them qualifying.",
    `Put companies in it: <code>ledgerline groups --assign ${esc(grp.name)} ` +
    "--tickers NVDA,AMD</code>."];
  }
  // The filter is restated as one noun phrase hanging off a single head noun,
  // and the count is reported separately from it. Joining the filters as
  // predicates of "companies are ..." produced "companies are that cannot be
  // assessed", and for the assessability filters it also said the opposite of
  // what happened: "none of your companies cannot be assessed" claims they can
  // all be assessed, when the real reason for the empty table is that nobody
  // has run `check` and every one of them is still unknown.
  const bits = ["companies"];
  if (f.assessable) bits.push(ASSESSABLE_PHRASE[f.assessable]);
  if (f.group) bits.push(`in the group “${esc(f.group)}”`);
  if (f.q) bits.push(`with “${esc(f.q)}” in the ticker or name`);
  const out = ["Nothing matched.",
    `You asked for ${bits.join(" ")}. None of your ` +
    `${num(list(data.companies).length)} watched companies fit.`];
  if (f.assessable === "yes" || f.assessable === "no") {
    out.push("Assessability is recorded by <code>ledgerline check</code>; " +
      "until that has run a company is neither, because it hasn't been " +
      "checked.");
  }
  // Only sound when assessability is the ONLY thing narrowing the table.
  // Alongside a search box that matched nothing, "every watched company has
  // been checked" is a claim about the whole watchlist drawn from a result
  // that says nothing about it -- and on this machine it is false.
  if (f.assessable === "unknown" && !f.q && !f.group) {
    out.push("Every watched company has been checked. Try " +
      '<a href="/watchlist?assessable=no">the ones that cannot be assessed</a>.');
  }
  out.push('<a href="/watchlist">Show every watched company.</a>');
  return out;
}

// "Nothing has been checked" and "everything has been checked and none of it
// can be assessed" are two pieces of news, and this sentence used to say the
// first one for both: it branched on the truthiness of n_assessable, which is
// zero either way. A watchlist where `check` has run against a cold cache
// therefore told the reader to run the very command whose result was being
// reported, directly above rows each carrying a "cannot assess" chip.
// n_checked is what separates them; a file published before that field existed
// carries no answer, and the old sentence is the honest one for it.
function assessableSentence(data) {
  const watched = num(data.n_companies);
  if (data.n_assessable) {
    return `${num(data.n_assessable)} of the ${watched} watched have been ` +
      "checked and can be assessed; every other row says why not.";
  }
  if (data.n_checked) {
    return `${num(data.n_checked)} of the ${watched} watched have been ` +
      "checked, and none of them can be assessed yet; every row says why not.";
  }
  return "None of them have been checked yet, so nothing here is known to be " +
    "assessable. <code>ledgerline check</code> records that. It's quick " +
    "once <code>ledgerline fetch</code> has run.";
}

export const PAGE_SIZE = 250;

export function watchlist(data, f) {
  const q = (f.q || "").trim().toLowerCase();
  const rows = list(data.companies).filter((c) => {
    if (q && !(String(c.ticker || "").toLowerCase().includes(q) ||
               String(c.name || "").toLowerCase().includes(q))) return false;
    if (f.group && !list(c.groups).some(
      (g) => g.toLowerCase() === f.group.toLowerCase())) return false;
    if (f.assessable === "yes" && c.assessable !== true) return false;
    if (f.assessable === "no" && c.assessable !== false) return false;
    if (f.assessable === "unknown" && c.assessable !== null) return false;
    return true;
  });
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(Math.max(1, Number(f.page) || 1), pages);
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const watched = list(data.companies).length;

  const kpis = [
    kpi({ ic: "list", label: "Watched", value: num(data.n_companies),
      sub: data.generated ? `Published ${esc(data.generated)}` : "" }),
    kpi({ ic: "check", label: "Can be assessed", value: num(data.n_assessable),
      sub: data.n_checked ? `${num(data.n_checked)} checked so far`
        : "Nobody has run the check yet" }),
    data.n_checked ? kpi({ ic: "x", label: "Cannot be assessed",
      value: num(data.n_checked - (data.n_assessable || 0)),
      sub: "Each row says why" }) : "",
    kpi({ ic: "alert", label: "Flagged", value: num(data.n_flagged),
      tone: data.n_flagged ? "flag" : "",
      sub: "In their latest saved assessment" }),
  ].join("");

  let inner = filters(data, f);
  if (!rows.length) {
    inner += '<div class="card-body"><div class="empty-inline">' +
      emptyExplanation(data, f).map((p) => `<p>${p}</p>`).join("") + "</div></div>";
  } else {
    const range = rows.length > PAGE_SIZE
      ? `, showing ${num((page - 1) * PAGE_SIZE + 1)}–${
        num((page - 1) * PAGE_SIZE + shown.length)}`
      : "";
    inner += `<p class="table-caption">${plural(rows.length, "company", "companies")}${
      rows.length === watched ? " watched" : ` of ${num(watched)} watched`}${range}.
      ${assessableSentence(data)}</p>`;
    inner += `<div class="table-wrap"><table class="watch">
    <thead><tr><th>Company</th><th>Measures it supports</th>
      <th>Latest saved assessment</th><th>What to know about this row</th></tr></thead>
    <tbody>${shown.map((c) => `<tr>
      <td class="tick"><a href="/company/${encodeURIComponent(c.ticker || "")}">${esc(c.ticker || "—")}</a>
        <span class="name">${esc(c.name || "")}</span>${list(c.groups).length
          ? `<span class="grp">${list(c.groups).map(esc).join(", ")}</span>` : ""}</td>
      <td class="nowrap">${coverage(c)}</td>
      <td>${lastAssessment(c)}</td>
      <td>${chips(c.quality) || '<span class="muted">nothing outstanding</span>'}</td>
    </tr>`).join("")}</tbody>
    </table></div>`;
    if (pages > 1) {
      inner += `<div class="pager">
        ${page > 1 ? `<a class="btn secondary" href="/watchlist${query({ ...f, page: page - 1 })}">← Previous</a>` : "<span></span>"}
        <span class="muted">Page ${num(page)} of ${num(pages)}</span>
        ${page < pages ? `<a class="btn secondary" href="/watchlist${query({ ...f, page: page + 1 })}">Next →</a>` : "<span></span>"}
      </div>`;
    }
    inner += `<p class="card-note">A score is a reading against this company's own past,
       0–100, flagged at 45 with at least two measures out of line. Not being
       flagged is not a clean bill of health. In its own test this detector
       missed seven deteriorations in ten.</p>`;
  }
  const body = pageHead({ title: "Watchlist",
    sub: "Every company being watched, whether it can be assessed, and what " +
      "its latest saved assessment said." }) +
    `<div class="kpis">${kpis}</div>` +
    `<section class="card flush watchlist-card">${inner}</section>`;
  return layout({
    title: "Watchlist", current: "/watchlist",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------- company

/* How far a measure sits from this company's own normal, drawn on one shared
   axis so the rows compare. The axis runs from 4 times its usual wobble in
   the harmless direction to 6 times in the bad-news direction; the shaded
   zone starts at the trigger (2.0, where a measure starts counting as out of
   line). The published z is signed so that positive is the bad-news
   direction, and the row's own out_of_line flag decides the colour -- a
   large move the harmless way is not a red dot. Past either end the dot sits
   on the end with an arrow, and the number beside it says how far.

   The gate stops crediting extra beyond 2.5 (Z_CAP), so a dot far to the
   right added no more to the score than one just inside the zone. A floored
   scale is a hollow amber dot, because that multiple is a ceiling. */
const Z_TRIGGER = 2.0;
const Z_LO = -4;
const Z_HI = 6;

function zpos(z) {
  return ((Math.min(Z_HI, Math.max(Z_LO, z)) - Z_LO) / (Z_HI - Z_LO)) * 100;
}

function distance(m) {
  const z = Number(m.z);
  if (!Number.isFinite(z)) return '<span class="muted">—</span>';
  const cls = m.out_of_line ? " flag" : m.floored ? " floored" : "";
  const off = z > Z_HI ? '<span class="dist-off right">›</span>'
    : z < Z_LO ? '<span class="dist-off left">‹</span>' : "";
  return '<div class="dist-cell"><div class="dist">' +
    `<span class="dist-zone" style="left:${zpos(Z_TRIGGER)}%"></span>` +
    `<span class="dist-zero" style="left:${zpos(0)}%"></span>` +
    `<span class="dist-dot${cls}" style="left:${zpos(z).toFixed(1)}%"></span>` +
    `${off}</div><span class="zval${cls}">${Math.abs(z).toFixed(1)}×` +
    `${z < 0 ? '<small>the harmless way</small>' : ""}</span></div>`;
}

function measuresTable(measures) {
  if (!measures.length) return "";
  return `<div class="table-wrap"><table class="measures">
  <thead><tr><th>Measure</th><th class="r">Latest reading</th>
    <th>Distance from its own normal</th><th>What this row says</th></tr></thead>
  <tbody>${measures.map((m) => {
    let past = '<span class="muted">—</span>';
    if (m.unavailable_reason) {
      past = '<span class="muted">not measured</span>';
    } else if (m.z !== null && m.z !== undefined) {
      past = distance(m);
      if (m.baseline_median !== null && m.baseline_median !== undefined) {
        past += `<div class="cell-sub">Own median ${reading(m.baseline_median)},` +
          ` spread ${reading(m.baseline_scale)}, over its last ${num(m.baseline_n)}` +
          " readings</div>";
      }
    }
    let says;
    if (m.unavailable_reason) {
      says = `<span class="muted">Cannot be computed: ` +
        `${esc(m.unavailable_reason)}.</span>`;
    } else if (m.out_of_line) {
      says = `<span class="says-flag">Out of line.</span> ` +
        `${esc(m.breaks_when)}.` + (m.floored
          ? ' <span class="cell-sub">This figure barely moves, so a minimum ' +
            "wobble was used instead of its own. Read the multiple as a " +
            "ceiling.</span>" : "");
    } else {
      says = '<span class="muted">Within this company\'s own pattern.</span>';
    }
    return `<tr class="${m.out_of_line ? "is-flag" : ""}${m.unavailable_reason ? " is-na" : ""}">
      <td><div class="m-name">${esc(m.measure)}</div>${m.technical
        ? `<div class="m-tech">${esc(m.technical)}</div>` : ""}</td>
      <td class="r num">${m.unavailable_reason ? '<span class="muted">—</span>' : reading(m.value)}</td>
      <td>${past}</td><td class="says">${says}</td></tr>`;
  }).join("")}</tbody>
  </table></div>
  <div class="card-note">
  <p>The dot shows how far each measure sits from this company's own normal,
   in multiples of its usual wobble. The thin line is its normal, and the
   shaded zone starts at 2×, the trigger: a dot in it counts as out of line.
   Left of the line is the harmless direction. The gate stops crediting extra
   beyond 2.5×, so a dot far to the right added no more to the score than one
   just inside the zone. A hollow amber dot means the figure barely moves, so
   a minimum wobble was used instead of its own. Read that multiple as a
   ceiling.</p>
  <p>“Its usual wobble” is this company's own quarter-to-quarter spread,
   measured over its own recent history. Other companies don't enter into
   it. The score adds up the measures that moved far enough in the bad-news
   direction, and the company is flagged at 45 of 100 with at least two of
   them.</p></div>`;
}

// The explain text is the paragraph `ledgerline explain` prints, written by
// the Python. It's set here as prose: an indented block is one measure (its
// name and plain reading, then how far it moved, then the technical line),
// and everything else is a paragraph. No word of it is changed.
function explainBlocks(text) {
  return String(text || "").split(/\n\s*\n/).filter((b) => b.trim()).map((b) => {
    const lines = b.split("\n");
    if (lines.every((ln) => /^\s{2,}\S/.test(ln))) {
      const [first, ...rest] = lines.map((ln) => ln.trim());
      const m = first.match(/^([^:]{1,40}):\s*(.*)$/);
      return '<div class="x-item">' +
        (m ? `<div class="x-name">${esc(m[1])}</div><div class="x-say">${esc(m[2])}</div>`
          : `<div class="x-say">${esc(first)}</div>`) +
        rest.map((ln) => /^\(technical:/.test(ln)
          ? `<div class="x-tech">${esc(ln)}</div>`
          : `<div class="x-more">${esc(ln)}</div>`).join("") + "</div>";
    }
    return `<p>${lines.map((ln) => esc(ln.trim())).join("<br>")}</p>`;
  }).join("");
}

function timelineTable(page) {
  const f = list(page.filings);
  if (!f.length) {
    return '<div class="empty-inline"><p>No filings are stored for this ' +
      "company yet.</p><p>Download its filing history: <code>ledgerline fetch " +
      `--only ${esc(page.ticker)}</code>.</p></div>`;
  }
  const chart = filingTimeline(f);
  return (chart ? `<div class="chart-wrap">${chart}</div>` : "") +
    `<details class="more"><summary>Show all ${plural(f.length, "filing", "filings")}</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>Form</th><th>Filed</th><th>Period it reports</th><th>Accession</th></tr></thead>
  <tbody>${f.map((r) => `<tr>
    <td><span class="form">${esc(r.form || "—")}</span></td><td class="num">${esc(r.filed || "—")}</td>
    <td class="num">${esc(r.period || "—")}${r.n_periods > 1
      ? `<div class="cell-sub">cited by ${num(r.n_periods)} quarters</div>` : ""}</td>
    <td class="acc"><a href="${esc(secUrl(page.cik, r.accession))}"
      rel="noreferrer">${esc(r.accession)}</a></td></tr>`).join("")}</tbody>
  </table></div></details>
  <p class="card-note">${plural(f.length, "filing", "filings")} behind the stored
   figures, newest first${page.filings_truncated
    ? ", truncated to the most recent ones" : ""}. Each accession opens that
   filing on the SEC's own site. A quarter worked out by subtracting one
   year-to-date report from another cites both filings, which is why one
   filing can be cited by more than one quarter.</p>`;
}

function restatementsTable(page) {
  const r = list(page.restatements);
  if (!r.length) {
    return '<p class="card-empty">No revised figures recorded for this company. ' +
      "Revisions are noticed when a later filing restates a figure this tool " +
      "already stored, so a company fetched once has nothing to compare " +
      "against yet.</p>";
  }
  return `<div class="table-wrap"><table>
  <thead><tr><th>Figure</th><th>Quarter</th><th class="r">First filed</th>
    <th class="r">Revised to</th><th class="r">Change</th><th>Revised on</th></tr></thead>
  <tbody>${r.map((x) => `<tr>
    <td>${esc(x.metric_plain || x.metric)}</td>
    <td class="num">${esc(x.end_date)}</td>
    <td class="r num">${reading(x.prior_value)}
      <div class="cell-sub">filed ${esc(x.prior_filed || "—")}</div></td>
    <td class="r num">${reading(x.value)}</td>
    <td class="r num">${x.rel_change === null || x.rel_change === undefined
      ? "—" : `${(x.rel_change * 100).toFixed(1)}% ${esc(x.direction)}`}
      ${x.material ? "" : '<div class="cell-sub">under 1%</div>'}</td>
    <td class="num">${esc(x.filed || "—")}<div class="cell-sub">${
      esc(x.form || "")}${x.on_amendment ? ", an amendment" : ""}</div></td>
  </tr>`).join("")}</tbody>
  </table></div>
  <p class="card-note">Revisions under 1% are shown too. They are 42.5% of every
   revision measured here, and a page that hid them would be reporting a
   revision rate it had already filtered.</p>`;
}

// Why this section asks whether anything was assessed before it says anything
// about tracing: an unassessable company used to render "Every figure behind
// the measures that broke was traced back to the filing it came from" followed
// by "No measure broke from this company's pattern in the latest assessment".
// Both sentences were false in the same way. Zero of the thirteen measures
// were evaluated -- the same page prints "not measured" on every one of them --
// so nothing broke, nothing was quiet, and no trail was ever resolved to
// guarantee. Read together they are a clean bill of health with a provenance
// stamp on it, on a company this tool declined to assess, which is the single
// reading the project exists to prevent (render.py:12 records the earlier
// version of this bug at the terminal).
function provenanceTrail(page, cik) {
  const prov = page.provenance || {};
  const measures = list(prov.measures);
  const label = prov.label;
  const latest = page.latest;
  if (!latest) {
    return '<p class="card-empty">No assessment has been saved for this company, ' +
      "so no measure has been evaluated and there is nothing to trace. The " +
      "filings above are the whole record of what was read.</p>";
  }
  if (!latest.scoreable && !measures.length) {
    return '<p class="card-empty">This company could not be assessed, so no ' +
      "measure was evaluated and no figure was traced. The filings above are " +
      "the whole record of what was read.</p>" +
      (latest.reason ? `<p class="card-empty">${esc(latest.reason)}</p>` : "");
  }
  const head = [];
  if (label) {
    head.push(label === "TRACED"
      ? "Every figure behind the measures that broke was traced back to the " +
        "filing it came from."
      : label === "PARTIAL"
        ? "Some of the figures behind the measures that broke could not be " +
          "traced back to a filing."
        : "The figures behind this reading could not be traced back to their " +
          "filings, so no score was published from it.");
  }
  if (prov.derived_fraction !== null && prov.derived_fraction !== undefined) {
    head.push(`${(prov.derived_fraction * 100).toFixed(0)}% of the quarterly ` +
      "figures behind this reading were worked out by subtracting one " +
      "year-to-date report from another rather than read straight off a " +
      "filing. That's the normal path for cash-flow figures." +
      (prov.derived_fraction_high
        ? " This company is above every filer measured, so it's worth a " +
          "second look." : ""));
  }
  const notes = head.map((p) => `<p>${esc(p)}</p>`).join("");
  if (!measures.length) {
    return `<div class="trail-head">${notes}</div>
      <p class="card-empty">No measure broke from this company's pattern in the
       latest assessment, so there is no per-figure trail to show. The filings
       above are the whole record of what was read.</p>`;
  }
  return `<div class="trail-head">${notes}</div>` +
    `<div class="table-wrap"><table>
    <thead><tr><th>Measure</th><th>Figure it used</th><th>As filed</th>
        <th>Filing it came from</th></tr></thead>
    <tbody>${measures.map((m) => list(m.inputs).map((t, i) =>
      `<tr class="${i === 0 ? "group-start" : "group-cont"}"><td>${i === 0
        ? `<span class="m-name">${esc(m.measure)}</span>` : ""}</td>
        <td>${esc(t.figure)}<div class="cell-sub mono">${esc(t.concept || "")}</div></td>
        <td class="num">${esc(t.period || "—")}<div class="cell-sub">${
        t.origin === "derived"
          ? "worked out from year-to-date reports"
          : "reported directly"}</div></td>
        <td class="acc">${list(t.sources).map((a) =>
          `<a href="${esc(secUrl(cik, a))}" rel="noreferrer">${esc(a)}</a>`)
        .join("<br>") || "—"}<div class="cell-sub">${esc(t.form || "")}${
        t.filed ? `, filed ${esc(t.filed)}` : ""}</div></td></tr>`).join(""))
      .join("")}</tbody>
    </table></div>
    <p class="card-note">Every figure above opens the filing it was read from on the
     SEC's own site. A figure marked as worked out from year-to-date reports
     cites both filings it was differenced from. Most filers report cash flow
     only as year-to-date totals, so that's the only way to get a quarter.</p>`;
}

function historyTable(page) {
  const h = list(page.history);
  if (h.length < 2) return "";
  return card({ id: "history", title: "Earlier assessments", flush: true,
    sub: "Saved assessments, newest first. Each one was made only from " +
      "figures filed by its own date. None of them can see a later filing.",
    body: `<details class="more"><summary>Show all ${num(h.length)} assessments</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>As of</th><th>Quarter</th><th>Result</th><th>Measures out of line</th></tr></thead>
  <tbody>${h.map((r) => `<tr><td class="num">${esc(r.as_of)}</td>
    <td class="num">${esc(r.period || "—")}</td>
    <td>${!r.scoreable
      ? pill("could not assess", "none") +
        `<div class="cell-sub">${esc(r.reason || "")}</div>`
      : `<div class="assess">${pill(r.flagged ? "flagged" : "not flagged",
        r.flagged ? "flag" : "quiet")}${scoreCell(r.score, r.flagged)}</div>`}</td>
    <td class="muted">${list(r.flags).map(esc).join(", ") || "—"}</td></tr>`).join("")}</tbody>
  </table></div></details>` });
}

// The latest assessment at a glance. Every sentence it can show is one the
// sections below say in full; it decides nothing they don't.
function latestCard(page) {
  const l = page.latest;
  const measures = list(page.measures);
  if (!l) {
    return card({ title: "Latest assessment", cls: "latest",
      body: pill("nothing assessed yet", "none") +
        '<p class="latest-note">No assessment has been saved for this company, ' +
        `so there are no readings to show. <code>ledgerline score ` +
        `${esc(page.ticker)} --emit</code> saves one.</p>` });
  }
  const run = l.run || {};
  const source = run.source === "replay"
    ? `Replayed${run.split === "tuning" ? " over the practice half" : ""}`
    : run.source ? `Recorded as “${esc(run.source)}”` : "";
  const facts = [
    ["Quarter ending", esc(l.period || "—")],
    ["Figures filed by", esc(l.as_of || "—")],
    source ? ["Source", source] : null,
  ].filter(Boolean);
  if (l.scoreable) {
    const prov = page.provenance || {};
    if (prov.label === "TRACED") facts.push(["Traced to filings", "every figure"]);
    else if (prov.label === "PARTIAL") facts.push(["Traced to filings", "some figures"]);
  }
  const factList = `<dl class="facts">${facts.map(([k, v]) =>
    `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  if (!l.scoreable) {
    return card({ title: "Latest assessment", cls: "latest",
      body: pill("could not assess", "none") +
        `<p class="latest-note">${esc(l.reason || "No reason recorded.")}</p>${factList}` });
  }
  let strip = "";
  if (measures.length) {
    const states = measures.map((m) =>
      (m.unavailable_reason ? "" : m.out_of_line ? "flag" : "on"));
    const out = states.filter((s) => s === "flag").length;
    const within = states.filter((s) => s === "on").length;
    const missing = states.length - out - within;
    strip = '<div class="strip">' +
      segments(states, `${out} out of line, ${within} within, ${missing} not measured`) +
      `<div class="strip-label"><b class="${out ? "flag" : ""}">${out} out of line</b>` +
      ` · ${within} within${missing ? ` · ${missing} not measured` : ""}</div></div>`;
  }
  return card({ title: "Latest assessment", cls: "latest",
    body: '<div class="big-score">' +
      `<span class="n${l.flagged ? " flag" : ""}">${esc(l.score)}</span>` +
      '<span class="of">out of 100</span>' +
      pill(l.flagged ? "flagged" : "not flagged", l.flagged ? "flag" : "quiet") +
      "</div>" +
      '<div class="gauge" role="img" aria-label="Concern score against the flag line at 45">' +
      `<span class="gauge-fill${l.flagged ? " flag" : ""}" ` +
      `style="width:${clamp100(l.score).toFixed(1)}%"></span>` +
      `<span class="gauge-mark" style="left:${FLAG_LINE}%"><span>flag line ${FLAG_LINE}</span></span></div>` +
      strip + factList });
}

export function company(page) {
  const history = list(page.history);
  const measures = list(page.measures);
  const archive = companyArchive(page.cik);
  const meta = [
    ["CIK", page.cik],
    ["Industry code", page.sic || "not recorded"],
    ["Groups", list(page.groups).join(", ") || "none"],
  ];
  const head = `<header class="co-head">
  <div class="co-id">
    <span class="ticker-badge">${esc(page.ticker)}</span>
    <div><h2>${esc(page.name || page.ticker)}</h2>
      <dl class="co-meta">${meta.map(([k, v]) =>
        `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></div>
  </div>
  ${archive ? `<a class="btn secondary" href="${esc(archive)}" rel="noreferrer">${icon("external")}Every filing on the SEC's site</a>` : ""}
</header>
<nav class="tabs" aria-label="Sections of this page">
  <a href="#summary">Summary</a><a href="#reading">In plain words</a>
  <a href="#measures">Measures</a><a href="#filings">Filings</a>
  <a href="#revisions">Revisions</a><a href="#sources">Sources</a>${
  history.length >= 2 ? '<a href="#history">History</a>' : ""}
</nav>`;

  const chart = history.length >= 2
    ? card({ title: "Concern score at each saved assessment",
      sub: "Oldest on the left. Each one used only figures filed by its own date.",
      aside: legend([["flag", "flagged"], ["", "not flagged"],
        ["na", "could not assess"], ["line", `flag line ${FLAG_LINE}`]]),
      body: `<div class="chart-wrap">${scoreHistory(history, FLAG_LINE)}</div>` })
    : "";
  const summary = `<div class="co-summary${chart ? "" : " solo"}" id="summary">` +
    `${latestCard(page)}${chart}</div>`;

  const readingCard = card({ id: "reading", title: "In plain words",
    sub: `The same reading <code>ledgerline explain ${esc(page.ticker)}</code> prints.`,
    body: `<div class="reading">${explainBlocks(page.explain)}</div>` });

  const measuresCard = card({ id: "measures", title: "The thirteen measures",
    sub: "How far each one sits from this company's own normal.",
    aside: measures.length ? legend([["dot", "within its pattern"],
      ["dot flag", "out of line"], ["ring", "minimum wobble used"]]) : "",
    flush: measures.length > 0,
    body: measuresTable(measures) || '<p class="card-empty">No assessment has ' +
      "been saved for this company, so there are no readings to show. " +
      `<code>ledgerline score ${esc(page.ticker)} --emit</code> saves one.</p>` });

  const filingsCard = card({ id: "filings", title: "Filings these numbers came from",
    sub: list(page.filings).length ? "Each mark is one filing, by the date it was filed." : "",
    aside: list(page.filings).length >= 2 ? legend([["annual", "annual report"],
      ["quarterly", "quarterly report"], ["other", "other filing"],
      ["amend", "amendment"]]) : "",
    flush: list(page.filings).length > 0, body: timelineTable(page) });

  const revisionsCard = card({ id: "revisions", title: "Figures later revised",
    flush: list(page.restatements).length > 0, body: restatementsTable(page) });

  const sourcesCard = card({ id: "sources", title: "Where each number came from",
    flush: true, cls: "sources", body: provenanceTrail(page, page.cik) });

  const body = head + summary + readingCard + measuresCard + filingsCard +
    revisionsCard + sourcesCard + historyTable(page);
  return layout({
    title: page.ticker, current: "/company",
    validation: page.validation, body, generated: page.generated,
  });
}

// The Companies tab with no company named: the list is 1,498 rows long, so the
// useful landing is a search box and the companies something was said about.
export function companyIndex(data, note) {
  const flagged = list(data.companies).filter(
    (c) => (c.latest || {}).flagged).slice(0, 25);
  const body = pageHead({ title: "Companies",
    sub: "Every watched company has a page: its plain-language reading, the " +
      "thirteen measures, the filings behind each number, and anything later " +
      'revised. Browse them all on the <a href="/watchlist">watchlist</a>.' }) +
    (note ? `<section class="callout caution">${icon("alert")}<p>${note}</p></section>` : "") +
    card({ title: "Open a company", cls: "lookup", body: `
<form class="lookup-form" method="get" action="/company">
  <label class="field-search big">${icon("search")}<input type="text" name="ticker"
    placeholder="Ticker, e.g. FMC" aria-label="Ticker" autocomplete="off"></label>
  <button type="submit" class="btn">Open the company page</button>
</form>` }) +
    (flagged.length ? card({ title: "Flagged in their latest saved assessment",
      flush: true, body: `<div class="table-wrap"><table>
<thead><tr><th>Company</th><th>Concern score</th><th>Measures out of line</th></tr></thead>
<tbody>${flagged.map((c) => `<tr>
  <td class="tick"><a href="/company/${encodeURIComponent(c.ticker)}">${esc(c.ticker)}</a>
    <span class="name">${esc(c.name || "")}</span></td>
  <td>${scoreCell(c.latest.score, true)}</td>
  <td><div class="chips">${list(c.latest.flags).map((x) =>
    `<span class="chip flag">${esc(x)}</span>`).join("")}</div></td></tr>`).join("")}</tbody>
</table></div>
<p class="card-note">Flagged at 45 of 100 with at least two measures out of line.
 A flag is a prompt to read the filings. It doesn't show anything is wrong.
 This detector's false-alarm rate was 7.5 times that of the crude two-line
 rule it had to beat.</p>` }) : "");
  return layout({
    title: "Companies", current: "/company",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------ activity

export function activity(data) {
  const runs = list(data.runs);
  const head = pageHead({ title: "Activity",
    sub: "Every scan and fetch this machine has run, newest first. A scan " +
      "makes one request for each day of the SEC's daily filing index it " +
      "reads. Watching more companies doesn't add requests. That's why this " +
      "tool doesn't poll each company." });
  if (!runs.length) {
    return layout({
      title: "Activity", current: "/activity", validation: data.validation,
      generated: data.generated,
      body: head + '<div class="empty-state"><div class="empty-icon">' +
        `${icon("pulse")}</div><h2>Nothing has run yet on this machine</h2>` +
        "<p>Read today's filings: <code>ledgerline scan</code>. Download filing " +
        "histories first with <code>ledgerline fetch</code>.</p></div>",
    });
  }
  const last = runs[0];
  const lastDay = String(last.started_at || "").slice(0, 10);
  const kpis = [
    kpi({ ic: "clock", label: "Last run", value: esc(monthDay(lastDay)),
      tone: last.status === "failed" ? "flag" : "",
      sub: `${esc(last.job)} #${esc(last.run_id)}, ${esc(last.status)}` }),
    kpi({ ic: "layers", label: "Requests to the SEC", value: num(last.requests),
      sub: `${num(last.cache_hits)} served from cache, ${bytes(last.bytes_fetched)}` }),
    kpi({ ic: "file", label: "Filings listed", value: num(last.index_rows),
      sub: `${num(last.universe_hits)} from watched companies` }),
    kpi({ ic: "sigma", label: "Revised figures found", value: num(last.restatements),
      sub: "In the last run" }),
  ].join("");
  const chart = runHistory(runs);
  const table = `<div class="table-wrap"><table>
  <thead><tr><th>Run</th><th>Started</th><th class="r">Took</th><th>Requests to the SEC</th>
      <th>What it read</th><th>What it found</th><th class="r">Could not assess</th></tr></thead>
  <tbody>${runs.map((r) => {
      const secs = (r.started_at && r.finished_at)
        ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : null;
      return `<tr>
    <td><span class="run-id">${esc(r.job)} <span class="muted">#${esc(r.run_id)}</span></span>
      <div>${pill(esc(r.status), r.status === "failed" ? "flag" : "quiet")}</div>${r.error
          ? `<div class="cell-sub">${esc(String(r.error).split("\n")[0])}</div>` : ""}</td>
    <td class="num">${esc(when(r.started_at))}</td>
    <td class="r num">${secs === null ? "—" : `${secs.toFixed(1)}s`}</td>
    <td class="num">${num(r.requests)}<div class="cell-sub">${
        num(r.cache_hits)} served from cache, ${bytes(r.bytes_fetched)}</div></td>
    <td class="num">${num(r.index_rows)} filings listed
      <div class="cell-sub">${num(r.universe_hits)} from watched companies,
      ${num(r.filers_done)} read${r.filers_failed
        ? `, ${num(r.filers_failed)} failed` : ""}</div></td>
    <td class="num">${r.assessed === null || r.assessed === undefined
        ? '<span class="muted">nothing assessed</span>'
        : `${num(r.assessed)} assessed<div class="cell-sub">${
          num(r.gated_in)} flagged, ${num(r.restatements)} revisions found</div>`}</td>
    <td class="r num">${r.could_not_assess === null || r.could_not_assess === undefined
        ? "—" : num(r.could_not_assess)}</td>
  </tr>`;
    }).join("")}</tbody>
  </table></div>
  <p class="card-note">“Could not assess” is part of the denominator, so it's
   counted here. A run that flagged six of 471 assessed companies and skipped
   eighteen more is a different result from one that assessed all 489. A run
   that flagged nothing doesn't mean nothing went wrong. This detector misses
   roughly seven deteriorations in ten.</p>`;
  const body = head + `<div class="kpis">${kpis}</div>` +
    (chart ? card({ title: "Filings from watched companies, per run",
      sub: "Oldest on the left. Hover a bar for its date.",
      aside: legend([["brand", "completed run"], ["flag", "failed run"]]),
      body: `<div class="chart-wrap">${chart}</div>` }) : "") +
    card({ title: "Runs", flush: true, body: table });
  return layout({
    title: "Activity", current: "/activity",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------ the test

// The page the banner links to. Every number and every sentence about the
// six checks comes from verdict.json, which ledgerline/api/views.py writes
// from the frozen record; the page only lays it out.
export function verdict(data) {
  const v = data.validation || {};
  const checks = list(data.checks);
  const failed = v.verdict === "KILL";
  const cases = data.cases || {};
  const per = data.per_filer || {};
  const base = data.baseline || {};
  const fp = data.fingerprints || {};
  const reg = data.registry || {};

  const head = pageHead({
    eyebrow: "The detector's own test",
    title: "How it was tested, and how it did",
    sub: "The rules for passing were written down, hashed and committed before " +
      "anything was scored. Then the detector was scored once, on a sealed " +
      "half of the data it had never seen. Any single failed check was a fail.",
    aside: pill(failed ? "failed" : esc(v.status), failed ? "flag" : "quiet") +
      `<span class="tag">${icon("calendar")}Scored ${esc(data.scored_on || v.scored_on)}</span>`,
  });

  const hero = `<section class="hero${failed ? " fail" : ""}">
  <div class="hero-num">${num(data.n_failed)}<small> of ${checks.length}</small></div>
  <div><h3>checks failed.</h3><p>${esc(v.statement)}</p></div>
</section>`;

  const rows = checks.map((c) => {
    const m = c.meter;
    const bar = m ? meter(m, { failed: !c.passed,
      limitLabel: m.kind === "ceiling" ? "at most" : "at least" }) : "";
    return `<div class="check${c.passed ? " pass" : " fail"}">
    <div class="check-icon">${icon(c.passed ? "check" : "x")}</div>
    <div class="check-main"><div class="check-name">${esc(c.name)}</div>
      <div class="check-explain">${esc(c.explain)}</div></div>
    <div class="check-result"><div class="check-value">${esc(c.result)}</div>
      <div class="check-req">needed ${esc(c.required)}</div></div>
    <div class="check-meter">${bar}</div>
    <div class="check-verdict">${pill(c.passed ? "passed" : "failed",
      c.passed ? "pass" : "flag")}</div></div>`;
  }).join("");
  const scorecard = card({ title: "The six checks",
    sub: "Written down before the test was run, in the order the write-up lists them.",
    flush: true, cls: "checks", body: rows });

  const perCard = card({ title: "The number that wasn't graded",
    body: `<div class="bignum${failed ? " flag" : ""}">${pct(per.value)}</div>
    <p class="lead">of the companies that never deteriorated were flagged at least
      once across their history${per.control_filer_quarters
      ? ` (${num(per.control_filer_quarters)} quiet company-quarters)` : ""}.</p>
    ${meter({ value: per.value, limit: null, max: 1 }, { failed })}
    <p class="muted small">The rule only asked for this number to be reported.
      A reader meets false alarms one company at a time, so a future test
      should grade it.</p>` });

  const casesCard = card({ title: "The cases it was scored on",
    body: `<div class="duo">
      <div><div class="bignum">${num(cases.positives)}</div><p class="muted small">companies
        that deteriorated</p></div>
      <div><div class="bignum">${num(cases.controls)}</div><p class="muted small">companies
        that didn't</p></div></div>
    <p class="small">${num(cases.censored)} of the ${num(cases.positives)} were
      flagged on the first date they could be assessed, so their warning time
      couldn't be measured. They're left out of both scores, which leaves
      ${num(cases.assessable_positives)}.</p>` });

  const eras = card({ title: "Market eras in the test",
    sub: "A detector that only works in a falling market is measuring the market.",
    body: `<div class="eras">${list(data.regimes).map((r) => `<div class="era${r.caught ? "" : " missed"}">
      <div class="era-name">${icon(r.caught ? "check" : "x")}${esc(r.name)}</div>
      <p>${esc(r.detail)}</p>
      <div class="era-state">${r.caught ? "caught a case ahead of time" : "caught nothing ahead of time"}</div></div>`).join("")}</div>` });

  const rule = card({ title: "The two-line rule it had to beat",
    body: `<blockquote class="rule">${esc(base.rule)}</blockquote>
    <div class="vs">
      <div><span class="muted small">The rule's false alarms</span>
        <b>${pct(base.fpr, 2)}</b></div>
      <div><span class="muted small">The detector's</span>
        <b class="${failed ? "flag" : ""}">${pct((v.measured || {}).fpr_per_control_quarter, 2)}</b></div>
    </div>
    <p class="muted small">Per quiet company-quarter${base.control_filer_quarters
      ? `, over ${num(base.control_filer_quarters)} of them` : ""}. Lower is better.</p>` });

  const check = card({ title: "Check it yourself",
    sub: "Anyone with this repository can re-derive every number on this page.",
    body: `<div class="cmd"><span class="prompt">$</span>ledgerline reproduce</div>
    <p class="small">Re-runs the exact code that scored the test, in a throwaway
      copy of the repository, and compares every published number to the last
      digit. Any difference fails and names the number that moved.</p>
    <dl class="facts">
      <div><dt>Decision rule, SHA-256</dt><dd class="hash">${esc(fp.decision_rule || "—")}</dd></div>
      <div><dt>Test split, SHA-256</dt><dd class="hash">${esc(fp.split || "—")}</dd></div>
      <div><dt>Full write-up</dt><dd><code>${esc(data.writeup || "reports/PHASE0.md")}</code></dd></div>
    </dl>` });

  let next;
  if (reg.error) {
    next = `<section class="callout caution">${icon("alert")}<p>The hypothesis ` +
      `registry can't be read: ${esc(reg.error)}</p></section>`;
  } else {
    const hyps = list(reg.hypotheses).map((h) => `<tr>
      <td class="tick"><span class="hid">${esc(h.id)}</span></td>
      <td><b>${esc(h.name || h.id)}</b><div class="cell-sub">${esc(h.description || "")}</div></td>
      <td>${pill(esc(h.status || "—"), h.status === "scored" ? "quiet" : "brand")}</td>
      <td>${h.verdict === "KILL" ? pill("failed", "flag") : h.verdict
        ? pill(esc(h.verdict), "quiet") : '<span class="muted">not scored yet</span>'}</td>
      <td class="acc">${esc(h.commit || "")}</td></tr>`).join("");
    const sets = list(reg.reserved).map((s) => `<div class="reserved">
      <div class="reserved-head"><span class="hid">${esc(s.name)}</span>
        <span>Reserved test set</span>
        ${s.spent ? pill("used", "flag") : pill("not used yet", "quiet")}</div>
      <div class="reserved-grid">
        ${kpi({ label: "Companies", value: num(s.n_companies) })}
        ${kpi({ label: "Checkpoints", value: num(s.n_checkpoints),
          sub: `${esc(s.first_checkpoint)} to ${esc(s.last_checkpoint)}` })}
        ${kpi({ label: "Registration closes", value: esc(s.registration_closes),
          tone: "warn", sub: "Register a new hypothesis before this date" })}
        ${kpi({ label: "First outcomes", value: esc(s.earliest_scoreable),
          sub: "Nothing can be scored before then" })}
      </div></div>`).join("");
    const alpha = reg.alpha
      ? `<p class="small">Chance of a false pass allowed across every hypothesis
        tested on these sets: ${pct(reg.alpha.budget)} in total, ${pct(reg.alpha.spent)}
        used so far. Each registration draws on it, so testing more ideas
        can't raise the overall chance of a lucky pass past that.</p>` : "";
    next = `<div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Hypothesis</th><th>Status</th><th>Result</th><th>Commit</th></tr></thead>
      <tbody>${hyps}</tbody></table></div><div class="card-body">${sets}${alpha}
      <p class="small">Draft one with <code>ledgerline hypothesis new</code>, commit it,
        then <code>ledgerline hypothesis register</code>.</p></div>`;
  }
  const nextCard = card({ title: "What can be tested next",
    sub: "A new idea gets the same treatment: registered before its data exists, " +
      "scored once, compared with this one on identical terms.",
    flush: !reg.error, body: next });

  const body = head + hero + scorecard +
    `<div class="grid-2">${perCard}${casesCard}</div>` + eras +
    `<div class="grid-2">${rule}${check}</div>` + nextCard;
  return layout({
    title: "The test", current: "/verdict",
    validation: v, body, generated: data.generated,
  });
}
