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
function hm(iso) {                                // the viewer's zone, named; UTC in the tooltip
  if (!iso) return { text: "–", utc: "" };
  const utc = iso.slice(11, 16) + " UTC";
  return { text: T ? T.format(iso.replace("Z", ":00Z"), { date: false }) : utc, utc };
}
const timeSpan = (iso) => { const t = hm(iso); return `<span class="mono" title="${esc(t.utc)}">${esc(t.text)}</span>`; };

async function init() {
  const res = await fetch("data/forecast.json");
  const doc = await res.json();
  const stamp = document.getElementById("vis-stamp");
  if (stamp) {
    const g = doc.generated_at;
    stamp.textContent = `Generated ${T ? T.format(g, { year: true }) : g.replace("T", " ").slice(0, 16) + " UTC"} · 82 ensemble members`;
    stamp.title = g.replace("T", " ").slice(0, 16) + " UTC";
  }

  const hz = Object.fromEntries((doc.hazards || []).map((h) => [h.id, h]));
  const days = (doc.zones[0]?.days || []).map((d) => d.day);
  const thead = document.getElementById("vis-thead");
  thead.innerHTML = `<tr><th scope="col">Zone &amp; reference station</th>${days
    .map((d) => `<th scope="col">${esc(fmtDay(d))}</th>`)
    .join("")}<th scope="col">Tonight’s illumination</th></tr>`;

  const tbody = document.getElementById("vis-tbody");
  tbody.innerHTML = (doc.zones || [])
    .map((z) => {
      const cells = (z.days || [])
        .map((d) => {
          const drivers = Object.entries(d.hazards || {})
            .filter(([, v]) => v.colour !== "green")
            .sort((a, b) => Math.max(b[1].p_amber, b[1].p_red) - Math.max(a[1].p_amber, a[1].p_red));
          const lines = drivers.slice(0, 2).map(([k, v]) => {
            const p = v.colour === "red" ? v.p_red : v.p_amber;
            const term = likelihoodLabel(Math.max(0.01, Math.min(0.99, p || 0.01)));
            return `<div class="driver-line"><b>${esc(hz[k]?.name || k)}</b>: ${esc(term)} · <span class="mono">${Math.round(p * 100)}%</span></div>`;
          });
          return `<td class="day-cell col-${esc(d.colour)}"><div class="day-head">${esc(d.colour.toUpperCase())}</div>${
            lines.join("") || '<div class="driver-line">All hazards below Yellow</div>'
          }</td>`;
        })
        .join("");
      const n = z.days?.[0]?.night || {};
      const lowH = Math.floor((n.dark_low_light_min || 0) / 60);
      const lowM = (n.dark_low_light_min || 0) % 60;
      const nightHtml = `<div><b>Moon ${Math.round((n.moon_lit || 0) * 100)}% lit</b> · low-light dark <span class="mono">${lowH}h ${lowM}m</span></div>
        <div>Civil dusk ${timeSpan(n.civil_dusk)} · dawn ${timeSpan(n.civil_dawn)}</div>`;
      const alt = (z.alternates || []).map((a) => `${a.station} (${a.icao})`).join(", ");
      return `<tr id="zone-${esc(z.id)}">
        <th scope="row">${esc(z.name)}<span class="station-meta">${esc(z.station)} · ${esc(z.icao)} (${z.lat.toFixed(2)}, ${z.lon.toFixed(2)})${alt ? ` · alt: ${esc(alt)}` : ""}</span></th>
        ${cells}
        <td class="night-cell">${nightHtml}</td>
      </tr>`;
    })
    .join("");

  const ul = document.getElementById("vis-thresholds");
  ul.innerHTML = (doc.hazards || [])
    .map(
      (h) =>
        `<li><b>${esc(h.name)}</b>: Amber <span class="mono">${esc(h.amber)} ${esc(h.unit)}</span>, Red <span class="mono">${esc(h.red)} ${esc(h.unit)}</span>. ${esc(h.why)}.</li>`
    )
    .join("");
}

init();
if (T) { T.mountSettings(document.getElementById("time-settings")); T.onChange(() => init()); }
