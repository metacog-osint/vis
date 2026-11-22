"""Location detail for every forecast point (22 Nov 2026): what the pop-up on the page shows and what COP can read.

    python tools/locations.py [--forecast out/forecast.json] [--climate out/climate.json] [--out out/locations.json]
    python tools/locations.py --probe      # fetch for real and print one line per point (an informational check)

For each of Vis's points it gathers, in four calls for all points together:
  - Open-Meteo's forecast API (its best-match deterministic models): hourly weather for the past 7 days and the next
    7, daily highs and lows, and the current state, on the point's own grid cell choice (land, sea or nearest);
  - the latest METAR and TAF for the station from the Aviation Weather Center (aviationweather.gov, US Government
    data, public domain), when the station issues them;
and joins what Vis already has: the ensemble hazard days and the sun and moon from out/forecast.json, the station
and model-cell elevations from the forecast's grid check, and the climatology (normals, records for each calendar
day, Köppen-Geiger class) from out/climate.json when tools/climate.py has built the point.

A part that cannot be fetched is left null and named in `errors`; this step never fails the build, because the
ensemble forecast is the product and this is its context. Output schema `vis-location/1` (docs/location-data.md).
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import climate  # noqa: E402
import fetch  # noqa: E402

SCHEMA = "vis-location/1"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
METAR_URL = "https://aviationweather.gov/api/data/metar"
TAF_URL = "https://aviationweather.gov/api/data/taf"
PAST_DAYS = 7
FORECAST_DAYS = 7
# 15 hourly variables over 14 days count as 1.5 Open-Meteo calls a point (pricing page, read 22 Nov 2026): about 50
# calls a run for 31 points, beside the ensemble's several hundred.
HOURLY = ["temperature_2m", "dew_point_2m", "relative_humidity_2m", "apparent_temperature", "precipitation",
          "precipitation_probability", "weather_code", "cloud_cover", "cloud_cover_low", "visibility",
          "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl", "cape"]
DAILY = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
         "precipitation_probability_max", "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant"]
CURRENT = ["temperature_2m", "relative_humidity_2m", "apparent_temperature", "weather_code", "cloud_cover",
           "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl", "precipitation"]
UNITS = {"temperature_2m": "C", "dew_point_2m": "C", "relative_humidity_2m": "%", "apparent_temperature": "C",
         "precipitation": "mm", "precipitation_probability": "%", "weather_code": "WMO 4677 code",
         "cloud_cover": "%", "cloud_cover_low": "%", "visibility": "m", "wind_speed_10m": "km/h",
         "wind_direction_10m": "deg", "wind_gusts_10m": "km/h", "pressure_msl": "hPa", "cape": "J/kg"}
SOURCES = [
    {"id": "open-meteo-forecast", "name": "Open-Meteo Forecast API (best-match deterministic models)",
     "url": "https://open-meteo.com/en/docs", "licence": "CC BY 4.0; free API for non-commercial use",
     "attribution": "Weather data by Open-Meteo.com"},
    {"id": "awc-metar-taf", "name": "NOAA Aviation Weather Center data API (METAR, TAF)",
     "url": "https://aviationweather.gov/data/api/", "licence": "US Government work, public domain",
     "attribution": "NOAA/NWS Aviation Weather Center"},
    climate.SOURCE,
    {"id": "open-meteo-elevation", "name": "Open-Meteo Elevation API (Copernicus DEM GLO-90)",
     "url": "https://open-meteo.com/en/docs/elevation-api", "licence": "CC BY 4.0 (Open-Meteo); Copernicus DEM licence",
     "attribution": "Copernicus DEM, ESA; via Open-Meteo.com"},
    {"id": "astro-meeus", "name": "Sun and moon computed by Vis (Meeus)", "url": "https://github.com/metacog-osint/vis",
     "licence": "Computed by Vis (code GNU AGPL-3.0)", "attribution": "Vis"},
]


def _round(v, n):
    return None if v is None else round(v, n)


def _iso(ts):
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def fetch_forecast(points, opener=None, wait=time.sleep):
    """One Open-Meteo call per cell choice, answers back in the points' order (None where it failed)."""
    groups = {}
    for i, z in enumerate(points):
        groups.setdefault(z.get("cell") or (z.get("grid") or {}).get("cell") or "land", []).append(i)
    out = [None] * len(points)
    for cell, idx in groups.items():
        params = {
            "latitude": ",".join(f"{points[i]['lat']:.4f}" for i in idx),
            "longitude": ",".join(f"{points[i]['lon']:.4f}" for i in idx),
            "hourly": ",".join(HOURLY), "daily": ",".join(DAILY), "current": ",".join(CURRENT),
            "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS, "timezone": "auto", "timeformat": "unixtime",
        }
        if cell != "land":
            params["cell_selection"] = cell
        doc = fetch.get_json(FORECAST_URL, params, timeout=90, opener=opener, wait=wait)
        docs = doc if isinstance(doc, list) else [doc]
        for i, d in zip(idx, docs):
            out[i] = d
    return out


