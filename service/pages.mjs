// Every page this service serves, as HTML strings. Node built-ins only, and
// in fact nothing at all is imported: these are pure functions from published
// JSON to markup, which is what lets a test hold one against the other.
//
// Why the pages are rendered here and not in the browser: the verdict banner.
// The single page this replaced fetched /digest and painted the banner from
// JavaScript, so for one frame -- and forever, with scripting off -- the page
// read "LOADING…" where the failed test belongs. A person who cannot see the
// verdict is a person being shown an unvalidated score as though it were a
// number. Rendering on the server makes the banner the first thing in the
// document body on every route, before the masthead, before the navigation,
// with no execution required to reveal it. tests/unit/test_web_pages.py pins
// that ordering.
//
// Why the pages compute nothing: the same boundary ledgerline/api/__init__
// states. Plain names for measures, the reason a company cannot be assessed,
// the explain text, the quality chips -- all of it arrives already written
// from ledgerline/api/views.py. A second implementation of "can this company
// be assessed" in JavaScript would be a second answer, and the one on screen.
// What follows is layout, escaping, and arithmetic no larger than a
// percentage of two published counts.

const NAV = [
  ["/", "Overview"],
  ["/watchlist", "Watchlist"],
  ["/company", "Company"],
  ["/activity", "Activity"],
];

const TAGLINE = "Reads companies' SEC filings and flags numbers that break " +
  "from that company's own past.";

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

function num(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: digits ?? 0, maximumFractionDigits: digits ?? 0,
  });
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

export function query(params) {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
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
    return '<div class="banner" role="note"><b>VERDICT NOT AVAILABLE</b>' +
      "This page cannot read the record of the detector's own test, so it " +
      "cannot show you the verdict. Nothing here is a claim that the " +
      "detector works. Check that <code>ledgerline/data/phase0.json</code> " +
      "is present, then run <code>ledgerline publish</code>.</div>";
  }
  return '<div class="banner" role="note"><b>' + esc(validation.status) +
    " — tested " + esc(validation.scored_on) + "</b>" +
    esc(validation.statement) +
    ' <a href="/validation">The measured numbers.</a></div>';
}

function nav(current) {
  return '<nav class="pages">' + NAV.map(([href, label]) =>
    `<a href="${href}"${href === current ? ' aria-current="page"' : ""}>` +
    `${label}</a>`).join("") + "</nav>";
}

export function layout({ title, current, validation, body, generated }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} — Ledgerline</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<div class="wrap${current === "/watchlist" || current === "/activity" ? " wide" : ""}">
${banner(validation)}
<header>
  <h1><a href="/">Ledgerline</a></h1>
  <p class="sub">${TAGLINE}</p>
</header>
${nav(current)}
${body}
<footer>
  Local read service. It re-serves what the Python emitted and computes
  nothing${generated ? `; these pages were written by <code>ledgerline publish</code> on ${esc(generated)}` : ""}.
  Raw feeds: <a href="/digest">/digest</a> · <a href="/signals">/signals</a> ·
  <a href="/validation">/validation</a>.
  CLI: <code>ledgerline explain TICKER</code>.
</footer>
</div>
</body>
</html>
`;
}

// One page for every "there is nothing to show here": what happened, and the
// command that changes it. An empty screen and a stack trace are the two
// answers this file is not allowed to give.
export function message({ title, current, validation, heading, paragraphs }) {
  const body = `<h2>${esc(heading)}</h2><div class="empty">` +
    paragraphs.map((p) => `<p>${p}</p>`).join("") + "</div>";
  return layout({ title, current, validation, body });
}

// ------------------------------------------------------------------ overview

export function overview(digest) {
  const run = digest.run || {};
  const stats = [
    ["run", run.run_date ?? "—"],
    ["assessed", num(run.scoreable)],
    ["cannot assess", num(run.unscoreable)],
    ["flagged", num(run.gated_in)],
  ];
  const expected = Number(digest.expected_false_positives_if_nothing_wrong ?? 0);
  const fires = digest.fires || [];
  const body = `
