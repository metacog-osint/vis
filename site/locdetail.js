// Vis location detail (22 Nov 2026): the pop-up for one forecast point, opened from its row in the table, from a
// link to #loc-<point id>, from a map marker (the map's "See the row" link, #zone-<point id>, or
// window.VisDetail.open(id) / a "vis:select" event with {detail: {id}}), and closed with Esc, the close button or a
// click outside it. Reads data/locations/<id>.json (schema vis-location/1, docs/location-data.md), built by
// tools/locations.py, and draws its charts as plain SVG (no library; the page's CSP allows only its own scripts).
// Times show in the viewer's zone through tic-time.js, with the zone named; sun and moon times show the point's own
// clock first, then the viewer's. Day boundaries and the daily table are the station's own local days, as the
// forecast API returns them. Anything that ages (the METAR's age, whether the TAF is still valid) is worked out when
// the pop-up is drawn, never taken from the build.

const T = globalThis.TicTime;   // window.TicTime in the page; absent when the helpers are tested outside a browser
const NS = "http://www.w3.org/2000/svg";
const WMO = {
  0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
  51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Light freezing drizzle", 57: "Freezing drizzle",
  61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Light freezing rain", 67: "Freezing rain",
  71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
  82: "Violent showers", 85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
  96: "Thunderstorm, hail", 99: "Thunderstorm, heavy hail",
};
const HAZ_NAMES = { wind: "Strong wind", lowcloud: "Low cloud cover", vis: "Poor visibility", rain: "Heavy rain",
  snow: "Heavy snow", icing: "Freezing rain / icing", heat: "Extreme heat", cold: "Extreme cold" };
const RANK = { green: 0, yellow: 1, amber: 2, red: 3 };
const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
// A METAR older than this is marked old and its flight category is hidden: stations report every 30 or 60 minutes,
// so past 90 minutes at least one routine report is missing.
export const METAR_OLD_MIN = 90;
const cache = new Map();
let hazards = {};          // hazard thresholds from data/forecast.json, set by vis.js through setHazards()
let current = null;        // the open point's id
let opener = null;         // the element focus returns to on close

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
const num = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const deg = (v) => (v === null || v === undefined ? "–" : `${num(v, 1)} °C`);
const iso = (s) => (s && s.length === 17 ? s.replace("Z", ":00Z") : s);   // "...T05:00Z" -> "...T05:00:00Z"
function when(s, opts = {}) {
  if (!s) return "–";
  const v = s.length === 17 ? s.replace("Z", ":00Z") : s;   // "2026-11-22T05:21Z" -> seconds for Date parsing
  return T ? T.format(v, opts) : v.replace("T", " ").slice(0, 16) + " UTC";
}
const timeOnly = (s) => when(s, { date: false });

