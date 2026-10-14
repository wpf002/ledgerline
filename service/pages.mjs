// Every page this service serves, as HTML strings. The one import is
// ./svg.mjs, the icons and charts. These are pure functions from published
// JSON to markup, which is what lets a test hold one against the other.
//
// The accuracy note is rendered here, on the server, as the first thing in
// the body of every page. The page this replaced painted it from JavaScript
// after a fetch, so with scripting off it never appeared while the scores
// did. tests/unit/test_web_pages.py pins the ordering.
//
// The pages compute nothing. Names, reasons, row status, counts, the wording
// of the test's checks: all of it arrives written from
// ledgerline/api/views.py. What's here is layout, escaping, formatting,
// filtering and paging rows, and scaling published numbers to pixels.

import { filingTimeline, icon, logo, runHistory, scoreHistory } from "./svg.mjs";

const NAV = [
  ["/", "Overview", "overview"],
  ["/watchlist", "Watchlist", "list"],
  ["/activity", "Activity", "pulse"],
  ["/verdict", "Accuracy", "target"],
];

// The score at which a company with two measures out of line is flagged.
const FLAG_LINE = 45;

// A published file that has a key holding the wrong type renders as empty
// rather than throwing; `x || []` let a string through to `.map`.
function list(x) {
  return Array.isArray(x) ? x : [];
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// A filing's page on the SEC's own site. Every number here can be walked back
// to the filing it came from.
export function secUrl(cik, accession) {
  const bare = String(accession).replace(/-/g, "");
  const num = String(cik ?? "").replace(/^0+/, "");
  return `https://www.sec.gov/Archives/edgar/data/${num}/${bare}/` +
    `${accession}-index.htm`;
}

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

function pct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return `${Number((Number(v) * 100).toFixed(digits))}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
  "Oct", "Nov", "Dec"];

// "2025-11-15" reads as "Nov 15, 2025". A timestamp is shown on the day it
// fell on this machine's clock: a 9:30 PM scan is stored as the next day UTC.
function date(iso) {
  const s = String(iso ?? "");
  if (s.includes("T")) {
    const d = new Date(s);
    if (!Number.isNaN(d.getTime())) {
      return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    }
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return iso ? esc(iso) : "—";
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

function shortDate(iso) {
  return date(iso).replace(/, \d{4}$/, "");
}

function clock(iso) {
  const d = new Date(String(iso ?? ""));
  if (Number.isNaN(d.getTime())) return "";
  const h = d.getHours();
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`;
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

function head(title, sub = "", aside = "") {
  return `<header class="page-head"><div><h2>${title}</h2>` +
    `${sub ? `<p>${sub}</p>` : ""}</div>${aside}</header>`;
}

function section({ id = "", title, sub = "", aside = "", body }) {
  return `<section class="section"${id ? ` id="${id}"` : ""}>` +
    `<div class="section-head"><div><h3>${title}</h3>` +
    `${sub ? `<p>${sub}</p>` : ""}</div>${aside}</div>${body}</section>`;
}

// A label and a number. Anything more goes in the tooltip.
function stats(items) {
  return '<div class="stats">' + items.filter(Boolean).map((s) =>
    `<div class="stat"${s.title ? ` title="${esc(s.title)}"` : ""}>` +
    `<div class="stat-label">${s.label}</div>` +
    `<div class="stat-value${s.tone ? ` ${s.tone}` : ""}">${s.value}</div></div>`).join("") +
    "</div>";
}

// A result against its goal: the fill is the result, the mark is the goal.
// Red when the goal was missed.
function goalMeter(c) {
  const m = c && c.meter;
  if (!m || !Number(m.max)) return "";
  const at = (v) => clamp100((Number(v) / Number(m.max)) * 100).toFixed(1);
  return `<div class="meter${c.passed ? "" : " danger"}" role="img" ` +
    `aria-label="${esc(c.result)}, needed ${esc(String(c.required).toLowerCase())}">` +
    `<span class="meter-fill" style="width:${at(m.value)}%"></span>` +
    `<span class="meter-mark" style="left:${at(m.limit)}%"></span></div>`;
}

function pill(text, tone = "") {
  return `<span class="pill${tone ? ` ${tone}` : ""}">${text}</span>`;
}

// Measure names as tags. A table row shows the first few and counts the
// rest, so a company with eight out of line doesn't make a row eight lines
// tall; the full list is on hover and on the company's page.
function tags(names, max = 0) {
  const items = list(names);
  if (!items.length) return '<span class="muted">—</span>';
  const shown = max && items.length > max ? items.slice(0, max) : items;
  const rest = items.slice(shown.length);
  return `<div class="chips">${shown.map((x) => pill(esc(x))).join("")}` +
    (rest.length ? `<span class="pill none" title="${esc(rest.join(", "))}">+${rest.length}</span>` : "") +
    "</div>";
}

function legend(items) {
  return '<div class="legend">' + items.map(([cls, label]) =>
    `<span><i class="${cls}"></i>${label}</span>`).join("") + "</div>";
}

function scoreCell(score, flagged) {
  return '<span class="score">' +
    `<span class="score-num${flagged ? " danger" : ""}">${esc(score)}</span>` +
    `<span class="sbar" title="${esc(score)} of 100. A flag starts at ${FLAG_LINE}.">` +
    `<span class="sbar-fill${flagged ? " danger" : ""}" ` +
    `style="width:${clamp100(score).toFixed(1)}%"></span></span></span>`;
}

function coCell(ticker, name) {
  return `<td class="co"><a href="/company/${encodeURIComponent(ticker || "")}">` +
    `${esc(ticker || "—")}</a><span>${esc(name || "")}</span></td>`;
}

// One tick per measure: "danger" out of line, "on" measured, "" no data.
function ticks(states, label) {
  return `<span class="ticks" role="img" aria-label="${esc(label)}" ` +
    `title="${esc(label)}">${states.map((s) =>
      `<i${s ? ` class="${s}"` : ""}></i>`).join("")}</span>`;
}

// ------------------------------------------------------------- the banner

// The accuracy note, first, on every page. Its numbers come from the
// validation block, which ledgerline/api/contract.py builds from the frozen
// test record. It's about how reliable a flag is, so it's worded as that and
// not as the app having failed. The status travels as an attribute for
// programs that check it.
export function banner(validation) {
  if (!validation || !validation.statement) {
    return '<div class="banner" role="note"><b>Accuracy Unknown</b>' +
      "<span>The record of the detector's backtest is missing. Restore " +
      "<code>ledgerline/data/phase0.json</code> and run <code>ledgerline " +
      "publish</code>.</span></div>";
  }
  const m = validation.measured || {};
  const words = validation.verdict === "KILL" && m.positive_hit_rate !== undefined
    ? `<b>Accuracy Note</b><span>In testing, flags caught ${pct(m.positive_hit_rate)} ` +
      "of the companies that later deteriorated. The goal was " +
      `${pct(m.positive_hit_rate_floor)}.</span>`
    : `<b>${esc(validation.status)}</b><span>${esc(validation.statement)}</span>`;
  return `<div class="banner" role="note" data-status="${esc(validation.status)}">` +
    `${words}<a href="/verdict">How It Was Tested</a></div>`;
}

function nav(current) {
  return '<nav class="pages">' + NAV.map(([href, label, ic]) =>
    `<a href="${href}"${href === current ? ' aria-current="page"' : ""}>` +
    `${icon(ic)}<span>${label}</span></a>`).join("") + "</nav>";
}

export function layout({ title, current, validation, body }) {
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
  <form class="search" method="get" action="/company" role="search">
    ${icon("search")}<input type="text" name="ticker" placeholder="Go to Ticker"
      aria-label="Ticker" autocomplete="off" spellcheck="false">
  </form>
  ${nav(current)}
</aside>
<main class="main">
  <div class="content">
${body}
  </div>
</main>
</div>
</body>
</html>
`;
}

// Every "nothing to show here": what happened, and the command that fixes it.
export function message({ title, current, validation, heading, paragraphs }) {
  const body = `<div class="empty"><h2>${esc(heading)}</h2>` +
    paragraphs.map((p) => `<p>${p}</p>`).join("") + "</div>";
  return layout({ title, current, validation, body });
}

// ------------------------------------------------------------------ overview

// Rows per table on the front page.
const OVERVIEW_ROWS = 10;

// How far to trust a flag: the test's results against its goals.
function trackRecord(track) {
  const rows = list(track).filter((c) => c && c.meter);
  if (!rows.length) return "";
  return section({ title: "Track Record",
    aside: '<a class="more-link" href="/verdict">How It Was Tested</a>',
    body: '<div class="goals">' + rows.map((c) =>
      `<div class="goal" title="${esc(c.explain)}">` +
      `<div class="stat-label">${esc(c.name)}</div>` +
      `<div class="goal-row"><span class="goal-value${c.passed ? "" : " danger"}">` +
      `${esc(c.result)}</span><span class="muted">${esc(c.required)}</span></div>` +
      `${goalMeter(c)}</div>`).join("") + "</div>" });
}

// Whether the page is showing current readings or backtest replays. A replay
// once rendered as the latest run with nothing saying otherwise.
export function freshness(assessed) {
  const a = assessed || {};
  if (!a.live && !a.replay) return "";
  if (!a.live) {
    return `Showing backtest replays through ${date(a.newest)}, not current ` +
      "readings. Run <code>ledgerline assess</code> to assess every company now.";
  }
  return `Assessed ${date(a.newest)}.`;
}

export function overview(data) {
  const counts = data.counts || {};
  const assessed = data.assessed || {};
  const flagged = list(data.flagged);
  const filings = list(data.filings);
  const scan = data.last_scan;

  if (!assessed.live && !assessed.replay) {
    return layout({ title: "Overview", current: "/", validation: data.validation,
      body: head("Overview", `${plural(data.n_companies, "company", "companies")} watched.`) +
        '<div class="empty"><h2>Nothing Assessed Yet</h2>' +
        "<p>Run <code>ledgerline assess</code> to assess every watched company. " +
        "It takes about a minute.</p></div>" });
  }

  const strip = stats([
    { label: "Flagged", value: num(counts.flagged), tone: counts.flagged ? "danger" : "",
      title: `${num(counts.flagged)} of ${num((counts.flagged || 0) + (counts.clear || 0))} assessed` },
    { label: "Not Flagged", value: num(counts.clear) },
    { label: "Can't Assess", value: num(counts.unassessable), title: "Too little data" },
    scan && { label: "Last Check", value: esc(shortDate(scan.started_at)),
      title: `${clock(scan.started_at)}, ${plural(scan.universe_hits, "new filing", "new filings")}` },
  ]);

  // The front page shows the ten highest scores; the watchlist has them all.
  const top = flagged.slice(0, OVERVIEW_ROWS);
  const more = (counts.flagged || 0) > top.length
    ? `<a class="more-link" href="/watchlist?status=flagged&sort=score">All ${num(counts.flagged)} Flagged</a>` : "";
  const flaggedBody = top.length === 0
    ? '<div class="panel"><p class="panel-empty">No company is flagged right now.</p></div>'
    : `<div class="panel"><div class="table-wrap"><table>
      <thead><tr><th>Company</th><th>Score</th><th>Out of Line</th><th>Quarter Ended</th></tr></thead>
      <tbody>${top.map((f) => `<tr>
        ${coCell(f.ticker, f.name)}
        <td><div class="assess">${scoreCell(f.score, true)}${f.source === "replay"
          ? pill("Replay", "none") : ""}</div></td>
        <td>${tags(f.flags, 6)}</td>
        <td class="num">${date(f.period)}</td>
      </tr>`).join("")}</tbody></table></div></div>`;

  const reports = filings.slice(0, OVERVIEW_ROWS);
  const filingsBody = reports.length === 0
    ? '<div class="panel"><p class="panel-empty">No new reports yet.</p></div>'
    : `<div class="panel"><ul class="rows two">${reports.map((f) =>
      `<li><a class="row-main" href="/company/${encodeURIComponent(f.ticker || "")}">` +
      `<b>${esc(f.ticker || "—")}</b><span>${esc(f.name || "")}</span></a>` +
      `<a class="row-meta" href="${esc(secUrl(f.cik, f.accession))}" rel="noreferrer" ` +
      `title="Open this filing on the SEC's site">${pill(esc(f.form))}` +
      `<span class="num">${shortDate(f.filed)}</span></a></li>`).join("")}</ul></div>`;

  const body = head("Overview",
    `${plural(data.n_companies, "company", "companies")} watched. ${freshness(assessed)}`) +
    strip +
    section({ title: "Flagged Companies",
      sub: "Two or more measures broke from the company's own history. Highest score first.",
      aside: more, body: flaggedBody }) +
    section({ title: "Latest Reports", sub: "Quarterly and annual filings.",
      body: filingsBody }) +
    trackRecord(data.track);
  return layout({ title: "Overview", current: "/", validation: data.validation, body });
}

// ----------------------------------------------------------------- watchlist

// The first clause of a reason, for a table cell; the whole of it is on hover.
function shortReason(reason) {
  const r = String(reason || "").replace(/^Cannot assess:\s*/, "");
  const cut = r.split(/ -- |; /)[0];
  return cut.charAt(0).toUpperCase() + cut.slice(1);
}

// An unassessed company gets words, never a number.
function scoreColumn(c) {
  const l = c.latest;
  if (l && l.scoreable) {
    return `<div class="assess">${scoreCell(l.score, l.flagged)}` +
      `${l.flagged ? pill("Flagged", "danger") : ""}</div>`;
  }
  const why = (l && l.reason) || c.assessable_reason || "";
  if ((l && !l.scoreable) || c.assessable === false) {
    return pill("Can't Assess", "none") + (why
      ? `<span class="sub" title="${esc(why)}">${esc(shortReason(why))}</span>` : "");
  }
  return pill("Not Assessed", "none");
}

function coverage(c) {
  const have = Number(c.measures_available);
  const all = Number(c.measures_total);
  if (!Number.isFinite(have) || !Number.isFinite(all) || all <= 0) {
    return '<span class="muted">—</span>';
  }
  const states = Array.from({ length: all }, (_, i) => (i < have ? "on" : ""));
  return ticks(states, `${have} of ${all} measures have data`) +
    `<span class="ticks-label">${num(have)}/${num(all)}</span>`;
}

const STATUS_TABS = [
  ["", "All"],
  ["flagged", "Flagged"],
  ["clear", "Not Flagged"],
  ["unassessable", "Can't Assess"],
  ["pending", "Not Assessed"],
];

// Each filter as a modifier of "companies", for the sentence that restates a
// filter which matched nothing.
const STATUS_PHRASE = {
  flagged: "that are flagged",
  clear: "that aren't flagged",
  unassessable: "that can't be assessed",
  pending: "that haven't been assessed",
};
const ASSESSABLE_PHRASE = {
  yes: "that can be assessed",
  no: "that cannot be assessed",
  unknown: "not checked yet",
};

function toolbar(data, f) {
  const counts = data.counts || {};
  const total = list(data.companies).length;
  const keep = { q: f.q, group: f.group, sort: f.sort };
  const tabs = STATUS_TABS
    .filter(([v]) => v !== "pending" || counts.pending || f.status === "pending")
    .map(([v, label]) => {
      const on = v === (f.status || "") && !f.assessable;
      const n = v ? counts[v] : total;
      return `<a href="/watchlist${query({ ...keep, status: v })}"` +
        `${on ? ' class="on" aria-current="true"' : ""}>${label}` +
        `${n === undefined ? "" : ` <span>${num(n)}</span>`}</a>`;
    }).join("");
  const groups = list(data.groups).filter((g) => g.n > 0 || g.name === f.group);
  const groupRow = groups.length
    ? '<nav class="tabs-row sub-row" aria-label="Groups"><span class="muted">Groups</span>' +
      `<a href="/watchlist${query({ ...keep, status: f.status, group: "" })}"${f.group ? "" : ' class="on"'}>All</a>` +
      groups.map((g) => `<a href="/watchlist${query({ ...keep, status: f.status, group: g.name })}"` +
        `${g.name === f.group ? ' class="on"' : ""}>${esc(g.name)} <span>${num(g.n)}</span></a>`).join("") +
      "</nav>"
    : "";
  return `<div class="toolbar">
  <form class="find" method="get" action="/watchlist" role="search">
    ${icon("search")}<input type="text" name="q" value="${esc(f.q)}"
      placeholder="Search by Name or Ticker" aria-label="Search by name or ticker">
    ${f.status ? `<input type="hidden" name="status" value="${esc(f.status)}">` : ""}
    ${f.group ? `<input type="hidden" name="group" value="${esc(f.group)}">` : ""}
    ${f.sort ? `<input type="hidden" name="sort" value="${esc(f.sort)}">` : ""}
  </form>
  <nav class="tabs-row" aria-label="Filter by status">${tabs}</nav>
</div>${groupRow}`;
}

// Four ways to get an empty table, and four different pieces of news. An
// unknown group is a typo; an empty group is one nobody has filled in.
function emptyExplanation(data, f) {
  const known = list(data.groups).map((g) => g.name);
  if (!list(data.companies).length) {
    return ["No companies are being watched yet.",
      "Add some: <code>ledgerline watch --add AAPL,MSFT,NVDA</code>."];
  }
  if (f.group && !known.some((n) => n.toLowerCase() === f.group.toLowerCase())) {
    return [`There is no group called “${esc(f.group)}”.`,
      known.length
        ? `Your groups: ${known.map(esc).join(", ")}.`
        : "You haven't created any groups yet.",
      "Create one: <code>ledgerline groups --assign semis --tickers NVDA,AMD,INTC</code>."];
  }
  const grp = list(data.groups).find(
    (g) => g.name.toLowerCase() === (f.group || "").toLowerCase());
  if (grp && grp.n === 0) {
    return [`The group “${esc(grp.name)}” exists, and no watched company is ` +
      "in it yet.",
    `Add some: <code>ledgerline groups --assign ${esc(grp.name)} ` +
    "--tickers NVDA,AMD</code>."];
  }
  // One noun phrase, then the count. Joining the filters as predicates of
  // "companies are ..." produced "companies are that cannot be assessed", and
  // "none of your companies cannot be assessed" claims they all can.
  const bits = ["companies"];
  if (f.status) bits.push(STATUS_PHRASE[f.status]);
  if (f.assessable) bits.push(ASSESSABLE_PHRASE[f.assessable]);
  if (f.group) bits.push(`in the group “${esc(f.group)}”`);
  if (f.q) bits.push(`with “${esc(f.q)}” in the ticker or name`);
  const out = [`You asked for ${bits.join(" ")}. None of your ` +
    `${num(list(data.companies).length)} watched companies fit.`];
  if (f.assessable === "yes" || f.assessable === "no") {
    out.push("Run <code>ledgerline check</code> first. Until that has run " +
      "a company is neither.");
  }
  // Only true when assessability is the only filter. Next to a search that
  // matched nothing, it'd be a claim about the whole watchlist.
  if (f.assessable === "unknown" && !f.q && !f.group) {
    out.push("Every watched company has been checked.");
  }
  out.push('<a href="/watchlist">Show All</a>');
  return out;
}

// Said only when nothing can be assessed, and then it says which of two
// things is true: nobody ran the check, or it ran and nothing passed.
function assessableSentence(data) {
  if (data.n_assessable) return "";
  if (data.n_checked) {
    return `${num(data.n_checked)} checked, and none can be assessed yet. ` +
      "Each row says why.";
  }
  return "None of them have been checked yet. Run <code>ledgerline check</code>.";
}

export const PAGE_SIZE = 50;

// Page links: first, last, and the two either side of the current page.
function pager(page, pages, href) {
  if (pages <= 1) return "";
  const want = new Set([1, pages, page - 1, page, page + 1]);
  const shown = [...want].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);
  let out = "";
  let prev = 0;
  for (const p of shown) {
    if (p - prev > 1) out += '<span class="gap">…</span>';
    out += p === page ? `<b aria-current="page">${num(p)}</b>`
      : `<a href="${href(p)}">${num(p)}</a>`;
    prev = p;
  }
  return '<nav class="pages-nav" aria-label="Pages">' +
    (page > 1 ? `<a href="${href(page - 1)}">Previous</a>` : '<span class="off">Previous</span>') +
    out +
    (page < pages ? `<a href="${href(page + 1)}">Next</a>` : '<span class="off">Next</span>') +
    "</nav>";
}

export function watchlist(data, f) {
  const q = (f.q || "").trim().toLowerCase();
  const rows = list(data.companies).filter((c) => {
    if (q && !(String(c.ticker || "").toLowerCase().includes(q) ||
               String(c.name || "").toLowerCase().includes(q))) return false;
    if (f.group && !list(c.groups).some(
      (g) => g.toLowerCase() === f.group.toLowerCase())) return false;
    if (f.status && c.status !== f.status) return false;
    if (f.assessable === "yes" && c.assessable !== true) return false;
    if (f.assessable === "no" && c.assessable !== false) return false;
    if (f.assessable === "unknown" && c.assessable !== null) return false;
    return true;
  });
  // Published order is by ticker. Sorting by score puts the highest first and
  // every company without a score after them.
  if (f.sort === "score") {
    const s = (c) => (c.latest && c.latest.scoreable ? Number(c.latest.score) : -1);
    rows.sort((a, b) => s(b) - s(a));
  }
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(Math.max(1, Number(f.page) || 1), pages);
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const watched = list(data.companies).length;
  const href = (p) => `/watchlist${query({ ...f, page: p > 1 ? p : "" })}`;
  const sortLink = (key, label) => {
    const on = (f.sort || "ticker") === key;
    return `<a class="sort${on ? " on" : ""}" href="/watchlist${query({ ...f, sort: key === "ticker" ? "" : key, page: "" })}">` +
      `${label}${on ? (key === "score" ? " ↓" : " ↑") : ""}</a>`;
  };

  let inner = toolbar(data, f);
  if (!rows.length) {
    inner += '<div class="panel-empty">' +
      emptyExplanation(data, f).map((p) => `<p>${p}</p>`).join("") + "</div>";
  } else {
    const from = (page - 1) * PAGE_SIZE + 1;
    const count = `<span class="muted">${num(from)}–${num(from + shown.length - 1)} of ${num(rows.length)}</span>`;
    inner += `<div class="pager top">${count}${pager(page, pages, href)}</div>
    <div class="table-wrap"><table>
    <thead><tr><th>${sortLink("ticker", "Company")}</th><th title="${SCORE_SCALE}">${sortLink("score", "Score")}</th>
      <th>Out of Line</th><th>Quarter Ended</th><th>Measures With Data</th></tr></thead>
    <tbody>${shown.map((c) => `<tr>
      ${coCell(c.ticker, c.name)}
      <td>${scoreColumn(c)}</td>
      <td>${c.latest && c.latest.scoreable ? tags(c.latest.flags, 3) : ""}</td>
      <td class="num">${c.latest && c.latest.scoreable ? date(c.latest.period) +
        (c.latest.source === "replay" ? '<span class="sub">Backtest replay</span>' : "") : ""}</td>
      <td class="num">${coverage(c)}</td>
    </tr>`).join("")}</tbody>
    </table></div>
    ${pages > 1 ? `<div class="pager">${count}${pager(page, pages, href)}</div>` : ""}`;
  }
  const sentence = assessableSentence(data);
  const body = head("Watchlist", plural(watched, "company", "companies")) +
    (sentence ? `<p class="note lead-note">${sentence}</p>` : "") +
    `<div class="panel">${inner}</div><p class="note">${SCORE_NOTE}</p>`;
  return layout({ title: "Watchlist", current: "/watchlist", validation: data.validation, body });
}

// One line under the table. What the scale is goes on the Score header.
const SCORE_NOTE = "A score of 45 with two measures out of line is a flag. " +
  "No flag doesn't mean the company is fine.";
const SCORE_SCALE = "0 to 100, against the company's own history";

// ------------------------------------------------------------------- company

/* Distance from the company's own normal, on one axis for every row: from 4
   times its usual swing the harmless way to 6 times the bad-news way. The
   shaded zone starts at the trigger, 2. The published z is signed so that
   positive is the bad-news direction, and the row's own out_of_line flag
   picks the colour, so a big move the harmless way is never red. A floored
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
  const off = z > Z_HI ? '<span class="dist-off">›</span>'
    : z < Z_LO ? '<span class="dist-off left">‹</span>' : "";
  return '<div class="dist-row"><div class="dist">' +
    `<span class="dist-zone" style="left:${zpos(Z_TRIGGER)}%"></span>` +
    `<span class="dist-zero" style="left:${zpos(0)}%"></span>` +
    `<span class="dist-dot${cls}" style="left:${zpos(z).toFixed(1)}%"></span>${off}</div>` +
    `<span class="zval${cls}" title="${z < 0 ? "Moved the harmless way" : "Moved the bad-news way"}">` +
    `${z < 0 ? "−" : ""}${Math.abs(z).toFixed(1)}×</span></div>`;
}

function measuresTable(measures) {
  if (!measures.length) return "";
  return `<div class="panel"><div class="table-wrap"><table>
  <thead><tr><th>Measure</th><th>Distance From Normal</th><th>What It Shows</th></tr></thead>
  <tbody>${measures.map((m) => {
    let says;
    if (m.unavailable_reason) {
      says = `<span class="muted" title="${esc(m.unavailable_reason)}">No data</span>`;
    } else if (m.out_of_line) {
      says = `<span class="says-flag">${esc(m.breaks_when)}</span>` +
        (m.floored ? '<span class="sub">This figure rarely moves, so the ' +
          "size of the move is overstated.</span>" : "");
    } else {
      says = '<span class="muted">Normal</span>';
    }
    return `<tr class="${m.unavailable_reason ? "na" : ""}">
      <td>${esc(m.measure)}</td>
      <td>${m.unavailable_reason ? "" : distance(m)}</td>
      <td class="says">${says}</td></tr>`;
  }).join("")}</tbody>
  </table></div></div>`;
}

function filingsSection(page) {
  const f = list(page.filings);
  if (!f.length) {
    return '<div class="panel"><div class="panel-empty"><p>No filings stored ' +
      `yet.</p><p>Run <code>ledgerline fetch --only ${esc(page.ticker)}</code>.` +
      "</p></div></div>";
  }
  const chart = filingTimeline(f);
  return `<div class="panel">${chart ? `<div class="panel-body">${chart}</div>` : ""}
  <details class="more"><summary>All ${plural(f.length, "Filing", "Filings")}</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>Form</th><th>Filed</th><th>Period</th><th>SEC Filing</th></tr></thead>
  <tbody>${f.map((r) => `<tr>
    <td>${esc(r.form || "—")}</td><td class="num">${date(r.filed)}</td>
    <td class="num">${date(r.period)}</td>
    <td class="acc"><a href="${esc(secUrl(page.cik, r.accession))}"
      rel="noreferrer">${esc(r.accession)}</a></td></tr>`).join("")}</tbody>
  </table></div></details></div>`;
}

function reading(v) {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  if (a >= 100) return num(v, 0);
  if (a >= 10) return num(v, 1);
  return Number(v).toFixed(3);
}

function revisionsSection(page) {
  const r = list(page.restatements);
  if (!r.length) {
    return '<div class="panel"><p class="panel-empty">None recorded yet.</p></div>';
  }
  return `<div class="panel"><div class="table-wrap"><table>
  <thead><tr><th>Figure</th><th>Quarter Ended</th><th class="r">Was</th>
    <th class="r">Now</th><th class="r">Change</th><th>Revised</th></tr></thead>
  <tbody>${r.map((x) => `<tr>
    <td>${esc(x.metric_plain || x.metric)}</td>
    <td class="num">${date(x.end_date)}</td>
    <td class="r num">${reading(x.prior_value)}</td>
    <td class="r num">${reading(x.value)}</td>
    <td class="r num">${x.rel_change === null || x.rel_change === undefined
      ? "—" : `${(x.rel_change * 100).toFixed(1)}%`}</td>
    <td class="num">${date(x.filed)}</td>
  </tr>`).join("")}</tbody>
  </table></div></div>`;
}

// Whether anything was assessed decides what this section may say. An
// unassessable company once read "every figure was traced" followed by "no
// measure broke": a clean bill of health with a provenance stamp, for a
// company with zero measures evaluated.
function sourcesSection(page, cik) {
  const prov = page.provenance || {};
  const measures = list(prov.measures);
  const latest = page.latest;
  if (!latest) {
    return '<div class="panel"><p class="panel-empty">Nothing assessed, so ' +
      "nothing to trace.</p></div>";
  }
  if (!latest.scoreable && !measures.length) {
    return '<div class="panel"><p class="panel-empty">Not assessed, so no ' +
      "measure was evaluated and nothing was traced.</p></div>";
  }
  const lines = [];
  if (prov.label === "TRACED") lines.push("Every figure is traced to its filing.");
  else if (prov.label === "PARTIAL") lines.push("Some figures couldn't be traced to a filing.");
  else if (prov.label) lines.push("These figures couldn't be traced, so no score was published.");
  const intro = lines.length ? `<p class="note">${lines.map(esc).join(" ")}</p>` : "";
  if (!measures.length) {
    return '<div class="panel"><p class="panel-empty">No measure broke from its ' +
      `pattern, so there's nothing to trace.</p></div>${intro}`;
  }
  return `<div class="panel"><div class="table-wrap"><table>
    <thead><tr><th>Measure</th><th>Figure</th><th>Period</th><th>SEC Filing</th></tr></thead>
    <tbody>${measures.map((m) => list(m.inputs).map((t, i) =>
      `<tr><td>${i === 0 ? esc(m.measure) : ""}</td>
        <td>${esc(t.figure)}</td>
        <td class="num">${date(t.period)}</td>
        <td class="acc">${list(t.sources).map((a) =>
          `<a href="${esc(secUrl(cik, a))}" rel="noreferrer">${esc(a)}</a>`)
        .join("<br>") || "—"}</td></tr>`).join(""))
      .join("")}</tbody>
    </table></div></div>${intro}`;
}

