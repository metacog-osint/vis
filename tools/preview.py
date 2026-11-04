"""A static preview of out/forecast.json: zones by day, each cell a stoplight with its driver and likelihood term,
plus tonight's illumination. Prototype only (the real page will follow the house design). Writes out/preview.html."""
import html
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COL = {"green": ("#DDEFD9", "#1E5B2A", "No significant impact"), "yellow": ("#FFF1B8", "#5C4A00", "Be aware"),
       "amber": ("#FFD7A6", "#7A3B00", "Be prepared"), "red": ("#F7B5B5", "#7A1010", "Take action")}


def hm(iso):
    return iso[11:16] if iso else "–"


def main():
    r = json.load(open(os.path.join(ROOT, "out", "forecast.json"), encoding="utf-8"))
    hz = {h["id"]: h for h in r["hazards"]}
    days = [d["day"] for d in r["zones"][0]["days"]]
    head = "".join(f"<th>{datetime.strptime(d, '%Y-%m-%d'):%a %d %b}</th>" for d in days)
    rows = []
    for z in r["zones"]:
        cells = []
        for d in z["days"]:
            bg, fg, word = COL[d["colour"]]
            drivers = sorted(((k, v) for k, v in d["hazards"].items() if v["colour"] != "green"),
                             key=lambda kv: -max(kv[1]["p_amber"], kv[1]["p_red"]))
            txt = "<br>".join(f"{html.escape(hz[k]['name'])}: {html.escape(v['term_amber'].lower())}"
                              f" ({round(v['p_amber'] * 100)}%)" for k, v in drivers[:2]) or "&nbsp;"
            cells.append(f'<td style="background:{bg};color:{fg}"><b>{d["colour"].title()}</b><br><small>{txt}</small></td>')
        n = z["days"][0]["night"]
        night = (f"Moon {round(n['moon_lit'] * 100)}% lit; civil dusk {hm(n['civil_dusk'])}, dawn {hm(n['civil_dawn'])} UTC; "
                 f"{n['dark_low_light_min'] // 60} h {n['dark_low_light_min'] % 60} min dark with the moon down or under a quarter lit")
        rows.append(f"<tr><th scope='row'>{html.escape(z['name'])}<br><small>{z['lat']:.2f}, {z['lon']:.2f}</small></th>{''.join(cells)}"
                    f"<td class='n'><small>{night}</small></td></tr>")
    legend = " ".join(f'<span style="background:{b};color:{f}">{k.title()}: {w}</span>' for k, (b, f, w) in COL.items())
    thr = "".join(f"<li><b>{html.escape(h['name'])}</b>: amber {h['amber']} {h['unit']}, red {h['red']} {h['unit']}"
                  f"{' (with rain ' + str(h['with']['amber']) + ' / ' + str(h['with']['red']) + ' mm/h in the same hour)' if h.get('with') else ''}"
                  f" ({'reaching' if h['dir'] == 'above' else 'falling to'} it). {html.escape(h['why'])}.</li>" for h in r["hazards"])
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Weather impact preview</title>
<style>body{{font:14px/1.45 "IBM Plex Sans",system-ui,sans-serif;margin:24px;color:#13202E;background:#fff}}
table{{border-collapse:separate;border-spacing:2px;width:100%}}th,td{{padding:6px 8px;vertical-align:top;text-align:left}}
thead th{{font-size:12px;color:#5A6472}}tbody th{{background:#F2F3F0;min-width:150px}}td{{border-radius:4px;min-width:110px}}
td.n{{background:#EEF0F2;color:#34404F;min-width:220px}}.legend span{{display:inline-block;padding:2px 8px;border-radius:4px;margin-right:6px}}
small{{font-size:12px}}h1{{margin:0 0 4px}}p{{margin:4px 0 12px;color:#34404F}}</style></head><body>
<h1>Weather impact: prototype</h1>
<p>Generated {r['generated_at'].replace('T', ' ')[:16]} UTC. Each cell is a UTC day at the zone's point: the share of 82 ensemble members
(GFS 31, ECMWF 51; visibility GFS only) that reach the amber threshold, its ICD 203 term, and the stoplight from a likelihood-by-impact
matrix. Thresholds are indicative. Night column: the night after today, from computed sun and moon positions.</p>
<p class="legend">{legend}</p>
<table><thead><tr><th>Zone</th>{head}<th>Tonight</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Indicative thresholds</h2><ul>{thr}</ul>
<p><small>{html.escape(r['source'])}</small></p></body></html>"""
    out = os.path.join(ROOT, "out", "preview.html")
    open(out, "w", encoding="utf-8").write(page)
    print("wrote", out)


if __name__ == "__main__":
    main()