// Minutes from a UTC time to now (the viewer's clock), or null.
export function ageMin(s, nowMs = Date.now()) {
  const t = Date.parse(iso(s) || "");
  return Number.isFinite(t) ? Math.round((nowMs - t) / 60000) : null;
}
export function ago(min) {
  if (min === null || min === undefined) return "";
  if (min < 0) return "time ahead of this device's clock";
  if (min < 90) return `${min} min ago`;
  if (min < 48 * 60) return `${(min / 60).toFixed(min < 600 ? 1 : 0)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}
// "current" while the TAF's validity has not ended, "ended" once it has, "none" when there is no TAF.
export function tafState(taf, nowMs = Date.now()) {
  if (!taf || !taf.raw) return "none";
  const end = Date.parse(iso(taf.valid_to) || "");
  return Number.isFinite(end) && end > nowMs ? "current" : "ended";
}
// The point's own clock (an IANA zone), zone named, on the viewer's 12- or 24-hour setting; null when unknown.
function placeTime(s, tz) {
  if (!s || !tz) return null;
  try {
    const o = { timeZone: tz, hour: "numeric", minute: "2-digit", timeZoneName: "short" };
    o.hourCycle = T && !T.hour12() ? "h23" : "h12";
    return new Intl.DateTimeFormat(undefined, o).format(new Date(iso(s)));
  } catch (e) { return null; }
}
let placeTz = null;        // the open point's zone, set in render()
// The point's local time, then the viewer's; one time when the two read the same. Returns escaped HTML.
function pair(s) {
  if (!s) return "–";
  const local = placeTime(s, placeTz), mine = timeOnly(s);
  if (!local || local === mine) return esc(local || mine);
  return `${esc(local)} local <span class="loc-also">· ${esc(mine)} yours</span>`;
}
const hm = (min) => (min === null || min === undefined ? "–" : `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m`);
const compass = (d) => (d === null || d === undefined ? "" : ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(d / 45) % 8]);

function el(tag, attrs = {}, kids = []) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) e.setAttribute(k, String(v));
  for (const k of kids) e.appendChild(k);
  return e;
}

function niceTicks(lo, hi, n = 4) {
  if (!(hi > lo)) { hi = lo + 1; lo -= 1; }
  const raw = (hi - lo) / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return { ticks: out, lo: Math.min(lo, out[0] ?? lo), hi: Math.max(hi, out[out.length - 1] ?? hi) };
}

// One time-series chart. series: [{y: [...], cls, kind: "line"|"bar"|"area", label, unit, axis: "left"|"right"}],
// band: {lo: [...], hi: [...], cls, label} stepped per hour; hlines: [{y, cls, label}]. Every array is hourly, aligned
// with t (ms). Returns a figure element with the chart, a key and a readout line that follows the pointer.
function chart({ t, series, band, hlines = [], right, title, unitLeft, dayTicks, now, floor0 = false }) {
  const W = 720, H = 190, L = 44, R = 44, TOP = 10, B = 24;   // the same margins on every chart, so their days line up
  const x0 = t[0], x1 = t[t.length - 1] || x0 + 1;
  const X = (ms) => L + ((ms - x0) / (x1 - x0)) * (W - L - R);
  const vals = [];
  for (const s of series) if (s.axis !== "right") vals.push(...s.y.filter((v) => v !== null && v !== undefined));
  if (band) vals.push(...band.lo.filter((v) => v != null), ...band.hi.filter((v) => v != null));
  for (const h of hlines) if (h.y !== null && h.y !== undefined && h.include !== false) vals.push(h.y);
  let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  if (floor0) lo = Math.min(0, lo);
  const pad = (hi - lo) * 0.06 || 1;
  const nt = niceTicks(floor0 ? lo : lo - pad, hi + pad);
  const Y = (v) => TOP + (1 - (v - nt.lo) / (nt.hi - nt.lo)) * (H - TOP - B);
  const YR = right ? (v) => TOP + (1 - (v - right.lo) / (right.hi - right.lo)) * (H - TOP - B) : null;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "loc-chart", role: "img", "aria-label": title });
  const g = el("g");
  svg.appendChild(g);
  if (now && now > x0 && now < x1) g.appendChild(el("rect", { x: L, y: TOP, width: X(now) - L, height: H - TOP - B, class: "c-past" }));
  for (const v of nt.ticks) {
    g.appendChild(el("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "c-grid" }));
    const tx = el("text", { x: L - 6, y: Y(v) + 4, class: "c-axis", "text-anchor": "end" });
    tx.textContent = String(v);
    g.appendChild(tx);
  }
  if (right) {
    for (const v of right.ticks) {
      const tx = el("text", { x: W - R + 6, y: YR(v) + 4, class: "c-axis" });
      tx.textContent = `${v}${right.unit || ""}`;
      g.appendChild(tx);
    }
  }
  for (const d of dayTicks || []) {
    if (d.ms < x0 || d.ms > x1) continue;
    g.appendChild(el("line", { x1: X(d.ms), x2: X(d.ms), y1: TOP, y2: H - B, class: "c-day" }));
    const tx = el("text", { x: X(d.ms) + 3, y: H - 8, class: "c-axis" });
    tx.textContent = d.label;
    g.appendChild(tx);
  }
  if (unitLeft) {
    const tx = el("text", { x: 4, y: TOP + 2, class: "c-axis c-unit" });
    tx.textContent = unitLeft;
    g.appendChild(tx);
  }
  const path = (ys, yf) => {
    let d = "", pen = false;
    ys.forEach((v, i) => {
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(t[i]).toFixed(1)},${yf(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  if (band) {
    // stepped band: one rectangle per run of equal values, so daily normals read as daily
    for (let i = 0; i < t.length - 1; i++) {
      const a = band.lo[i], b = band.hi[i];
      if (a == null || b == null) continue;
      g.appendChild(el("rect", { x: X(t[i]), width: X(t[i + 1]) - X(t[i]) + 0.4, y: Y(b), height: Math.max(0.5, Y(a) - Y(b)), class: band.cls }));
    }
  }
  for (const h of hlines) {
    if (h.y === null || h.y === undefined || h.y < nt.lo || h.y > nt.hi) continue;
    g.appendChild(el("line", { x1: L, x2: W - R, y1: Y(h.y), y2: Y(h.y), class: h.cls }));
    if (h.label) {
      const tx = el("text", { x: W - R - 4, y: Y(h.y) - 3, class: `c-axis ${h.cls}-t`, "text-anchor": "end" });
      tx.textContent = h.label;
      g.appendChild(tx);
    }
  }
  for (const s of series) {
    const yf = s.axis === "right" ? YR : Y;
    if (s.kind === "bar") {
      const w = Math.max(1, (W - L - R) / t.length - 0.3);
      s.y.forEach((v, i) => {
        if (!v) return;
        g.appendChild(el("rect", { x: X(t[i]) - w / 2, width: w, y: yf(v), height: Math.max(0.5, yf(s.base ?? 0) - yf(v)), class: s.cls }));
      });
    } else if (s.kind === "area") {
      const d = path(s.y, yf);
      if (d) g.appendChild(el("path", { d: `${d}L${X(t[t.length - 1])},${yf(s.base ?? 0)}L${X(t[0])},${yf(s.base ?? 0)}Z`, class: s.cls }));
    } else {
      const d = path(s.y, yf);
      if (d) g.appendChild(el("path", { d, class: s.cls }));
    }
  }
  if (now && now > x0 && now < x1) {
    g.appendChild(el("line", { x1: X(now), x2: X(now), y1: TOP, y2: H - B, class: "c-now" }));
    const tx = el("text", { x: X(now) + 3, y: TOP + 10, class: "c-axis c-now-t" });
    tx.textContent = "now";
    g.appendChild(tx);
  }
  const cursor = el("line", { x1: 0, x2: 0, y1: TOP, y2: H - B, class: "c-cursor", visibility: "hidden" });
  g.appendChild(cursor);

  const fig = document.createElement("figure");
  fig.className = "loc-fig";
  const cap = document.createElement("figcaption");
  const key = [...(band ? [{ cls: band.cls, label: band.label }] : []), ...series.map((s) => ({ cls: s.cls, label: s.label })),
    ...hlines.filter((h) => h.key).map((h) => ({ cls: h.cls, label: h.key }))];
  cap.innerHTML = `<b>${esc(title)}</b> ${key.map((k) => `<span class="loc-key"><i class="sw ${esc(k.cls)}"></i>${esc(k.label)}</span>`).join("")}`;
  fig.appendChild(cap);
  fig.appendChild(svg);
  const read = document.createElement("p");
  read.className = "loc-readout";
  read.textContent = "Point at the chart to read the values.";
  fig.appendChild(read);
  const pt = svg.createSVGPoint ? svg.createSVGPoint() : null;
  const move = (ev) => {
    if (!pt) return;
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const ms = x0 + ((p.x - L) / (W - L - R)) * (x1 - x0);
    let i = 0, best = Infinity;
    for (let k = 0; k < t.length; k++) { const dd = Math.abs(t[k] - ms); if (dd < best) { best = dd; i = k; } }
    cursor.setAttribute("x1", X(t[i])); cursor.setAttribute("x2", X(t[i])); cursor.setAttribute("visibility", "visible");
    const parts = series.map((s) => `${s.label} ${s.y[i] === null || s.y[i] === undefined ? "–" : num(s.y[i], s.digits ?? 1)}${s.unit || ""}`);
    if (band && band.lo[i] != null) parts.push(`${band.label} ${num(band.lo[i], 1)} to ${num(band.hi[i], 1)}`);
    read.textContent = `${when(new Date(t[i]).toISOString(), { weekday: true })}: ${parts.join(" · ")}`;
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", () => cursor.setAttribute("visibility", "hidden"));
  return fig;
}

// Bars of monthly rain under a line of monthly mean temperature: the year at the place, 1991-2020.
function climateChart(m) {
  const W = 360, H = 150, L = 34, R = 34, TOP = 10, B = 20;
  const pr = m.prcp_mm || [], tm = m.tmean_c || [], tx = m.tmax_c || [], tn = m.tmin_c || [];
  const pMax = Math.max(10, ...pr.filter((v) => v != null));
  const tt = niceTicks(Math.min(...tn.filter((v) => v != null), 0), Math.max(...tx.filter((v) => v != null), 10), 4);
  const pt = niceTicks(0, pMax, 3);
  const X = (i) => L + (i + 0.5) * ((W - L - R) / 12);
  const Yt = (v) => TOP + (1 - (v - tt.lo) / (tt.hi - tt.lo)) * (H - TOP - B);
  const Yp = (v) => TOP + (1 - v / pt.hi) * (H - TOP - B);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "loc-chart loc-clim", role: "img", "aria-label": "Monthly climate, 1991 to 2020" });
  for (const v of tt.ticks) {
    svg.appendChild(el("line", { x1: L, x2: W - R, y1: Yt(v), y2: Yt(v), class: "c-grid" }));
    const t = el("text", { x: L - 5, y: Yt(v) + 4, class: "c-axis", "text-anchor": "end" });
    t.textContent = `${v}°`;
    svg.appendChild(t);
  }
  for (const v of pt.ticks) {
    const t = el("text", { x: W - R + 5, y: Yp(v) + 4, class: "c-axis" });
    t.textContent = `${v}`;
    svg.appendChild(t);
  }
  const bw = (W - L - R) / 12 - 4;
  pr.forEach((v, i) => {
    if (v != null) svg.appendChild(el("rect", { x: X(i) - bw / 2, width: bw, y: Yp(v), height: Yp(0) - Yp(v), class: "c-rain" }));
    const t = el("text", { x: X(i), y: H - 6, class: "c-axis", "text-anchor": "middle" });
    t.textContent = MONTHS[i];
    svg.appendChild(t);
  });
  const line = (arr, cls) => {
    const d = arr.map((v, i) => (v == null ? "" : `${i ? "L" : "M"}${X(i).toFixed(1)},${Yt(v).toFixed(1)}`)).join("");
    if (d) svg.appendChild(el("path", { d, class: cls }));
  };
  line(tx, "c-hi"); line(tm, "c-temp"); line(tn, "c-lo");
  return svg;
}

function dayTicks(doc) {
  const off = (doc.site?.utc_offset_seconds || 0) * 1000;
  return (doc.daily?.date || []).map((d) => {
    const ms = Date.parse(d + "T00:00:00Z") - off;
    const dt = new Date(Date.parse(d + "T00:00:00Z"));
    return { ms, label: `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][dt.getUTCDay()]} ${dt.getUTCDate()}` };
  });
}

// Spread each local day's normal and record onto its hours, for the temperature chart's band and record lines.
function perHour(doc, t, key) {
  const off = (doc.site?.utc_offset_seconds || 0) * 1000;
  const idx = new Map((doc.daily?.date || []).map((d, i) => [d, i]));
  return t.map((ms) => {
    const d = new Date(ms + off).toISOString().slice(0, 10);
    const i = idx.get(d);
    return i === undefined ? null : (doc.daily[key]?.[i] ?? null);
  });
}

function nowCard(doc, nowMs) {
  const m = doc.now?.metar;
  const mod = doc.now?.model;
  if (m) {
    const age = ageMin(m.obs_time, nowMs);
    const old = age === null || age > METAR_OLD_MIN;
    const wind = m.wind_kt == null ? "–" : `${m.wind_dir_deg === "VRB" ? "variable" : m.wind_dir_deg == null ? "" : `${num(m.wind_dir_deg)}°`} ${num(m.wind_kt)} kt${m.gust_kt ? `, gusts ${num(m.gust_kt)} kt` : ""}`;
    const tag = old
      ? `<span class="loc-tag loc-tag-old" title="Over ${METAR_OLD_MIN} minutes old, so its flight category is not shown">old report</span>`
      : m.flight_category ? `<span class="loc-tag" title="Flight category in this report: VFR, MVFR, IFR or LIFR">${esc(m.flight_category)}</span>` : "";
    return `<div class="loc-card"><h3>Latest METAR ${tag}</h3>
      <p class="loc-big">${deg(m.temp_c)} <span class="small">dew point ${deg(m.dewpoint_c)}</span></p>
      <dl class="loc-dl"><dt>Report</dt><dd>${esc(when(m.obs_time))}${age !== null ? ` <span class="${old ? "loc-old" : ""}">(${esc(ago(age))})</span>` : ""}</dd>
      <dt>Wind</dt><dd>${esc(wind)}</dd><dt>Visibility</dt><dd>${esc(m.visibility ?? "–")} sm</dd>
      <dt>Ceiling</dt><dd>${m.ceiling_ft == null ? "none reported" : `${num(m.ceiling_ft)} ft`}</dd>
      <dt>Weather</dt><dd>${esc(m.weather || "nil")}</dd>${m.altimeter_hpa != null ? `<dt>Pressure</dt><dd>${num(m.altimeter_hpa)} hPa</dd>` : ""}</dl>
      ${old ? `<p class="small loc-old">This is the station's newest report Vis holds, ${esc(ago(age))}: it may have stopped reporting.</p>` : ""}
      <p class="loc-raw mono">${esc(m.raw || "")}</p></div>`;
  }
  return `<div class="loc-card"><h3>Model values <span class="small">at the last build</span></h3>
    ${mod ? `<p class="loc-big">${deg(mod.temperature_2m)} <span class="small">feels ${deg(mod.apparent_temperature)}</span></p>
    <dl class="loc-dl"><dt>Weather</dt><dd>${esc(WMO[mod.weather_code] || "–")}</dd><dt>Wind</dt><dd>${esc(compass(mod.wind_direction_10m))} ${num(mod.wind_speed_10m)} km/h, gusts ${num(mod.wind_gusts_10m)} km/h</dd>
    <dt>Pressure</dt><dd>${num(mod.pressure_msl)} hPa</dd><dt>Valid</dt><dd>${esc(when(mod.time))}</dd></dl>` : `<p class="small">No current data.</p>`}
    <p class="small">No METAR from ${esc(doc.icao)} in the 48 hours before the last build: the station may not be reporting (many airfields in conflict zones are closed).</p></div>`;
}

function todayCard(doc) {
  const t = doc.today || {};
  const recHi = t.record_tmax_c == null ? "–" : `${deg(t.record_tmax_c)} (${esc(t.record_tmax_year)})`;
  const recLo = t.record_tmin_c == null ? "–" : `${deg(t.record_tmin_c)} (${esc(t.record_tmin_year)})`;
  const dHi = t.tmax_c != null && t.normal_tmax_c != null ? t.tmax_c - t.normal_tmax_c : null;
  const dLo = t.tmin_c != null && t.normal_tmin_c != null ? t.tmin_c - t.normal_tmin_c : null;
  const signed = (v) => (v == null ? "" : ` <span class="loc-anom ${v >= 0 ? "warm" : "cool"}" title="today's forecast against the normal">(today ${v >= 0 ? "+" : "−"}${num(Math.abs(v), 1)})</span>`);
  return `<div class="loc-card"><h3>Today ${t.date ? `<span class="small">${esc(t.date)} local</span>` : ""}</h3>
    <p class="loc-big">${deg(t.tmax_c)} / ${deg(t.tmin_c)}</p>
    <dl class="loc-dl"><dt>Normal</dt><dd>${deg(t.normal_tmax_c)}${signed(dHi)} / ${deg(t.normal_tmin_c)}${signed(dLo)}</dd>
    <dt>Record high</dt><dd>${recHi}</dd><dt>Record low</dt><dd>${recLo}</dd>
    <dt>Wettest</dt><dd>${t.record_prcp_mm == null ? "–" : `${num(t.record_prcp_mm, 1)} mm (${esc(t.record_prcp_year)})`}</dd></dl>
    <p class="small">${t.normal_tmax_c == null ? "Climatology for this point is still being built." : `Normals ${esc(doc.site?.normals_period)}, records ${esc(doc.site?.records_period)}, both from the ERA5 reanalysis grid cell, not the station's own record.`}</p></div>`;
}

function sunCard(doc) {
  const s = doc.today?.sun || {};
  const n = doc.today?.night || {};
  const rise = s.polar === "polar_night" ? "no sunrise (polar night)" : s.polar === "midnight_sun" ? "sun up all day" : null;
  return `<div class="loc-card"><h3>Sun and moon</h3>
    <dl class="loc-dl">${rise ? `<dt>Sun</dt><dd>${esc(rise)}</dd>` : `<dt>Sunrise</dt><dd>${pair(s.sunrise)}</dd><dt>Sunset</dt><dd>${pair(s.sunset)}</dd>`}
    <dt>Day length</dt><dd>${hm(s.day_length_min)}</dd><dt>Solar noon</dt><dd>${pair(s.solar_noon)} (sun ${num(s.noon_altitude_deg, 0)}° up)</dd>
    <dt>Civil dusk</dt><dd>${pair(n.civil_dusk)}</dd><dt>Civil dawn</dt><dd>${pair(n.civil_dawn)}</dd>
    <dt>Moon</dt><dd>${esc(n.moon_phase || "–")}, ${num((n.moon_lit || 0) * 100)}% lit</dd>
    <dt>Moonrise</dt><dd>${(n.moonrise || []).map(pair).join(", ") || "–"}</dd><dt>Moonset</dt><dd>${(n.moonset || []).map(pair).join(", ") || "–"}</dd>
    <dt>Low-light dark</dt><dd>${hm(n.dark_low_light_min)}</dd></dl></div>`;
}

function siteCard(doc) {
  const s = doc.site || {};
  const m = s.monthly_normals || {};
  return `<div class="loc-card"><h3>The place</h3>
    <dl class="loc-dl"><dt>Position</dt><dd class="mono">${num(doc.lat, 3)}, ${num(doc.lon, 3)}</dd>
    <dt>Elevation</dt><dd>${s.station_elevation_m == null ? "–" : `${num(s.station_elevation_m)} m`} <span class="small">(terrain model at the station)</span></dd>
    <dt>Model cells</dt><dd>ensemble ${s.ensemble_cell_elevation_m == null ? "–" : `${num(s.ensemble_cell_elevation_m)} m`}, forecast ${s.forecast_cell_elevation_m == null ? "–" : `${num(s.forecast_cell_elevation_m)} m`} (${esc(s.cell)})</dd>
    <dt>Time zone</dt><dd>${esc(s.timezone || "–")}${s.timezone_abbreviation ? ` (${esc(s.timezone_abbreviation)})` : ""}</dd>
    <dt>Climate</dt><dd>${s.koppen ? `<b>${esc(s.koppen)}</b> ${esc(s.koppen_name)}` : "still being built"}</dd>
    ${m.annual_tmean_c != null ? `<dt>Year</dt><dd>mean ${deg(m.annual_tmean_c)}, ${num(m.annual_prcp_mm)} mm of precipitation</dd>` : ""}
    <dt>Biome</dt><dd>${s.biome ? esc(s.biome) : `<span class="small">not yet available</span>`}</dd></dl>
    <div id="loc-clim"></div></div>`;
}

function outlook(doc) {
  const days = doc.ensemble_days || [];
  if (!days.length) return "";
  return `<div class="loc-outlook" aria-label="Ensemble hazard outlook">${days.map((d) => {
    const top = Object.entries(d.hazards || {}).filter(([, v]) => v.colour !== "green")
      .sort((a, b) => RANK[b[1].colour] - RANK[a[1].colour] || Math.max(b[1].p_amber, b[1].p_red) - Math.max(a[1].p_amber, a[1].p_red))[0];
    const dt = new Date(d.day + "T00:00:00Z");
    return `<div class="loc-od col-${esc(d.colour)}"><b>${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][dt.getUTCDay()]} ${dt.getUTCDate()}</b>
      <span>${esc(d.colour.toUpperCase())}</span><span class="small">${top ? `${esc(HAZ_NAMES[top[0]] || top[0])} ${num(Math.max(top[1].p_amber, top[1].p_red) * 100)}%` : "no driver"}</span></div>`;
  }).join("")}</div>`;
}

function trendText(tr) {
  if (!tr) return "";
  const line = (name, x) => {
    if (!x || !x.days) return "";
    const a = x.tmean_anomaly_c;
    const t = a == null ? `mean ${deg(x.tmean_c)}` : `${num(Math.abs(a), 1)} °C ${a >= 0 ? "warmer" : "colder"} than normal (mean ${deg(x.tmean_c)})`;
    const r = x.prcp_mm == null ? "" : `; ${num(x.prcp_mm, 1)} mm of precipitation${x.normal_prcp_mm != null ? ` against ${num(x.normal_prcp_mm, 1)} mm normal` : ""}`;
    const rec = [...(x.above_record_high || []).map((d) => `${d} above the record high`), ...(x.below_record_low || []).map((d) => `${d} below the record low`)];
    return `<li><b>${name}:</b> ${t}${r}.${rec.length ? ` ${esc(rec.join("; "))}.` : ""}</li>`;
  };
  return `<ul class="thresh-list">${line("Past 7 days", tr.past_7_days)}${line("Next 7 days", tr.next_7_days)}</ul>
    <p class="small">The past days are the models' own analysis, not station observations. Records are the ERA5 grid cell's.</p>`;
}

function dailyTable(doc) {
  const d = doc.daily || {};
  const today = doc.today?.date;
  const sun = Object.fromEntries((doc.ensemble_days || []).map((x) => [x.day, x]));
  const rows = (d.date || []).map((date, i) => {
    if (today && date < today) return "";
    const e = sun[date] || {};
    const rec = d.record_tmax_c?.[i] != null ? `${num(d.record_tmax_c[i], 1)} (${esc(d.record_tmax_year[i])}) / ${num(d.record_tmin_c[i], 1)} (${esc(d.record_tmin_year[i])})` : "–";
    return `<tr><th scope="row">${esc(date)}</th><td>${esc(WMO[d.weather_code?.[i]] || "–")}</td>
      <td class="mono">${num(d.temperature_2m_max?.[i], 1)} / ${num(d.temperature_2m_min?.[i], 1)}</td>
      <td class="mono">${d.normal_tmax_c?.[i] != null ? `${num(d.normal_tmax_c[i], 1)} / ${num(d.normal_tmin_c[i], 1)}` : "–"}</td>
      <td class="mono">${rec}</td>
      <td class="mono">${num(d.precipitation_sum?.[i], 1)} mm${d.precipitation_probability_max?.[i] != null ? ` (${num(d.precipitation_probability_max[i])}%)` : ""}</td>
      <td class="mono">${num(d.wind_gusts_10m_max?.[i])} km/h ${esc(compass(d.wind_direction_10m_dominant?.[i]))}</td>
      <td class="mono">${e.sun ? `${esc(placeTime(e.sun.sunrise, placeTz) || timeOnly(e.sun.sunrise))} / ${esc(placeTime(e.sun.sunset, placeTz) || timeOnly(e.sun.sunset))}` : "–"}</td>
      <td>${e.night ? `${esc(e.night.moon_phase || "")} ${num((e.night.moon_lit || 0) * 100)}%` : "–"}</td></tr>`;
  }).join("");
  return `<div class="table-wrap loc-table-wrap" tabindex="0"><table class="loc-table"><thead><tr><th scope="col">Day (local)</th><th scope="col">Weather</th>
    <th scope="col">High / low °C</th><th scope="col">Normal</th><th scope="col">Record high / low (year)</th><th scope="col">Precipitation (chance)</th>
    <th scope="col">Max gust</th><th scope="col">Sunrise / sunset (local)</th><th scope="col">Moon</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function tafBlock(doc, nowMs) {
  const t = doc.now?.taf;
  const st = tafState(t, nowMs);
  if (st === "current") {
    return `<h3 class="loc-h">TAF <span class="small">issued ${esc(when(t.issued))}; valid ${esc(when(t.valid_from))} to ${esc(when(t.valid_to))}</span></h3>
      <p class="loc-raw mono">${esc(t.raw)}</p>`;
  }
  const why = st === "ended" ? ` The last one Vis holds ran to ${esc(when(t.valid_to))}.` : "";
  return `<h3 class="loc-h">TAF</h3><p class="small">No current TAF from ${esc(doc.icao)}.${why}</p>`;
}

