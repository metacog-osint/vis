import { likelihoodLabel } from "./vendor/tic-doctrine.js";
import { setup as setupDetail } from "./locdetail.js";

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
// The point's own clock (its IANA zone from config/points.json), zone named, on the viewer's 12- or 24-hour setting.
function placeTime(iso, tz) {
  if (!iso || !tz) return null;
  try {
    const o = { timeZone: tz, hour: "numeric", minute: "2-digit", timeZoneName: "short" };
    o.hourCycle = T && !T.hour12() ? "h23" : "h12";
    return new Intl.DateTimeFormat(undefined, o).format(new Date(iso.replace("Z", ":00Z")));
  } catch (e) { return null; }
}
// Local time at the point, then the viewer's; one time only when the two read the same.
function timePair(iso, tz) {
  const local = placeTime(iso, tz), t = hm(iso);
  if (!local) return timeSpan(iso);
  const there = `<span class="mono" title="${esc("Local time at the point (" + tz + "); " + t.utc)}">${esc(local)}</span>`;
  if (local === t.text) return there;
  return `${there} local <span class="also">· <span class="mono" title="${esc(t.utc)}">${esc(t.text)}</span> yours</span>`;
}
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
    const sev = level === "red" && v.colour !== "red" ? ' <span class="small" title="This colour comes from the chance of the severe (red) threshold. Yellow starts at 5%, Amber at 20%, Red at 45%.">(severe threshold)</span>' : "";
    return `<div class="driver-line"><b>${esc(hz[k]?.name || k)}${esc(lim)}</b>: ${esc(term)} · <span class="mono">${Math.round(p * 100)}%</span>${sev}</div>`;
  });
  return `<td class="day-cell col-${esc(d.colour)}"><div class="day-head">${esc(d.colour.toUpperCase())}</div>${
    lines.join("") || '<div class="driver-line">All hazards below Yellow</div>'
  }</td>`;
}

function nightCell(n, tz) {
  const lowH = Math.floor((n.dark_low_light_min || 0) / 60);
  const lowM = (n.dark_low_light_min || 0) % 60;
  return `<td class="night-cell"><div><b>${esc(n.moon_phase || "Moon")}, ${Math.round((n.moon_lit || 0) * 100)}% lit</b> · low-light dark <span class="mono">${lowH}h ${lowM}m</span></div>
    <div>Sunset ${timePair(n.sunset_dusk, tz)}</div>
    <div>Civil dusk ${timePair(n.civil_dusk, tz)}</div>
    <div>Civil dawn ${timePair(n.civil_dawn, tz)}</div>
    <div>Sunrise ${timePair(n.sunset_dawn, tz)}</div></td>`;
}

// The low ceiling trial at each point, from the build's cell checks (tools/gefs.py add_reach): left out, or which of
// its thresholds the GEFS field can reach above the station given the model's ground at the cell.
let REACH = {};
function ceilingReach(doc) {
  const c = doc.ceiling || {};
  if (c.state !== "used" || !Array.isArray(c.cells)) return {};
  const left = new Set(c.left_out || []);
  const out = {};
  (doc.zones || []).forEach((z, i) => {
    const cell = c.cells[i];
    if (cell) out[z.id] = { left: left.has(z.icao) || left.has(z.id), r: cell.reach || null, diff: cell.diff_m, z };
  });
  return out;
}
function reachShort(x) {
  if (!x) return "";
  if (x.left) return "Ceiling trial: not read here";
  if (x.r && !x.r.amber) return "Ceiling trial: Amber and Red not reachable here";
  if (x.r && !x.r.red) return "Ceiling trial: Red not reachable here";
  return "";
}
function reachLong(x) {
  if (!x) return "";
  if (x.left) return `The low ceiling trial is not read here: the best GEFS cell’s ground is ${x.diff == null ? "unknown" : `${Math.abs(Math.round(x.diff))} m ${x.diff >= 0 ? "above" : "below"} the station`}, more than 100 m off.`;
  const r = x.r;
  if (!r) return "";
  const d = r.ground_minus_station_m;
  const ground = `GEFS’s ground at this point’s cell is ${Math.abs(Math.round(d))} m ${d >= 0 ? "above" : "below"} the station`;
  if (d >= 0) {
    const lim = !r.amber ? "neither Amber (305 m) nor Red (152 m) can be reached"
      : !r.red ? `Red (152 m) cannot be reached${r.lowest_layer_only.includes("amber") ? ", and Amber (305 m) only from the lowest model layer" : ""}`
      : r.lowest_layer_only.length ? `${r.lowest_layer_only.includes("amber") ? "Amber (305 m)" : "Red (152 m)"} is reached only from the lowest model layer` : "";
    return `${ground}; the ceiling field sits at least about 17 m above that ground, so the lowest ceiling it can give here is about ${r.lowest_agl_m} m above the station${lim ? `: ${lim}` : ""}.`;
  }
  return `${ground}, so ceilings read up to ${Math.abs(Math.round(d))} m lower here than above the model’s ground, and a base in the lowest model layer reads 0 m above the station (Red).`;
}

