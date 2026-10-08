// Inline SVG for the pages: the icons and the charts. Same rules as pages.mjs:
// nothing imported, no script, nothing fetched. Every chart is drawn from
// numbers a published file already holds. The only arithmetic here is scaling
// those numbers to pixels, so a chart can't say anything its file doesn't.
//
// No xmlns attribute on any <svg>: inline SVG in HTML doesn't need one, and
// tests/unit/test_web_pages.py holds every URL on a page to the SEC's archive.
// Colours come from classes in style.css, so the charts follow dark mode.

function attr(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Drawn for this project on a 24-unit grid, stroked with currentColor.
const ICONS = {
  overview: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>' +
    '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>' +
    '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>' +
    '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/>' +
    '<circle cx="4.8" cy="6.5" r="1.2"/><circle cx="4.8" cy="12" r="1.2"/>' +
    '<circle cx="4.8" cy="17.5" r="1.2"/>',
  building: '<path d="M3.5 20.5h17"/>' +
    '<path d="M6 20.5V5.2c0-.9.7-1.7 1.6-1.7h8.8c.9 0 1.6.8 1.6 1.7v15.3"/>' +
    '<path d="M9.5 7.5h1.2M13.3 7.5h1.2M9.5 11h1.2M13.3 11h1.2M9.5 14.5h1.2M13.3 14.5h1.2"/>',
  pulse: '<path d="M3 12h4l2.5-6.5 5 13 2.5-6.5h4"/>',
  flask: '<path d="M9.5 3.5h5"/>' +
    '<path d="M10.5 3.5v5.3l-5.2 9.1c-.7 1.2.2 2.6 1.6 2.6h10.2c1.4 0 2.3-1.4 1.6-2.6l-5.2-9.1V3.5"/>' +
    '<path d="M7.6 14.5h8.8"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
  external: '<path d="M14 4.5h5.5V10"/><path d="M19.5 4.5 11 13"/>' +
    '<path d="M18 14v4.5c0 .8-.7 1.5-1.5 1.5h-11c-.8 0-1.5-.7-1.5-1.5v-11C4 6.7 4.7 6 5.5 6H10"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  alert: '<path d="M10.3 4.6 2.9 17.4c-.8 1.3.2 3.1 1.7 3.1h14.8c1.5 0 2.5-1.8 1.7-3.1L13.7 4.6c-.8-1.3-2.6-1.3-3.4 0z"/>' +
    '<path d="M12 9.5v4.5M12 17.2v.1"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  file: '<path d="M7 3.5h6.5L18 8v11.5c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1v-15c0-.6.4-1 1-1z"/>' +
    '<path d="M13.5 3.5V8H18"/>',
  terminal: '<rect x="3.5" y="5" width="17" height="14" rx="2.2"/>' +
    '<path d="m7.5 10 2.5 2-2.5 2M12.5 14.5h4"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/>' +
    '<path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.2M12 7.9v.1"/>',
  calendar: '<rect x="4" y="5.5" width="16" height="15" rx="2.2"/>' +
    '<path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
  layers: '<path d="m12 3.5 8.5 4.5L12 12.5 3.5 8z"/>' +
    '<path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16l8.5 4.5 8.5-4.5"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/>' +
    '<circle cx="12" cy="12" r=".8"/>',
  sigma: '<path d="M17.5 5.5h-11l6 6.5-6 6.5h11"/>',
};

export function icon(name, cls = "icon") {
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" ` +
    `focusable="false">${ICONS[name] || ""}</svg>`;
}

// The mark: a page of ledger paper. Ruled lines, and the red margin line every
// ledger has down its left side.
export function logo() {
  return '<svg class="logo" viewBox="0 0 32 32" aria-hidden="true" focusable="false">' +
    '<rect class="logo-bg" width="32" height="32" rx="9"/>' +
    '<path class="logo-rule" d="M14.5 11h9.5M14.5 16h9.5M14.5 21h6"/>' +
    '<path class="logo-margin" d="M10.5 7v18"/></svg>';
}

function f1(n) {
  return Number(n).toFixed(1);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
  "Oct", "Nov", "Dec"];

// "2026-10-03" reads as "Oct 3".
export function monthDay(iso) {
  const [, m, d] = String(iso).split("-").map(Number);
  return m && d ? `${MONTHS[m - 1]} ${d}` : String(iso);
}

// ------------------------------------------------------------ score history

/* One bar per saved assessment, oldest on the left. Height is the concern
   score out of 100; a bar is red when that assessment flagged the company,
   and a pale full-height column marks an assessment that couldn't be made.
   The dashed line is the flag line at 45. Each bar carries a <title>, which
   browsers show on hover with no script. */
export function scoreHistory(rows, threshold = 45) {
  const pts = rows.filter((r) => r && r.as_of).slice().reverse();
  if (pts.length < 2) return "";
  const W = 760, H = 240, L = 34, R = 10, T = 16, B = 28;
  const pw = W - L - R, ph = H - T - B;
  const step = pw / pts.length;
  const bw = Math.max(2, Math.min(22, step * 0.66));
  const y = (v) => T + ph - (Math.max(0, Math.min(100, v)) / 100) * ph;

  let grid = "";
  for (const v of [0, 25, 50, 75, 100]) {
    grid += `<line class="grid" x1="${L}" x2="${W - R}" y1="${f1(y(v))}" y2="${f1(y(v))}"/>` +
      `<text class="axis" x="${L - 8}" y="${f1(y(v) + 3.5)}" text-anchor="end">${v}</text>`;
  }

  let bars = "";
  let labels = "";
  let lastYear = "";
  let lastLabelX = -Infinity;
  pts.forEach((p, i) => {
    const x = L + i * step + (step - bw) / 2;
    const when = `${p.as_of}${p.period ? `, quarter ending ${p.period}` : ""}`;
    if (!p.scoreable || p.score === null || p.score === undefined) {
      bars += `<rect class="bar na" x="${f1(x)}" y="${T}" width="${f1(bw)}" ` +
        `height="${ph}" rx="1.5"><title>${attr(when)}: could not assess</title></rect>`;
    } else {
      const top = y(Number(p.score));
      bars += `<rect class="bar${p.flagged ? " flag" : ""}" x="${f1(x)}" ` +
        `y="${f1(top)}" width="${f1(bw)}" height="${f1(Math.max(1.5, T + ph - top))}" ` +
        `rx="1.5"><title>${attr(when)}: ${attr(p.score)} out of 100, ` +
        `${p.flagged ? "flagged" : "not flagged"}</title></rect>`;
    }
    const year = String(p.as_of).slice(0, 4);
    const cx = x + bw / 2;
    if (year !== lastYear && cx - lastLabelX >= 34) {
      labels += `<text class="axis" x="${f1(cx)}" y="${H - 8}" text-anchor="middle">${attr(year)}</text>`;
      lastLabelX = cx;
    }
    lastYear = year;
  });

  const ly = f1(y(threshold));
  const limit = `<line class="limit" x1="${L}" x2="${W - R}" y1="${ly}" y2="${ly}">` +
    `<title>Flag line, ${threshold}</title></line>`;

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" ` +
    `aria-label="Concern score at each of ${pts.length} saved assessments, ` +
    `oldest to newest. Every one is listed in the table below the chart.">` +
    `${grid}${bars}${limit}${labels}</svg>`;
}

// --------------------------------------------------------- filing timeline

function yearOf(iso) {
  const [Y, M, D] = String(iso).split("-").map(Number);
  return Y + (M - 1) / 12 + (D - 1) / 365;
}

// Annual reports stand tallest, quarterly reports half as tall, and anything
// else (current reports, proxies, amendments to them) is a short tick.
function formFamily(form) {
  const f = String(form || "").toUpperCase();
  if (/^(10-K|20-F|40-F)/.test(f)) return "annual";
  if (/^10-Q/.test(f)) return "quarterly";
  return "other";
}

export function filingTimeline(filings) {
  const pts = filings.filter((f) => /^\d{4}-\d{2}-\d{2}$/.test(f.filed || ""));
  if (pts.length < 2) return "";
  const ts = pts.map((f) => yearOf(f.filed));
  const t0 = Math.floor(Math.min(...ts));
  const t1 = Math.floor(Math.max(...ts)) + 1;
  const W = 760, H = 92, L = 10, R = 10, base = 62;
  const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const tall = { annual: 40, quarterly: 24, other: 11 };

  let axis = `<line class="grid strong" x1="${L}" x2="${W - R}" y1="${base}" y2="${base}"/>`;
  const span = t1 - t0;
  const every = span > 16 ? 3 : span > 9 ? 2 : 1;
  for (let yr = t0; yr <= t1; yr++) {
    axis += `<line class="grid" x1="${f1(x(yr))}" x2="${f1(x(yr))}" y1="${base}" y2="${base + 5}"/>`;
    if ((yr - t0) % every === 0 && yr < t1) {
      axis += `<text class="axis" x="${f1(x(yr + 0.5))}" y="${base + 20}" ` +
        `text-anchor="middle">${yr}</text>`;
    }
  }
  const marks = pts.map((f) => {
    const fam = formFamily(f.form);
    const h = tall[fam];
    const amend = /\/A$/i.test(f.form || "") ? " amend" : "";
    return `<rect class="mark ${fam}${amend}" x="${f1(x(yearOf(f.filed)) - 1.25)}" ` +
      `y="${base - h}" width="2.5" height="${h}" rx="1"><title>${attr(f.form || "filing")} ` +
      `filed ${attr(f.filed)}${f.period ? `, reporting ${attr(f.period)}` : ""}</title></rect>`;
  }).join("");
  return `<svg class="chart timeline" viewBox="0 0 ${W} ${H}" role="img" ` +
    `aria-label="${pts.length} filings by date filed, from ${t0} to ${t1 - 1}. ` +
    `Every one is listed in the table below.">${axis}${marks}</svg>`;
}

// ------------------------------------------------------------- run history

/* One bar per run, oldest on the left: how many filings from watched
   companies that run found in the daily index. A failed run is red. */
export function runHistory(runs) {
  const pts = runs.filter((r) => r && r.started_at).slice().reverse();
  if (pts.length < 2) return "";
  const vals = pts.map((r) => Number(r.universe_hits) || 0);
  const top = Math.max(1, ...vals);
  const W = 760, H = 168, L = 40, R = 10, T = 12, B = 28;
  const pw = W - L - R, ph = H - T - B;
  const step = pw / pts.length;
  const bw = Math.max(3, Math.min(30, step * 0.62));
  const nice = top <= 10 ? 10 : Math.ceil(top / 50) * 50;
  const y = (v) => T + ph - (v / nice) * ph;

  let grid = "";
  for (const v of [0, nice / 2, nice]) {
    grid += `<line class="grid" x1="${L}" x2="${W - R}" y1="${f1(y(v))}" y2="${f1(y(v))}"/>` +
      `<text class="axis" x="${L - 8}" y="${f1(y(v) + 3.5)}" text-anchor="end">${v}</text>`;
  }
  let bars = "";
  let labels = "";
  let lastLabelX = -Infinity;
  pts.forEach((r, i) => {
    const v = vals[i];
    const x = L + i * step + (step - bw) / 2;
    const day = String(r.started_at).slice(0, 10);
    const top2 = y(v);
    bars += `<rect class="bar${r.status === "failed" ? " flag" : " brand"}" x="${f1(x)}" ` +
      `y="${f1(top2)}" width="${f1(bw)}" height="${f1(Math.max(1.5, T + ph - top2))}" ` +
      `rx="2"><title>${attr(r.job)} #${attr(r.run_id)}, ${attr(day)}: ${v} filings ` +
      `from watched companies${r.status === "failed" ? ", failed" : ""}</title></rect>`;
    const cx = x + bw / 2;
    if (cx - lastLabelX >= 64 || (i === pts.length - 1 && cx - lastLabelX >= 40)) {
      labels += `<text class="axis" x="${f1(cx)}" y="${H - 8}" text-anchor="middle">` +
        `${attr(monthDay(day))}</text>`;
      lastLabelX = cx;
    }
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" ` +
    `aria-label="Filings from watched companies found by each of the last ` +
    `${pts.length} runs, oldest to newest. Every run is listed in the table below.">` +
    `${grid}${bars}${labels}</svg>`;
}