const FALLBACK_SOURCES = 'forecast and elevation: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo.com</a> (CC BY 4.0); METAR and TAF: NOAA Aviation Weather Center (public domain); normals and records: ERA5 via Open-Meteo, contains modified Copernicus Climate Change Service information; sun and moon computed by Vis';

// The source line: each source once, linked, with its licence and any notice it asks for.
function sourceLine(doc) {
  const seen = new Set(), parts = [], notes = new Set();
  for (const s of doc.sources || []) {
    const text = s.attribution || s.name;
    if (s.disclaimer) notes.add(s.disclaimer);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    const lic = s.licence ? `, ${esc(s.licence)}` : "";
    parts.push(/^https:\/\//.test(s.url || "") ? `<a href="${esc(s.url)}" rel="noopener">${esc(text)}</a>${lic}` : `${esc(text)}${lic}`);
  }
  return `<p class="small loc-src">Sources: ${parts.join(" · ") || FALLBACK_SOURCES}. ${[...notes].map(esc).join(" ")}
    Not an official forecast and not for flight planning; every threshold is indicative.
    <a href="data/locations/${esc(doc.id)}.json">This point's data (JSON)</a>.</p>`;
}

function render(doc, body) {
  const h = doc.hourly || {};
  const t = (h.time || []).map((s) => Date.parse(iso(s)));
  const nowMs = Date.now();
  placeTz = window.__visPoints?.[doc.id]?.tz || doc.site?.timezone || null;
  const ticks = dayTicks(doc);
  body.innerHTML = `
    ${doc.errors?.length ? `<p class="stale">${esc(doc.errors.filter((e) => !e.startsWith("climate")).length ? "Some of this location's data could not be fetched in the last build; the parts shown are what came back." : "Climatology for this point is still being built (a few points a day); normals and records follow.")}</p>` : ""}
    <div class="loc-cards">${nowCard(doc, nowMs)}${todayCard(doc)}${sunCard(doc)}${siteCard(doc)}</div>
    <h3 class="loc-h">Ensemble outlook <span class="small">(the table's stoplight for this point)</span></h3>${outlook(doc)}
    <h3 class="loc-h">Forecast and the past week <span class="small">(Open-Meteo best-match models; times in your zone, days in the station's)</span></h3>
    <div id="loc-charts"></div>
    <h3 class="loc-h">Trend against normal</h3>${trendText(doc.trends)}
    <h3 class="loc-h">Day by day</h3>${dailyTable(doc)}
    ${tafBlock(doc, nowMs)}
    ${sourceLine(doc)}`;
  const charts = body.querySelector("#loc-charts");
  if (t.length) {
    const normLo = perHour(doc, t, "normal_tmin_c"), normHi = perHour(doc, t, "normal_tmax_c");
    const recHi = perHour(doc, t, "record_tmax_c"), recLo = perHour(doc, t, "record_tmin_c");
    const haveNorm = normLo.some((v) => v != null);
    charts.appendChild(chart({
      t, now: nowMs, dayTicks: ticks, unitLeft: "°C", title: "Temperature",
      band: haveNorm ? { lo: normLo, hi: normHi, cls: "c-norm", label: "Normal range" } : null,
      series: [
        ...(haveNorm ? [{ y: recHi, cls: "c-rec", label: "Record high", unit: " °C" }, { y: recLo, cls: "c-rec c-rec-lo", label: "Record low", unit: " °C" }] : []),
        { y: h.dew_point_2m || [], cls: "c-dew", label: "Dew point", unit: " °C" },
        { y: h.temperature_2m || [], cls: "c-temp", label: "Temperature", unit: " °C" },
      ],
      hlines: [{ y: 0, cls: "c-zero", include: false }],
    }));
    const amber = hazards.wind?.amber, red = hazards.wind?.red;
    charts.appendChild(chart({
      t, now: nowMs, dayTicks: ticks, unitLeft: "km/h", title: "Wind", floor0: true,
      series: [
        { y: h.wind_speed_10m || [], cls: "c-wind", label: "Wind", unit: " km/h", digits: 0 },
        { y: h.wind_gusts_10m || [], cls: "c-gust", label: "Gusts", unit: " km/h", digits: 0 },
      ],
      // The lines are drawn only when the gusts reach them; the key names them, indicative, either way.
      hlines: [{ y: amber, cls: "c-amber", label: amber ? `Amber ${amber}, indicative` : "", key: amber ? `Amber ${amber} km/h (indicative)` : "", include: false },
        { y: red, cls: "c-red", label: red ? `Red ${red}, indicative` : "", key: red ? `Red ${red} km/h (indicative)` : "", include: false }],
    }));
    const pr = h.precipitation || [];
    const pMax = Math.max(1, ...pr.filter((v) => v != null));
    charts.appendChild(chart({
      t, now: nowMs, dayTicks: ticks, unitLeft: "mm/h", title: "Precipitation", floor0: true,
      right: { lo: 0, hi: 100, ticks: [0, 50, 100], unit: "%" },
      series: [
        { y: pr, cls: "c-rain", label: "Precipitation", unit: " mm", kind: "bar" },
        { y: h.precipitation_probability || [], cls: "c-pop", label: "Chance", unit: "%", axis: "right", digits: 0 },
      ],
      hlines: [{ y: pMax, cls: "c-none", include: true }],
    }));
    charts.appendChild(chart({
      t, now: nowMs, dayTicks: ticks, unitLeft: "hPa", title: "Pressure and cloud",
      right: { lo: 0, hi: 100, ticks: [0, 50, 100], unit: "%" },
      series: [
        { y: h.cloud_cover || [], cls: "c-cloud", label: "Cloud", unit: "%", kind: "area", axis: "right", digits: 0 },
        { y: h.cloud_cover_low || [], cls: "c-lowcloud", label: "Low cloud", unit: "%", axis: "right", digits: 0 },
        { y: h.pressure_msl || [], cls: "c-pres", label: "Pressure", unit: " hPa" },
      ],
    }));
  } else {
    charts.innerHTML = `<p class="small">The hourly forecast for this point could not be fetched in the last build.</p>`;
  }
  const m = doc.site?.monthly_normals;
  const box = body.querySelector("#loc-clim");
  if (m && box) {
    box.innerHTML = `<p class="small">The year at this place, ${esc(doc.site.normals_period)}: precipitation bars (mm a month, right), mean, high and low (°C, left).</p>`;
    box.appendChild(climateChart(m));
  }
}

async function load(id) {
  if (cache.has(id)) return cache.get(id);
  const p = fetch(`data/locations/${encodeURIComponent(id)}.json`).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
  cache.set(id, p);
  p.catch(() => cache.delete(id));
  return p;
}

function dialog() { return document.getElementById("loc-dialog"); }

async function open(id, from) {
  const dlg = dialog();
  if (!dlg || !id) return;
  current = id;
  opener = from || document.activeElement;
  const body = document.getElementById("loc-body");
  const row = window.__visPoints?.[id];
  document.getElementById("loc-title").textContent = row ? `${row.station} · ${row.icao}` : id;
  document.getElementById("loc-zone").textContent = row ? `${row.name}${row.role === "alternate" ? " · alternate point" : ""}` : "";
  document.getElementById("loc-sub").textContent = row ? `${row.lat.toFixed(3)}, ${row.lon.toFixed(3)}` : "";
  body.innerHTML = `<p class="small">Loading…</p>`;
  if (!dlg.open) dlg.showModal();
  if (location.hash !== `#loc-${id}`) history.replaceState(null, "", `#loc-${id}`);
  try {
    const doc = await load(id);
    if (current !== id) return;
    render(doc, body);
  } catch (e) {
    body.innerHTML = `<p class="stale">This location's detail is not available yet (${esc(e.message)}). It is built with each forecast run.</p>`;
  }
}

function close() {
  const dlg = dialog();
  if (dlg?.open) dlg.close();
}

function onClose() {
  current = null;
  if (location.hash.startsWith("#loc-")) history.replaceState(null, "", location.pathname + location.search);
  if (opener && typeof opener.focus === "function") opener.focus();
}

function fromHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("loc-")) return h.slice(4);
  return null;
}

export function setup({ hazardList = [], points = [] } = {}) {
  hazards = Object.fromEntries(hazardList.map((h) => [h.id, h]));
  window.__visPoints = Object.fromEntries(points.map((z) => [z.id, z]));
  const dlg = dialog();
  if (dlg && !dlg.dataset.ready) {
    dlg.dataset.ready = "1";
    document.getElementById("loc-close").addEventListener("click", close);
    dlg.addEventListener("close", onClose);
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });   // a click on the backdrop
    document.addEventListener("click", (e) => {
      const b = e.target.closest?.("[data-loc]");
      if (b) { e.preventDefault(); open(b.getAttribute("data-loc"), b); }
    });
    window.addEventListener("hashchange", () => {
      const h = decodeURIComponent(location.hash.slice(1));
      if (h.startsWith("loc-")) open(h.slice(4));
      else if (h.startsWith("zone-") && window.__visPoints?.[h.slice(5)]) open(h.slice(5));   // the map's marker link
    });
    document.addEventListener("vis:select", (e) => open(e.detail?.id));
    window.VisDetail = { open: (id) => open(id), close };
    if (T) T.onChange(() => { if (current) { const id = current; current = null; open(id); } });
  }
  const id = fromHash();
  if (id) open(id);
}