function historySection(page) {
  const h = list(page.history);
  if (h.length < 2) return "";
  return section({ id: "history", title: "All Assessments",
    body: `<div class="panel"><details class="more first"><summary>Show ${num(h.length)}</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>Data Through</th><th>Quarter Ended</th><th>Score</th><th>Out of Line</th></tr></thead>
  <tbody>${h.map((r) => `<tr><td class="num">${date(r.as_of)}</td>
    <td class="num">${date(r.period)}</td>
    <td>${!r.scoreable ? pill("Can't Assess", "none")
      : `<div class="assess">${scoreCell(r.score, r.flagged)}${r.flagged
        ? pill("Flagged", "danger") : ""}</div>`}</td>
    <td>${tags(r.flags)}</td></tr>`).join("")}</tbody>
  </table></div></details></div>` });
}

function latestPanel(page) {
  const l = page.latest;
  const measures = list(page.measures);
  const label = '<div class="stat-label">Latest Assessment</div>';
  if (!l) {
    return `<div class="panel panel-body">${label}` +
      `<p class="pill-row">${pill("Not Assessed", "none")}</p>` +
      '<p class="note">Run <code>ledgerline assess</code>.</p></div>';
  }
  const run = l.run || {};
  const facts = [
    ["Quarter Ended", date(l.period)],
    ["Data Through", date(l.as_of)],
    run.source === "replay" ? ["Type", "Backtest Replay"] : null,
  ].filter(Boolean);
  const factList = `<dl class="facts">${facts.map(([k, v]) =>
    `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  if (!l.scoreable) {
    return `<div class="panel panel-body">${label}` +
      `<p class="pill-row">${pill("Can't Assess", "none")}</p>` +
      `<p class="note">${esc(shortReason(l.reason) || "No reason recorded.")}</p>` +
      `<dl class="facts"><div><dt>Data Through</dt><dd>${date(l.as_of)}</dd></div></dl></div>`;
  }
  let strip = "";
  if (measures.length) {
    const states = measures.map((m) =>
      (m.unavailable_reason ? "" : m.out_of_line ? "danger" : "on"));
    const out = states.filter((s) => s === "danger").length;
    const none = states.filter((s) => s === "").length;
    strip = '<div class="ticks-row">' +
      ticks(states, `${out} out of line, ${none} with no data`) +
      `<span class="ticks-label">${out} of ${states.length} out of line</span></div>`;
  }
  return `<div class="panel panel-body">${label}<div class="big">` +
    `<span class="big-num${l.flagged ? " danger" : ""}">${esc(l.score)}</span>` +
    '<span class="muted">/ 100</span>' +
    (l.flagged ? pill("Flagged", "danger") : pill("Not Flagged")) + "</div>" +
    '<div class="gauge" role="img" aria-label="Score against the flag line at 45">' +
    `<span class="gauge-fill${l.flagged ? " danger" : ""}" ` +
    `style="width:${clamp100(l.score).toFixed(1)}%"></span>` +
    `<span class="gauge-mark" style="left:${FLAG_LINE}%"><span>Flag at ${FLAG_LINE}</span></span></div>` +
    strip + factList + "</div>";
}

