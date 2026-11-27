"""Build Vis's static site (site/index.html, site/style.css, site/vis.js, site/data/forecast.json, site/data/feed.geojson,
and, when out/locations.json exists, site/data/locations.json and site/data/locations/<point id>.json).

    python tools/build_site.py [--in out/forecast.json] [--out site] [--locations out/locations.json]

site/locdetail.js and site/locdetail.css (the point pop-up) are plain files, not generated: edit them in place.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import feed_v2  # noqa: E402
import gefs  # noqa: E402
import locations  # noqa: E402
import verify  # noqa: E402
import wxmap  # noqa: E402

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23142235'/%3E%3Ccircle cx='16' cy='16' r='9' fill='none' stroke='%23e3a83b' stroke-width='3'/%3E%3Ccircle cx='16' cy='16' r='3' fill='%23e3a83b'/%3E%3C/svg%3E">
  <title>Vis: operational weather and illumination · The Intelligence Company</title>
  <meta name="description" content="7-day multi-model ensemble weather hazard likelihood and night illumination for each conflict zone.">
  <link rel="canonical" href="https://vis.theintelligence.company/">
  <link rel="preload" href="fonts/ibm-plex-sans-latin.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="tic-nav.css">
  <link rel="stylesheet" href="tic-time.css">
  <link rel="stylesheet" href="tic-tabs.css">
  <link rel="stylesheet" href="tic-fold.css">
  <link rel="stylesheet" href="style.css">
  <link rel="alternate" type="application/geo+json" title="Vis feed (GeoJSON)" href="data/feed.geojson">
  <link rel="stylesheet" href="locdetail.css">
</head>
<body>
  <a class="skip" href="#main">Skip to content</a>
  <header class="site-head">
    <div class="wrap head-inner">
      <div>
        <p class="eyebrow">The Intelligence Company · Physical Environment</p>
        <h1>Vis <span class="sub">Operational weather &amp; illumination</span></h1>
        <p class="lede">How likely the next seven days’ weather is to degrade operations at a reference airfield or port in each conflict zone, from up to 82 ensemble members (NOAA GEFS 31, ECMWF IFS 51), and tonight’s light.</p>
        <p class="caveat">These are forecasts with indicative thresholds, not an official forecast and not for flight planning. <a href="https://github.com/metacog-osint/vis/blob/main/doctrine/method.md" rel="noopener">How Vis reads them</a>.</p>
        <details class="terms" id="terms">
          <summary>Terms on this page</summary>
          <dl>
            <dt>Ensemble members</dt><dd>One model run many times from slightly different starting conditions. The share of members that reach a threshold is the percentage shown.</dd>
            <dt>GEFS, ECMWF IFS</dt><dd>NOAA’s Global Ensemble Forecast System (31 members) and the European Centre for Medium-Range Weather Forecasts’ Integrated Forecasting System ensemble (51 members).</dd>
            <dt>00Z, 12Z, UTC</dt><dd>Times in Coordinated Universal Time (Z, “Zulu”). Each day column is a UTC day.</dd>
            <dt>ICD 203 term</dt><dd>The fixed likelihood words of the US intelligence community’s analytic standards (Intelligence Community Directive 203), such as “very unlikely” for 5 to 20%.</dd>
            <dt>Green, Yellow, Amber, Red</dt><dd>The UK Met Office’s likelihood-by-impact colours: no significant impact, be aware, be prepared, take action.</dd>
            <dt>ICAO code</dt><dd>The four-letter airport code from the International Civil Aviation Organization, as in UKBB for Kyiv Boryspil.</dd>
            <dt>METAR</dt><dd>An airfield’s routine weather observation report.</dd>
            <dt>ISR, UAS, VFR</dt><dd>Intelligence, surveillance and reconnaissance; uncrewed aircraft systems (drones); visual flight rules, flying by sight rather than instruments.</dd>
            <dt>JP 3-59</dt><dd>US joint doctrine publication 3-59, Meteorological and Oceanographic Operations.</dd>
            <dt>DRC, M23, NATO</dt><dd>The Democratic Republic of the Congo; the March 23 Movement, an armed group in its east; the North Atlantic Treaty Organization.</dd>
            <dt>Civil dusk and dawn</dt><dd>When the sun is 6° below the horizon, the end and start of usable daylight.</dd>
          </dl>
        </details>
      </div>
      <div class="meta-box">
        <div id="vis-stamp" class="stamp">Loading forecast…</div>
        <div><span id="time-settings"></span></div>
      </div>
    </div>
  </header>

  <main id="main" class="wrap">
    <noscript><p class="noscript">This page needs JavaScript to draw its forecasts.</p></noscript>
    <p id="vis-stale" class="stale" role="status" hidden></p>
    <!-- Sections row (tic-tabs.js from the hub): one group at a time on a phone, a jumping row wider -->
    <div data-tic-tabs>
    <div id="sec-forecast" data-tab-label="Forecast">
    <section class="wx-card" id="weather-map" aria-labelledby="wx-title">
      <div class="wx-top">
        <div>
          <h2 id="wx-title">Weather map</h2>
          <p class="small" id="wx-note">Recent satellite imagery, radar, lightning, the jet stream, snow, smoke and hail, with Vis’s forecast points coloured by its outlook for the current UTC day.</p>
        </div>
        <button type="button" id="wx-open" class="wx-btn wx-open">Show the map</button>
        <div class="wx-loop" id="wx-loop" hidden>
          <button type="button" id="wx-play" class="wx-btn wx-play">Play loop</button>
          <input type="range" id="wx-frame" min="0" max="0" value="0" disabled aria-label="Frame">
          <span id="wx-time" class="mono wx-time" aria-live="polite">–</span>
        </div>
      </div>
      <div id="wx-body" hidden>
        <div class="wx-controls">
          <div id="wx-products" class="wx-products" role="group" aria-label="Imagery"></div>
        </div>
        <div id="wx-overlays" class="wx-overlays" role="group" aria-label="Overlays"><label class="wx-check"><input type="checkbox" id="wx-points" checked> Forecast points</label></div>
        <p id="wx-otime" class="small wx-otime" hidden></p>
        <div id="wx-map" class="wx-map" role="region" aria-label="Weather map"></div>
        <ul id="wx-overlay-notes" class="small wx-notes" hidden></ul>
        <p id="wx-status" class="small wx-status" role="status" hidden></p>
      </div>
      <p class="small wx-foot">The map loads only when you open it (about 2 MB of imagery); a loop loads only when you press Play. What it shows was measured, then reached the map later: NASA’s GOES and Himawari imagery about an hour after the scan, Meteosat about 30 minutes, radar a few minutes, IMERG precipitation several hours. Each frame names its own time. Jet stream, snow, smoke and hail come from the last site build. Not for flight planning.</p>
    </section>

    <section class="legend-bar" aria-label="Colour key">
      <div class="matrix-pills">
        <span class="pill col-green"><b>Green</b> · No significant impact</span>
        <span class="pill col-yellow"><b>Yellow</b> · Be aware</span>
        <span class="pill col-amber"><b>Amber</b> · Be prepared</span>
        <span class="pill col-red"><b>Red</b> · Take action</span>
      </div>
      <div class="chips-note">
        <span class="chips-hint">Select a forecast point’s name for its forecast, charts and climate.</span>
        <span class="chip chip-conf" title="Set by rule from the forecast centres that supply the hazard: moderate when both NOAA and ECMWF do, low when only one does (visibility comes from NOAA alone, low cloud from ECMWF alone, and a centre whose run is over 24 hours old is left out). It goes no higher until the forecasts' calibration is measured: the scores against METARs in “Measured skill” have started, and most of their rows are still too few to read.">Confidence: moderate with two centres, low with one (set by rule, not from the scores)</span>
      </div>
    </section>

    <!-- A window about ten rows tall that scrolls, header kept in view, with "Show all" (tic-fold.js from the hub) -->
    <div class="table-wrap" tabindex="0">
      <table id="vis-table" class="vis-table" data-tic-fold aria-label="Seven-day operational weather and illumination by zone">
        <thead id="vis-thead"></thead>
        <tbody id="vis-tbody"></tbody>
      </table>
    </div>
    </div>

    <div id="sec-method" data-tab-label="How to read it">
    <section class="method-grid" aria-label="Operational thresholds and method">
      <div class="card">
        <h2>Hazard thresholds</h2>
        <p class="small">Each day column is a UTC day. Times show in your own time zone (the Time button changes it).</p>
        <p class="small">Both models step every 3 hours (Open-Meteo serves the hours between interpolated), so a hazard must hold on two consecutive 3-hourly steps in a UTC day; a brief shower or gust does not colour the whole day.</p>
        <ul id="vis-thresholds" class="thresh-list"></ul>
      </div>
      <div class="card">
        <h2>How to read the table</h2>
        <ul class="thresh-list">
          <li><b>Reference points:</b> each zone is forecast at a named airfield or port (its ICAO code is shown), not at the zone’s geographic centre, which can sit on a mountain range or in empty desert. Most zones also have an alternate airfield or port, forecast as a row of its own. The codes also let each forecast be checked against the station’s METAR reports (see “Measured skill” below).</li>
          <li><b>Likelihood and colour:</b> a percentage is the share of ensemble members that reach a threshold, each model weighted equally (its own share is shown beside it), given with its ICD 203 term. Most hazards use both models; visibility uses GEFS’s 31 and low cloud ECMWF’s 51, because each model publishes only one of them. The low ceiling trial uses GEFS’s 31, read from NOAA’s own files on AWS Open Data, since neither ensemble on Open-Meteo carries cloud base. Colours follow the UK Met Office likelihood-by-impact matrix.</li>
          <li><b>What sets a cell’s colour:</b> each hazard has an amber and a more severe red threshold, and the colour comes from the chance of reaching either one. Yellow: at least 20% for the amber threshold, or at least 5% for the red one. Amber: at least 45% for amber, or 20% for red. Red: at least 45% for red. A severe outcome therefore colours a cell at a low chance, and the line names the threshold behind the colour. Example: “Low cloud cover ≥ 95 %: Very unlikely · 6% (severe threshold)” is Yellow, because 6% is above the 5% needed for the severe threshold. The day’s colour is its worst hazard. Low cloud cover, and the low ceiling while it is on trial, never go above Yellow.</li>
          <li><b>Tonight’s light:</b> sunset, civil dusk and dawn and the next sunrise, the moon’s phase and lit fraction at local midnight, and low-light dark: the time with the sun more than 12° below the horizon and the moon down or under a quarter lit. Each time shows the point’s local time, then yours.</li>
          <li><b>A point’s detail:</b> select its name to open its latest METAR and TAF, a deterministic forecast for the next 7 days beside the past 7, today against its 1991–2020 normal and the 1991–2025 records for the date, sun and moon for the week, its elevation and Köppen climate class. The detail forecast is a single best-estimate model run and can differ from the ensemble colours, which stay the product.</li>
        </ul>
      </div>
    </section>
<!--VIS-VERIFY-->
    </div>

    <div id="sec-about" data-tab-label="About and sources">
    <section class="method-grid" aria-label="About Vis and its sources">
      <div class="card" id="about">
        <h2>About Vis</h2>
        <p class="small"><b>What it is.</b> A program of The Intelligence Company. For each of <a href="https://watchfloor.theintelligence.company">Watchfloor</a>’s conflict zones, Vis estimates how likely the coming week’s weather is to degrade air, ISR, maritime or ground operations, names the hazard behind it, and gives tonight’s light.</p>
        <p class="small"><b>Method.</b> Twice a day, after the 00Z and 12Z ensemble runs reach Open-Meteo, Vis reads every member’s forecast at each point (3-hourly model steps) and counts the members that reach each hazard’s amber and red thresholds. A model whose newest run on Open-Meteo is more than 24 hours old is left out, and the page says so. Today’s column counts only the hours still ahead. Sun and moon are computed with Meeus’s formulas; checked against the US Naval Observatory at four points on 31 Oct 2026, sun times agreed within 1 minute and moon times within 2.</p>
        <p class="small"><b>Limits.</b></p>
        <ul class="thresh-list" id="vis-limits">
          <li>Not an official forecast, and not for flight planning or go/no-go decisions. Use your weather unit or the national meteorological service.</li>
          <li>Thresholds are indicative. Joint doctrine (JP 3-59) names the parameters that matter but publishes no numeric limits, and real limits depend on the platform.</li>
          <li>Each point is read from a model grid of about 25 km, so local effects such as valley fog, sea breezes and single thunderstorms are not resolved. There is no thunderstorm hazard yet.</li>
          <li>Visibility comes from GEFS alone, where it is a diagnosed field that often runs too low in fog and rain.</li>
          <li>Only <!--VIS-CHECKED--> can be checked against METARs, and only at stations that report; the sample since 19 Nov 2026 is small, and “Measured skill” says where it is too small to read.</li>
          <!--VIS-CEILING-LIMITS-->
          <li>On the weather map, only the coloured points are Vis’s forecast. The imagery, radar, lightning, smoke and hail are observations, and the jet stream and snow depth are NOAA GFS model values. Its imagery and radar come straight from NASA, EUMETSAT, NOAA and Environment and Climate Change Canada; when a service is down, its part of the map goes blank. Radar covers only the US, its territories and Canada; lightning only Europe, Africa, the Middle East and Canada; hail reports and smoke only North America. The jet stream, snow, smoke and hail layers are as fresh as the last site build. Radar services keep only their last 2 to 3 hours, so a loop’s older frames can have no radar, and the map says so. The coastlines are coarse (Natural Earth’s 1:110m scale, off by up to about 10 km), so the map stops at a regional zoom. Opening the map has your browser fetch tiles from NASA, EUMETSAT, NOAA and Environment and Climate Change Canada.</li>
        </ul>
        <p class="small"><b>Terms.</b> Provided as is, without warranty. Code under the GNU AGPL-3.0; data under its sources’ licences, below.</p>
      </div>
      <div class="card" id="sources">
        <h2>Sources</h2>
        <ul class="thresh-list">
          <li><b>Ensemble forecasts:</b> <a href="https://open-meteo.com/en/docs/ensemble-api" rel="noopener">Open-Meteo Ensemble API</a>, CC BY 4.0, free for non-commercial use.</li>
          <li><b>NOAA NCEP GEFS</b> 0.25°, 31 members: US public domain.</li>
          <li><b>ECMWF IFS ensemble</b> 0.25°, 51 members: ECMWF open data, CC BY 4.0.</li>
          <li><b>Observations:</b> METARs from the <a href="https://aviationweather.gov/data/api/" rel="noopener">NOAA Aviation Weather Center</a>, US public domain, kept in Vis’s own store: they score the forecasts, and each point’s detail shows the newest one with its age. TAFs from the same service for each point’s detail, while they are valid.</li>
          <li><b>Weather map imagery:</b> <a href="https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api" rel="noopener">NASA GIBS</a> (we acknowledge the use of imagery provided by services from NASA’s Global Imagery Browse Services, part of NASA’s Earth Science Data and Information System): <!--VIS-GIBS-->. <a href="https://view.eumetsat.int/" rel="noopener">EUMETSAT EUMETView</a>: Meteosat imagery (Third Generation at 0°, second generation at 0° and over the Indian Ocean) and the Lightning Imager, CC BY 4.0; contains modified EUMETSAT Meteosat data; also H SAF rain rate and rapidly developing thunderstorms. <b>Radar:</b> <a href="https://opengeo.ncep.noaa.gov/" rel="noopener">NOAA MRMS</a> through NCEP’s open map server, public domain. <b>Canada radar and lightning:</b> Data Source: Environment and Climate Change Canada (<a href="https://eccc-msc.github.io/open-data/licence/readme_en/" rel="noopener">Data Server End-use Licence</a>). <b>Jet stream and snow depth:</b> NOAA GFS through NOMADS, public domain. <b>Hail reports:</b> NOAA Storm Prediction Center preliminary local storm reports. <b>Smoke:</b> NOAA NESDIS Hazard Mapping System. RainViewer radar tiles are not used: RainViewer limits them to personal and educational use.</li>
          <li><b>Map outlines:</b> Natural Earth, public domain. <b>Map library:</b> Leaflet, BSD 2-Clause.</li>
          <li><b>Sun and moon:</b> computed by Vis from Jean Meeus, <i>Astronomical Algorithms</i>.</li>
          <li><b>Point detail forecast:</b> <a href="https://open-meteo.com/en/docs" rel="noopener">Open-Meteo Forecast API</a> (best-match national and global models), CC BY 4.0, free for non-commercial use.</li>
          <li><b>Climatology:</b> ERA5 reanalysis through <a href="https://open-meteo.com/en/docs/historical-weather-api" rel="noopener">Open-Meteo’s Historical Weather API</a>, CC BY 4.0; contains modified Copernicus Climate Change Service information <!--VIS-C3S-YEAR-->; neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains. Normals 1991–2020; records 1991–2025, for the 25 km grid cell, not the station.</li>
          <li><b>Elevation:</b> Copernicus DEM GLO-90 through Open-Meteo’s Elevation API.</li>
          <li><b>Zones:</b> Watchfloor’s conflict zones. <b>Reference points:</b> chosen by Vis (<a href="https://github.com/metacog-osint/vis/blob/main/config/points.json" rel="noopener">list</a>).</li>
          <li><b>Fonts:</b> IBM Plex, SIL Open Font License.</li>
        </ul>
      </div>
    </section>
    </div>
    </div>
  </main>

  <dialog id="loc-dialog" class="loc-dialog" aria-labelledby="loc-title">
    <div class="loc-inner">
      <header class="loc-head">
        <div>
          <p class="eyebrow" id="loc-zone"></p>
          <h2 id="loc-title">Location</h2>
          <p class="station-meta" id="loc-sub"></p>
        </div>
        <button type="button" class="loc-close" id="loc-close" aria-label="Close">×</button>
      </header>
      <div id="loc-body"></div>
    </div>
  </dialog>

  <footer class="wrap site-foot">
    <p>A project of <a href="https://theintelligence.company">The Intelligence Company</a>. Forecast generated <span id="vis-asof">…</span>. <a href="https://github.com/metacog-osint/vis" rel="noopener">Source code</a> (GNU AGPL-3.0). Weather data: <a href="https://open-meteo.com/" rel="noopener">Open-Meteo</a> (CC BY 4.0), from NOAA GEFS and ECMWF IFS. Map imagery: NASA GIBS, EUMETSAT, NOAA and ECCC. <a href="#about">About and limits</a> · <a href="#sources">Sources</a>.</p>
    <nav class="tic-data" aria-label="Data and downloads"><span class="tic-data-k">Data and downloads</span>
      <a href="data/feed.geojson">Feed (GeoJSON)</a>
      <a href="data/forecast.json">Forecast data (JSON)</a>
      <a href="data/locations.json">Locations (JSON)</a>
      <a href="data/verification.json">Scores (JSON)</a>
      <a href="https://github.com/metacog-osint/vis/tree/main/history" rel="noopener">Observations and scored forecasts</a>
      <a href="https://github.com/metacog-osint/vis" rel="noopener">Source code</a></nav>
  </footer>

  <script src="tic-time.js"></script>
  <script src="tic-tabs.js" defer></script>
  <script src="tic-fold.js" defer></script>
  <script src="tic-nav.js" data-current="vis" defer data-health="on"></script>
  <script type="module" src="vis.js"></script>
  <script type="module" src="wxmap.js"></script>
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
/* Terms on this page (ideas#264): a tap target, so a phone reaches it; every number stays where it is. */
.caveat { margin: 8px 0 0; max-width: 780px; font-size: 13px; color: var(--muted); }
.caveat a, .terms a { color: var(--accent); }
.terms { margin: 6px 0 0; max-width: 780px; font-size: 13px; }
.terms summary { cursor: pointer; padding: 6px 0; min-height: 24px; color: var(--ink); }
.terms dl { margin: 4px 0 0; }
.terms dt { font-weight: 600; margin-top: 6px; color: var(--ink); }
.terms dd { margin: 2px 0 0; color: var(--muted); }
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
.table-wrap:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
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
.also { color: #5A6B80; }
/* Sections row (tic-tabs.css) in Vis's colours */
.tic-tabs-nav { --panel: var(--card); --line: var(--rule); --link: var(--accent); }
/* Long-table window and its "Show all" button (tic-fold.css) in Vis's colours */
.tic-fold-box, .tic-fold-bar, .tic-fold-find { --panel: var(--card); --line: var(--rule); --link: var(--accent); }
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
table.skill { border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 0 0 10px; }
table.skill th, table.skill td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
table.skill tr.skill-all td { border-top: 2px solid var(--rule); font-weight: 500; }
.thresh-list { margin: 0; padding-left: 18px; font-size: 13px; }
.thresh-list li { margin-bottom: 6px; }
.cig-reach { display: block; margin-top: 3px; font-family: "IBM Plex Sans", system-ui, sans-serif; font-size: 11px;
  color: var(--ink); text-decoration: underline dotted; text-underline-offset: 2px; cursor: help; }
.reach-head { display: block; margin: 6px 0 2px; }
.reach-list { margin: 2px 0 0; padding-left: 18px; }
.reach-list li { margin-bottom: 3px; }
.site-foot {
  padding: 14px 20px 28px;
  font-size: 12px;
  color: var(--muted);
}
.site-foot a { color: var(--accent); }
/* Footer row for data and code links (hub tic-shell.css v1.1, ideas#84), in Vis's colours */
.tic-data { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 16px; margin: 16px 0 8px; padding-top: 12px;
  border-top: 1px solid var(--rule); font-size: 0.85rem; line-height: 1.4; }
.tic-data-k { font-size: 0.72rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.tic-data a { display: inline-flex; align-items: center; min-height: 24px; text-decoration: underline; text-underline-offset: 2px; }
.tic-data a:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
@media (max-width: 760px) { .tic-data { gap: 2px 14px; } .tic-data-k { flex-basis: 100%; } }
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
/* Weather map (wxmap.js) */
.wx-card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 14px 16px 10px;
  margin: 16px 0 4px;
}
.wx-card h2 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
.wx-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}
.wx-top .small { max-width: 760px; }
.wx-loop { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.wx-loop input[type=range] { width: 150px; accent-color: var(--accent); }
.wx-time { font-size: 12px; color: var(--muted); min-width: 12ch; }
.wx-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  margin: 8px 0 10px;
}
.wx-products { display: flex; flex-wrap: wrap; gap: 6px; }
.wx-btn {
  font: inherit;
  font-size: 12.5px;
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: #F3F4F1;
  color: var(--ink);
  cursor: pointer;
}
.wx-btn[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.wx-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.wx-btn:disabled { opacity: 0.5; cursor: default; }
.wx-play { min-width: 92px; }
.wx-open { font-weight: 600; }
.wx-open[hidden], .wx-loop[hidden], #wx-body[hidden] { display: none; }
.wx-otime { margin: 0 0 6px; color: var(--muted); }
.wx-otime[hidden] { display: none; }
.wx-check { font-size: 12.5px; color: var(--ink); display: inline-flex; align-items: center; gap: 4px; }
.wx-map {
  height: 520px;
  border-radius: 4px;
  background: #101C2C;
  border: 1px solid var(--rule);
}
.wx-map .leaflet-container, .wx-map.leaflet-container { background: #101C2C; font: 12px/1.4 "IBM Plex Sans", system-ui, sans-serif; }
.wx-map .leaflet-control-attribution { font-size: 10.5px; max-width: 70%; }
.wx-map .leaflet-popup-content { font-size: 12.5px; }
.wx-status { margin: 8px 0 0; color: #5A2C00; }
.wx-status[hidden] { display: none; }
.wx-foot { margin: 8px 0 0; }
.wx-overlays { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: -2px 0 10px; }
.wx-notes { margin: 8px 0 0; padding-left: 18px; }
.wx-notes li { margin: 2px 0; }
.wx-notes[hidden] { display: none; }
.wx-canvas { position: absolute; left: 0; top: 0; pointer-events: none; }
.wx-canvas[hidden] { display: none; }
@media (max-width: 640px) {
  .wx-map { height: 360px; }
  .wx-loop input[type=range] { width: 110px; }
}
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
/* Skip link and noscript line (ideas#300) */
.skip { position: absolute; left: -9999px; top: 8px; z-index: 100; background: var(--card); color: var(--accent); padding: 6px 10px; border: 1px solid var(--rule); border-radius: 4px; }
.skip:focus { left: 8px; }
.noscript { margin: 16px 0 0; padding: 10px 12px; border: 1px solid var(--rule); border-radius: 6px; background: var(--card); }
"""

