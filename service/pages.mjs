// Every page this service serves, as HTML strings. The one import is
// ./svg.mjs, the icons and charts. These are pure functions from published
// JSON to markup, which is what lets a test hold one against the other.
//
// The verdict banner is rendered here, on the server, as the first thing in
// the body of every page. The page this replaced painted it from JavaScript
// after a fetch, so with scripting off it never appeared while the scores
// did. tests/unit/test_web_pages.py pins the ordering.
//
// The pages compute nothing. Plain names, reasons, the wording of the test's
// checks: all of it arrives written from ledgerline/api/views.py. What's here
// is layout, escaping, formatting, and scaling published numbers to pixels.

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

// The concern score at which a company with two measures out of line is
// flagged. Every chart and note here uses the same line.
const FLAG_LINE = 45;

// Plain names for the measure codes /digest carries. Only the overview needs
// them; every other page reads a file where the Python wrote the plain name.
// Mirrors ledgerline/render.py PLAIN.
const PLAIN = {
  CASH_CONVERSION_GAP: "cash-vs-sales", ACCRUAL_RATIO: "paper-vs-cash profit",
  RECEIVABLES_VS_REVENUE: "unpaid-bills", INVENTORY_VS_REVENUE: "stockpile",
  DSO: "collection-days", DIO: "shelf-days",
  DEFERRED_VS_REVENUE_GAP: "prepaid-orders", REVENUE_ACCEL: "growth-brake",
  GROSS_MARGIN: "product-margin", OP_MARGIN: "operating-margin",
  OCF_TO_REVENUE: "cash-per-sale", NET_DEBT_TO_TTM_OCF: "debt-vs-cash",
  DILUTION_YOY: "share-creep",
};

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

// "2025-11-15" reads as "Nov 15, 2025".
function date(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ""));
  if (!m) return iso ? esc(iso) : "—";
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

