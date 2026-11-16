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
import time
import urllib.error
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
MODEL_MEMBERS = {"ncep_gefs025": 31, "ecmwf_ifs025": 51}
# Open-Meteo publishes each model's latest run in a metadata file. The ensemble API's `ecmwf_ifs025` reads the
# `ecmwf_ifs025_ensemble` data set (checked 16 Nov 2026: its member data ended 28 Nov 14:00 UTC, the end of the
# 13 Nov 12Z run that metadata file named, while the plain `ecmwf_ifs025` file named 15 Nov 18Z).
META_URL = "https://ensemble-api.open-meteo.com/data/{}/static/meta.json"
META_DATASET = {"ncep_gefs025": "ncep_gefs025", "ecmwf_ifs025": "ecmwf_ifs025_ensemble"}
# Both models run every 6 h and reach Open-Meteo about 6-11 h after their run time, so a healthy run is under about
# 17 h old. A run older than this is left out of the pool rather than mixed in unseen (review 15 Nov 2026: a 38-hour-old
# ECMWF run was pooled while the page said 82 members; on 16 Nov the newest ECMWF run on Open-Meteo was 66 h old).
MAX_RUN_AGE_HOURS = 24.0
VARS = "wind_gusts_10m,precipitation,temperature_2m,snowfall,visibility,cloud_cover_low"