VIS_JS = """import { likelihoodLabel } from "./vendor/tic-doctrine.js";
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
    if (cell) out[z.id] = { left: left.has(z.icao) || left.has(z.id), r: cell.reach || null, z };
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
  if (x.left) return "The low ceiling trial is not read here: the GEFS cell is more than 300 m from the station’s height.";
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
  if (x.left) return "not read (the GEFS cell is more than 300 m from the station’s height).";
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
  return `<span class="small reach-head">Where GEFS’s own ground at the point’s cell (its 0.25° terrain, HGT:surface) differs from the station’s height by more than 50 m, or a threshold is out of reach. The ceiling field sits at least about 17 m above that ground, which bounds the lowest ceiling it can give above the station (this build’s cells):</span><ul class="reach-list">${rows
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
"""

NOT_FOUND_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23142235'/%3E%3Ccircle cx='16' cy='16' r='9' fill='none' stroke='%23e3a83b' stroke-width='3'/%3E%3Ccircle cx='16' cy='16' r='3' fill='%23e3a83b'/%3E%3C/svg%3E">
<title>Not found · Vis</title>
<meta name="description" content="Page not found.">
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/tic-nav.css">
<link rel="stylesheet" href="/tic-time.css">
<link rel="stylesheet" href="/style.css">
<script src="/tic-time.js"></script>
<script src="/tic-nav.js" defer data-current="vis"></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-head">
  <div class="wrap head-inner">
    <div>
      <p class="eyebrow">The Intelligence Company · Physical Environment</p>
      <h1>Vis <span class="sub">Operational weather &amp; illumination</span></h1>
    </div>
  </div>
