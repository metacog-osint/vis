#!/usr/bin/env python3
"""Fetch storm and smoke observations for the weather map: US hail reports and North American smoke.

- Hail: NOAA Storm Prediction Center's preliminary local storm reports (today's and yesterday's hail CSVs; each
  convective day runs 12Z to 12Z). US public domain. Written to site/data/hail.json as points with size in inches.
- Smoke: NOAA NESDIS Hazard Mapping System smoke polygons (analysts' daily smoke analysis from GOES and polar
  satellites over North America; light, medium and heavy). US public domain. The newest day's KML is written to
  site/data/smoke.json as polygons by density.

Neither is global: no free service maps hail or smoke worldwide. Like the other map tools this never fails the
build; a source that cannot be read writes "ok": false and the map hides that layer. Reads time out after
TIMEOUT_S seconds, and a host that times out once is not asked again this run.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"}
SPC = "https://www.spc.noaa.gov/climo/reports/{day}_hail.csv"
HMS = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/KML/{d:%Y/%m}/hms_smoke{d:%Y%m%d}.kml"
KML = {"k": "http://www.opengis.net/kml/2.2"}
DENSITIES = ("Light", "Medium", "Heavy")


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TIMEOUT_S = 15      # per socket operation; a host that times out once is not asked again this run
_HUNG: set[str] = set()


def fetch(url: str, timeout: int = TIMEOUT_S) -> bytes:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in _HUNG:
        raise TimeoutError(f"{host} timed out earlier in this run; not asked again")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except (TimeoutError, urllib.error.URLError) as e:
        if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
            _HUNG.add(host)
        raise


def convective_day(now: datetime, back: int) -> datetime:
    """Start (12Z) of the SPC convective day `back` days before the one containing `now`."""
    start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start - timedelta(days=back)


def parse_hail(text: str, day_start: datetime) -> list[dict]:
    """SPC hail CSV rows as {lat, lon, in, time, place}. Size is in hundredths of an inch; time is HHMM UTC within
    the convective day (12Z to 12Z)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat, lon, size = float(row["Lat"]), float(row["Lon"]), int(row["Size"])
            hhmm = row["Time"].strip().zfill(4)
            t = day_start.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
            if t < day_start:
                t += timedelta(days=1)
        except (KeyError, ValueError, TypeError):
            continue
        place = ", ".join(x for x in (row.get("Location", "").strip().title(), row.get("State", "").strip()) if x)
        out.append({"lat": lat, "lon": lon, "in": size / 100, "time": iso(t), "place": place})
    return out


def hail(now: datetime, get=fetch, log=print) -> dict:
    doc = {"generated_at": iso(now), "ok": False, "reports": [],
           "credits": [{"text": "Hail reports: NOAA SPC (preliminary)", "url": "https://www.spc.noaa.gov/climo/reports/"}]}
    got = 0
    for back, name in ((0, "today"), (1, "yesterday")):
        try:
            rows = parse_hail(get(SPC.format(day=name)).decode("utf-8", "replace"), convective_day(now, back))
        except Exception as e:  # noqa: BLE001
            log(f"SPC {name} hail: not read ({type(e).__name__}: {e})")
            continue
        got += 1
        doc["reports"] += rows
        log(f"SPC {name} hail: {len(rows)} reports")
    doc["ok"] = got > 0
    doc["since"] = iso(convective_day(now, 1))
    return doc


def _density(pm: ET.Element, folder: str) -> str | None:
    text = " ".join(filter(None, [pm.findtext("k:description", namespaces=KML), pm.findtext("k:styleUrl", namespaces=KML),
                                  pm.findtext("k:name", namespaces=KML), folder]))
    m = re.search(r"\b(Light|Medium|Heavy)\b", text)
    return m.group(1) if m else None


def parse_smoke(kml: bytes, ndigits: int = 2) -> list[dict]:
    """HMS smoke KML as [{density, ring: [[lon, lat], ...]}], outer rings only, rounded to ndigits decimals."""
    root = ET.fromstring(kml)
    out = []
    for folder in root.iter("{%s}Folder" % KML["k"]):
        fname = folder.findtext("k:name", default="", namespaces=KML)
        for pm in folder.findall("k:Placemark", KML):
            dens = _density(pm, fname)
            for c in pm.iter("{%s}coordinates" % KML["k"]):
                ring = []
                for tok in (c.text or "").split():
                    bits = tok.split(",")
                    if len(bits) >= 2:
                        try:
                            ring.append([round(float(bits[0]), ndigits), round(float(bits[1]), ndigits)])
                        except ValueError:
                            pass
                if len(ring) >= 3 and dens:
                    out.append({"density": dens, "ring": ring})
    return out


def smoke(now: datetime, get=fetch, log=print) -> dict:
    doc = {"generated_at": iso(now), "ok": False, "polygons": [],
           "credits": [{"text": "Smoke: NOAA NESDIS Hazard Mapping System", "url": "https://www.ospo.noaa.gov/products/land/hms.html"}]}
    for back in range(3):
        d = now - timedelta(days=back)
        try:
            polys = parse_smoke(get(HMS.format(d=d)))
        except Exception as e:  # noqa: BLE001 - today's file appears during the day; fall back to an earlier day
            log(f"HMS smoke {d:%Y-%m-%d}: not read ({type(e).__name__}: {e})")
            continue
        counts = {k: sum(1 for p in polys if p["density"] == k) for k in DENSITIES}
        log(f"HMS smoke {d:%Y-%m-%d}: {len(polys)} polygons {counts}")
        doc.update(ok=True, date=f"{d:%Y-%m-%d}", polygons=polys)
        return doc
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(ROOT / "site" / "data"))
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    for name, doc in (("hail.json", hail(now)), ("smoke.json", smoke(now))):
        p = Path(a.dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, separators=(",", ":")))
        print(f"wrote {p} ({'ok' if doc['ok'] else 'unavailable'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
