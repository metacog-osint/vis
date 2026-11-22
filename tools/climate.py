"""Climatology for each forecast point (22 Nov 2026): daily normals, records for every calendar day, monthly normals
and the Köppen-Geiger climate class, from the ERA5 reanalysis on Open-Meteo's Historical Weather API.

    python tools/climate.py [--cache out/climate.json] [--max-points 4] [--first-year 1991] [--last-year 2025]
    python tools/climate.py --probe        # one point, one year: checks the call and the answer's shape

The cache is filled a few points per run and kept between runs (a release asset, `vis-climate`, in CI), because a
multi-decade request counts as many Open-Meteo calls (requests over 2 weeks per location count as more than one
call; free tier 600 a minute, 5,000 an hour, 10,000 a day, shared with the twice-daily ensemble run). A point whose
entry matches the current period and method version is skipped, so a finished cache costs nothing.

Normals are 1991-2020 (the WMO standard period); records run from --first-year to --last-year, so they are "the
highest in ERA5 since 1991", not station records. ERA5 is a 0.25 deg (about 25 km) grid: a record is the grid
cell's, which can differ from the station's by several degrees on a coast or in hills. Days are local calendar
days (timezone=auto). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fetch  # noqa: E402

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SCHEMA = "vis-climate/1"
METHOD = "1"                       # bump when the computation changes; entries with another method are rebuilt
NORMAL_PERIOD = (1991, 2020)
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum"
CHUNK_YEARS = 5                    # one request per 5 years per point keeps each request's weight modest
PAUSE_S = 15                       # between requests, so a run never bunches its calls into one minute
SMOOTH_DAYS = 7                    # daily normals are a centred 15-day mean of the 30-year day means
SOURCE = {
    "id": "open-meteo-era5",
    "name": "ERA5 reanalysis (ECMWF / Copernicus Climate Change Service) via Open-Meteo Historical Weather API",
    "url": "https://open-meteo.com/en/docs/historical-weather-api",
    "licence": "CC BY 4.0 (Open-Meteo); free API for non-commercial use",
    "attribution": "Contains modified Copernicus Climate Change Service information; weather data by Open-Meteo.com",
}

# Calendar of 366 month-days (a leap year), index 0 = 01-01, 59 = 02-29.
MD = [date(2000, 1, 1).fromordinal(date(2000, 1, 1).toordinal() + i).strftime("%m-%d") for i in range(366)]
MD_INDEX = {md: i for i, md in enumerate(MD)}

KOPPEN_NAMES = {
    "Af": "Tropical rainforest", "Am": "Tropical monsoon", "Aw": "Tropical savanna",
    "BWh": "Hot desert", "BWk": "Cold desert", "BSh": "Hot semi-arid", "BSk": "Cold semi-arid",
    "Csa": "Hot-summer Mediterranean", "Csb": "Warm-summer Mediterranean", "Csc": "Cold-summer Mediterranean",
    "Cwa": "Monsoon-influenced humid subtropical", "Cwb": "Subtropical highland", "Cwc": "Cold subtropical highland",
    "Cfa": "Humid subtropical", "Cfb": "Temperate oceanic", "Cfc": "Subpolar oceanic",
    "Dsa": "Hot-summer continental, dry summer", "Dsb": "Warm-summer continental, dry summer",
    "Dsc": "Subarctic, dry summer", "Dsd": "Extremely cold subarctic, dry summer",
    "Dwa": "Monsoon-influenced hot-summer continental", "Dwb": "Monsoon-influenced warm-summer continental",
    "Dwc": "Monsoon-influenced subarctic", "Dwd": "Monsoon-influenced extremely cold subarctic",
    "Dfa": "Hot-summer humid continental", "Dfb": "Warm-summer humid continental", "Dfc": "Subarctic",
    "Dfd": "Extremely cold subarctic", "ET": "Tundra", "EF": "Ice cap",
}


def koppen(t_month, p_month, lat):
    """Köppen-Geiger class from 12 monthly mean temperatures (C) and totals (mm), by Peel, Finlayson and McMahon
    (2007), with 0 C between C and D. Summer is April-September north of the equator, October-March south of it."""
    T, P = list(t_month), list(p_month)
    summer = [3, 4, 5, 6, 7, 8] if lat >= 0 else [9, 10, 11, 0, 1, 2]
    winter = [m for m in range(12) if m not in summer]
    mat, map_ = sum(T) / 12, sum(P)
    thot, tcold = max(T), min(T)
    pdry = min(P)
    psdry, pswet = min(P[m] for m in summer), max(P[m] for m in summer)
    pwdry, pwwet = min(P[m] for m in winter), max(P[m] for m in winter)
    p_summer = sum(P[m] for m in summer)
    if map_ > 0 and (map_ - p_summer) >= 0.7 * map_:
        pth = 2 * mat
    elif map_ > 0 and p_summer >= 0.7 * map_:
        pth = 2 * mat + 28
    else:
        pth = 2 * mat + 14
    if thot < 10:
        return "ET" if thot > 0 else "EF"
    if map_ < 10 * pth:
        return ("BW" if map_ < 5 * pth else "BS") + ("h" if mat >= 18 else "k")
    if tcold >= 18:
        if pdry >= 60:
            return "Af"
        return "Am" if pdry >= 100 - map_ / 25 else "Aw"
    first = "C" if tcold > 0 else "D"
    if psdry < 40 and psdry < pwwet / 3:
        second = "s"
    elif pwdry < pswet / 10:
        second = "w"
    else:
        second = "f"
    warm_months = sum(1 for t in T if t >= 10)
    if thot >= 22:
        third = "a"
    elif warm_months >= 4:
        third = "b"
    elif first == "D" and tcold < -38:
        third = "d"
    else:
        third = "c"
    return first + second + third


def _r(x, n=1):
    return None if x is None else round(x, n)


def summarise(daily, first_year, last_year, lat):
    """daily: Open-Meteo `daily` dict (time as YYYY-MM-DD). Returns the climate entry for one point."""
    n0, n1 = NORMAL_PERIOD
    sums = {k: [0.0] * 366 for k in ("tmax", "tmin", "prcp")}
    cnts = {k: [0] * 366 for k in ("tmax", "tmin", "prcp")}
    rec = {"tmax_hi": [None] * 366, "tmax_hi_year": [None] * 366, "tmin_lo": [None] * 366,
           "tmin_lo_year": [None] * 366, "prcp_hi": [None] * 366, "prcp_hi_year": [None] * 366,
           "tmax_lo": [None] * 366, "tmin_hi": [None] * 366}
    mon = {k: [[0.0, 0] for _ in range(12)] for k in ("tmax", "tmin", "prcp")}
    years_seen = set()
    src = {"tmax": daily.get("temperature_2m_max") or [], "tmin": daily.get("temperature_2m_min") or [],
           "prcp": daily.get("precipitation_sum") or []}
    for i, t in enumerate(daily.get("time") or []):
        y = int(t[:4])
        md = MD_INDEX[t[5:10]]
        m = int(t[5:7]) - 1
        vals = {k: (src[k][i] if i < len(src[k]) else None) for k in src}
        if any(v is not None for v in vals.values()):
            years_seen.add(y)
        for k, v in vals.items():
            if v is None:
                continue
            if n0 <= y <= n1:
                sums[k][md] += v
                cnts[k][md] += 1
                mon[k][m][0] += v
                mon[k][m][1] += 1
        tx, tn, pr = vals["tmax"], vals["tmin"], vals["prcp"]
        if tx is not None:
            if rec["tmax_hi"][md] is None or tx > rec["tmax_hi"][md]:
                rec["tmax_hi"][md], rec["tmax_hi_year"][md] = tx, y
            if rec["tmax_lo"][md] is None or tx < rec["tmax_lo"][md]:
                rec["tmax_lo"][md] = tx
        if tn is not None:
            if rec["tmin_lo"][md] is None or tn < rec["tmin_lo"][md]:
                rec["tmin_lo"][md], rec["tmin_lo_year"][md] = tn, y
            if rec["tmin_hi"][md] is None or tn > rec["tmin_hi"][md]:
                rec["tmin_hi"][md] = tn
        if pr is not None and (rec["prcp_hi"][md] is None or pr > rec["prcp_hi"][md]):
            rec["prcp_hi"][md], rec["prcp_hi_year"][md] = pr, y

    def smooth(k):
        raw = [sums[k][i] / cnts[k][i] if cnts[k][i] else None for i in range(366)]
        out = []
        for i in range(366):
            w = [raw[(i + j) % 366] for j in range(-SMOOTH_DAYS, SMOOTH_DAYS + 1)]
            w = [x for x in w if x is not None]
            out.append(_r(sum(w) / len(w), 2 if k == "prcp" else 1) if w else None)
        return out

    m_tmax = [mon["tmax"][m][0] / mon["tmax"][m][1] if mon["tmax"][m][1] else None for m in range(12)]
    m_tmin = [mon["tmin"][m][0] / mon["tmin"][m][1] if mon["tmin"][m][1] else None for m in range(12)]
    # Monthly precipitation total: the mean daily amount times the month's length (a mean over the period's years).
    mdays = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    m_prcp = [mon["prcp"][m][0] / mon["prcp"][m][1] * mdays[m] if mon["prcp"][m][1] else None for m in range(12)]
    m_tmean = [(a + b) / 2 if a is not None and b is not None else None for a, b in zip(m_tmax, m_tmin)]
    k = None
    if all(x is not None for x in m_tmean + m_prcp):
        k = koppen(m_tmean, m_prcp, lat)
    return {
        "method": METHOD,
        "normals_period": f"{n0}-{n1}",
        "records_period": f"{first_year}-{last_year}",
        "years_with_data": len(years_seen),
        "koppen": k, "koppen_name": KOPPEN_NAMES.get(k),
        "monthly": {"tmean_c": [_r(x) for x in m_tmean], "tmax_c": [_r(x) for x in m_tmax],
                    "tmin_c": [_r(x) for x in m_tmin], "prcp_mm": [_r(x, 0) for x in m_prcp],
                    "annual_tmean_c": _r(sum(m_tmean) / 12) if all(x is not None for x in m_tmean) else None,
                    "annual_prcp_mm": _r(sum(m_prcp), 0) if all(x is not None for x in m_prcp) else None},
        # 366 values each, index = MD (month-day of a leap year; 59 is 29 Feb)
        "daily": {"normal_tmax_c": smooth("tmax"), "normal_tmin_c": smooth("tmin"), "normal_prcp_mm": smooth("prcp"),
                  "record_tmax_c": [_r(x) for x in rec["tmax_hi"]], "record_tmax_year": rec["tmax_hi_year"],
                  "record_tmin_c": [_r(x) for x in rec["tmin_lo"]], "record_tmin_year": rec["tmin_lo_year"],
                  "record_prcp_mm": [_r(x) for x in rec["prcp_hi"]], "record_prcp_year": rec["prcp_hi_year"],
                  "lowest_tmax_c": [_r(x) for x in rec["tmax_lo"]], "highest_tmin_c": [_r(x) for x in rec["tmin_hi"]]},
    }


def fetch_point(z, first_year, last_year, wait=time.sleep, opener=None, pause_s=PAUSE_S):
    """All daily values for one point, in CHUNK_YEARS-year requests."""
    out = {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}
    y = first_year
    first = True
    while y <= last_year:
        y1 = min(last_year, y + CHUNK_YEARS - 1)
        if not first:
            wait(pause_s)
        first = False
        params = {"latitude": f"{z['lat']:.4f}", "longitude": f"{z['lon']:.4f}", "start_date": f"{y}-01-01",
                  "end_date": f"{y1}-12-31", "daily": DAILY, "models": "era5", "timezone": "auto"}
        if (z.get("cell") or "land") != "land":
            params["cell_selection"] = z["cell"]
        doc = fetch.get_json(ARCHIVE_URL, params, timeout=120, wait=wait, opener=opener)
        d = (doc or {}).get("daily") or {}
        for k in out:
            out[k].extend(d.get(k) or [])
        y = y1 + 1
    return out


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("schema") == SCHEMA:
            return doc
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA, "source": SOURCE, "points": {}}


def save_cache(path, doc):
    doc["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def needs(entry, first_year, last_year):
    return not entry or entry.get("method") != METHOD or entry.get("records_period") != f"{first_year}-{last_year}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=os.path.join(ROOT, "out", "climate.json"))
    ap.add_argument("--points", default=os.path.join(ROOT, "config", "points.json"))
    ap.add_argument("--max-points", type=int, default=4)
    ap.add_argument("--first-year", type=int, default=NORMAL_PERIOD[0])
    ap.add_argument("--last-year", type=int, default=datetime.now(timezone.utc).year - 1)
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args(argv)
    import forecast  # noqa: E402 - the same point list as the forecast
    zones = forecast.expand_points(forecast.load_zones(a.points))
    if a.probe:
        z = zones[0]
        d = fetch_point(z, a.last_year, a.last_year)
        n = len(d["time"])
        print(f"probe {z['icao']}: {n} days for {a.last_year}; first {d['time'][:1]}, "
              f"tmax {d['temperature_2m_max'][:3]}, tmin {d['temperature_2m_min'][:3]}, prcp {d['precipitation_sum'][:3]}")
        return 0 if n >= 365 else 1
    cache = load_cache(a.cache)
    cache["source"] = SOURCE
    todo = [z for z in zones if needs(cache["points"].get(z["id"]), a.first_year, a.last_year)]
    print(f"climate: {len(zones) - len(todo)} of {len(zones)} points cached; building up to {a.max_points}", flush=True)
    done = 0
    for z in todo[: a.max_points]:
        try:
            d = fetch_point(z, a.first_year, a.last_year)
        except fetch.Refused as e:
            print(f"  {z['icao']}: Open-Meteo refused ({e}); stopping this run, the rest wait for the next", flush=True)
            break
        except Exception as e:  # noqa: BLE001 - one point's failure never loses the others
            print(f"  {z['icao']}: failed ({e.__class__.__name__}: {e})", flush=True)
            continue
        entry = summarise(d, a.first_year, a.last_year, z["lat"])
        entry["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache["points"][z["id"]] = entry
        save_cache(a.cache, cache)          # saved after every point, so a timeout keeps what was done
        done += 1
        print(f"  {z['icao']}: {entry['years_with_data']} years, Köppen {entry['koppen']}", flush=True)
        time.sleep(PAUSE_S)
    save_cache(a.cache, cache)
    left = sum(1 for z in zones if needs(cache["points"].get(z["id"]), a.first_year, a.last_year))
    print(f"climate: built {done}; {left} points still to build", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