function bytes(n) {
  if (!n) return "0 KB";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
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

function stats(items) {
  return '<div class="stats">' + items.filter(Boolean).map((s) =>
    `<div class="stat"><div class="stat-label">${s.label}</div>` +
    `<div class="stat-value${s.tone ? ` ${s.tone}` : ""}">${s.value}</div>` +
    `${s.sub ? `<div class="stat-sub">${s.sub}</div>` : ""}</div>`).join("") +
    "</div>";
}

function pill(text, tone = "") {
  return `<span class="pill${tone ? ` ${tone}` : ""}">${text}</span>`;
}

function legend(items) {
  return '<div class="legend">' + items.map(([cls, label]) =>
    `<span><i class="${cls}"></i>${label}</span>`).join("") + "</div>";
}

function scoreCell(score, flagged) {
  return '<span class="score">' +
    `<span class="score-num${flagged ? " danger" : ""}">${esc(score)}</span>` +
    `<span class="sbar" title="${esc(score)} of 100; the line is ${FLAG_LINE}">` +
    `<span class="sbar-fill${flagged ? " danger" : ""}" ` +
    `style="width:${clamp100(score).toFixed(1)}%"></span></span></span>`;
}

// One tick per measure: "danger" out of line, "on" measured, "" no data.
function ticks(states, label) {
  return `<span class="ticks" role="img" aria-label="${esc(label)}" ` +
    `title="${esc(label)}">${states.map((s) =>
      `<i${s ? ` class="${s}"` : ""}></i>`).join("")}</span>`;
}

function meter(value, max, { mark = null, danger = false } = {}) {
  const at = (v) => Math.max(0, Math.min(100, (Number(v) / Number(max)) * 100));
  return `<div class="meter${danger ? " danger" : ""}">` +
    `<span class="meter-fill" style="width:${at(value).toFixed(1)}%"></span>` +
    (mark === null || mark === undefined ? ""
      : `<span class="meter-mark" style="left:${at(mark).toFixed(1)}%"></span>`) +
    "</div>";
}

// ------------------------------------------------------------- the banner

// The verdict, first, on every page. Its numbers come from the validation
// block, which ledgerline/api/contract.py builds from the frozen record; the
// full sentence is on /verdict. The status travels as an attribute so
// programs that check for it still can.
export function banner(validation) {
  if (!validation || !validation.statement) {
    return '<div class="banner" role="note"><b>Verdict unavailable.</b>' +
      "<span>The record of the detector's test is missing, so nothing here " +
      "should be read as working. Restore <code>ledgerline/data/phase0.json" +
      "</code> and run <code>ledgerline publish</code>.</span></div>";
  }
  const m = validation.measured || {};
  const words = validation.verdict === "KILL" && m.positive_hit_rate !== undefined
    ? `<b>Failed its own test.</b><span>It flagged ${pct(m.positive_hit_rate)} ` +
      "of the companies that went on to deteriorate. It needed " +
      `${pct(m.positive_hit_rate_floor)}.</span>`
    : `<b>${esc(validation.status)}.</b><span>${esc(validation.statement)}</span>`;
  return `<div class="banner" role="note" data-status="${esc(validation.status)}">` +
    `${words}<a href="/verdict">Details</a></div>`;
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
  <form class="search" method="get" action="/company" role="search">
    ${icon("search")}<input type="text" name="ticker" placeholder="Find a ticker"
      aria-label="Ticker" autocomplete="off" spellcheck="false">
  </form>
  ${nav(current)}
  <p class="side-foot">Runs on this machine only.${generated ? `<br>Updated ${date(generated)}.` : ""}</p>
</aside>
<main class="main">
  <div class="content">
${body}
  </div>
  <footer class="foot">Raw data: <a href="/digest">digest</a> ·
    <a href="/signals">signals</a> · <a href="/validation">validation</a></footer>
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

// Where the overview's numbers came from. A replay over the practice half
// used to render as "Latest run" with nothing saying it wasn't live.
const REPLAY_SPLIT = {
  tuning: " on the practice half, the companies used to tune the thresholds",
  holdout: " on the sealed test half",
};
const LIVE_SOURCES = ["scan", "score", "emit"];

export function provenance(run) {
  const source = run.source || "unrecorded";
  if (source === "replay") {
    return `A replay${REPLAY_SPLIT[run.split] || ""}. Not a live result.`;
  }
  if (LIVE_SOURCES.includes(source)) {
    return `A live run on ${date(run.run_date)}.`;
  }
  return `Recorded as “${esc(source)}”${run.split ? `, ${esc(run.split)} half` : ""}, ` +
    `${date(run.run_date)}.`;
}

function testSummary(validation) {
  const m = (validation || {}).measured;
  if (!m) return "";
  const failed = validation.verdict === "KILL";
  const row = (label, value, bar, sub) =>
    `<div class="check-row"><div class="check-row-top"><span>${label}</span>` +
    `<b>${value}</b></div>${bar}<span class="sub">${sub}</span></div>`;
  return section({ title: "How it did on its test",
    body: '<div class="check-list">' +
      row("Deteriorations caught", pct(m.positive_hit_rate),
        meter(m.positive_hit_rate, 1, { mark: m.positive_hit_rate_floor, danger: failed }),
        `Needed ${pct(m.positive_hit_rate_floor)}`) +
      row("False alarms per quarter", pct(m.fpr_per_control_quarter, 2),
        meter(m.fpr_per_control_quarter, 0.06, { mark: m.naive_baseline_fpr, danger: failed }),
        `A two-line rule managed ${pct(m.naive_baseline_fpr, 2)}`) +
      row("Healthy companies flagged", pct(m.fpr_per_filer),
        meter(m.fpr_per_filer, 1, { danger: failed }), "At least once") +
      '</div><p class="note"><a href="/verdict">All six checks</a></p>' });
}

export function overview(digest, extra = {}) {
  const run = digest.run || {};
  const expected = Number(digest.expected_false_positives_if_nothing_wrong ?? 0);
  const fires = list(digest.fires);
  const wl = extra.watchlist;
  const last = extra.lastRun;

  const strip = stats([
    { label: "Assessed", value: num(run.scoreable),
      sub: `${num(run.unscoreable)} couldn't be` },
    { label: "Flagged", value: num(run.gated_in), tone: run.gated_in ? "danger" : "",
      sub: `About ${Math.round(expected)} by chance alone` },
    wl && { label: "Watched", value: num(wl.n_companies),
      sub: `${num(wl.n_assessable)} can be assessed` },
    last && { label: "Last scan",
      value: esc(monthDay(String(last.started_at || "").slice(0, 10))),
      sub: plural(last.universe_hits, "new filing", "new filings") },
  ]);

  const flagged = fires.length === 0
    ? '<div class="panel"><p class="panel-empty">Nothing was flagged in this run.</p></div>'
    : `<div class="panel"><div class="table-wrap"><table>
      <thead><tr><th>Company</th><th>Score</th><th>Out of line</th></tr></thead>
      <tbody>${fires.map((f) => `<tr>
        <td class="co"><a href="/company/${encodeURIComponent(f.ticker)}">${esc(f.ticker)}</a></td>
        <td>${scoreCell(f.score, true)}</td>
        <td><div class="chips">${list(f.flags).map((x) =>
          pill(esc(PLAIN[x] || x))).join("")}</div></td>
      </tr>`).join("")}</tbody></table></div></div>
      <p class="note">A flag means two or more measures broke from the
        company's own history. It isn't proof of a problem.</p>`;

  const body = head("Overview",
    `Latest run ${date(run.run_date)}. ${provenance(run)}`) +
    strip +
    '<div class="section cols">' +
    section({ title: "Flagged", body: flagged }) +
    testSummary(digest.validation) + "</div>";
  return layout({ title: "Overview", current: "/", validation: digest.validation, body });
}

// ----------------------------------------------------------------- watchlist

// Chips worth a reader's attention. Missing measures are already drawn as
// ticks, and "no assessment saved" is already the row's pill.
function notes(items) {
  const keep = list(items).filter((c) =>
    !/measures? unavailable|no assessment saved/.test(c.label || ""));
  if (!keep.length) return "";
  return '<div class="chips">' + keep.map((c) =>
    `<span class="pill${/cannot assess|failed|no filings|never fetched/.test(c.label)
      ? " danger" : ""}" title="${esc(c.detail)}">${esc(c.label)}</span>`).join("") +
    "</div>";
}

// The first clause of a reason, for a table cell; the whole of it is on hover.
function shortReason(reason) {
  const r = String(reason || "No reason recorded.").replace(/^Cannot assess:\s*/, "");
  const cut = r.split(/ -- |; /)[0];
  return cut.charAt(0).toUpperCase() + cut.slice(1);
}

// An unassessed company gets words, never a number.
function lastAssessment(c) {
  const l = c.latest;
  if (!l) return pill("nothing assessed yet", "none");
  if (!l.scoreable) {
    return pill("could not assess", "none") +
      `<span class="sub" title="${esc(l.reason || "")}">${esc(shortReason(l.reason))}</span>`;
  }
  const from = l.source === "replay"
    ? ' · <span title="Replayed on the practice half, the companies used to ' +
      'tune the thresholds">replay</span>'
    : "";
  return `<div class="assess">${scoreCell(l.score, l.flagged)}` +
    `${l.flagged ? pill("flagged", "danger") : ""}</div>` +
    `<span class="sub">Quarter to ${date(l.period)}${from}</span>`;
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

const ASSESSABLE_OPTIONS = [
  ["", "All"],
  ["yes", "Assessable"],
  ["no", "Not assessable"],
  ["unknown", "Unchecked"],
];

// The three states as modifiers of "companies", for the sentence that
// restates a filter which matched nothing.
const ASSESSABLE_PHRASE = {
  yes: "that can be assessed",
  no: "that cannot be assessed",
  unknown: "not checked yet",
};

function filters(data, f) {
  const groupOpts = ['<option value="">All groups</option>'].concat(
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
    <input type="text" name="q" value="${esc(f.q)}" placeholder="Ticker or name"
      aria-label="Ticker or name">
    ${list(data.groups).length ? `<select name="group" aria-label="Group">${groupOpts}</select>` : ""}
    ${f.assessable ? `<input type="hidden" name="assessable" value="${esc(f.assessable)}">` : ""}
    <button type="submit" class="btn quiet">Search</button>
    ${f.q || f.group || f.assessable ? '<a class="clear" href="/watchlist">Clear</a>' : ""}
  </form>
  <nav class="seg" aria-label="Filter by whether a company can be assessed">${states}</nav>
</div>`;
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
  out.push('<a href="/watchlist">Show all</a>');
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

const SCORE_NOTE = "Scores run 0–100 against the company's own history. " +
  "A score of 45 with two measures out of line is a flag. No flag doesn't " +
  "mean the company is fine.";

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

  const strip = stats([
    { label: "Can be assessed", value: num(data.n_assessable) },
    data.n_checked ? { label: "Can't be assessed",
      value: num(data.n_checked - (data.n_assessable || 0)) } : null,
    { label: "Flagged", value: num(data.n_flagged),
      tone: data.n_flagged ? "danger" : "" },
  ]);

  let inner = filters(data, f);
  if (!rows.length) {
    inner += '<div class="panel-empty">' +
      emptyExplanation(data, f).map((p) => `<p>${p}</p>`).join("") + "</div>";
  } else {
    inner += `<div class="table-wrap"><table>
    <thead><tr><th>Company</th><th>Measures</th><th>Latest assessment</th><th>Notes</th></tr></thead>
    <tbody>${shown.map((c) => `<tr>
      <td class="co"><a href="/company/${encodeURIComponent(c.ticker || "")}">${esc(c.ticker || "—")}</a>
        <span>${esc(c.name || "")}${list(c.groups).length ? ` · ${list(c.groups).map(esc).join(", ")}` : ""}</span></td>
      <td class="num">${coverage(c)}</td>
      <td>${lastAssessment(c)}</td>
      <td>${notes(c.quality)}</td>
    </tr>`).join("")}</tbody>
    </table></div>`;
    if (pages > 1) {
      inner += `<div class="pager">
        ${page > 1 ? `<a class="btn quiet" href="/watchlist${query({ ...f, page: page - 1 })}">Previous</a>` : "<span></span>"}
        <span class="muted">Page ${num(page)} of ${num(pages)}</span>
        ${page < pages ? `<a class="btn quiet" href="/watchlist${query({ ...f, page: page + 1 })}">Next</a>` : "<span></span>"}
      </div>`;
    }
  }
  const sentence = assessableSentence(data);
  const shownLine = rows.length === watched ? plural(watched, "company", "companies")
    : `${num(rows.length)} of ${plural(watched, "company", "companies")}`;
  const body = head("Watchlist", shownLine) + strip +
    (sentence ? `<p class="note">${sentence}</p>` : "") +
    `<div class="section"><div class="panel">${inner}</div>
    <p class="note">${SCORE_NOTE}</p></div>`;
  return layout({
    title: "Watchlist", current: "/watchlist",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------- company

/* Distance from the company's own normal, on one axis for every row: from 4
   times its usual wobble the harmless way to 6 times the bad-news way. The
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
  <thead><tr><th>Measure</th><th>Distance from its normal</th><th>Reading</th></tr></thead>
  <tbody>${measures.map((m) => {
    let says;
    if (m.unavailable_reason) {
      says = `<span class="muted" title="${esc(m.unavailable_reason)}">No data</span>`;
    } else if (m.out_of_line) {
      says = `<span class="says-flag">${esc(m.breaks_when)}</span>` +
        (m.floored ? '<span class="sub">This figure barely moves, so treat ' +
          "the multiple as a ceiling.</span>" : "");
    } else {
      says = '<span class="muted">Normal</span>';
    }
    return `<tr class="${m.unavailable_reason ? "na" : ""}">
      <td><span${m.technical ? ` title="${esc(m.technical)}"` : ""}>${esc(m.measure)}</span></td>
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
  <details class="more"><summary>All ${plural(f.length, "filing", "filings")}</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>Form</th><th>Filed</th><th>Period</th><th>Accession</th></tr></thead>
  <tbody>${f.map((r) => `<tr>
    <td>${esc(r.form || "—")}</td><td class="num">${date(r.filed)}</td>
    <td class="num">${date(r.period)}${r.n_periods > 1
      ? ` <span class="muted">(${num(r.n_periods)} quarters)</span>` : ""}</td>
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
  <thead><tr><th>Figure</th><th>Quarter to</th><th class="r">Was</th>
    <th class="r">Now</th><th class="r">Change</th><th>Revised</th></tr></thead>
  <tbody>${r.map((x) => `<tr>
    <td>${esc(x.metric_plain || x.metric)}</td>
    <td class="num">${date(x.end_date)}</td>
    <td class="r num">${reading(x.prior_value)}</td>
    <td class="r num">${reading(x.value)}</td>
    <td class="r num">${x.rel_change === null || x.rel_change === undefined
      ? "—" : `${(x.rel_change * 100).toFixed(1)}%`}</td>
    <td class="num">${date(x.filed)}${x.on_amendment ? " · amendment" : ""}</td>
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
  if (prov.derived_fraction !== null && prov.derived_fraction !== undefined) {
    lines.push(`${(prov.derived_fraction * 100).toFixed(0)}% were worked out by ` +
      "subtracting one year-to-date report from another" +
      (prov.derived_fraction_high ? ", more than any other filer measured." : "."));
  }
  const intro = lines.length ? `<p class="note">${lines.map(esc).join(" ")}</p>` : "";
  if (!measures.length) {
    return '<div class="panel"><p class="panel-empty">No measure broke from its ' +
      `pattern, so there's nothing to trace.</p></div>${intro}`;
  }
  return `<div class="panel"><div class="table-wrap"><table>
    <thead><tr><th>Measure</th><th>Figure</th><th>Period</th><th>Filing</th></tr></thead>
    <tbody>${measures.map((m) => list(m.inputs).map((t, i) =>
      `<tr><td>${i === 0 ? esc(m.measure) : ""}</td>
        <td>${esc(t.figure)}${t.origin === "derived"
          ? ' <span class="muted" title="Worked out from year-to-date reports">(derived)</span>' : ""}</td>
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
  return section({ id: "history", title: "Every assessment",
    body: `<div class="panel"><details class="more first"><summary>Show ${num(h.length)}</summary>
  <div class="table-wrap"><table>
  <thead><tr><th>Data as of</th><th>Quarter to</th><th>Score</th><th>Out of line</th></tr></thead>
  <tbody>${h.map((r) => `<tr><td class="num">${date(r.as_of)}</td>
    <td class="num">${date(r.period)}</td>
    <td>${!r.scoreable ? pill("could not assess", "none")
      : `<div class="assess">${scoreCell(r.score, r.flagged)}${r.flagged
        ? pill("flagged", "danger") : ""}</div>`}</td>
    <td class="muted">${list(r.flags).map(esc).join(", ") || "—"}</td></tr>`).join("")}</tbody>
  </table></div></details></div>` });
}

function latestPanel(page) {
  const l = page.latest;
  const measures = list(page.measures);
  const label = '<div class="stat-label">Latest assessment</div>';
  if (!l) {
    return `<div class="panel panel-body">${label}` +
      `<p class="pill-row">${pill("nothing assessed yet", "none")}</p>` +
      `<p class="note">Save one with <code>ledgerline score ${esc(page.ticker)} ` +
      "--emit</code>.</p></div>";
  }
  const run = l.run || {};
  const facts = [
    ["Quarter to", date(l.period)],
    ["Data as of", date(l.as_of)],
    run.source ? ["Source", run.source === "replay" ? "Replay" : esc(run.source)] : null,
  ].filter(Boolean);
  const factList = `<dl class="facts">${facts.map(([k, v]) =>
    `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  if (!l.scoreable) {
    return `<div class="panel panel-body">${label}` +
      `<p class="pill-row">${pill("could not assess", "none")}</p>` +
      `<p class="note">${esc(l.reason || "No reason recorded.")}</p>${factList}</div>`;
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
    (l.flagged ? pill("flagged", "danger") : pill("not flagged")) + "</div>" +
    '<div class="gauge" role="img" aria-label="Score against the flag line at 45">' +
    `<span class="gauge-fill${l.flagged ? " danger" : ""}" ` +
    `style="width:${clamp100(l.score).toFixed(1)}%"></span>` +
    `<span class="gauge-mark" style="left:${FLAG_LINE}%"><span>${FLAG_LINE}</span></span></div>` +
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
  ${archive ? `<a class="btn quiet" href="${esc(archive)}" rel="noreferrer">${icon("external")}SEC filings</a>` : ""}
</header>
<nav class="tabs" aria-label="Sections">
  <a href="#summary">Summary</a><a href="#measures">Measures</a>
  <a href="#filings">Filings</a><a href="#sources">Sources</a>
</nav>`;

  const chart = history.length >= 2
    ? '<div class="panel panel-body"><div class="section-head">' +
      '<div class="stat-label">Score history</div>' +
      legend([["danger", "flagged"], ["", "not flagged"], ["na", "couldn't assess"],
        ["line", `flag line ${FLAG_LINE}`]]) + "</div>" +
      scoreHistory(history, FLAG_LINE) + "</div>"
    : "";
  const summary = `<div class="summary${chart ? "" : " solo"}" id="summary">` +
    `${latestPanel(page)}${chart}</div>`;

  const body = top + summary +
    section({ id: "measures", title: "Measures",
      sub: "How far each one sits from this company's own normal.",
      aside: measures.length ? legend([["", "normal"], ["danger", "out of line"],
        ["ring", "ceiling"]]) : "",
      body: measuresTable(measures) || '<div class="panel"><p class="panel-empty">' +
        "No readings yet.</p></div>" }) +
    section({ id: "filings", title: "Filings",
      aside: list(page.filings).length >= 2 ? legend([["chart", "annual"],
        ["", "quarterly"], ["na", "other"]]) : "",
      body: filingsSection(page) }) +
    section({ id: "revisions", title: "Revised figures", body: revisionsSection(page) }) +
    section({ id: "sources", title: "Sources", body: sourcesSection(page, page.cik) }) +
    historySection(page);
  return layout({
    title: page.ticker, current: "/company",
    validation: page.validation, body, generated: page.generated,
  });
}

// The Companies tab with no company named: a search box, and the companies
// something was said about.
export function companyIndex(data, note) {
  const flagged = list(data.companies).filter(
    (c) => (c.latest || {}).flagged).slice(0, 25);
  const body = head("Companies", "Open any watched company.") +
    (note ? `<div class="panel panel-body notice">${note}</div>` : "") +
    `<form class="lookup" method="get" action="/company">
  <input type="text" name="ticker" placeholder="Ticker, e.g. FMC" aria-label="Ticker" autocomplete="off">
  <button type="submit" class="btn">Open</button>
</form>` +
    (flagged.length ? section({ title: "Flagged", body: `<div class="panel"><div class="table-wrap"><table>
<thead><tr><th>Company</th><th>Score</th><th>Out of line</th></tr></thead>
<tbody>${flagged.map((c) => `<tr>
  <td class="co"><a href="/company/${encodeURIComponent(c.ticker)}">${esc(c.ticker)}</a>
    <span>${esc(c.name || "")}</span></td>
  <td>${scoreCell(c.latest.score, true)}</td>
  <td><div class="chips">${list(c.latest.flags).map((x) => pill(esc(x))).join("")}</div></td></tr>`).join("")}</tbody>
</table></div></div>` }) : "");
  return layout({
    title: "Companies", current: "/company",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------ activity

export function activity(data) {
  const runs = list(data.runs);
  if (!runs.length) {
    return layout({
      title: "Activity", current: "/activity", validation: data.validation,
      generated: data.generated,
      body: '<div class="empty"><h2>Nothing has run yet</h2>' +
        "<p>Run <code>ledgerline fetch</code>, then <code>ledgerline scan</code>.</p></div>",
    });
  }
  const last = runs[0];
  const strip = stats([
    { label: "Last run", value: esc(monthDay(String(last.started_at || "").slice(0, 10))),
      tone: last.status === "failed" ? "danger" : "", sub: esc(last.status) },
    { label: "Requests to the SEC", value: num(last.requests),
      sub: bytes(last.bytes_fetched) },
    { label: "Filings listed", value: num(last.index_rows),
      sub: `${num(last.universe_hits)} from watched companies` },
    { label: "Revised figures", value: num(last.restatements) },
  ]);
  const chart = runHistory(runs);
  const table = `<div class="panel"><div class="table-wrap"><table>
  <thead><tr><th>Run</th><th>Started</th><th class="r">Took</th><th class="r">Requests</th>
    <th class="r">Filings listed</th><th class="r">From watched</th><th class="r">Revised</th><th>Assessed</th></tr></thead>
  <tbody>${runs.map((r) => {
    const secs = (r.started_at && r.finished_at)
      ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : null;
    return `<tr>
    <td>${esc(r.job)} <span class="muted">#${esc(r.run_id)}</span>${r.status === "failed"
      ? ` ${pill("failed", "danger")}` : ""}${r.error
      ? `<span class="sub">${esc(String(r.error).split("\n")[0])}</span>` : ""}</td>
    <td class="num">${date(r.started_at)}</td>
    <td class="r num">${secs === null ? "—" : `${secs.toFixed(1)}s`}</td>
    <td class="r num">${num(r.requests)}</td>
    <td class="r num">${num(r.index_rows)}</td>
    <td class="r num">${num(r.universe_hits)}</td>
    <td class="r num">${num(r.restatements)}</td>
    <td class="num">${r.assessed === null || r.assessed === undefined
      ? '<span class="muted">—</span>'
      : `${num(r.assessed)}${r.could_not_assess ? ` <span class="muted">(${num(r.could_not_assess)} couldn't)</span>` : ""}`}</td>
  </tr>`;
  }).join("")}</tbody>
  </table></div></div>`;
  const body = head("Activity", "Every scan and fetch, newest first.") + strip +
    (chart ? section({ title: "Filings from watched companies",
      aside: legend([["chart", "run"], ["danger", "failed"]]),
      body: `<div class="panel panel-body">${chart}</div>` }) : "") +
    section({ title: "Runs", body: table +
      '<p class="note">A scan costs one request per day of filings, however ' +
      "many companies you watch.</p>" });
  return layout({
    title: "Activity", current: "/activity",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------ the test

// The page the banner links to. Every number and the wording of every check
// come from verdict.json, which ledgerline/api/views.py writes from the
// frozen record.
export function verdict(data) {
  const v = data.validation || {};
  const checks = list(data.checks);
  const failed = v.verdict === "KILL";
  const cases = data.cases || {};
  const per = data.per_filer || {};
  const base = data.baseline || {};
  const fp = data.fingerprints || {};
  const reg = data.registry || {};

  const hero = '<div class="verdict-hero">' +
    `<span class="big-num">${failed ? "Failed" : esc(v.status)}</span>` +
    `<span>${num(data.n_failed)} of ${checks.length} checks missed</span></div>` +
    '<p class="lead">Treat its flags as prompts to read the filings. No flag ' +
    "doesn't mean a company is fine.</p>";

  const rows = `<div class="panel"><div class="table-wrap"><table>
  <thead><tr><th>Check</th><th class="r">Result</th><th class="r">Needed</th><th class="r">Outcome</th></tr></thead>
  <tbody>${checks.map((c) => `<tr>
    <td>${esc(c.name)}<span class="sub">${esc(c.explain)}</span></td>
    <td class="r num${c.passed ? "" : " fail"}">${esc(c.result)}</td>
    <td class="r num muted">${esc(c.required)}</td>
    <td class="r">${c.passed ? '<span class="pass">Passed</span>' : pill("Failed", "danger")}</td>
  </tr>`).join("")}</tbody></table></div></div>`;

  const numbers = stats([
    { label: "Healthy companies flagged at least once", value: pct(per.value),
      tone: failed ? "danger" : "", sub: "Reported, not graded" },
    { label: "Cases", value: `${num(cases.positives)} / ${num(cases.controls)}`,
      sub: "Deteriorated / didn't" },
    { label: "The two-line rule's false alarms", value: pct(base.fpr, 2),
      sub: esc(base.rule || "") },
  ]);

  const eras = `<div class="eras">${list(data.regimes).map((r) =>
    `<span class="pill${r.caught ? "" : " danger"}" title="${esc(r.detail)}">` +
    `${icon(r.caught ? "check" : "x")}${esc(r.name)}</span>`).join("")}</div>`;

  const repro = `<div class="cmd"><span>$</span>ledgerline reproduce</div>
    <p class="note">Re-runs the exact code that scored the test and checks every
      number above. Write-up: <code>${esc(data.writeup || "reports/PHASE0.md")}</code>.</p>
    <dl class="facts">
      <div><dt>Rules, SHA-256</dt><dd class="hash">${esc(fp.decision_rule || "—")}</dd></div>
      <div><dt>Test data, SHA-256</dt><dd class="hash">${esc(fp.split || "—")}</dd></div>
    </dl>`;

  let next;
  if (reg.error) {
    next = `<div class="panel"><p class="panel-empty">Can't read the registry: ${esc(reg.error)}</p></div>`;
  } else {
    const set = list(reg.reserved)[0];
    next = (set ? stats([
      { label: "Registration closes", value: date(set.registration_closes), tone: "warn",
        sub: `Reserved set ${esc(set.name)}, ${num(set.n_companies)} companies` },
      { label: "First results", value: date(set.earliest_scoreable),
        sub: `${num(set.n_checkpoints)} checkpoints to ${date(set.last_checkpoint)}` },
      reg.alpha ? { label: "Error budget used", value: pct(reg.alpha.spent),
        sub: `of ${pct(reg.alpha.budget)}, shared by every hypothesis` } : null,
    ]) : "") +
    `<div class="panel spaced"><div class="table-wrap"><table>
      <thead><tr><th>Hypothesis</th><th>Status</th><th>Result</th><th>Commit</th></tr></thead>
      <tbody>${list(reg.hypotheses).map((h) => `<tr>
        <td><span class="mono">${esc(h.id)}</span> ${esc(h.name || "")}</td>
        <td>${esc(h.status || "—")}</td>
        <td>${h.verdict === "KILL" ? pill("Failed", "danger")
          : h.verdict ? esc(h.verdict) : '<span class="muted">Not scored</span>'}</td>
        <td class="acc">${esc(h.commit || "")}</td></tr>`).join("")}</tbody>
    </table></div></div>
    <p class="note">New idea: <code>ledgerline hypothesis new</code>, commit it,
      then <code>ledgerline hypothesis register</code>.</p>`;
  }

  const body = head("The test",
    `Scored once on ${date(data.scored_on || v.scored_on)}, on companies it had never seen.`) +
    hero +
    section({ title: "The six checks", sub: "Written down before the test. Missing any one was a fail.",
      body: rows }) +
    section({ title: "Other numbers", body: numbers }) +
    section({ title: "Market eras",
      sub: list(data.regimes).every((r) => r.caught)
        ? "It caught at least one case early in each." : "",
      body: eras }) +
    section({ title: "Check it yourself", body: repro }) +
    section({ title: "What's next", body: next });
  return layout({
    title: "The test", current: "/verdict",
    validation: v, body, generated: data.generated,
  });
}