export function company(page) {
  const history = list(page.history);
  const measures = list(page.measures);
  const archive = companyArchive(page.cik);
  const meta = [`CIK ${esc(String(page.cik ?? "").replace(/^0+/, ""))}`,
    page.sic ? `SIC ${esc(page.sic)}` : "", ...list(page.groups).map(esc)]
    .filter(Boolean).join(" · ");

  const top = `<header class="co-head">
  <div class="co-id"><span class="badge">${esc(page.ticker)}</span>
    <div><h2>${esc(page.name || page.ticker)}</h2><p>${meta}</p></div></div>
  ${archive ? `<a class="btn quiet" href="${esc(archive)}" rel="noreferrer">${icon("external")}SEC Filings</a>` : ""}
</header>
<nav class="tabs" aria-label="Sections">
  <a href="#summary">Summary</a><a href="#measures">Measures</a>
  <a href="#filings">Filings</a><a href="#sources">Sources</a>
</nav>`;

  const chart = history.length >= 2
    ? '<div class="panel panel-body"><div class="section-head">' +
      '<div class="stat-label">Score History</div>' +
      legend([["danger", "Flagged"], ["", "Not Flagged"], ["na", "Can't Assess"],
        ["line", `Flag at ${FLAG_LINE}`]]) + "</div>" +
      scoreHistory(history, FLAG_LINE) + "</div>"
    : "";
  const summary = `<div class="summary${chart ? "" : " solo"}" id="summary">` +
    `${latestPanel(page)}${chart}</div>`;

  const body = top + summary +
    section({ id: "measures", title: "Measures",
      sub: "How far each one sits from this company's own normal.",
      aside: measures.length ? legend([["", "Normal"], ["danger", "Out of Line"],
        ["ring", "Rarely Moves"]]) : "",
      body: measuresTable(measures) || '<div class="panel"><p class="panel-empty">' +
        "No readings yet.</p></div>" }) +
    section({ id: "filings", title: "Filings",
      aside: list(page.filings).length >= 2 ? legend([["chart", "Annual"],
        ["", "Quarterly"], ["na", "Other"]]) : "",
      body: filingsSection(page) }) +
    section({ id: "revisions", title: "Revised Figures",
      sub: "Past numbers the company later changed.", body: revisionsSection(page) }) +
    section({ id: "sources", title: "Sources",
      sub: "The filing behind each figure that's out of line.",
      body: sourcesSection(page, page.cik) }) +
    historySection(page);
  return layout({ title: page.ticker, current: "/watchlist", validation: page.validation, body });
}

