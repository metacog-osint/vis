"""Vis's historical database (22 Nov 2026): one SQLite file rebuilt from the per-build archive.

    python tools/history.py build [--from DIR] [--db out/history.sqlite]
    python tools/history.py build --releases [--repo metacog-osint/vis]    # download from the monthly releases
    python tools/history.py stats [--db out/history.sqlite]

Every build since 18 Nov 2026 uploads its forecast.json (gzip) to the monthly release vis-data-YYYY-MM, and since
22 Nov 2026 its locations.json beside it (tools/archive.py). Those assets are the record: they are never overwritten,
so this database is a cache that can be thrown away and rebuilt, and loading the same asset twice changes nothing.

Tables (all times UTC):
  builds(asset, kind, generated_at, run_stamp)       one row per archive file loaded
  ensemble(generated_at, point_id, day, hazard, colour, p_amber, p_red, median, p90, members, lead_days)
  ensemble_day(generated_at, point_id, day, colour, lead_days)
  metar(icao, obs_time, point_id, temp_c, dewpoint_c, wind_dir, wind_kt, gust_kt, visibility, ceiling_ft,
        flight_category, weather, raw, seen_at)       unique per station and observation time
  taf(icao, issued, raw, valid_from, valid_to, seen_at)
  point_daily(generated_at, point_id, date, lead_days, tmax_c, tmin_c, prcp_mm, prcp_prob, gust_max_kmh,
              weather_code)                           every build's daily rows, past days (lead < 0) included
  daily_latest  (view) the newest build's row for each point and date: the best estimate of what happened

What it is for: verifying the ensemble against METARs, lead-time studies, and the location
pop-up's trends. Standard library only (sqlite3, gzip, urllib).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = "Vis/1.0 (+https://vis.theintelligence.company)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (asset TEXT PRIMARY KEY, kind TEXT, generated_at TEXT, run_stamp TEXT);
CREATE TABLE IF NOT EXISTS ensemble (generated_at TEXT, point_id TEXT, day TEXT, hazard TEXT, colour TEXT,
  p_amber REAL, p_red REAL, median REAL, p90 REAL, members INTEGER, lead_days INTEGER,
  PRIMARY KEY (generated_at, point_id, day, hazard));
CREATE TABLE IF NOT EXISTS ensemble_day (generated_at TEXT, point_id TEXT, day TEXT, colour TEXT, lead_days INTEGER,
  PRIMARY KEY (generated_at, point_id, day));
CREATE TABLE IF NOT EXISTS metar (icao TEXT, obs_time TEXT, point_id TEXT, temp_c REAL, dewpoint_c REAL,
  wind_dir TEXT, wind_kt REAL, gust_kt REAL, visibility TEXT, ceiling_ft REAL, flight_category TEXT, weather TEXT,
  raw TEXT, seen_at TEXT, PRIMARY KEY (icao, obs_time));
CREATE TABLE IF NOT EXISTS taf (icao TEXT, issued TEXT, raw TEXT, valid_from TEXT, valid_to TEXT, seen_at TEXT,
  PRIMARY KEY (icao, issued));
CREATE TABLE IF NOT EXISTS point_daily (generated_at TEXT, point_id TEXT, date TEXT, lead_days INTEGER,
  tmax_c REAL, tmin_c REAL, prcp_mm REAL, prcp_prob REAL, gust_max_kmh REAL, weather_code INTEGER,
  PRIMARY KEY (generated_at, point_id, date));
CREATE VIEW IF NOT EXISTS daily_latest AS
  SELECT p.* FROM point_daily p JOIN (SELECT point_id, date, MAX(generated_at) g FROM point_daily
    GROUP BY point_id, date) m ON p.point_id = m.point_id AND p.date = m.date AND p.generated_at = m.g;
"""


def _lead(day: str, generated_at: str) -> int | None:
    try:
        return (date.fromisoformat(day[:10]) - date.fromisoformat(generated_at[:10])).days
    except (TypeError, ValueError):
        return None


def load_forecast(con, doc: dict, asset: str, run_stamp: str | None = None) -> int:
    g = doc["generated_at"]
    n = 0
    for z in doc.get("zones") or []:
        for d in z.get("days") or []:
            lead = _lead(d["day"], g)
            con.execute("INSERT OR IGNORE INTO ensemble_day VALUES (?,?,?,?,?)", (g, z["id"], d["day"], d["colour"], lead))
            for hid, v in (d.get("hazards") or {}).items():
                con.execute("INSERT OR IGNORE INTO ensemble VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (g, z["id"], d["day"], hid, v.get("colour"), v.get("p_amber"), v.get("p_red"),
                             v.get("median"), v.get("p90"), v.get("members"), lead))
                n += 1
    con.execute("INSERT OR IGNORE INTO builds VALUES (?,?,?,?)", (asset, "forecast", g, run_stamp))
    return n