// Points where the model's ground and the station differ by more than 50 m, or a threshold is out of reach or the
// trial is not read, for the thresholds list.
function reachCompact(x) {
  if (x.left) return `not read: the best cell’s ground is ${x.diff == null ? "unknown" : `${Math.abs(Math.round(x.diff))} m ${x.diff >= 0 ? "above" : "below"} the station`}.`;
  const r = x.r, d = Math.round(r.ground_minus_station_m);
  if (d < 0) return `model ground ${-d} m below the station: ceilings read up to ${-d} m low, and a base in the lowest model layer reads 0 m (Red).`;
  const lim = !r.amber ? "Amber and Red not reachable"
    : !r.red ? `Red not reachable${r.lowest_layer_only.includes("amber") ? "; Amber only from the lowest model layer" : ""}`
    : r.lowest_layer_only.length ? `${r.lowest_layer_only.includes("amber") ? "Amber" : "Red"} only from the lowest model layer` : "";
  return `model ground ${d} m above the station; lowest ceiling about ${r.lowest_agl_m} m above it${lim ? `. ${lim}` : ""}.`;
}
function reachList() {
  const rows = Object.values(REACH).filter((x) => x.left || (x.r && (Math.abs(x.r.ground_minus_station_m) > 50
    || !x.r.amber || !x.r.red || x.r.lowest_layer_only.length)));
  if (!rows.length) return "";
  return `<span class="small reach-head">Each point reads the one of its four surrounding 0.5° GEFS cells whose model ground (HGT:surface) is nearest the station’s height. These points are left out, because their best cell is more than 100 m off (this build’s cells):</span><ul class="reach-list">${rows
    .map((x) => `<li><b>${esc(x.z.station)} · ${esc(x.z.icao)}</b>: ${esc(reachCompact(x))}</li>`).join("")}</ul>`;
}

function rowHead(z) {
  // The station opens the point's detail pop-up (locdetail.js); the button carries the point id.
  const btn = `<button type="button" class="loc-open" data-loc="${esc(z.id)}" title="Forecast, charts and climate for ${esc(z.station)}">${esc(z.station)} · ${esc(z.icao)}</button>`;
  const short = reachShort(REACH[z.id]);
  const mark = short ? `<span class="cig-reach" title="${esc(reachLong(REACH[z.id]))}">${esc(short)}</span>` : "";
  const meta = `${btn} <span class="mono">(${z.lat.toFixed(2)}, ${z.lon.toFixed(2)})</span>${mark}`;
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
    const ms = doc.models || [];
    const used = ms.filter((m) => m.used !== false);
    const n = doc.members_used || 82;
    const short = (m) => (m.id === "ncep_gefs025" ? "GEFS" : m.id === "ecmwf_ifs025" ? "ECMWF" : m.id);
    const runs = ms.map((m) => (m.used === false
      ? `${short(m)} left out: newest run ${m.run_time ? when(m.run_time) : "unknown"}, ${m.age_hours} h old`
      : `${short(m)} run ${m.run_time ? when(m.run_time) : "time unknown"}`)).join("; ");
    const c = doc.ceiling || {};
    const cig = c.state === "used" ? `; ceiling trial: GEFS run ${when(c.run_time)} from NOAA's files`
      : c.state ? "; ceiling trial: not read this run" : "";
    stamp.textContent = `Generated ${when(g)} · ${n} ensemble members${runs ? ` (${runs}${cig})` : ""}`;
    stamp.title = g.replace("T", " ").slice(0, 16) + " UTC";
    if (used.length < ms.length) stamp.classList.add("partial");
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
  REACH = ceilingReach(doc);
  const days =(doc.zones[0]?.days || []).map((d) => d.day);
  const dayHours = doc.zones[0]?.days?.[0]?.hours ?? 24;   // today's column counts only the hours still ahead
  const dayFrom = doc.zones[0]?.days?.[0]?.from;
  const thead = document.getElementById("vis-thead");
  thead.innerHTML = `<tr><th scope="col">Zone and forecast point</th>${days
    .map((d, i) => `<th scope="col">${esc(fmtDay(d))}${i === 0 && dayHours < 24 ? `<span class="station-meta">${esc(`from ${T ? T.format(dayFrom) : dayFrom.replace("T", " ") + " UTC"}`)}</span>` : ""}</th>`)
    .join("")}<th scope="col">Tonight’s light</th></tr>`;

  const tbody = document.getElementById("vis-tbody");
  tbody.innerHTML = (doc.zones || [])
    .map((z) => `<tr id="zone-${esc(z.id)}" class="${z.role === "alternate" ? "alt-row" : "ref-row"}">
        ${rowHead(z)}
        ${(z.days || []).map((d) => dayCell(d, hz)).join("")}
        ${nightCell(z.days?.[0]?.night || {}, z.tz)}
      </tr>`)
    .join("");

  const first = doc.zones[0]?.days?.[0]?.hazards || {};
  const ul = document.getElementById("vis-thresholds");
  ul.innerHTML = (doc.hazards || [])
    .map((h) => {
      const lim = limit(h);
      const n = first[h.id]?.members;
      const extra = h.id === "ceiling" ? reachList() : "";
      return `<li><b>${esc(h.name)}</b>: Amber <span class="mono">${esc(lim("amber"))}</span>, Red <span class="mono">${esc(lim("red"))}</span>${n ? ` (${n} members)` : ""}. ${esc(h.why)}.${extra}</li>`;
    })
    .join("");
  return doc;
}

// A link to a zone's row (#zone-..., from COP) lands before the rows are drawn; land it once they are
// (19 Nov 2026: every row below the first stopped 1,800 to 3,400 px short).
function land() {
  const id = location.hash.length > 1 ? decodeURIComponent(location.hash.slice(1)) : "";
  const el = id ? document.getElementById(id) : null;
  if (el) el.scrollIntoView({ block: "start" });
}

init().then((doc) => { land(); setupDetail({ hazardList: doc.hazards || [], points: doc.zones || [] }); }, () => {});
if (T) { T.mountSettings(document.getElementById("time-settings")); T.onChange(() => init()); }