// ------------------------------------------------------------------ activity

const JOB_NAMES = { scan: "Filing Check", assess: "Full Assessment", backfill: "Download" };
export const RUNS_PAGE_SIZE = 10;
const DAY_NAMES = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays",
  "Fridays", "Saturdays"];

// The next time launchd starts the nightly check, from the schedule the run
// log publishes. launchd counts weekdays the way getDay() does, Sunday 0, and
// also accepts 7 for Sunday.
export function nextCheck(s, now = new Date()) {
  const days = list(s && s.weekdays).map((d) => Number(d) % 7);
  if (!s || !s.installed || !days.length) return null;
  for (let i = 0; i < 8; i++) {
    const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i,
      Number(s.hour) || 0, Number(s.minute) || 0);
    if (at > now && days.includes(at.getDay())) return at;
  }
  return null;
}

function cadence(s) {
  const days = [...new Set(list(s.weekdays).map((d) => Number(d) % 7))].sort();
  const time = clock(new Date(2000, 0, 1, Number(s.hour) || 0, Number(s.minute) || 0).toISOString());
  if (days.length === 7) return `every night at ${time}`;
  if (days.join() === "1,2,3,4,5") return `weeknights at ${time}`;
  return `${days.map((d) => DAY_NAMES[d]).join(", ")} at ${time}`;
}