def fetch_awc(url, icaos, opener=None, wait=time.sleep, hours=None):
    """Latest report per station from the Aviation Weather Center, {ICAO: report}. One call for every station; the
    API answers at most 400 entries, far above 31 stations."""
    params = {"ids": ",".join(sorted(set(icaos))), "format": "json"}
    if hours:
        params["hours"] = hours
    doc = fetch.get_json(url, params, timeout=60, opener=opener, wait=wait, wait_s=30, empty_ok=True) or []
    latest = {}
    for r in doc if isinstance(doc, list) else []:
        k = r.get("icaoId")
        t = r.get("obsTime") or r.get("issueTime") or 0
        if isinstance(t, str):                       # TAF issueTime is an ISO string
            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            except ValueError:
                t = 0
        if k and (k not in latest or t > latest[k][0]):
            latest[k] = (t, r)
    return {k: v[1] for k, v in latest.items()}


def metar_view(r, now):
    """The fields the pop-up shows from an AWC METAR, with its age; wind in knots as reported."""
    if not r:
        return None
    t = r.get("obsTime")
    clouds = [{"cover": c.get("cover"), "base_ft": c.get("base")} for c in (r.get("clouds") or [])]
    ceiling = min((c["base_ft"] for c in clouds if c["cover"] in ("BKN", "OVC", "OVX") and c["base_ft"] is not None),
                  default=None)
    return {"raw": r.get("rawOb"), "obs_time": _iso(t), "age_min": round((now.timestamp() - t) / 60) if t else None,
            "temp_c": r.get("temp"), "dewpoint_c": r.get("dewp"), "wind_dir_deg": r.get("wdir"),
            "wind_kt": r.get("wspd"), "gust_kt": r.get("wgst"), "visibility": r.get("visib"),
            "altimeter_hpa": r.get("altim"), "weather": r.get("wxString"), "clouds": clouds, "ceiling_ft": ceiling,
            "flight_category": r.get("fltCat"), "station_name": r.get("name")}


