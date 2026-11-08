"""Build Vis's static site (site/index.html, site/style.css, site/vis.js, site/data/forecast.json, site/data/feed.geojson).

    python tools/build_site.py [--in out/forecast.json] [--out site]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import feed_v2  # noqa: E402

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vis: operational weather and illumination · The Intelligence Company</title>
  <meta name="description" content="7-day multi-model ensemble weather hazard likelihood and night illumination for each conflict zone.">
  <link rel="canonical" href="https://vis.theintelligence.company/">
  <link rel="preload" href="fonts/ibm-plex-sans-latin.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="tic-nav.css">
  <link rel="stylesheet" href="tic-time.css">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="site-head">
    <div class="wrap head-inner">
      <div>
        <p class="eyebrow">The Intelligence Company · Physical Environment</p>
        <h1>Vis <span class="sub">Operational weather &amp; illumination</span></h1>
        <p class="lede">How likely the next seven days’ weather is to degrade operations at a reference airfield or port in each conflict zone, from 82 ensemble members (NOAA GEFS 31, ECMWF IFS 51), and tonight’s light.</p>
      </div>
      <div class="meta-box">
        <div id="vis-stamp" class="stamp">Loading forecast…</div>
        <div><span id="time-settings"></span></div>
        <div class="links">
          <a href="data/feed.geojson">Feed (GeoJSON)</a> ·
          <a href="data/forecast.json">Forecast data (JSON)</a>
        </div>
      </div>
    </div>
  </header>

  <main class="wrap">
    <p id="vis-stale" class="stale" role="status" hidden></p>
    <section class="legend-bar" aria-label="Colour key">
      <div class="matrix-pills">
        <span class="pill col-green"><b>Green</b> · No significant impact</span>
        <span class="pill col-yellow"><b>Yellow</b> · Be aware</span>
        <span class="pill col-amber"><b>Amber</b> · Be prepared</span>
        <span class="pill col-red"><b>Red</b> · Take action</span>
      </div>
      <div class="chips-note">
        <span class="chip chip-conf" title="Set by rule: two independent forecast centres (NOAA and ECMWF). It stays at moderate until Vis's forecasts have been scored against observations.">Confidence: moderate (by rule; not yet scored)</span>
      </div>
    </section>

    <section class="table-wrap" aria-label="Seven-day operational weather and illumination by zone">
      <table id="vis-table" class="vis-table">
        <thead id="vis-thead"></thead>
        <tbody id="vis-tbody"></tbody>
      </table>
    </section>

    <section class="method-grid" aria-label="Operational thresholds and method">
      <div class="card">
        <h2>Hazard thresholds</h2>
        <p class="small">Each day column is a UTC day. Times show in your own time zone (the Time button changes it).</p>
        <p class="small">Hourly hazards must last at least 3 hours in a UTC day (2 for freezing rain), so a brief shower or gust does not colour the whole day.</p>
        <ul id="vis-thresholds" class="thresh-list"></ul>
      </div>
      <div class="card">
        <h2>How to read the table</h2>
        <ul class="thresh-list">
          <li><b>Reference points:</b> each zone is forecast at one named airfield or port (its ICAO code is shown), not at the zone’s geographic centre, which can sit on a mountain range or in empty desert. The codes also let the forecasts be checked against the station’s METAR reports later; that scoring is not built yet.</li>
          <li><b>Likelihood and colour:</b> a percentage is the share of ensemble members that reach a threshold, given with its ICD 203 term. Most hazards use all 82 members; visibility uses GEFS’s 31 and low cloud ECMWF’s 51, because each model publishes only one of them. Colours follow the UK Met Office likelihood-by-impact matrix.</li>
          <li><b>Tonight’s light:</b> civil dusk and dawn, the moon’s lit fraction at local midnight, and low-light dark: the time with the sun more than 12° below the horizon and the moon down or under a quarter lit.</li>
        </ul>
      </div>
    </section>

    <section class="method-grid" aria-label="About Vis and its sources">
      <div class="card" id="about">
        <h2>About Vis</h2>
        <p class="small"><b>What it is.</b> A program of The Intelligence Company. For each of <a href="https://watchfloor.theintelligence.company">Watchfloor</a>’s conflict zones, Vis estimates how likely the coming week’s weather is to degrade air, ISR, maritime or ground operations, names the hazard behind it, and gives tonight’s light.</p>
        <p class="small"><b>Method.</b> Twice a day, after the 00Z and 12Z ensemble runs reach Open-Meteo, Vis reads every member’s hourly forecast at each point and counts the members that reach each hazard’s amber and red thresholds. Sun and moon are computed with Meeus’s formulas; checked against the US Naval Observatory at four points on 31 Oct 2026, sun times agreed within 1 minute and moon times within 2.</p>
        <p class="small"><b>Limits.</b></p>
        <ul class="thresh-list" id="vis-limits">
          <li>Not an official forecast, and not for flight planning or go/no-go decisions. Use your weather unit or the national meteorological service.</li>
          <li>Thresholds are indicative. Joint doctrine (JP 3-59) names the parameters that matter but publishes no numeric limits, and real limits depend on the platform.</li>
          <li>One point per zone on a model grid of about 25 km, so local effects such as valley fog, sea breezes and single thunderstorms are not resolved. There is no thunderstorm hazard yet.</li>
          <li>Visibility comes from GEFS alone, where it is a diagnosed field that often runs too low in fog and rain.</li>
          <li>Forecasts are not yet scored against observations, so their calibration is unmeasured.</li>
        </ul>
        <p class="small"><b>Terms.</b> Provided as is, without warranty. Code under the GNU AGPL-3.0; data under its sources’ licences, below.</p>
      </div>
      <div class="card" id="sources">
        <h2>Sources</h2>
        <ul class="thresh-list">
          <li><b>Ensemble forecasts:</b> <a href="https://open-meteo.com/en/docs/ensemble-api" rel="noopener">Open-Meteo Ensemble API</a>, CC BY 4.0, free for non-commercial use.</li>
          <li><b>NOAA NCEP GEFS</b> 0.25°, 31 members: US public domain.</li>
          <li><b>ECMWF IFS ensemble</b> 0.25°, 51 members: ECMWF open data, CC BY 4.0.</li>
          <li><b>Sun and moon:</b> computed by Vis from Jean Meeus, <i>Astronomical Algorithms</i>.</li>
          <li><b>Zones:</b> Watchfloor’s conflict zones. <b>Reference points:</b> chosen by Vis (<a href="https://github.com/metacog-osint/vis/blob/main/config/points.json" rel="noopener">list</a>).</li>
          <li><b>Fonts:</b> IBM Plex, SIL Open Font License.</li>
        </ul>
      </div>
    </section>
  </main>

  <footer class="wrap site-foot">
    <p>A project of <a href="https://theintelligence.company">The Intelligence Company</a>. Forecast generated <span id="vis-asof">…</span>. <a href="https://github.com/metacog-osint/vis" rel="noopener">Source code</a> (GNU AGPL-3.0). Weather data: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo</a> (CC BY 4.0), from NOAA GEFS and ECMWF IFS. <a href="#about">About and limits</a> · <a href="#sources">Sources</a>.</p>
  </footer>

  <script src="tic-time.js"></script>
  <script src="tic-nav.js" data-current="vis" defer></script>
  <script type="module" src="vis.js"></script>
</body>
</html>
"""