// "Oct 13, 9:32 PM", with the year only when it isn't this one.
function when(iso) {
  const d = new Date(String(iso ?? ""));
  if (Number.isNaN(d.getTime())) return "—";
  const day = d.getFullYear() === new Date().getFullYear() ? shortDate(iso) : date(iso);
  return `${day}, ${clock(iso)}`;
}

function took(r) {
  if (!r.started_at || !r.finished_at) return "—";
  const secs = (new Date(r.finished_at) - new Date(r.started_at)) / 1000;
  if (Number.isNaN(secs)) return "—";
  return secs < 90 ? `${secs.toFixed(0)}s` : `${(secs / 60).toFixed(0)}m`;
}

export function activity(data, f = {}) {
  const runs = list(data.runs);
  if (!runs.length) {
    return layout({
      title: "Activity", current: "/activity", validation: data.validation,
      body: '<div class="empty"><h2>Nothing Has Run Yet</h2>' +
        "<p>Run <code>ledgerline fetch</code>, then <code>ledgerline scan</code>.</p></div>",
    });
  }
  const sched = data.schedule || {};
  const next = nextCheck(sched);
  const sub = sched.installed
    ? `Checks the SEC for new filings ${cadence(sched)}.`
    : "Nightly checks are off. Run <code>ledgerline schedule install</code>.";

  const scans = runs.filter((r) => r.job === "scan");
  const last = scans[0];
  const failed = last && last.status === "failed";
  const strip = last ? stats([
    { label: "Last Check", value: esc(shortDate(last.started_at)),
      tone: failed ? "danger" : "",
      title: failed ? `Failed at ${clock(last.started_at)}` : clock(last.started_at) },
    next && { label: "Next Check", value: esc(shortDate(next.toISOString())),
      title: clock(next.toISOString()) },
    { label: "New Filings", value: num(last.universe_hits), title: "From watched companies" },
    { label: "Companies Updated", value: num(last.filers_done) },
  ]) : "";
  const chart = runHistory(scans);

  const pages = Math.max(1, Math.ceil(runs.length / RUNS_PAGE_SIZE));
  const page = Math.min(Math.max(1, Number(f.page) || 1), pages);
  const shown = runs.slice((page - 1) * RUNS_PAGE_SIZE, page * RUNS_PAGE_SIZE);
  const from = (page - 1) * RUNS_PAGE_SIZE + 1;
  const href = (p) => `/activity${query({ page: p > 1 ? p : "" })}`;
  const dash = (v) => (v === null || v === undefined ? "—" : num(v));
  const table = `<div class="panel"><div class="table-wrap"><table class="log">
  <thead><tr><th>When</th><th>What Ran</th><th class="r">New Filings</th>
    <th class="r" title="Past numbers that changed">Revised Figures</th>
    <th class="r">Companies Updated</th><th class="r">Assessed</th>
    <th class="r">Flagged</th><th class="r">Took</th></tr></thead>
  <tbody>${shown.map((r) => `<tr>
    <td class="num">${esc(when(r.started_at))}</td>
    <td>${esc(JOB_NAMES[r.job] || r.job)}${r.status === "failed"
      ? ` <span title="${esc(String(r.error || "").split("\n")[0])}">${pill("Failed", "danger")}</span>` : ""}</td>
    <td class="r num">${r.job === "scan" ? num(r.universe_hits) : "—"}</td>
    <td class="r num">${r.job === "assess" ? "—" : num(r.restatements)}</td>
    <td class="r num">${r.job === "assess" ? "—" : num(r.filers_done)}</td>
    <td class="r num">${dash(r.assessed)}</td>
    <td class="r num">${r.assessed === null || r.assessed === undefined ? "—" : num(r.gated_in)}</td>
    <td class="r num muted">${took(r)}</td>
  </tr>`).join("")}</tbody>
  </table></div>
  ${pages > 1 ? `<div class="pager"><span class="muted">${num(from)}–${num(from + shown.length - 1)} of ${num(runs.length)}</span>${pager(page, pages, href)}</div>` : ""}</div>`;

  const body = head("Activity", sub) + strip +
    (chart ? section({ title: "New Filings per Check",
      body: `<div class="panel panel-body">${chart}</div>` }) : "") +
    section({ title: "History", body: table });
  return layout({ title: "Activity", current: "/activity", validation: data.validation, body });
}