</header>
<main id="main" class="wrap">
  <section class="card">
    <h2>Not found</h2>
    <p class="lede">That page doesn't exist. It may have moved when the site grew.</p>
    <p><a href="/">Vis home</a> · <a href="https://theintelligence.company/">The Intelligence Company</a></p>
  </section>
</main>
<footer class="wrap site-foot">
  <p>Vis, The Intelligence Company. Public information only, non-commercial.</p>
</footer>
</body>
</html>
"""

HEADERS = """/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://gibs.earthdata.nasa.gov https://view.eumetsat.int https://opengeo.ncep.noaa.gov https://geo.weather.gc.ca; connect-src 'self' https://cop.theintelligence.company; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: geolocation=(), camera=(), microphone=()
  Strict-Transport-Security: max-age=31536000
/data/*
  Cache-Control: no-cache
"""


HAZ_NAMES = {"wind": "Strong wind (gusts)", "vis": "Poor visibility", "heat": "Extreme heat", "cold": "Extreme cold",
             "ceiling": "Low ceiling (trial)"}
# How the "About and limits" list names each hazard tools/verify.py scores (verify.SCORED).
CHECKED_NAMES = {"wind": "wind", "vis": "visibility", "heat": "heat", "cold": "cold",
                 "ceiling": "the low ceiling trial"}


def checked_text(scored=None) -> str:
    """The hazards METARs can check, in verify.SCORED's order, so the limits list follows what is scored."""
    names = [CHECKED_NAMES.get(h) or HAZ_NAMES.get(h, h).lower() for h in (verify.SCORED if scored is None else scored)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def ceiling_unreachable(forecast_doc: dict) -> tuple[list[str], list[str]]:
    """Trial points where the GEFS ceiling cannot reach Amber and Red, and Red only, as "Station (ICAO)"."""
    both, red = gefs.unreachable(forecast_doc)
    name = lambda z: f'{z.get("station")} ({z.get("icao")})'  # noqa: E731
    return [name(z) for z in both], [name(z) for z in red]


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def ceiling_limits_html(forecast_doc: dict) -> str:
    """The Limits list's line on the low ceiling trial: what it is scored against, its two measured limits with the
    points they hit, and what is scored where (Vis's own score, low cloud cover, Verdict's re-score)."""
    from html import escape
    both, red = ceiling_unreachable(forecast_doc)
    where = []
    if both:
        where.append(f"Amber and Red cannot be reached at {_and(both)}")
    if red:
        where.append(f"Red cannot be reached at {_and(red)}")
    where_txt = ", and ".join(where) if where else "no trial point is out of reach in this build"
    return ('<li><b>Low ceiling trial.</b> Vis scores it against the METAR ceiling (the lowest broken, overcast or '
            'obscured layer), but NOAA’s GEFS ceiling field can show only part of what a ceiling forecast should, for '
            'two reasons measured in NOAA’s own files. First, GEFS v12 sets the field only where surface-based CAPE '
            '(convective available potential energy, a measure of how unstable the air near the ground is) is at '
            'least about 0.5 J/kg, so stratus and fog in stable air read as no ceiling. Second, the field cannot sit '
            'lower than about 17 m above the model’s own ground, which at some points is well above the station: '
            f'{escape(where_txt)}; “Hazard thresholds” under “How to read it” lists each point. Its scores therefore measure '
            'these limits as much as ceiling skill. Low cloud cover is not scored: it is a model cloud fraction, and '
            'a METAR gives each layer’s amount in eighths of the sky and its base, not a fraction. Verdict’s '
            'independent re-score of Vis covers wind, visibility, heat and cold, not the ceiling trial.</li>')


def _num(v, fmt="{:.3f}"):
    return "–" if v is None else fmt.format(v)


def verification_html(doc: dict | None) -> str:
    """The "Measured skill" card, rendered at build time from tools/verify.py's scores (escaped, no script)."""
    from html import escape
    head = ('<section class="method-grid" aria-label="Measured skill"><div class="card" id="skill">'
            '<h2>Measured skill</h2>')
    if not doc or not doc.get("scores"):
        return head + '<p class="small">No forecast has been scored yet.</p></div></section>'
    r = doc.get("rules", {})
    out = [head, f'<p class="small">Each forecast since 19 Nov 2026 (nothing earlier was stored) is checked against the '
           f'station’s METAR reports once its UTC day has ended: {escape(str(doc.get("first_day")))} to '
           f'{escape(str(doc.get("last_day")))}, {doc.get("resolved", 0):,} forecasts resolved and '
           f'{doc.get("unresolved", 0):,} unresolved (no reports from the station, too few reports, or a window '
           f'too short for the two-step rule). One forecast per model run is counted. Lower Brier is better; skill '
           f'is against the sample’s own observed frequency, so 0 means no better than always forecasting that '
           f'rate. A row is marked too few to read unless it has {escape(str(r.get("readable_when", "")))}. '
           f'Rain, snow, low cloud cover and icing are not scored: METARs do not measure them the way the forecast '
           f'states them. The low ceiling trial is scored against the METAR ceiling (lowest broken, overcast or '
           f'obscured layer); its rows measure the GEFS field’s two known limits (no ceiling in stable air, and '
           f'points where the model’s ground sits well above the station; see “Hazard thresholds” above) as much '
           f'as ceiling skill. Thresholds are indicative; not for flight planning.</p>',
           '<div class="table-wrap"><table class="skill"><thead><tr><th>Hazard</th><th>Threshold</th>'
           '<th>Lead (days)</th><th>Forecasts</th><th>Observed</th><th>Points (with events)</th><th>Brier</th>'
           '<th>Skill</th><th>Read</th></tr></thead><tbody>']
    for c in doc["scores"]:
        thr = "" if c.get("threshold") is None else f'{"≤" if c["hazard"] in ("vis", "cold", "ceiling") else "≥"} {c["threshold"]:g} {c.get("unit") or ""}'
        lead = "all" if c["lead_days"] == "all" else str(c["lead_days"])
        read = "yes" if c.get("readable") else "too few to read"
        cls = ' class="skill-all"' if lead == "all" else ""
        out.append(f'<tr{cls}><td>{escape(HAZ_NAMES.get(c["hazard"], c["hazard"]))} '
                   f'({escape(c["level"])})</td><td>{escape(thr)}</td><td>{lead}</td><td>{c["n"]}</td>'
                   f'<td>{c["events"]}</td><td>{c.get("points", 0)} ({c.get("points_with_events", 0)})</td>'
                   f'<td>{_num(c.get("brier"))}</td><td>{_num(c.get("brier_skill_vs_sample_climatology"), "{:+.2f}")}</td>'
                   f'<td>{read}</td></tr>')
    out.append('</tbody></table></div>')
    rel = [c for c in doc["scores"] if c["lead_days"] == "all" and c.get("readable")]
    for c in rel:
        out.append(f'<p class="small"><b>Reliability, {escape(HAZ_NAMES.get(c["hazard"], c["hazard"]))} '
                   f'({escape(c["level"])}), all leads:</b> ' + "; ".join(
                       f'{escape(b["band"])}: forecast {b["mean_p"]:.0%}, observed {b["observed"]:.0%} (n {b["n"]})'
                       for b in c.get("reliability", [])) + '.</p>')
    out.append('<p class="small">Data: <a href="data/verification.json">scores (JSON)</a>; every observation and '
               'scored forecast is kept in <a href="https://github.com/metacog-osint/vis/tree/main/history" '
               'rel="noopener">history/</a>.</p></div></section>')
    return "".join(out)


def c3s_year(locations_doc: dict | None, forecast_doc: dict) -> str:
    """The year in the Copernicus notice (licence clause 5.1.2): when the climate cache was last built from ERA5,
    else the forecast's year."""
    y = str((locations_doc or {}).get("climate_updated_at") or "")[:4]
    return y if y.isdigit() else str(forecast_doc.get("generated_at") or "")[:4]


def location_files(locations_doc: dict | None) -> dict[str, str]:
    """data/locations.json (one compact row per point, schema vis-locations-index/1) and data/locations/<id>.json
    (the full detail, schema vis-location/1) from tools/locations.py's out/locations.json. Without it the page's
    pop-up says the detail is not built yet; the rest of the site is unchanged."""
    if not locations_doc:
        return {}
    points = locations_doc.get("points") or []
    index = {
        "schema": "vis-locations-index/1", "program": "vis", "generated_at": locations_doc.get("generated_at"),
        "forecast_generated_at": locations_doc.get("forecast_generated_at"),
        "climate_updated_at": locations_doc.get("climate_updated_at"),
        "docs": "https://github.com/metacog-osint/vis/blob/main/docs/location-data.md",
        "sources": locations_doc.get("sources") or [],
        "points": [locations.index_row(p) for p in points],
    }
    files = {"data/locations.json": json.dumps(index, ensure_ascii=False, indent=1) + "\n"}
    for p in points:
        files[f"data/locations/{p['id']}.json"] = json.dumps(
            {**p, "sources": locations_doc.get("sources") or []}, ensure_ascii=False, separators=(",", ":")) + "\n"
    return files


def build(forecast_doc: dict, out_dir: Path | None = None, verification: dict | None = None,
          locations_doc: dict | None = None, wxmap_doc: dict | None = None) -> dict[str, str]:
    """wxmap_doc is the site's data/wxmap.json when it exists; without it the Sources list points to that file,
    and tools/wxmap.py fills in the layers it resolved when it writes the file (wxmap.stamp_page)."""
    fc = feed_v2.build_feed(forecast_doc)
    files = {
        "index.html": INDEX_HTML.replace("<!--VIS-VERIFY-->", verification_html(verification)).replace(
            "<!--VIS-C3S-YEAR-->", c3s_year(locations_doc, forecast_doc)).replace(
            "<!--VIS-CHECKED-->", checked_text()).replace(
            "<!--VIS-CEILING-LIMITS-->", ceiling_limits_html(forecast_doc)).replace(
            "<!--VIS-GIBS-->", wxmap.gibs_sources_span(wxmap_doc)),
        "style.css": STYLE_CSS,
        "vis.js": VIS_JS,
        "_headers": HEADERS,
        "404.html": NOT_FOUND_HTML,
        "data/forecast.json": json.dumps(forecast_doc, ensure_ascii=False, indent=1) + "\n",
        "data/feed.geojson": json.dumps(fc, ensure_ascii=False, indent=1) + "\n",
        **location_files(locations_doc),
    }
    if verification is not None:
        files["data/verification.json"] = json.dumps(verification, ensure_ascii=False, indent=1) + "\n"
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
    ap.add_argument("--verification", default=str(ROOT / "out" / "verification.json"))
    ap.add_argument("--locations", default=str(ROOT / "out" / "locations.json"),
                    help="tools/locations.py output; skipped when the file is missing")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    vp = Path(a.verification)
    ver = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else None
    loc_path = Path(a.locations)
    loc = json.loads(loc_path.read_text(encoding="utf-8")) if loc_path.exists() else None
    out_dir = Path(a.out)
    wx_path = out_dir / "data" / "wxmap.json"
    try:
        wx = json.loads(wx_path.read_text(encoding="utf-8")) if wx_path.exists() else None
    except ValueError:
        wx = None
    files = build(doc, out_dir, ver, loc, wx)
    print(f"built {len(files)} files in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
