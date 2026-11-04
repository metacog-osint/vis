"""Vis — Operational weather & illumination for Watchfloor's conflict zones.

    python tools/forecast.py [--points config/points.json] [--days 7] [--out out]

Fetches hourly ensemble members from Open-Meteo (NOAA GEFS 31 members + ECMWF IFS 51 members = 82 members) for each
zone's reference ICAO airfield/port, turns member counts into probabilities of crossing operational hazard thresholds,
maps those probabilities to ICD 203 likelihood terms and a UK Met Office-style impact matrix, and computes night
illumination from sun and moon positions. Standard library only.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "doctrine", "kit", "python"))
import astro  # noqa: E402
from tic_doctrine import likelihood  # noqa: E402

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
MODELS = [
    ("ncep_gefs025", "NOAA NCEP GEFS 0.25 deg (31 members)"),
    ("ecmwf_ifs025", "ECMWF IFS 0.25 deg (51 members)"),
]
VARS = "wind_gusts_10m,precipitation,temperature_2m,snowfall,visibility,cloud_cover_low"

# Operational thresholds for theatre weather (air/UAS/ISR, maritime, ground mobility).
# Transient hourly hazards require min_hours in the UTC day so a single 1-hour shower does not colour a 24h day.
HAZARDS = [
    {"id": "wind", "name": "Strong wind", "var": "wind_gusts_10m", "agg": "max", "dir": "above", "min_hours": 3,
     "amber": 65.0, "red": 90.0, "unit": "km/h",
     "why": "10 m gusts sustained >= 3 h; small UAS and rotary-wing limits ~65 km/h, severe air/port disruption >= 90 km/h"},
    {"id": "ceiling", "name": "Low cloud ceiling", "var": "cloud_cover_low", "agg": "max", "dir": "above", "min_hours": 3,
     "amber": 80.0, "red": 95.0, "unit": "%",
     "why": "Low cloud cover (0-3 km) >= 3 h; degrades optical ISR, laser designation and VFR air operations"},
    {"id": "vis", "name": "Poor visibility", "var": "visibility", "agg": "min", "dir": "below", "min_hours": 3,
     "amber": 3000.0, "red": 1000.0, "unit": "m",
     "why": "Surface visibility < 3 km (amber) or < 1 km (red) for >= 3 h (fog, mist, dust, smoke); GEFS members"},
    {"id": "rain", "name": "Heavy rain", "var": "precipitation", "agg": "sum", "dir": "above", "min_hours": 1,
     "amber": 25.0, "red": 60.0, "unit": "mm/24h",
     "why": "24-hour rainfall total; flash flooding, unpaved road softening and river-crossing degradation"},
    {"id": "snow", "name": "Heavy snow", "var": "snowfall", "agg": "sum", "dir": "above", "min_hours": 1,
     "amber": 10.0, "red": 25.0, "unit": "cm/24h",
     "why": "24-hour snowfall total; road, runway and supply-line disruption"},
    {"id": "icing", "name": "Freezing rain / icing", "var": "temperature_2m", "agg": "joint", "dir": "below", "min_hours": 2,
     "amber": 0.0, "red": 0.0, "unit": "C + mm/h",
     "with": {"var": "precipitation", "dir": "above", "amber": 1.0, "red": 3.0},
     "why": "2 m temperature <= 0 C with rain >= 1 / 3 mm/h in the same hour for >= 2 h; airframe and road icing"},
    {"id": "heat", "name": "Extreme heat", "var": "temperature_2m", "agg": "max", "dir": "above", "min_hours": 3,
     "amber": 40.0, "red": 45.0, "unit": "C",
     "why": "2 m air temperature >= 40 / 45 C for >= 3 h; heat-casualty risk and high density altitude for rotary-wing lift"},
    {"id": "cold", "name": "Extreme cold", "var": "temperature_2m", "agg": "min", "dir": "below", "min_hours": 3,
     "amber": -15.0, "red": -25.0, "unit": "C",
     "why": "2 m air temperature <= -15 / -25 C for >= 3 h; cold-injury, battery and hydraulic limits"},
]

COLOUR_RANK = {"green": 0, "yellow": 1, "amber": 2, "red": 3}


def matrix_colour(p_amber, p_red):
    """UK Met Office-style likelihood-by-impact matrix on ICD 203 probability boundaries."""
    if p_red >= 0.45:
        return "red"
    if p_amber >= 0.45 or p_red >= 0.20:
        return "amber"
    if p_amber >= 0.20 or p_red >= 0.05:
        return "yellow"
    return "green"


def fetch_model(zones, model, days):
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{z['lat']:.4f}" for z in zones),
        "longitude": ",".join(f"{z['lon']:.4f}" for z in zones),
        "hourly": VARS, "models": model, "forecast_days": days, "timezone": "UTC",
    })
    req = urllib.request.Request(f"{ENSEMBLE_URL}?{q}", headers={"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data if isinstance(data, list) else [data]


def member_series(hourly, var):
    prefix = var + "_member"
    keys = sorted(k for k in hourly if k == var or k.startswith(prefix))
    return [hourly[k] for k in keys if any(v is not None for v in hourly[k])]


def hit(vals, threshold, direction):
    return (vals >= threshold) if direction == "above" else (vals <= threshold)


def member_hits_day(h, m_slice, m2_slice=None):
    """Return (amber_hit, red_hit, summary_val) for one ensemble member over one 24-hour UTC slice."""
    min_h = h.get("min_hours", 1)
    if h["agg"] == "joint":
        if m2_slice is None:
            return False, False, None
        w = h["with"]
        pairs = [(a, b) for a, b in zip(m_slice, m2_slice) if a is not None and b is not None]
        if not pairs:
            return False, False, None
        ah = sum(1 for a, b in pairs if hit(a, h["amber"], h["dir"]) and hit(b, w["amber"], w["dir"]))
        rh = sum(1 for a, b in pairs if hit(a, h["red"], h["dir"]) and hit(b, w["red"], w["dir"]))
        return ah >= min_h, rh >= min_h, None
    vals = [v for v in m_slice if v is not None]
    if not vals:
        return False, False, None
    if h["agg"] == "sum":
        total = sum(vals)
        return hit(total, h["amber"], h["dir"]), hit(total, h["red"], h["dir"]), total
    ah = sum(1 for v in vals if hit(v, h["amber"], h["dir"]))
    rh = sum(1 for v in vals if hit(v, h["red"], h["dir"]))
    stat = max(vals) if h["agg"] == "max" else min(vals)
    return ah >= min_h, rh >= min_h, stat


def score_zone_days(zone_models, days):
    times = zone_models[0]["hourly"]["time"]
    day_Warning = []
    for d in range(days):
        i0, i1 = d * 24, (d + 1) * 24
        if i1 > len(times):
            break
        day_Warning.append((times[i0][:10], i0, i1))

    out = []
    for day_str, i0, i1 in day_Warning:
        hazards_out = {}
        worst = "green"
        models_contributing = set()
        for h in HAZARDS:
            amber_hits = red_hits = total = 0
            stat_vals = []
            for m_idx, zm in enumerate(zone_models):
                members = member_series(zm["hourly"], h["var"])
                if members:
                    models_contributing.add(m_idx)
                with_members = member_series(zm["hourly"], h["with"]["var"]) if h["agg"] == "joint" else None
                for k, m in enumerate(members):
                    m2 = with_members[k][i0:i1] if (with_members and k < len(with_members)) else None
                    ah, rh, sval = member_hits_day(h, m[i0:i1], m2)
                    if h["agg"] == "joint" and m2 is None:
                        continue
                    if sval is None and h["agg"] != "joint":
                        continue
                    total += 1
                    if ah:
                        amber_hits += 1
                    if rh:
                        red_hits += 1
                    if sval is not None:
                        stat_vals.append(sval)
            if total == 0:
                continue
            pa, pr = amber_hits / total, red_hits / total
            col = matrix_colour(pa, pr)
            if COLOUR_RANK[col] > COLOUR_RANK[worst]:
                worst = col
            stat_vals.sort()
            med = round(stat_vals[len(stat_vals) // 2], 1) if stat_vals else None
            p90_idx = int(0.9 * (len(stat_vals) - 1)) if h["dir"] == "above" else int(0.1 * (len(stat_vals) - 1))
            p90 = round(stat_vals[p90_idx], 1) if stat_vals else None
            ta = likelihood.term_for(max(0.01, min(0.99, pa)) if 0 < pa < 1 else (0.01 if pa == 0 else 0.99))
            tr = likelihood.term_for(max(0.01, min(0.99, pr)) if 0 < pr < 1 else (0.01 if pr == 0 else 0.99))
            hazards_out[h["id"]] = {
                "members": total, "amber_hits": amber_hits, "red_hits": red_hits,
                "p_amber": round(pa, 3), "term_amber": ta["term"], "band_amber": ta["band_label"],
                "p_red": round(pr, 3), "term_red": tr["term"], "band_red": tr["band_label"],
                "median": med, "p90": p90, "colour": col,
            }
        out.append({"day": day_str, "colour": worst, "models_present": len(models_contributing), "hazards": hazards_out})
    return out


def load_zones(points_path=None, entities_path=None):
    """Load conflict zones with ICAO reference stations from config/points.json (or fall back to entities.json)."""
    p = points_path or os.path.join(ROOT, "config", "points.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        return doc["zones"]
    ep = entities_path or os.path.join(ROOT, "..", "more-context", "catalog", "entities.json")
    with open(ep, encoding="utf-8") as f:
        ent = json.load(f)
    return [{"id": z["id"], "name": z["name"], "lat": z["map"][0], "lon": z["map"][1],
             "station": z["name"], "icao": "ZZZZ", "countries": []} for z in ent["zones"]]


def build_forecast_doc(zones, by_model, days=7, generated_at=None):
    out_zones = []
    for i, z in enumerate(zones):
        z_days = score_zone_days([by_model[m][i] for m, _ in MODELS if m in by_model], days)
        for d in z_days:
            day_dt = datetime.strptime(d["day"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d["night"] = astro.night(z["lat"], z["lon"], day_dt)
        out_zones.append({
            "id": z["id"], "name": z["name"],
            "warning_problem_id": z.get("warning_problem_id") or f"WF-WP-Z{z['id'][:2]}",
            "station": z.get("station", z["name"]), "icao": z.get("icao", "ZZZZ"),
            "countries": z.get("countries", []),
            "lat": z["lat"], "lon": z["lon"],
            "alternates": z.get("alternates", []),
            "days": z_days,
        })
    return {
        "program": "vis",
        "title": "Vis — Operational weather & illumination",
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": [{"id": m, "label": label} for m, label in MODELS],
        "source": "Open-Meteo Ensemble API (open-meteo.com, CC BY 4.0); sun and moon computed (Meeus)",
        "hazards": [{k: v for k, v in h.items()} for h in HAZARDS],
        "zones": out_zones,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--points", default=os.path.join(ROOT, "config", "points.json"))
    ap.add_argument("--zones", default=None, help="legacy entities.json path")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args()

    zones = load_zones(a.points if not a.zones else None, a.zones)
    by_model = {}
    for model, label in MODELS:
        print(f"fetching {model} ({label}) for {len(zones)} stations...", flush=True)
        by_model[model] = fetch_model(zones, model, a.days)

    result = build_forecast_doc(zones, by_model, days=a.days)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "forecast.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