def load_locations(con, doc: dict, asset: str, run_stamp: str | None = None) -> int:
    g = doc["generated_at"]
    n = 0
    for p in doc.get("points") or []:
        now = p.get("now") or {}
        m = now.get("metar")
        if m and m.get("obs_time"):
            con.execute("INSERT OR IGNORE INTO metar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (p.get("icao"), m["obs_time"], p["id"], m.get("temp_c"), m.get("dewpoint_c"),
                         None if m.get("wind_dir_deg") is None else str(m.get("wind_dir_deg")), m.get("wind_kt"),
                         m.get("gust_kt"), None if m.get("visibility") is None else str(m.get("visibility")),
                         m.get("ceiling_ft"), m.get("flight_category"), m.get("weather"), m.get("raw"), g))
        t = now.get("taf")
        if t and t.get("issued"):
            con.execute("INSERT OR IGNORE INTO taf VALUES (?,?,?,?,?,?)",
                        (p.get("icao"), str(t["issued"]), t.get("raw"), t.get("valid_from"), t.get("valid_to"), g))
        dly = p.get("daily") or {}
        for i, day in enumerate(dly.get("date") or []):
            def col(k):
                arr = dly.get(k) or []
                return arr[i] if i < len(arr) else None
            con.execute("INSERT OR IGNORE INTO point_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (g, p["id"], day, _lead(day, g), col("temperature_2m_max"), col("temperature_2m_min"),
                         col("precipitation_sum"), col("precipitation_probability_max"), col("wind_gusts_10m_max"),
                         col("weather_code")))
            n += 1
    con.execute("INSERT OR IGNORE INTO builds VALUES (?,?,?,?)", (asset, "locations", g, run_stamp))
    return n


def load_asset(con, name: str, raw: bytes) -> str:
    """Load one archive file by its name (<run>.<kind>.gen-<build>.json.gz). Returns what happened."""
    if con.execute("SELECT 1 FROM builds WHERE asset = ?", (name,)).fetchone():
        return "already loaded"
    data = gzip.decompress(raw) if name.endswith(".gz") else raw
    doc = json.loads(data.decode("utf-8"))
    run_stamp = name.split(".", 1)[0]
    if ".locations." in name:
        return f"{load_locations(con, doc, name, run_stamp)} daily rows"
    if ".forecast." in name:
        return f"{load_forecast(con, doc, name, run_stamp)} hazard rows"
    return "skipped (unknown kind)"


def release_assets(repo: str, token: str | None = None):
    """(name, download url) for every asset in the repo's vis-data-* releases, oldest release first."""
    out, page = [], 1
    while True:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}",
                                     headers={"User-Agent": UA, "Accept": "application/vnd.github+json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            rels = json.loads(r.read().decode("utf-8"))
        if not rels:
            break
        for rel in rels:
            if rel.get("tag_name", "").startswith("vis-data-"):
                out += [(rel["tag_name"], a["name"], a["browser_download_url"]) for a in rel.get("assets") or []]
        page += 1
    out.sort()
    return [(n, u) for _, n, u in out]


def connect(db: Path):
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    return con


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["build", "stats"])
    ap.add_argument("--db", default=str(ROOT / "out" / "history.sqlite"))
    ap.add_argument("--from", dest="src", default=None, help="a folder of archive files (*.json.gz)")
    ap.add_argument("--releases", action="store_true", help="download from the repo's vis-data-* releases")
    ap.add_argument("--repo", default="metacog-osint/vis")
    a = ap.parse_args(argv)
    con = connect(Path(a.db))
    if a.cmd == "build":
        if a.src:
            for f in sorted(Path(a.src).glob("*.json*")):
                print(f"{f.name}: {load_asset(con, f.name, f.read_bytes())}")
        if a.releases:
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            for name, url in release_assets(a.repo, token):
                if con.execute("SELECT 1 FROM builds WHERE asset = ?", (name,)).fetchone():
                    continue
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/octet-stream"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    print(f"{name}: {load_asset(con, name, r.read())}")
        con.commit()
    for t in ("builds", "ensemble", "ensemble_day", "metar", "taf", "point_daily"):
        print(f"{t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
