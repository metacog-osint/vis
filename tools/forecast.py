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
import gefs  # noqa: E402
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
# Both ensembles are 3-hourly on Open-Meteo (metadata `temporal_resolution_seconds` 10800, checked 15 Nov 2026); the
# hourly series between steps are interpolated. Persistence is therefore counted on the model's own steps, at 00, 03,
# ... 21 UTC: a hazard must hold on `min_steps` consecutive steps (16 Nov 2026, from the 15 Nov review: under
# straight-line interpolation one strong 3-hourly value gave up to five hourly values over the line).
NATIVE_STEP_HOURS = 3
# Grid cell check (15 Nov 2026 review: Caracas Maiquetia, a sea-level runway, was read from a cell centred 936 m up in
# the coastal range; the sea cell beside it gave 0 of 31 members where the land cell gave 14). Each point names the
# cell Open-Meteo should use (`cell` in config/points.json: land, sea or nearest; default land), and every run compares
# the elevation of the cell's centre with the station's. A difference over this many metres keeps the point's
# Amber or Red out of COP's warnings.
GRID_MAX_DIFF_M = 300.0
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# Operational thresholds for theatre weather (air/UAS/ISR, maritime, ground mobility).
# Transient hazards must hold on `min_steps` consecutive 3-hourly model steps in the UTC day (see NATIVE_STEP_HOURS).
HAZARDS = [
    {"id": "wind", "name": "Strong wind", "var": "wind_gusts_10m", "agg": "max", "dir": "above", "min_steps": 2,
     "amber": 65.0, "red": 90.0, "unit": "km/h",
     "why": "10 m gusts at or over the line on two consecutive 3-hourly model steps; small UAS and rotary-wing limits ~65 km/h, severe air/port disruption >= 90 km/h"},
    # Cover, not ceiling height: neither ensemble on Open-Meteo carries cloud base (checked 8 Nov 2026), so low cloud
    # can only raise awareness (8 Nov 2026).
    {"id": "lowcloud", "name": "Low cloud cover", "var": "cloud_cover_low", "agg": "max", "dir": "above", "min_steps": 2,
     "amber": 80.0, "red": 95.0, "unit": "%", "max_colour": "yellow",
     "why": "Cloud cover in the lowest 3 km on two consecutive 3-hourly steps (ECMWF members only); it degrades optical ISR, laser designation "
            "and VFR flying, but it is cover, not ceiling height, so it shows at most Yellow"},
    # On trial from 27 Nov 2026: NOAA's own GEFS cloud ceiling (tools/gefs.py), above the station, scored against
    # METAR ceilings in tools/verify.py. Capped at Yellow during the trial; it comes from one model, so under method 1.1
    # it never reaches COP as a warning. The two limits in `why` are measured (tools/gefs.py docstring).
    {"id": "ceiling", "name": "Low ceiling (trial)", "var": "ceiling_agl", "agg": "min", "dir": "below", "min_steps": 2,
     "amber": 305.0, "red": 152.0, "unit": "m", "max_colour": "yellow", "trial": True,
     "why": "GEFS cloud ceiling above the station at or below 305 m (1,000 ft or lower, so a reported 1,000 ft ceiling "
            "counts) or 152 m (a reported 500 ft ceiling, 152.4 m, does not count) on two consecutive 3-hourly steps, "
            "read from NOAA's own GEFS files. On trial, shown at most Yellow, and scored against METAR ceilings. "
            "Two known limits. First, NOAA's GEFS v12 post-processing sets this ceiling only where surface-based CAPE "
            "(convective available potential energy, a measure of how unstable the air near the ground is) is at "
            "least about 0.5 J/kg. Stratus and fog in stable air have none, so they read as no ceiling: under full "
            "cloud cover about 4% of model points with no CAPE carry a ceiling, against about 98% with some. Second, "
            "the field is a height above sea level that cannot sit lower than about 17 m above the model's own "
            "ground, and at some points that ground is well above the station, so Amber or Red cannot be reached "
            "there (listed below). The model ceiling is the mid-point of the lowest model layer holding cloud, not "
            "the METAR ceiling (the lowest layer covering more than half the sky)"},
    {"id": "vis", "name": "Poor visibility", "var": "visibility", "agg": "min", "dir": "below", "min_steps": 2,
     "amber": 3000.0, "red": 1000.0, "unit": "m",
     "why": "Surface visibility < 3 km (amber) or < 1 km (red) on two consecutive 3-hourly steps (fog, mist, dust, "
            "smoke); GEFS members"},
    {"id": "rain", "name": "Heavy rain", "var": "precipitation", "agg": "sum", "dir": "above", "min_hours": 1,
     "amber": 25.0, "red": 60.0, "unit": "mm/24h",
     "why": "24-hour rainfall total; flash flooding, unpaved road softening and river-crossing degradation"},
    {"id": "snow", "name": "Heavy snow", "var": "snowfall", "agg": "sum", "dir": "above", "min_hours": 1,
     "amber": 10.0, "red": 25.0, "unit": "cm/24h",
     "why": "24-hour snowfall total; road, runway and supply-line disruption"},
    {"id": "icing", "name": "Freezing rain / icing", "var": "temperature_2m", "agg": "joint", "dir": "below", "min_steps": 2,
     "amber": 0.0, "red": 0.0, "unit": "C + mm/h",
     "with": {"var": "precipitation", "dir": "above", "amber": 1.0, "red": 3.0},
     "why": "2 m temperature <= 0 C with precipitation >= 1 / 3 mm/h at the same 3-hourly step, on two consecutive "
            "steps; airframe and road icing"},
    {"id": "heat", "name": "Extreme heat", "var": "temperature_2m", "agg": "max", "dir": "above", "min_steps": 2,
     "amber": 40.0, "red": 45.0, "unit": "C",
     "why": "2 m air temperature >= 40 / 45 C on two consecutive 3-hourly steps; heat-casualty risk and high density altitude for rotary-wing lift"},
    {"id": "cold", "name": "Extreme cold", "var": "temperature_2m", "agg": "min", "dir": "below", "min_steps": 2,
     "amber": -15.0, "red": -25.0, "unit": "C",
     "why": "2 m air temperature <= -15 / -25 C on two consecutive 3-hourly steps; cold-injury, battery and hydraulic limits"},
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


def fetch_model(zones, model, days, wait=time.sleep, opener=None):
    """One call per cell selection (land, sea, nearest) for all points that use it, answers back in the points'
    order. Open-Meteo weights ensemble calls by location, so splitting the points costs no more. A refused call
    (HTTP 429) or one with no answer (a URLError, timeout or dropped connection) waits a minute and tries again,
    twice. Other HTTP errors are raised at once."""
    groups = {}
    for i, z in enumerate(zones):
        groups.setdefault(z.get("cell") or "land", []).append(i)
    out = [None] * len(zones)
    for cell, idx in groups.items():
        got = _fetch_cells([zones[i] for i in idx], model, days, cell, wait, opener)
        for i, r in zip(idx, got):
            out[i] = r
    return out


def _fetch_cells(zones, model, days, cell, wait=time.sleep, opener=None):
    opener = opener or urllib.request.urlopen
    params = {
        "latitude": ",".join(f"{z['lat']:.4f}" for z in zones),
        "longitude": ",".join(f"{z['lon']:.4f}" for z in zones),
        "hourly": VARS, "models": model, "forecast_days": days, "timezone": "UTC",
    }
    if cell != "land":   # land is Open-Meteo's default; leaving it out keeps the land points' call as before
        params["cell_selection"] = cell
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{ENSEMBLE_URL}?{q}", headers={"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"})
    for attempt in range(3):
        try:
            with opener(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            print(f"  {model}: Open-Meteo refused the call (429); waiting 65 s", flush=True)
            wait(65)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # A network failure before any answer (run 44017839782, 17 Nov 2026: a TLS handshake timeout) is tried
            # again the same way; HTTPError is a URLError, so it is caught above first.
            if attempt == 2:
                raise
            print(f"  {model}: no answer from Open-Meteo ({type(e).__name__}: {e}); waiting 65 s", flush=True)
            wait(65)
    return data if isinstance(data, list) else [data]


def grid_check(zones, answers, opener=None):
    """Per point: the cell Open-Meteo used (its centre from the answer) and the elevation of that centre against the
    station's, both from Open-Meteo's elevation API in one call. ok is None when either is unknown."""
    opener = opener or urllib.request.urlopen
    rows = []
    for z, a in zip(zones, answers):
        a = a or {}
        rows.append({"cell": z.get("cell") or "land", "lat": a.get("latitude"), "lon": a.get("longitude"),
                     "cell_elevation_m": None, "station_elevation_m": None, "diff_m": None, "ok": None})
    have = [i for i, r in enumerate(rows) if r["lat"] is not None and r["lon"] is not None]
    if not have:
        return rows
    lats = [zones[i]["lat"] for i in have] + [rows[i]["lat"] for i in have]
    lons = [zones[i]["lon"] for i in have] + [rows[i]["lon"] for i in have]
    q = urllib.parse.urlencode({"latitude": ",".join(f"{x:.4f}" for x in lats),
                                "longitude": ",".join(f"{x:.4f}" for x in lons)})
    req = urllib.request.Request(f"{ELEVATION_URL}?{q}", headers={"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"})
    try:
        with opener(req, timeout=30) as r:
            elev = json.loads(r.read().decode("utf-8"))["elevation"]
    except Exception as e:  # noqa: BLE001 - unknown, never a stop; the feed then sends no warning for the point
        print(f"  grid check: elevation unreadable ({e.__class__.__name__})", flush=True)
        return rows
    n = len(have)
    for k, i in enumerate(have):
        st, ce = elev[k], elev[n + k]
        rows[i].update({"station_elevation_m": st, "cell_elevation_m": ce})
        if st is not None and ce is not None:
            rows[i]["diff_m"] = round(ce - st, 1)
            rows[i]["ok"] = abs(ce - st) <= GRID_MAX_DIFF_M
    return rows


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
    """One row per forecast point: each zone's reference point, then its alternates (8 Nov 2026: the
    alternates are forecast too). A reference row keeps the zone's id; an alternate's id is <zone id>-<ICAO>."""
    rows = []
    for z in zones:
        rows.append({**{k: v for k, v in z.items() if k != "alternates"}, "zone_id": z["id"], "role": "reference"})
        for a in z.get("alternates", []):
            rows.append({
                "id": f"{z['id']}-{a['icao']}", "zone_id": z["id"], "role": "alternate", "name": z["name"],
                "warning_problem_id": z.get("warning_problem_id"), "station": a["station"], "icao": a["icao"],
                "lat": a["lat"], "lon": a["lon"], "cell": a.get("cell"), "tz": a.get("tz"),
                "countries": [a["country"]] if a.get("country") else list(z.get("countries", [])),
            })
    return rows


def member_series(hourly, var):
    prefix = var + "_member"
    keys = sorted(k for k in hourly if k == var or k.startswith(prefix))
    return [hourly[k] for k in keys if any(v is not None for v in hourly[k])]


def hit(vals, threshold, direction):
    return (vals >= threshold) if direction == "above" else (vals <= threshold)


def _steps_run(flags, native):
    """Longest run of consecutive model steps (native True) that are all hits; hours between steps are skipped."""
    best = run = 0
    for f, n in zip(flags, native):
        if not n:
            continue
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def member_hits_day(h, m_slice, m2_slice=None, native=None):
    """Return (amber_hit, red_hit, summary_val) for one ensemble member over one 24-hour UTC slice. `native` marks
    the hours that are the model's own 3-hourly steps (default: every hour)."""
    min_s = h.get("min_steps", 1)
    native = native if native is not None else [True] * len(m_slice)
    if h["agg"] == "joint":
        if m2_slice is None:
            return False, False, None
        w = h["with"]
        if not any(a is not None and b is not None for a, b in zip(m_slice, m2_slice)):
            return False, False, None
        fa = [a is not None and b is not None and hit(a, h["amber"], h["dir"]) and hit(b, w["amber"], w["dir"])
              for a, b in zip(m_slice, m2_slice)]
        fr = [a is not None and b is not None and hit(a, h["red"], h["dir"]) and hit(b, w["red"], w["dir"])
              for a, b in zip(m_slice, m2_slice)]
        return _steps_run(fa, native) >= min_s, _steps_run(fr, native) >= min_s, None
    vals = [v for v in m_slice if v is not None]
    if not vals:
        return False, False, None
    if h["agg"] == "sum":
        total = sum(vals)
        return hit(total, h["amber"], h["dir"]), hit(total, h["red"], h["dir"]), total
    fa = [v is not None and hit(v, h["amber"], h["dir"]) for v in m_slice]
    fr = [v is not None and hit(v, h["red"], h["dir"]) for v in m_slice]
    stat = max(vals) if h["agg"] == "max" else min(vals)
    return _steps_run(fa, native) >= min_s, _steps_run(fr, native) >= min_s, stat


def score_zone_days(zone_models, days, now=None, model_ids=None):
    """Score each UTC day. With `now`, hours already past are left out: the first day is scored from the current
    UTC hour (review 15 Nov 2026: 11 of 18 warnings were issued 17 to 23 hours into the day they forecast, so they
    counted hours that had already happened)."""
    times = zone_models[0]["hourly"]["time"]
    start = first_hour_index(times, now)
    native_all = [int(t[11:13]) % NATIVE_STEP_HOURS == 0 for t in times]
    model_ids = model_ids or [f"model{k + 1}" for k in range(len(zone_models))]
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
            per_model = {}
            for m_idx, zm in enumerate(zone_models):
                pm = {"members": 0, "amber_hits": 0, "red_hits": 0}
                members = member_series(zm["hourly"], h["var"])
                if members:
                    models_contributing.add(m_idx)
                    hazard_models.add(m_idx)
                with_members = member_series(zm["hourly"], h["with"]["var"]) if h["agg"] == "joint" else None
                for k, m in enumerate(members):
                    m2 = with_members[k][i0:i1] if (with_members and k < len(with_members)) else None
                    ah, rh, sval = member_hits_day(h, m[i0:i1], m2, native_all[i0:i1])
                    if h["agg"] == "joint" and m2 is None:
                        continue
                    if sval is None and h["agg"] != "joint":
                        continue
                    total += 1
                    pm["members"] += 1
                    if ah:
                        amber_hits += 1
                        pm["amber_hits"] += 1
                    if rh:
                        red_hits += 1
                        pm["red_hits"] += 1
                    if sval is not None:
                        stat_vals.append(sval)
                if pm["members"]:
                    per_model[model_ids[m_idx]] = pm
            if total == 0:
                continue
            # Each model weighs the same, whatever its member count (16 Nov 2026; pooled by count, ECMWF's
            # 51 members had carried 62% of the weight). Each model's own share is kept beside it.
            pa = sum(v["amber_hits"] / v["members"] for v in per_model.values()) / len(per_model)
            pr = sum(v["red_hits"] / v["members"] for v in per_model.values()) / len(per_model)
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
                "models": len(hazard_models), "by_model": per_model,
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


def add_ceiling(zones, by_model, now, opener=None, procs=gefs.PROCS):
    """Put the GEFS ceiling members (tools/gefs.py) into the GEFS answers' hourly series, so the "ceiling" hazard is
    scored like the others. Returns the run info for the doc; with GEFS left out of this run, nothing is added."""
    answers = by_model.get("ncep_gefs025")
    if not answers or not answers[0] or not (answers[0].get("hourly") or {}).get("time"):
        return {"source": gefs.SOURCE["id"], "state": "unavailable", "why": "GEFS is not in this run", "run_time": None}
    times = answers[0]["hourly"]["time"]
    series, info = gefs.ceiling_series(zones, times, now, opener=opener, procs=procs)
    if series:
        for a, s in zip(answers, series):
            if s and a and (a.get("hourly") or {}).get("time") == times:
                a["hourly"].update(s)
    return info


def build_forecast_doc(zones, by_model, days=7, generated_at=None, runs=None, grids=None, ceiling=None):
    """`runs` is {model id: model_run() result}; a model missing from `by_model` was left out (stale run)."""
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    runs = runs or {}
    out_zones = []
    for i, z in enumerate(zones):
        ids = [m for m, _ in MODELS if m in by_model]
        z_days = score_zone_days([by_model[m][i] for m in ids], days, now=now, model_ids=ids)
        for d in z_days:
            day_dt = datetime.strptime(d["day"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d["night"] = astro.night(z["lat"], z["lon"], day_dt)
            d["sun"] = astro.sun_day(z["lat"], z["lon"], day_dt)
        zone_id = z.get("zone_id", z["id"])
        out_zones.append({
            "id": z["id"], "zone_id": zone_id, "role": z.get("role", "reference"), "name": z["name"],
            "warning_problem_id": z.get("warning_problem_id") or f"WF-WP-Z{zone_id[:2]}",
            "station": z.get("station", z["name"]), "icao": z.get("icao", "ZZZZ"),
            "countries": z.get("countries", []),
            "lat": z["lat"], "lon": z["lon"], "tz": z.get("tz"),
            "grid": (grids[i] if grids else None),
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
        "native_step_hours": NATIVE_STEP_HOURS, "grid_max_diff_m": GRID_MAX_DIFF_M,
        "model_weighting": "equal",
        "ceiling": ceiling or {"source": gefs.SOURCE["id"], "state": "unavailable", "why": "not read", "run_time": None},
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

    first = next(iter(by_model.values()))
    grids = grid_check(zones, first)
    bad = [f"{z.get('icao')} ({g['diff_m']:+.0f} m)" for z, g in zip(zones, grids) if g["ok"] is False]
    print(f"grid check: {sum(1 for g in grids if g['ok'])} of {len(zones)} points within {GRID_MAX_DIFF_M:g} m"
          + (f"; off: {', '.join(bad)}" if bad else ""), flush=True)
    print("reading GEFS cloud ceiling from NOAA's files (noaa-gefs-pds)...", flush=True)
    ceiling = add_ceiling(zones, by_model, now)
    print(f"ceiling: {ceiling.get('state')}, run {ceiling.get('run_time') or 'none'}"
          + (f", {ceiling.get('steps')} steps in {ceiling.get('seconds')} s" if ceiling.get("state") == "used" else "")
          + (f" ({ceiling['why']})" if ceiling.get("why") else ""), flush=True)
    result = build_forecast_doc(zones, by_model, days=a.days, generated_at=generated_at, runs=runs, grids=grids,
                                ceiling=ceiling)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "forecast.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
