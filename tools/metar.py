"""METAR store for verification (plan step W3): append-only reports from aviationweather.gov, one file per UTC day.

    python tools/metar.py [--hours 200] [--dir history/metar]

Source: NOAA Aviation Weather Center data API (https://aviationweather.gov/data/api/), US public domain. One call per
station, since a reply holds at most 400 reports (checked 24 Nov 2026); 200 hours covers a half-hourly station, and
every build asks again, so a missed build loses nothing while AWC still holds the reports (about 8 days at that rate).

Layout: history/metar/YYYY/MM/DD.jsonl, one report per line, keyed by station, observation time and raw text. A report
already stored is never rewritten or removed; a new one is appended. A station that answers with no reports is
counted in the run's summary and nothing is written for it: missing reports stay missing, never filled. Reports
stored since 26 Nov 2026 also keep AWC's dew point, wind direction, altimeter, weather, cloud layers and flight
category, for the location pop-up's latest-report card (`newest()`); earlier lines lack them.

The same service's TAFs (`fetch_tafs()`, one call for every station) feed the pop-up's TAF; they are not stored here
(each build's locations.json, archived per build, keeps the TAFs it showed). Standard library only.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://aviationweather.gov/api/data/metar?ids={ids}&format=json&hours={hours}"
TAF_API = "https://aviationweather.gov/api/data/taf?ids={ids}&format=json"
UA = "tic-vis/1.0 (+https://vis.theintelligence.company)"
KEEP = ("icao", "obs_time", "type", "temp_c", "dewp_c", "wdir", "wspd_kt", "wgst_kt", "visib", "altim_hpa", "wx",
        "clouds", "flt_cat", "raw")


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
        "dewp_c": _num(rec.get("dewp")),
        "wdir": rec.get("wdir") if isinstance(rec.get("wdir"), (int, float, str)) else None,   # degrees or "VRB"
        "wspd_kt": _num(rec.get("wspd")),
        "wgst_kt": _num(rec.get("wgst")),
        "visib": rec.get("visib"),
        "altim_hpa": _num(rec.get("altim")),
        "wx": rec.get("wxString") or None,
        "clouds": [{"cover": c.get("cover"), "base_ft": _num(c.get("base"))}
                   for c in rec.get("clouds") or [] if isinstance(c, dict)] or None,
        "flt_cat": rec.get("fltCat") or None,
        "raw": rec.get("rawOb"),
    }


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def key(r):
    return (r["icao"], r["obs_time"], r.get("raw") or "")


def fetch(icao, hours, opener=None, wait=time.sleep, url=None):
    url = url or API.format(ids=icao, hours=int(hours))
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


def newest(base, icaos, now, lookback_hours=48):
    """The newest stored report for each station, {ICAO: report}, from the day files covering `lookback_hours` before
    `now` (a UTC datetime). A report from after `now` is ignored. Stations with nothing in the window are left out."""
    lo = now - timedelta(hours=lookback_hours)
    days, d = [], lo.date()
    while d <= now.date():
        days.append(d.isoformat())
        d += timedelta(days=1)
    lo_s, hi_s = lo.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
    want, out = set(icaos), {}
    for icao, rows in load_range(base, days).items():
        if icao not in want:
            continue
        rows = [r for r in rows if lo_s <= r.get("obs_time", "") <= hi_s]
        if rows:   # the latest observation; a correction (same time, later in the file) wins over the original
            out[icao] = max(enumerate(rows), key=lambda x: (x[1]["obs_time"], x[0]))[1]
    return out


def _iso_utc(v):
    """AWC time (unix seconds or an ISO string) -> 'YYYY-MM-DDTHH:MM:SSZ', or None."""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(v, str) and v:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        pass
    return None


def norm_taf(rec):
    """AWC JSON TAF -> {icao, raw, issued, valid_from, valid_to} with UTC ISO times; None without the times."""
    if not isinstance(rec, dict) or not rec.get("icaoId"):
        return None
    t = {"icao": rec["icaoId"], "raw": rec.get("rawTAF"), "issued": _iso_utc(rec.get("issueTime")),
         "valid_from": _iso_utc(rec.get("validTimeFrom")), "valid_to": _iso_utc(rec.get("validTimeTo"))}
    return t if t["issued"] and t["valid_to"] and t["raw"] else None


def fetch_tafs(icaos, now, opener=None, wait=time.sleep):
    """The current TAF for each station, {ICAO: taf}: the latest issued whose validity has not ended at `now`. AWC
    answers with the last TAF it holds even when it ran out days ago, so an expired one is dropped here (the page
    checks again when it is read). One call for every station."""
    ids = ",".join(sorted(set(icaos)))
    if not ids:
        return {}
    rows = fetch(None, 0, opener=opener, wait=wait, url=TAF_API.format(ids=ids))
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for t in (norm_taf(r) for r in (rows if isinstance(rows, list) else [])):
        if not t or t["valid_to"] <= now_s:
            continue
        if t["icao"] not in out or t["issued"] > out[t["icao"]]["issued"]:
            out[t["icao"]] = t
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