<h2>Latest run</h2>
<dl class="stats">${stats.map(([k, v]) =>
    `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
<div class="expect">At the measured false-alarm rate, about
  ${expected.toFixed(1)} of the ${num(run.scoreable)} assessed companies would
  be expected to be flagged even if nothing were wrong. Read the list below
  with that in mind.</div>

<h2>Flagged</h2>
${fires.length === 0
    ? '<p class="quiet">Nothing flagged in this run. That is not a clean bill ' +
      "of health: this detector caught fewer than three in ten of the " +
      "deteriorations it was built to find.</p>"
    : `<div class="scroll"><table><tr><th>company</th><th>concern score, 0–100</th>
       <th>measures out of line</th></tr>${fires.map((f) =>
      `<tr><td class="tick"><a href="/company/${encodeURIComponent(f.ticker)}">` +
      `${esc(f.ticker)}</a></td><td class="num">${esc(f.score)}</td><td>` +
      `${(f.flags || []).map((x) => esc(PLAIN[x] || x)).join(", ")}</td></tr>`)
      .join("")}</table></div>
     <p class="note">A company is flagged at 45 of 100 with at least two
       measures out of line. The score is only meaningful against this
       company's own past — it is not comparable between companies, and it is
       not a probability of anything.</p>`}

<h2>Look up a company</h2>
<form method="get" action="/company">
  <input type="text" name="ticker" placeholder="e.g. FMC" aria-label="Ticker">
  <button type="submit">Open the company page</button>
</form>
<p class="note">Or browse <a href="/watchlist">every watched company</a>.</p>`;
  return layout({ title: "Overview", current: "/", validation: digest.validation, body });
}

// ----------------------------------------------------------------- watchlist

function chips(list) {
  if (!list || !list.length) return "";
  return '<div class="chips">' + list.map((c) => {
    const warn = /cannot assess|failed|no filings/.test(c.label) ? " warn" : "";
    return `<span class="chip${warn}" title="${esc(c.detail)}">${esc(c.label)}</span>`;
  }).join("") + "</div>";
}

// The one cell where a score can appear, and the rules that follow it here:
// an unassessed company gets words, never a number, and a quiet company is
// never described as clean.
function lastAssessment(c) {
  const l = c.latest;
  if (!l) return '<span class="verdict-none">nothing assessed yet</span>';
  if (!l.scoreable) {
    return '<span class="verdict-none">could not assess</span><br>' +
      `<span class="quiet">${esc(l.reason || "No reason recorded.")}</span>`;
  }
  const head = l.flagged
    ? '<span class="verdict-flagged">FLAGGED</span>'
    : '<span class="verdict-quiet">not flagged</span>';
  return `${head} <span class="num">${esc(l.score)} of 100</span><br>` +
    `<span class="quiet">quarter ending ${esc(l.period)}, from figures filed ` +
    `by ${esc(l.as_of)}${(l.flags || []).length
      ? ` — ${(l.flags).map(esc).join(", ")}` : ""}</span>`;
}

const ASSESSABLE_OPTIONS = [
  ["", "any"],
  ["yes", "can be assessed"],
  ["no", "cannot be assessed"],
  ["unknown", "not checked yet"],
];

// The same three states said as modifiers of "companies", for the sentence
// that restates a filter which matched nothing. The dropdown labels answer
// "which ones?"; these have to survive being read mid-sentence.
const ASSESSABLE_PHRASE = {
  yes: "that can be assessed",
  no: "that cannot be assessed",
  unknown: "not checked yet",
};

function filters(data, f) {
  const groupOpts = ['<option value="">every group</option>'].concat(
    (data.groups || []).map((g) =>
      `<option value="${esc(g.name)}"${g.name === f.group ? " selected" : ""}>` +
      `${esc(g.name)} (${num(g.n)})</option>`)).join("");
  const stateOpts = ASSESSABLE_OPTIONS.map(([v, label]) =>
    `<option value="${v}"${v === f.assessable ? " selected" : ""}>${label}</option>`
  ).join("");
  return `<form class="filters" method="get" action="/watchlist">
  <div class="field"><label for="q">search by ticker or name</label>
    <input type="text" id="q" name="q" value="${esc(f.q)}" placeholder="e.g. FMC"></div>
  <div class="field"><label for="group">group</label>
    <select id="group" name="group">${groupOpts}</select></div>
  <div class="field"><label for="assessable">assessability</label>
    <select id="assessable" name="assessable">${stateOpts}</select></div>
  <button type="submit">Show</button>
  ${f.q || f.group || f.assessable
    ? '<a class="clear" href="/watchlist">clear</a>' : ""}
</form>`;
}

// Why an empty result is never just an empty table: the four ways to get here
// are four different pieces of news, and one blank screen says the wrong one
// three times out of four. This is the same distinction `ledgerline watch
// --group` makes at the terminal -- an unknown group is a typo, an empty
// group is a group nobody has filled in.
function emptyExplanation(data, f) {
  const known = (data.groups || []).map((g) => g.name);
  if (!data.companies.length) {
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
  const grp = (data.groups || []).find(
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
    `${num(data.companies.length)} watched companies fit.`];
  if (f.assessable === "yes" || f.assessable === "no") {
    out.push("Assessability is recorded by <code>ledgerline check</code>; " +
      "until that has run a company is neither — it is not checked yet.");
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

export const PAGE_SIZE = 250;

export function watchlist(data, f) {
  const q = (f.q || "").trim().toLowerCase();
  const rows = (data.companies || []).filter((c) => {
    if (q && !(String(c.ticker || "").toLowerCase().includes(q) ||
               String(c.name || "").toLowerCase().includes(q))) return false;
    if (f.group && !(c.groups || []).some(
      (g) => g.toLowerCase() === f.group.toLowerCase())) return false;
    if (f.assessable === "yes" && c.assessable !== true) return false;
    if (f.assessable === "no" && c.assessable !== false) return false;
    if (f.assessable === "unknown" && c.assessable !== null) return false;
    return true;
  });
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(Math.max(1, Number(f.page) || 1), pages);
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  let body = `<h2>Watched companies</h2>${filters(data, f)}`;
  if (!rows.length) {
    body += '<div class="empty">' +
      emptyExplanation(data, f).map((p) => `<p>${p}</p>`).join("") + "</div>";
  } else {
    const range = rows.length > PAGE_SIZE
      ? `, showing ${num((page - 1) * PAGE_SIZE + 1)}–${
        num((page - 1) * PAGE_SIZE + shown.length)}`
      : "";
    const counts = `<p class="note">${plural(rows.length, "company", "companies")}${
      rows.length === data.companies.length ? " watched" : ` of ${
        num(data.companies.length)} watched`}${range}.
      ${data.n_assessable
        ? `${num(data.n_assessable)} of the ${num(data.n_companies)} watched have been checked and can be assessed; every other row says why not.`
        : "None of them have been checked yet, so nothing here is known to be assessable — <code>ledgerline check</code> records that, quickly once <code>ledgerline fetch</code> has run."}</p>`;
    const table = `<div class="scroll"><table>
    <tr><th>company</th><th>groups</th><th>last saved assessment</th>
        <th>what to know about this row</th></tr>
    ${shown.map((c) => `<tr>
      <td class="tick"><a href="/company/${encodeURIComponent(c.ticker || "")}">${esc(c.ticker || "—")}</a>
        <br><span class="quiet">${esc(c.name || "")}</span></td>
      <td class="quiet">${(c.groups || []).map(esc).join(", ") || "—"}</td>
      <td>${lastAssessment(c)}</td>
      <td>${chips(c.quality) || '<span class="quiet">nothing outstanding</span>'}</td>
    </tr>`).join("")}
    </table></div>`;
    const pager = pages > 1
      ? `<div class="pager">
        ${page > 1 ? `<a href="/watchlist${query({ ...f, page: page - 1 })}">← previous</a>` : ""}
        <span class="quiet">page ${num(page)} of ${num(pages)}</span>
        ${page < pages ? `<a href="/watchlist${query({ ...f, page: page + 1 })}">next →</a>` : ""}
      </div>`
      : "";
    body += counts + table + pager +
      `<p class="note">A score is a reading against this company's own past,
       0–100, flagged at 45 with at least two measures out of line. Not being
       flagged is not a clean bill of health — in its own test this detector
       missed seven deteriorations in ten.</p>`;
  }
  return layout({
    title: "Watchlist", current: "/watchlist",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------- company

function measuresTable(measures) {
  if (!measures || !measures.length) return "";
  return `<div class="scroll"><table>
  <tr><th>measure</th><th>latest reading</th><th>against its own past</th>
      <th>what this row says</th></tr>
  ${measures.map((m) => {
    let past = "—";
    if (m.unavailable_reason) {
      past = '<span class="quiet">not measured</span>';
    } else if (m.z !== null && m.z !== undefined) {
      past = `${Math.abs(m.z).toFixed(1)}× its usual wobble`;
      if (m.baseline_median !== null && m.baseline_median !== undefined) {
        past += `<br><span class="quiet">own median ${reading(m.baseline_median)},` +
          ` spread ${reading(m.baseline_scale)}, over its last ${num(m.baseline_n)}` +
          " readings</span>";
      }
    }
    let says;
    if (m.unavailable_reason) {
      says = `<span class="quiet">Cannot be computed: ` +
        `${esc(m.unavailable_reason)}.</span>`;
    } else if (m.out_of_line) {
      says = `<span class="verdict-flagged">Out of line.</span> ` +
        `${esc(m.breaks_when)}.` + (m.floored
          ? ' <span class="quiet">This figure barely moves, so a minimum ' +
            "wobble was used instead of its own — read the multiple as a " +
            "ceiling, not a measurement.</span>" : "");
    } else {
      says = '<span class="quiet">Within this company\'s own pattern.</span>';
    }
    return `<tr><td>${esc(m.measure)}</td>
      <td class="num">${reading(m.value)}</td><td>${past}</td><td>${says}</td></tr>`;
  }).join("")}
  </table></div>
  <p class="note">“Its usual wobble” is this company's own quarter-to-quarter
   spread, measured over its own recent history — never a comparison with other
   companies. A measure counts as out of line when it moves far enough against
   that spread in the direction that would be bad news; the score adds those
   up, and the company is flagged at 45 of 100 with at least two of them.</p>`;
}

function timelineTable(page) {
  const f = page.filings || [];
  if (!f.length) {
    return '<div class="empty"><p>No filings are stored for this company yet.' +
      "</p><p>Download its filing history: <code>ledgerline fetch --only " +
      `${esc(page.ticker)}</code>.</p></div>`;
  }
  return `<div class="scroll"><table>
  <tr><th>form</th><th>filed</th><th>period it reports</th><th>accession</th></tr>
  ${f.map((r) => `<tr>
    <td>${esc(r.form || "—")}</td><td class="num">${esc(r.filed || "—")}</td>
    <td class="num">${esc(r.period || "—")}${r.n_periods > 1
      ? `<br><span class="quiet">cited by ${num(r.n_periods)} quarters</span>` : ""}</td>
    <td class="acc"><a href="${esc(secUrl(page.cik, r.accession))}"
      rel="noreferrer">${esc(r.accession)}</a></td></tr>`).join("")}
  </table></div>
  <p class="note">${plural(f.length, "filing", "filings")} behind the stored
   figures, newest first${page.filings_truncated
    ? ", truncated to the most recent ones" : ""}. Each accession opens that
   filing on the SEC's own site. A quarter worked out by subtracting one
   year-to-date report from another cites both filings, which is why one
   filing can be cited by more than one quarter.</p>`;
}

function restatementsTable(page) {
  const r = page.restatements || [];
  if (!r.length) {
    return '<p class="quiet">No revised figures recorded for this company. ' +
      "Revisions are noticed when a later filing restates a figure this tool " +
      "already stored, so a company fetched once has nothing to compare " +
      "against yet.</p>";
  }
  return `<div class="scroll"><table>
  <tr><th>figure</th><th>quarter</th><th>first filed</th><th>revised to</th>
      <th>change</th><th>revised on</th></tr>
  ${r.map((x) => `<tr>
    <td>${esc(x.metric_plain || x.metric)}</td>
    <td class="num">${esc(x.end_date)}</td>
    <td class="num">${reading(x.prior_value)}<br>
      <span class="quiet">filed ${esc(x.prior_filed || "—")}</span></td>
    <td class="num">${reading(x.value)}</td>
    <td class="num">${x.rel_change === null || x.rel_change === undefined
      ? "—" : `${(x.rel_change * 100).toFixed(1)}% ${esc(x.direction)}`}
      ${x.material ? "" : '<br><span class="quiet">under 1%</span>'}</td>
    <td class="num">${esc(x.filed || "—")}<br><span class="quiet">${
      esc(x.form || "")}${x.on_amendment ? ", an amendment" : ""}</span></td>
  </tr>`).join("")}
  </table></div>
  <p class="note">Revisions under 1% are shown too. They are 42.5% of every
   revision measured here, and a page that hid them would be reporting a
   revision rate it had already filtered.</p>`;
}

function provenanceTrail(page, cik) {
  const prov = page.provenance || {};
  const measures = prov.measures || [];
  const label = prov.label;
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
      "filing. That is the normal path for cash-flow figures, not a defect" +
      (prov.derived_fraction_high
        ? " — but this company is above every filer measured, which is worth " +
          "a second look." : "."));
  }
  if (!measures.length) {
    return `${head.map((p) => `<p class="note">${esc(p)}</p>`).join("")}
      <p class="quiet">No measure broke from this company's pattern in the
       latest assessment, so there is no per-figure trail to show. The filings
       above are the whole record of what was read.</p>`;
  }
  return head.map((p) => `<p class="note">${esc(p)}</p>`).join("") +
    `<div class="scroll"><table>
    <tr><th>measure</th><th>figure it used</th><th>as filed</th>
        <th>filing it came from</th></tr>
    ${measures.map((m) => (m.inputs || []).map((t, i) =>
      `<tr><td>${i === 0 ? esc(m.measure) : ""}</td>
        <td>${esc(t.figure)}<br><span class="quiet">${esc(t.concept || "")}</span></td>
        <td class="num">${esc(t.period || "—")}<br><span class="quiet">${
        t.origin === "derived"
          ? "worked out from year-to-date reports"
          : "reported directly"}</span></td>
        <td class="acc">${(t.sources || []).map((a) =>
          `<a href="${esc(secUrl(cik, a))}" rel="noreferrer">${esc(a)}</a>`)
        .join("<br>") || "—"}<br><span class="quiet">${esc(t.form || "")}${
        t.filed ? `, filed ${esc(t.filed)}` : ""}</span></td></tr>`).join(""))
      .join("")}
    </table></div>
    <p class="note">Every figure above opens the filing it was read from on the
     SEC's own site. A figure marked as worked out from year-to-date reports
     cites both filings it was differenced from — that is how quarterly cash
     flow exists at all for most filers.</p>`;
}

function historyTable(page) {
  const h = page.history || [];
  if (h.length < 2) return "";
  return `<h2>Earlier assessments</h2><div class="scroll"><table>
  <tr><th>as of</th><th>quarter</th><th>result</th><th>measures out of line</th></tr>
  ${h.map((r) => `<tr><td class="num">${esc(r.as_of)}</td>
    <td class="num">${esc(r.period || "—")}</td>
    <td>${!r.scoreable
      ? `<span class="verdict-none">could not assess</span><br>` +
        `<span class="quiet">${esc(r.reason || "")}</span>`
      : r.flagged
        ? `<span class="verdict-flagged">FLAGGED</span> ` +
          `<span class="num">${esc(r.score)} of 100</span>`
        : `<span class="verdict-quiet">not flagged</span> ` +
          `<span class="num">${esc(r.score)} of 100</span>`}</td>
    <td class="quiet">${(r.flags || []).map(esc).join(", ") || "—"}</td></tr>`).join("")}
  </table></div>
  <p class="note">Saved assessments, newest first. Each one was made from the
   figures that had been filed by its own date — none of them can see a filing
   that came later.</p>`;
}

export function company(page) {
  const meta = [
    ["ticker", page.ticker],
    ["SEC identifier (CIK)", page.cik],
    ["industry code (SIC)", page.sic || "not recorded"],
    ["groups", (page.groups || []).join(", ") || "none"],
  ];
  const body = `
<h2>${esc(page.ticker)} — ${esc(page.name || "")}</h2>
<dl class="meta">${meta.map(([k, v]) =>
    `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
<pre class="explain">${esc(page.explain)}</pre>

<h2>The thirteen measures</h2>
${measuresTable(page.measures) || '<p class="quiet">No assessment has been ' +
  "saved for this company, so there are no readings to show. " +
  `<code>ledgerline score ${esc(page.ticker)} --emit</code> saves one.</p>`}

<h2>Filings these numbers came from</h2>
${timelineTable(page)}

<h2>Figures later revised</h2>
${restatementsTable(page)}

<h2>Where each number came from</h2>
${provenanceTrail(page, page.cik)}

${historyTable(page)}`;
  return layout({
    title: page.ticker, current: "/company",
    validation: page.validation, body, generated: page.generated,
  });
}

// The Company tab with no company named: the list is 1,498 rows long, so the
// useful landing is a search box and the companies something was said about.
export function companyIndex(data, note) {
  const flagged = (data.companies || []).filter(
    (c) => (c.latest || {}).flagged).slice(0, 25);
  const body = `
<h2>Open a company</h2>
${note ? `<div class="empty"><p>${note}</p></div>` : ""}
<form method="get" action="/company">
  <input type="text" name="ticker" placeholder="e.g. FMC" aria-label="Ticker">
  <button type="submit">Open the company page</button>
</form>
<p class="note">Every watched company has a page: its plain-language reading,
 the thirteen measures, the filings behind each number, and anything later
 revised. Browse them all on the <a href="/watchlist">watchlist</a>.</p>
${flagged.length ? `<h2>Flagged in their latest saved assessment</h2>
<div class="scroll"><table><tr><th>company</th><th>concern score, 0–100</th>
  <th>measures out of line</th></tr>
${flagged.map((c) => `<tr>
  <td class="tick"><a href="/company/${encodeURIComponent(c.ticker)}">${esc(c.ticker)}</a>
    <br><span class="quiet">${esc(c.name || "")}</span></td>
  <td class="num">${esc(c.latest.score)}</td>
  <td class="quiet">${(c.latest.flags || []).map(esc).join(", ")}</td></tr>`).join("")}
</table></div>
<p class="note">Flagged at 45 of 100 with at least two measures out of line.
 A flag is a prompt to read the filings, not a finding — this detector's
 false-alarm rate was 7.5 times the crude two-line rule it had to beat.</p>`
    : ""}`;
  return layout({
    title: "Company", current: "/company",
    validation: data.validation, body, generated: data.generated,
  });
}

// ------------------------------------------------------------------ activity

export function activity(data) {
  const runs = data.runs || [];
  const body = `
<h2>Runs</h2>
<p class="note">Every scan and fetch this machine has run, newest first. A scan
 costs one request to the SEC's daily filing index no matter how many companies
 are watched — the cost below does not grow with the watchlist, which is the
 whole reason there is no per-company polling in this tool.</p>
${!runs.length
    ? '<div class="empty"><p>Nothing has run yet on this machine.</p>' +
      "<p>Read today's filings: <code>ledgerline scan</code>. Download filing " +
      "histories first with <code>ledgerline fetch</code>.</p></div>"
    : `<div class="scroll"><table>
  <tr><th>run</th><th>started</th><th>took</th><th>requests to the SEC</th>
      <th>what it read</th><th>what it found</th><th>could not assess</th></tr>
  ${runs.map((r) => {
      const secs = (r.started_at && r.finished_at)
        ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : null;
      return `<tr>
    <td>${esc(r.job)} <span class="quiet">#${esc(r.run_id)}</span><br>
      <span class="${r.status === "failed" ? "verdict-flagged" : "quiet"}">${
        esc(r.status)}</span>${r.error
          ? `<br><span class="quiet">${esc(String(r.error).split("\n")[0])}</span>` : ""}</td>
    <td class="num">${esc(when(r.started_at))}</td>
    <td class="num">${secs === null ? "—" : `${secs.toFixed(1)}s`}</td>
    <td class="num">${num(r.requests)}<br><span class="quiet">${
        num(r.cache_hits)} served from cache, ${bytes(r.bytes_fetched)}</span></td>
    <td class="num">${num(r.index_rows)} filings listed<br>
      <span class="quiet">${num(r.universe_hits)} from watched companies,
      ${num(r.filers_done)} read${r.filers_failed
        ? `, ${num(r.filers_failed)} failed` : ""}</span></td>
    <td class="num">${r.assessed === null || r.assessed === undefined
        ? '<span class="quiet">nothing assessed</span>'
        : `${num(r.assessed)} assessed<br><span class="quiet">${
          num(r.gated_in)} flagged, ${num(r.restatements)} revisions found</span>`}</td>
    <td class="num">${r.could_not_assess === null || r.could_not_assess === undefined
        ? "—" : num(r.could_not_assess)}</td>
  </tr>`;
    }).join("")}
  </table></div>
  <p class="note">“Could not assess” is the denominator, and it is counted
   here on purpose: a run that flagged six of 471 assessed companies while
   walking away from eighteen more is a different result from one that
   assessed all 489. A run that flagged nothing is not a quiet market — this
   detector misses roughly seven deteriorations in ten.</p>`}`;
  return layout({
    title: "Activity", current: "/activity",
    validation: data.validation, body, generated: data.generated,
  });
}