// ------------------------------------------------------------------ accuracy

// The page the banner links to. Every number and the wording of every row
// come from verdict.json, which ledgerline/api/views.py writes from the
// frozen test record.
//
// What counts as deteriorated, from ledgerline/label.py: any two of these
// within four quarters.
const DETERIORATED = [
  "Sales growth falls 15 points below its usual pace",
  "Gross margin falls 5 points",
  "Operating cash flow halves or turns negative",
  "A write-down of 5% of assets or more",
  "Results restated",
];

export function verdict(data) {
  const v = data.validation || {};
  const m = v.measured || {};
  const checks = list(data.checks);
  const cases = data.cases || {};
  const fp = data.fingerprints || {};
  const reg = data.registry || {};
  const base = data.baseline || {};
  const set = list(reg.reserved)[0];
  const met = checks.filter((c) => c.passed).length;
  const total = (cases.positives || 0) + (cases.controls || 0);

  const results = `<div class="panel"><div class="table-wrap"><table class="results">
  <thead><tr><th>Goal</th><th class="r">Result</th><th class="r">Needed</th>
    <th class="meter-col"></th>
    <th class="r">Met</th></tr></thead>
  <tbody>${checks.map((c) => `<tr>
    <td><span class="tip" title="${esc(c.explain)}">${esc(c.name)}</span></td>
    <td class="r num result${c.passed ? "" : " danger"}">${esc(c.result)}</td>
    <td class="r num muted">${esc(c.required)}</td>
    <td class="meter-col">${goalMeter(c)}</td>
    <td class="r">${c.passed ? pill("Yes") : pill("No", "danger")}</td>
  </tr>`).join("")}</tbody></table></div></div>`;

  const fact = (label, value, title = "") =>
    `<div${title ? ` title="${esc(title)}"` : ""}><dt>${label}</dt><dd class="num">${value}</dd></div>`;
  const test = `<div class="panel panel-body"><dl class="facts flush">
    ${fact("Later Deteriorated", num(cases.positives),
      cases.censored ? `${num(cases.censored)} were already flagged at their first reading and aren't counted as caught or missed` : "")}
    ${fact("Stayed Healthy", num(cases.controls))}
    ${fact("Healthy Companies Ever Flagged", pct(m.fpr_per_filer),
      "Flagged at least once across every quarter tested")}
    ${fact("Simple Rule's False Alarms", pct(base.fpr, 2), base.rule || "")}
    ${fact("Run Once", date(data.scored_on || v.scored_on))}
  </dl></div>`;

  const meaning = `<div class="panel panel-body"><ul class="marks">${DETERIORATED.map(
    (x) => `<li>${x}</li>`).join("")}</ul></div>`;

  const technical = `<div class="panel"><details class="more first">
    <summary>Technical Details</summary><div class="panel-body">
    <div class="cmd" title="Re-runs the test and checks every number on this page"><span>$</span>ledgerline reproduce</div>
    <dl class="facts">
      <div><dt>Write-Up</dt><dd><code>${esc(data.writeup || "reports/PHASE0.md")}</code></dd></div>
      ${set ? `<div><dt>New Methods Must Register By</dt><dd>${date(set.registration_closes)}</dd></div>
      <div><dt>Their First Results</dt><dd>${date(set.earliest_scoreable)}</dd></div>` : ""}
      <div><dt>Goals, SHA-256</dt><dd class="hash">${esc(fp.decision_rule || "—")}</dd></div>
      <div><dt>Test Data, SHA-256</dt><dd class="hash">${esc(fp.split || "—")}</dd></div>
    </dl></div></details></div>`;

  const body = head("Accuracy", `Tested once on ${num(total)} companies it hadn't seen.`) +
    section({ title: "Results",
      aside: `<span class="muted">${num(met)} of ${num(checks.length)} Goals Met</span>`,
      body: results }) +
    '<div class="section cols half">' +
    section({ title: "The Test", body: test }) +
    section({ title: "Deteriorated Means",
      aside: '<span class="muted">Any Two Within a Year</span>', body: meaning }) +
    "</div>" +
    `<div class="section">${technical}</div>`;
  return layout({ title: "Accuracy", current: "/verdict", validation: v, body });
}