# Operational thresholds for theatre weather (air/UAS/ISR, maritime, ground mobility).
# Transient hourly hazards require min_hours in the UTC day so a single 1-hour shower does not colour a 24h day.
HAZARDS = [
    {"id": "wind", "name": "Strong wind", "var": "wind_gusts_10m", "agg": "max", "dir": "above", "min_hours": 3,
     "amber": 65.0, "red": 90.0, "unit": "km/h",
     "why": "10 m gusts sustained >= 3 h; small UAS and rotary-wing limits ~65 km/h, severe air/port disruption >= 90 km/h"},
    # Cover, not ceiling height: neither ensemble on Open-Meteo carries cloud base (checked 8 Nov 2026), so low cloud
    # can only raise awareness (Tom, 8 Nov 2026: "Cover, max Yellow").
    {"id": "lowcloud", "name": "Low cloud cover", "var": "cloud_cover_low", "agg": "max", "dir": "above", "min_hours": 3,
     "amber": 80.0, "red": 95.0, "unit": "%", "max_colour": "yellow",
     "why": "Cloud cover in the lowest 3 km for >= 3 h (ECMWF members only); it degrades optical ISR, laser designation "
            "and VFR flying, but it is cover, not ceiling height, so it shows at most Yellow"},
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


def fetch_model(zones, model, days, wait=time.sleep):
    """One call for all points. Open-Meteo weights ensemble calls heavily (one 16-point run used most of the free
    600-a-minute allowance on 8 Nov 2026), so a refused call (HTTP 429) waits a minute and tries again, twice."""
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{z['lat']:.4f}" for z in zones),
        "longitude": ",".join(f"{z['lon']:.4f}" for z in zones),
        "hourly": VARS, "models": model, "forecast_days": days, "timezone": "UTC",
    })
    req = urllib.request.Request(f"{ENSEMBLE_URL}?{q}", headers={"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            print(f"  {model}: Open-Meteo refused the call (429); waiting 65 s", flush=True)
            wait(65)
    return data if isinstance(data, list) else [data]


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def model_run(model, now=None, opener=None):
    """Read a model's latest run from Open-Meteo's metadata and judge its age.

    Returns {"id", "run_time", "available_at", "age_hours", "state"}; state is "used" (run under MAX_RUN_AGE_HOURS),
    "stale" (older: left out of the pool) or "unknown" (metadata unreadable: used, and the page says the run time is
    unknown)."""
    now = now or datetime.now(timezone.utc)
    opener = opener or urllib.request.urlopen
    out = {"id": model, "run_time": None, "available_at": None, "age_hours": None, "state": "unknown"}
    req = urllib.request.Request(META_URL.format(META_DATASET.get(model, model)),
                                 headers={"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"})
    try:
        with opener(req, timeout=30) as r:
            meta = json.loads(r.read().decode("utf-8"))
        init = int(meta["last_run_initialisation_time"])
    except Exception as e:  # noqa: BLE001 - any failure leaves the run time unknown, never stops the forecast
        print(f"  {model}: run time unreadable ({e.__class__.__name__}); using it, marked unknown", flush=True)
        return out
    out["run_time"] = _utc(init)
    if meta.get("last_run_availability_time"):
        out["available_at"] = _utc(int(meta["last_run_availability_time"]))
    out["age_hours"] = round((now.timestamp() - init) / 3600.0, 1)
    out["state"] = "stale" if out["age_hours"] > MAX_RUN_AGE_HOURS else "used"
    return out


def first_hour_index(times, now):
    """Index of the first hourly time at or after the start of the current UTC hour (0 if all are later)."""
    if now is None:
        return 0
    cut = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    for i, t in enumerate(times):
        if t[:16] >= cut:
            return i
    return len(times)


def expand_points(zones):
    """One row per forecast point: each zone's reference point, then its alternates (Tom, 8 Nov 2026: forecast the
    alternates too). A reference row keeps the zone's id; an alternate's id is <zone id>-<ICAO>."""
    rows = []
    for z in zones:
        rows.append({**{k: v for k, v in z.items() if k != "alternates"}, "zone_id": z["id"], "role": "reference"})
        for a in z.get("alternates", []):
            rows.append({
                "id": f"{z['id']}-{a['icao']}", "zone_id": z["id"], "role": "alternate", "name": z["name"],
                "warning_problem_id": z.get("warning_problem_id"), "station": a["station"], "icao": a["icao"],
                "lat": a["lat"], "lon": a["lon"],
                "countries": [a["country"]] if a.get("country") else list(z.get("countries", [])),
            })
    return rows


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


def score_zone_days(zone_models, days, now=None):
    """Score each UTC day. With `now`, hours already past are left out: the first day is scored from the current
    UTC hour (review 15 Nov 2026: 11 of 18 warnings were issued 17 to 23 hours into the day they forecast, so they
    counted hours that had already happened)."""
    times = zone_models[0]["hourly"]["time"]
    start = first_hour_index(times, now)
    day_Warning = []
    for d in range(days):
        i0, i1 = d * 24, (d + 1) * 24
        if i1 > len(times):
            break
        i0 = max(i0, start)
        if i0 >= i1:
            continue
        day_Warning.append((times[d * 24][:10], i0, i1))

    out = []
    for day_str, i0, i1 in day_Warning:
        hazards_out = {}
        worst = "green"
        models_contributing = set()
        for h in HAZARDS:
            amber_hits = red_hits = total = 0
            stat_vals = []
            hazard_models = set()
            for m_idx, zm in enumerate(zone_models):
                members = member_series(zm["hourly"], h["var"])
                if members:
                    models_contributing.add(m_idx)
                    hazard_models.add(m_idx)
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
            cap = h.get("max_colour")
            if cap and COLOUR_RANK[col] > COLOUR_RANK[cap]:
                col = cap
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
                "models": len(hazard_models),
            }
        out.append({"day": day_str, "from": times[i0][:13] + ":00Z", "hours": i1 - i0, "colour": worst,
                    "models_present": len(models_contributing), "hazards": hazards_out})
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


def build_forecast_doc(zones, by_model, days=7, generated_at=None, runs=None):
    """`runs` is {model id: model_run() result}; a model missing from `by_model` was left out (stale run)."""
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    runs = runs or {}
    out_zones = []
    for i, z in enumerate(zones):
        z_days = score_zone_days([by_model[m][i] for m, _ in MODELS if m in by_model], days, now=now)
        for d in z_days:
            day_dt = datetime.strptime(d["day"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d["night"] = astro.night(z["lat"], z["lon"], day_dt)
        zone_id = z.get("zone_id", z["id"])
        out_zones.append({
            "id": z["id"], "zone_id": zone_id, "role": z.get("role", "reference"), "name": z["name"],
            "warning_problem_id": z.get("warning_problem_id") or f"WF-WP-Z{zone_id[:2]}",
            "station": z.get("station", z["name"]), "icao": z.get("icao", "ZZZZ"),
            "countries": z.get("countries", []),
            "lat": z["lat"], "lon": z["lon"],
            "days": z_days,
        })
    return {
        "program": "vis",
        "title": "Vis — Operational weather & illumination",
        "generated_at": generated_at,
        "models": [{
            "id": m, "label": label, "members": MODEL_MEMBERS.get(m),
            "used": m in by_model,
            "run_time": runs.get(m, {}).get("run_time"),
            "available_at": runs.get(m, {}).get("available_at"),
            "age_hours": runs.get(m, {}).get("age_hours"),
            "state": runs.get(m, {}).get("state", "used" if m in by_model else "stale"),
        } for m, label in MODELS],
        "members_used": sum(MODEL_MEMBERS.get(m, 0) for m, _ in MODELS if m in by_model),
        "max_run_age_hours": MAX_RUN_AGE_HOURS,
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

    zones = expand_points(load_zones(a.points if not a.zones else None, a.zones))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    by_model, runs = {}, {}
    for model, label in MODELS:
        runs[model] = model_run(model, now)
        r = runs[model]
        if r["state"] == "stale":
            print(f"leaving out {model}: its newest run on Open-Meteo is {r['run_time']}, {r['age_hours']} h old "
                  f"(limit {MAX_RUN_AGE_HOURS:g} h)", flush=True)
            continue
        print(f"fetching {model} ({label}, run {r['run_time'] or 'unknown'}) for {len(zones)} points...", flush=True)
        by_model[model] = fetch_model(zones, model, a.days)
    if not by_model:
        print("every model's newest run is stale; no forecast written (the page keeps the last one and says how old "
              "it is)", flush=True)
        return 1

    result = build_forecast_doc(zones, by_model, days=a.days, generated_at=generated_at, runs=runs)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "forecast.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
