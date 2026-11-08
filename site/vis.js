import { likelihoodLabel } from "./vendor/tic-doctrine.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function fmtDay(iso) {
  const d = new Date(iso + "T00:00:00Z");
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

const T = window.TicTime;
// Two runs a day, but GitHub starts scheduled runs late on this account (gaps of up to about 16 hours measured,
// 6-8 Nov 2026), so 20 hours means a run was missed.
const STALE_HOURS = 20;

function hm(iso) {                                // the viewer's zone, named; UTC in the tooltip
  if (!iso) return { text: "–", utc: "" };
  const utc = iso.slice(11, 16) + " UTC";
  return { text: T ? T.format(iso.replace("Z", ":00Z"), { date: false }) : utc, utc };
}
const timeSpan = (iso) => { const t = hm(iso); return `<span class="mono" title="${esc(t.utc)}">${esc(t.text)}</span>`; };
const when = (g) => (T ? T.format(g, { year: true }) : g.replace("T", " ").slice(0, 16) + " UTC");

const RANK = { green: 0, yellow: 1, amber: 2, red: 3 };

// The threshold that explains a hazard's colour under the matrix, and its probability: Amber can come from a 20%
// chance of the red threshold, Yellow from a 5% chance of it.
function shown(v) {
  if (v.colour === "red") return ["red", v.p_red];
  if (v.colour === "amber") return v.p_amber >= 0.45 ? ["amber", v.p_amber] : ["red", v.p_red];
  return v.p_amber >= 0.2 ? ["amber", v.p_amber] : ["red", v.p_red];
}

function dayCell(d, hz) {
  const drivers = Object.entries(d.hazards || {})
    .filter(([, v]) => v.colour !== "green")
    .sort((a, b) => RANK[b[1].colour] - RANK[a[1].colour] || Math.max(b[1].p_amber, b[1].p_red) - Math.max(a[1].p_amber, a[1].p_red));
  const lines = drivers.slice(0, 2).map(([k, v]) => {
    const [level, p] = shown(v);
    const lim = hz[k] ? ` ${limit(hz[k])(level)}` : "";
    const term = likelihoodLabel(Math.max(0.01, Math.min(0.99, p || 0.01)));
    return `<div class="driver-line"><b>${esc(hz[k]?.name || k)}${esc(lim)}</b>: ${esc(term)} · <span class="mono">${Math.round(p * 100)}%</span></div>`;
  });
  return `<td class="day-cell col-${esc(d.colour)}"><div class="day-head">${esc(d.colour.toUpperCase())}</div>${
    lines.join("") || '<div class="driver-line">All hazards below Yellow</div>'
  }</td>`;
}

function nightCell(n) {
  const lowH = Math.floor((n.dark_low_light_min || 0) / 60);
  const lowM = (n.dark_low_light_min || 0) % 60;
  return `<td class="night-cell"><div><b>Moon ${Math.round((n.moon_lit || 0) * 100)}% lit</b> · low-light dark <span class="mono">${lowH}h ${lowM}m</span></div>
    <div>Civil dusk ${timeSpan(n.civil_dusk)} · dawn ${timeSpan(n.civil_dawn)}</div></td>`;
}

function rowHead(z) {
  const meta = `${esc(z.station)} · ${esc(z.icao)} (${z.lat.toFixed(2)}, ${z.lon.toFixed(2)})`;
  if (z.role === "alternate") {
    return `<th scope="row" class="alt-head"><span class="sr">${esc(z.name)}, </span>Alternate point<span class="station-meta">${meta}</span></th>`;
  }
  return `<th scope="row">${esc(z.name)}<span class="station-meta">${meta}</span></th>`;
}

function limit(h) {                               // "≥ 65 km/h", "≤ 3000 m", "≤ 0 C with ≥ 1 mm/h"
  const op = (dir) => (dir === "above" ? "≥ " : "≤ ");
  return (level) => (h.with
    ? `${op(h.dir)}${+h[level]} C with ${op(h.with.dir)}${+h.with[level]} mm/h`
    : `${op(h.dir)}${+h[level]} ${h.unit}`);
}

async function init() {
  const res = await fetch("data/forecast.json");
  const doc = await res.json();
  const g = doc.generated_at;
  const stamp = document.getElementById("vis-stamp");
  if (stamp) {
    stamp.textContent = `Generated ${when(g)} · 82 ensemble members`;
    stamp.title = g.replace("T", " ").slice(0, 16) + " UTC";
  }
  const asof = document.getElementById("vis-asof");
  if (asof) asof.textContent = when(g);
  const stale = document.getElementById("vis-stale");
  const ageH = (Date.now() - Date.parse(g)) / 3600000;
  if (stale) {
    stale.hidden = !(ageH > STALE_HOURS);
    stale.textContent = stale.hidden ? "" : `This forecast is ${Math.round(ageH)} hours old. Vis normally updates twice a day, so at least one update has failed; columns for days already past still show.`;
  }

  const hz = Object.fromEntries((doc.hazards || []).map((h) => [h.id, h]));
  const days = (doc.zones[0]?.days || []).map((d) => d.day);
  const thead = document.getElementById("vis-thead");
  thead.innerHTML = `<tr><th scope="col">Zone and forecast point</th>${days
    .map((d) => `<th scope="col">${esc(fmtDay(d))}</th>`)
    .join("")}<th scope="col">Tonight’s light</th></tr>`;

  const tbody = document.getElementById("vis-tbody");
  tbody.innerHTML = (doc.zones || [])
    .map((z) => `<tr id="zone-${esc(z.id)}" class="${z.role === "alternate" ? "alt-row" : "ref-row"}">
        ${rowHead(z)}
        ${(z.days || []).map((d) => dayCell(d, hz)).join("")}
        ${nightCell(z.days?.[0]?.night || {})}
      </tr>`)
    .join("");

  const first = doc.zones[0]?.days?.[0]?.hazards || {};
  const ul = document.getElementById("vis-thresholds");
  ul.innerHTML = (doc.hazards || [])
    .map((h) => {
      const lim = limit(h);
      const n = first[h.id]?.members;
      return `<li><b>${esc(h.name)}</b>: Amber <span class="mono">${esc(lim("amber"))}</span>, Red <span class="mono">${esc(lim("red"))}</span>${n ? ` (${n} members)` : ""}. ${esc(h.why)}.</li>`;
    })
    .join("");
}

init();
if (T) { T.mountSettings(document.getElementById("time-settings")); T.onChange(() => init()); }
