"""METAR store for verification (plan step W3): append-only reports from aviationweather.gov, one file per UTC day.

    python tools/metar.py [--hours 200] [--dir history/metar]

Source: NOAA Aviation Weather Center data API (https://aviationweather.gov/data/api/), US public domain. One call per
station, since a reply holds at most 400 reports (checked 24 Nov 2026); 200 hours covers a half-hourly station, and
every build asks again, so a missed build loses nothing while AWC still holds the reports (about 8 days at that rate).

Layout: history/metar/YYYY/MM/DD.jsonl, one report per line, keyed by station, observation time and raw text. A report
already stored is never rewritten or removed; a new one is appended. A station that answers with no reports is
counted in the run's summary and nothing is written for it: missing reports stay missing, never filled.
Standard library only.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://aviationweather.gov/api/data/metar?ids={ids}&format=json&hours={hours}"
UA = "tic-vis/1.0 (+https://vis.theintelligence.company)"
KEEP = ("icao", "obs_time", "type", "temp_c", "wspd_kt", "wgst_kt", "visib", "raw")


def stations(points_path=None):
    doc = json.loads(Path(points_path or ROOT / "config" / "points.json").read_text(encoding="utf-8"))
    rows = (doc.get("zones") or doc.get("points") or []) if isinstance(doc, dict) else doc
    out = []
    for r in rows if isinstance(rows, list) else []:
        for k in ("icao", "alternate_icao"):
            if isinstance(r, dict) and r.get(k):
                out.append(r[k])
        for alt in (r.get("alternates") or []) if isinstance(r, dict) else []:
            if isinstance(alt, dict) and alt.get("icao"):
                out.append(alt["icao"])
    return sorted(set(out))


def norm(rec):
    """AWC JSON report -> the stored fields (UTC ISO time; raw text kept so nothing is lost)."""
    t = rec.get("obsTime")
    if not isinstance(t, (int, float)) or not rec.get("icaoId"):
        return None
    return {
        "icao": rec["icaoId"],
        "obs_time": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": rec.get("metarType"),
        "temp_c": rec.get("temp"),
        "wspd_kt": rec.get("wspd") if isinstance(rec.get("wspd"), (int, float)) else None,
        "wgst_kt": rec.get("wgst") if isinstance(rec.get("wgst"), (int, float)) else None,
        "visib": rec.get("visib"),
        "raw": rec.get("rawOb"),
    }


def key(r):
    return (r["icao"], r["obs_time"], r.get("raw") or "")


def fetch(icao, hours, opener=None, wait=time.sleep):
    url = API.format(ids=icao, hours=int(hours))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    op = opener or urllib.request.urlopen
    for attempt in range(3):
        try:
            with op(req, timeout=60) as resp:
                body = resp.read()
            return json.loads(body) if body.strip() else []
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            if attempt == 2:
                raise
            wait(10 * (attempt + 1))
    return []


def day_path(base, iso):
    return Path(base) / iso[:4] / iso[5:7] / f"{iso[8:10]}.jsonl"


def load_day(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def append(base, reports):
    """Append reports not yet stored. Returns the number appended. Existing lines are never touched."""
    by_day = {}
    for r in reports:
        by_day.setdefault(day_path(base, r["obs_time"]), []).append(r)
    added = 0
    for path, rows in sorted(by_day.items()):
        seen = {key(r) for r in load_day(path)}
        new = []
        for r in sorted(rows, key=key):
            if key(r) not in seen:
                seen.add(key(r))
                new.append(r)
        if new:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                for r in new:
                    f.write(json.dumps({k: r.get(k) for k in KEEP}, ensure_ascii=False, separators=(",", ":")) + "\n")
            added += len(new)
    return added


def load_range(base, days):
    """All stored reports for the given UTC days (YYYY-MM-DD), grouped by station."""
    out = {}
    for d in days:
        for r in load_day(day_path(base, d + "T")):
            out.setdefault(r["icao"], []).append(r)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=int, default=200)
    ap.add_argument("--dir", default=str(ROOT / "history" / "metar"))
    ap.add_argument("--points", default=None)
    a = ap.parse_args(argv)
    got, empty, failed, added = 0, [], [], 0
    for icao in stations(a.points):
        try:
            recs = [n for n in (norm(x) for x in fetch(icao, a.hours)) if n]
        except Exception as e:  # one station's failure never stops the others
            failed.append(f"{icao} ({type(e).__name__})")
            continue
        if not recs:
            empty.append(icao)
            continue
        got += len(recs)
        added += append(a.dir, recs)
    print(f"metar: {got} reports read, {added} new stored; no reports: {', '.join(empty) or 'none'}; "
          f"failed: {', '.join(failed) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