STYLE_CSS = """@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 400 700; font-display: swap;
  src: url("fonts/ibm-plex-sans-latin.woff2") format("woff2"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+2074, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 400 700; font-display: swap;
  src: url("fonts/ibm-plex-sans-latin-ext.woff2") format("woff2"); unicode-range: U+0100-024F, U+0259, U+1E00-1EFF, U+2020, U+20A0-20AB, U+20AD-20CF, U+2113, U+2C60-2C7F, U+A720-A7FF; }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400; font-display: swap;
  src: url("fonts/ibm-plex-mono-400-latin.woff2") format("woff2"); unicode-range: U+0000-00FF, U+2000-206F, U+2212; }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 500; font-display: swap;
  src: url("fonts/ibm-plex-mono-500-latin.woff2") format("woff2"); unicode-range: U+0000-00FF, U+2000-206F, U+2212; }
:root {
  --bg: #FAFAF7;
  --card: #FFFFFF;
  --ink: #14233C;
  --muted: #4E5D73;
  --rule: #DCE1E7;
  --accent: #0B4F6C;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.48 "IBM Plex Sans", system-ui, -apple-system, sans-serif;
}
.wrap {
  max-width: 1380px;
  margin: 0 auto;
  padding: 0 20px;
}
.site-head {
  background: var(--card);
  border-bottom: 1px solid var(--rule);
  padding: 20px 0 18px;
}
.head-inner {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 20px;
  flex-wrap: wrap;
}
.eyebrow {
  margin: 0 0 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}
h1 {
  margin: 0 0 6px;
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-size: 28px;
  font-weight: 600;
  color: var(--ink);
}
h1 .sub {
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 17px;
  font-weight: 400;
  color: var(--muted);
  margin-left: 8px;
}
.lede {
  margin: 0;
  max-width: 780px;
  color: var(--muted);
}
.meta-box {
  text-align: right;
  font-size: 12px;
  color: var(--muted);
}
.meta-box a { color: var(--accent); text-decoration: none; }
.meta-box a:hover { text-decoration: underline; }
.legend-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin: 16px 0 12px;
}
.matrix-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pill {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid rgba(20, 35, 60, 0.12);
}
.col-green  { background: #DDEFD9; color: #1E5B2A; }
.col-yellow { background: #FFF1B8; color: #5C4A00; }
.col-amber  { background: #FFD7A6; color: #7A3B00; }
.col-red    { background: #F7B5B5; color: #7A1010; }
.chip-conf {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px dashed var(--ink);
  background: var(--card);
  color: var(--ink);
}
.table-wrap {
  overflow-x: auto;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 8px;
}
.vis-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 3px;
}
.vis-table th, .vis-table td {
  padding: 7px 9px;
  vertical-align: top;
  text-align: left;
  border-radius: 4px;
}
.vis-table thead th {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  background: #F3F4F1;
}
.vis-table tbody th {
  background: #F3F4F1;
  min-width: 185px;
  font-weight: 600;
}
.station-meta {
  display: block;
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
  margin-top: 2px;
}
.day-cell { min-width: 124px; }
.day-head {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 2px;
}
.driver-line {
  font-size: 11.5px;
  line-height: 1.35;
}
.night-cell {
  background: #EEF1F5;
  color: #243347;
  min-width: 220px;
  font-size: 12px;
}
.mono { font-family: "IBM Plex Mono", monospace; }
.method-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
  margin: 18px 0 28px;
}
.card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 16px 18px;
}
.card h2 {
  margin: 0 0 8px;
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-weight: 600;
  font-size: 18px;
}
.small { font-size: 12.5px; color: var(--muted); margin: 0 0 8px; }
.thresh-list { margin: 0; padding-left: 18px; font-size: 13px; }
.thresh-list li { margin-bottom: 6px; }
.site-foot {
  padding: 14px 20px 28px;
  font-size: 12px;
  color: var(--muted);
}
.site-foot a { color: var(--accent); }
.card a { color: var(--accent); }
.stale {
  margin: 14px 0 0;
  padding: 9px 12px;
  border: 1px solid #E0A867;
  border-radius: 6px;
  background: #FFE6C7;
  color: #5A2C00;
  font-size: 13px;
}
.stale[hidden] { display: none; }
.vis-table tbody th.alt-head {
  font-weight: 400;
  color: var(--muted);
  padding-left: 20px;
}
.vis-table tbody tr.ref-row th { border-top: 2px solid var(--rule); }
.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
"""

VIS_JS = """import { likelihoodLabel } from "./vendor/tic-doctrine.js";

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
"""

HEADERS = """/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: geolocation=(), camera=(), microphone=()
  Strict-Transport-Security: max-age=31536000
/data/*
  Cache-Control: no-cache
"""


def build(forecast_doc: dict, out_dir: Path | None = None) -> dict[str, str]:
    fc = feed_v2.build_feed(forecast_doc)
    files = {
        "index.html": INDEX_HTML,
        "style.css": STYLE_CSS,
        "vis.js": VIS_JS,
        "_headers": HEADERS,
        "data/forecast.json": json.dumps(forecast_doc, ensure_ascii=False, indent=1) + "\n",
        "data/feed.geojson": json.dumps(fc, ensure_ascii=False, indent=1) + "\n",
    }
    if out_dir is not None:
        for rel, content in files.items():
            p = out_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="\n")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inp", default=str(ROOT / "out" / "forecast.json"))
    ap.add_argument("--out", default=str(ROOT / "site"))
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    out_dir = Path(a.out)
    files = build(doc, out_dir)
    print(f"built {len(files)} files in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