def taf_view(r):
    if not r:
        return None
    return {"raw": r.get("rawTAF"), "issued": r.get("issueTime"),
            "valid_from": _iso(r.get("validTimeFrom")) if isinstance(r.get("validTimeFrom"), (int, float)) else r.get("validTimeFrom"),
            "valid_to": _iso(r.get("validTimeTo")) if isinstance(r.get("validTimeTo"), (int, float)) else r.get("validTimeTo")}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def daily_block(om, clim):
    """Daily rows (local calendar days from Open-Meteo, timezone=auto) joined with the normals and records for the
    same month-day."""
    d = (om or {}).get("daily") or {}
    off = (om or {}).get("utc_offset_seconds") or 0
    dates = [datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d") for t in d.get("time") or []]
    rows = {"date": dates}
    for k in DAILY:
        rows[k] = [_round(v, 1) if isinstance(v, float) else v for v in (d.get(k) or [None] * len(dates))]
    cd = (clim or {}).get("daily") or {}
    for k in ("normal_tmax_c", "normal_tmin_c", "normal_prcp_mm", "record_tmax_c", "record_tmax_year",
              "record_tmin_c", "record_tmin_year", "record_prcp_mm", "record_prcp_year"):
        arr = cd.get(k)
        rows[k] = [arr[climate.MD_INDEX[x[5:10]]] if arr else None for x in dates]
    return rows


def trends(rows, today):
    """Past 7 days and next 7 against the 1991-2020 normals: mean-temperature anomaly and rain against normal."""
    out = {}
    for name, sel in (("past_7_days", lambda x: x < today), ("next_7_days", lambda x: x >= today)):
        idx = [i for i, x in enumerate(rows["date"]) if sel(x)][-7:] if name == "past_7_days" else \
              [i for i, x in enumerate(rows["date"]) if sel(x)][:7]
        tm = [(rows["temperature_2m_max"][i] + rows["temperature_2m_min"][i]) / 2 for i in idx
              if rows["temperature_2m_max"][i] is not None and rows["temperature_2m_min"][i] is not None]
        nm = [(rows["normal_tmax_c"][i] + rows["normal_tmin_c"][i]) / 2 for i in idx
              if rows["normal_tmax_c"][i] is not None and rows["normal_tmin_c"][i] is not None]
        pr = [rows["precipitation_sum"][i] for i in idx if rows["precipitation_sum"][i] is not None]
        npr = [rows["normal_prcp_mm"][i] for i in idx if rows["normal_prcp_mm"][i] is not None]
        mt, mn = _mean(tm), _mean(nm)
        hi_rec = [rows["date"][i] for i in idx if rows["temperature_2m_max"][i] is not None
                  and rows["record_tmax_c"][i] is not None and rows["temperature_2m_max"][i] > rows["record_tmax_c"][i]]
        lo_rec = [rows["date"][i] for i in idx if rows["temperature_2m_min"][i] is not None
                  and rows["record_tmin_c"][i] is not None and rows["temperature_2m_min"][i] < rows["record_tmin_c"][i]]
        out[name] = {
            "days": len(idx), "from": rows["date"][idx[0]] if idx else None, "to": rows["date"][idx[-1]] if idx else None,
            "tmean_c": _round(mt, 1), "normal_tmean_c": _round(mn, 1),
            "tmean_anomaly_c": _round(mt - mn, 1) if mt is not None and mn is not None and len(tm) == len(nm) else None,
            "prcp_mm": _round(sum(pr), 1) if pr else None,
            "normal_prcp_mm": _round(sum(npr), 1) if npr and len(npr) == len(idx) else None,
            "above_record_high": hi_rec, "below_record_low": lo_rec,
            "basis": "Open-Meteo best-match models: the past days are the models' own analysis and short-range "
                     "forecasts, not observations",
        }
    return out


def hourly_block(om):
    h = (om or {}).get("hourly") or {}
    out = {"time": [_iso(t) for t in h.get("time") or []]}
    for k in HOURLY:
        n = 0 if k in ("weather_code", "relative_humidity_2m", "precipitation_probability", "cloud_cover",
                       "cloud_cover_low", "wind_direction_10m", "visibility", "cape") else 1
        out[k] = [_round(v, n) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                  for v in (h.get(k) or [None] * len(out["time"]))]
        if n == 0:
            out[k] = [int(v) if isinstance(v, float) else v for v in out[k]]
    return out


def build_point(z, om, metar, taf, clim, now, errors):
    today_local = None
    if om and om.get("utc_offset_seconds") is not None:
        today_local = (now + timedelta(seconds=om["utc_offset_seconds"])).strftime("%Y-%m-%d")
    rows = daily_block(om, clim)
    grid = z.get("grid") or {}
    cur = (om or {}).get("current") or {}
    today = {}
    if today_local and today_local in rows["date"]:
        i = rows["date"].index(today_local)
        today = {k: rows[k][i] for k in rows}
    days = z.get("days") or []
    return {
        "schema": SCHEMA, "id": z["id"], "zone_id": z.get("zone_id"), "role": z.get("role"), "name": z["name"],
        "station": z.get("station"), "icao": z.get("icao"), "countries": z.get("countries", []),
        "lat": z["lat"], "lon": z["lon"], "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page": f"https://vis.theintelligence.company/#loc-{z['id']}",
        "site": {
            "station_elevation_m": grid.get("station_elevation_m"),
            "ensemble_cell_elevation_m": grid.get("cell_elevation_m"),
            "forecast_cell_elevation_m": (om or {}).get("elevation"),
            "cell": grid.get("cell") or z.get("cell") or "land",
            "timezone": (om or {}).get("timezone"), "timezone_abbreviation": (om or {}).get("timezone_abbreviation"),
            "utc_offset_seconds": (om or {}).get("utc_offset_seconds"),
            "koppen": (clim or {}).get("koppen"), "koppen_name": (clim or {}).get("koppen_name"),
            "normals_period": (clim or {}).get("normals_period"), "records_period": (clim or {}).get("records_period"),
            "monthly_normals": (clim or {}).get("monthly"),
            "biome": None,
        },
        "now": {
            "metar": metar_view(metar, now), "taf": taf_view(taf),
            "model": ({"time": _iso(cur.get("time")), **{k: cur.get(k) for k in CURRENT}} if cur else None),
        },
        "today": {
            "date": today_local, "tmax_c": today.get("temperature_2m_max"), "tmin_c": today.get("temperature_2m_min"),
            "normal_tmax_c": today.get("normal_tmax_c"), "normal_tmin_c": today.get("normal_tmin_c"),
            "record_tmax_c": today.get("record_tmax_c"), "record_tmax_year": today.get("record_tmax_year"),
            "record_tmin_c": today.get("record_tmin_c"), "record_tmin_year": today.get("record_tmin_year"),
            "record_prcp_mm": today.get("record_prcp_mm"), "record_prcp_year": today.get("record_prcp_year"),
            "sun": (days[0].get("sun") if days else None), "night": (days[0].get("night") if days else None),
        },
        "ensemble_days": [{"day": d["day"], "colour": d["colour"], "hours": d.get("hours"),
                           "hazards": {k: {"colour": v["colour"], "p_amber": v["p_amber"], "p_red": v["p_red"],
                                           "median": v.get("median"), "p90": v.get("p90")}
                                       for k, v in (d.get("hazards") or {}).items()},
                           "sun": d.get("sun"), "night": d.get("night")} for d in days],
        "daily": rows,
        "hourly": hourly_block(om),
        "trends": trends(rows, today_local) if today_local else None,
        "units": UNITS,
        "errors": errors,
    }


def build(forecast_doc, clim_doc=None, now=None, opener=None, wait=time.sleep):
    now = now or datetime.now(timezone.utc)
    points = forecast_doc.get("zones") or []
    shared_errors = []
    try:
        oms = fetch_forecast(points, opener=opener, wait=wait)
    except Exception as e:  # noqa: BLE001 - context only; never a stop
        oms = [None] * len(points)
        shared_errors.append(f"forecast: {e.__class__.__name__}: {e}")
    icaos = [z["icao"] for z in points if z.get("icao") and z["icao"] != "ZZZZ"]
    metars, tafs = {}, {}
    try:
        metars = fetch_awc(METAR_URL, icaos, opener=opener, wait=wait, hours=3)
    except Exception as e:  # noqa: BLE001
        shared_errors.append(f"metar: {e.__class__.__name__}: {e}")
    try:
        tafs = fetch_awc(TAF_URL, icaos, opener=opener, wait=wait)
    except Exception as e:  # noqa: BLE001
        shared_errors.append(f"taf: {e.__class__.__name__}: {e}")
    cpts = (clim_doc or {}).get("points") or {}
    out = []
    for z, om in zip(points, oms):
        errs = list(shared_errors)
        if om is None and not any(e.startswith("forecast") for e in errs):
            errs.append("forecast: no answer for this point")
        if z["id"] not in cpts:
            errs.append("climate: not built yet for this point (tools/climate.py fills a few points a day)")
        out.append(build_point(z, om, metars.get(z.get("icao")), tafs.get(z.get("icao")), cpts.get(z["id"]), now, errs))
    return {
        "schema": "vis-locations/1", "program": "vis", "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecast_generated_at": forecast_doc.get("generated_at"),
        "climate_updated_at": (clim_doc or {}).get("updated_at"),
        "sources": SOURCES, "points": out,
    }


def index_row(p):
    """The compact row for data/locations.json and the feed: what a list or a map needs without the detail file."""
    m = (p.get("now") or {}).get("metar") or {}
    t = p.get("today") or {}
    sun = t.get("sun") or {}
    night = t.get("night") or {}
    s = p.get("site") or {}
    return {
        "id": p["id"], "zone_id": p.get("zone_id"), "role": p.get("role"), "name": p["name"],
        "station": p.get("station"), "icao": p.get("icao"), "lat": p["lat"], "lon": p["lon"],
        "elevation_m": s.get("station_elevation_m"), "koppen": s.get("koppen"), "timezone": s.get("timezone"),
        "metar_time": m.get("obs_time"), "metar_temp_c": m.get("temp_c"), "flight_category": m.get("flight_category"),
        "today_tmax_c": t.get("tmax_c"), "today_tmin_c": t.get("tmin_c"),
        "normal_tmax_c": t.get("normal_tmax_c"), "normal_tmin_c": t.get("normal_tmin_c"),
        "record_tmax_c": t.get("record_tmax_c"), "record_tmin_c": t.get("record_tmin_c"),
        "sunrise": sun.get("sunrise"), "sunset": sun.get("sunset"), "day_length_min": sun.get("day_length_min"),
        "moon_phase": night.get("moon_phase"), "moon_lit": night.get("moon_lit"),
        "detail": f"data/locations/{p['id']}.json",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forecast", default=os.path.join(ROOT, "out", "forecast.json"))
    ap.add_argument("--climate", default=os.path.join(ROOT, "out", "climate.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "locations.json"))
    ap.add_argument("--probe", action="store_true", help="print one line per point after the fetch")
    a = ap.parse_args(argv)
    if os.path.exists(a.forecast):
        with open(a.forecast, encoding="utf-8") as f:
            fc = json.load(f)
    else:                    # no ensemble run at hand (the PR probe): the points alone, with no hazard days
        import forecast  # noqa: E402
        print(f"locations: {a.forecast} not found; using config/points.json alone", flush=True)
        fc = {"zones": forecast.expand_points(forecast.load_zones()), "generated_at": None}
    clim = None
    if os.path.exists(a.climate):
        with open(a.climate, encoding="utf-8") as f:
            clim = json.load(f)
    doc = build(fc, clim)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    n = len(doc["points"])
    om_ok = sum(1 for p in doc["points"] if p["hourly"]["time"])
    met = sum(1 for p in doc["points"] if p["now"]["metar"])
    taf = sum(1 for p in doc["points"] if p["now"]["taf"])
    cl = sum(1 for p in doc["points"] if p["site"]["koppen"])
    print(f"locations: {n} points; forecast {om_ok}, METAR {met}, TAF {taf}, climate {cl}; wrote {a.out}", flush=True)
    if a.probe:
        for p in doc["points"]:
            m = p["now"]["metar"] or {}
            t = p["today"]
            print(f"  {p['icao']}: tz {p['site']['timezone']}, elev {p['site']['station_elevation_m']} m, "
                  f"hours {len(p['hourly']['time'])}, today {t['tmin_c']}/{t['tmax_c']} C, "
                  f"METAR {m.get('obs_time')} {m.get('flight_category')}, sunrise {(t['sun'] or {}).get('sunrise')}, "
                  f"errors {p['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
