"""Resolve the weather map's imagery layers and write site/data/wxmap.json for the page.

    python tools/wxmap.py [--out site/data/wxmap.json] [--gibs FILE] [--eumetview FILE]

The map draws imagery straight from open tile services, so nothing is stored or proxied here:

- NASA GIBS (WMTS, EPSG:3857): GOES-East, GOES-West and Himawari imagery, IMERG precipitation and GLM lightning.
  Each layer keeps its source's terms (NOAA GOES public domain; JMA Himawari, JMA's Public Data License with the
  source cited; NASA IMERG CC0); GIBS asks for an acknowledgement (shown on the map).
- EUMETSAT EUMETView (WMS): Meteosat Third Generation (0 degrees) imagery and Lightning Imager flash area, which
  fill Europe, Africa and the Middle East. EUMETSAT data policy: WMS visualisations are free and unrestricted under
  CC BY 4.0, credited "Contains modified EUMETSAT Meteosat data <year>".
- NOAA NCEP opengeo (WMS): MRMS radar mosaics for the US and its territories. US government work, public domain.
- ECCC GeoMet (WMS): Canada's radar composite and lightning density. Data Server End-use Licence (free use,
  credited "Data Source: Environment and Climate Change Canada").

Each product lists candidate layer names per satellite sector. This script reads each service's capabilities,
keeps the first candidate that exists, and records its tile address, time step and latest time. The page works
out the newest frames from the clock and the observed delay (latest time against this run's time), so a manifest
from the twice-daily build still gives current frames. A layer that a service no longer lists is left out and
named under "dropped"; a service that cannot be reached leaves only the other one. Each layer also records the
first time its service still lists ("first", "keep_s"), so the page never asks a radar server for a scan it no
longer keeps. The script never fails the build: with neither service it writes "ok": false and the page says
imagery is unavailable. Socket reads time out after TIMEOUT_S seconds, and a host that times out once is skipped
for the rest of the run, so a hung server costs the build well under a minute.

--gibs and --eumetview read capabilities from files instead (tests and offline runs); --offline then skips the
NOAA and ECCC services.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"}

GIBS_CAPS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml"
EUMETVIEW_WMS = "https://view.eumetsat.int/geoserver/wms"
# GeoServer workspaces read: MTG full disc (0 deg), MSG full disc (0 deg) and MSG Indian Ocean (45.5 deg E).
EUMETVIEW_WORKSPACES = ("mtg_fd", "msg_fes", "msg_iodc")
EUMETVIEW_CAPS = "https://view.eumetsat.int/geoserver/{ws}/wms?service=WMS&version=1.3.0&request=GetCapabilities"
# NOAA NCEP's open GeoServer: MRMS radar mosaics for the lower 48, Alaska, Hawaii, the Caribbean and Guam (US public
# domain). Times are irregular (about every 2 minutes); the server answers a request with the nearest scan.
NCEP_WMS = "https://opengeo.ncep.noaa.gov/geoserver/ows"
NCEP_WORKSPACES = ("conus", "alaska", "hawaii", "carib", "guam")
NCEP_CAPS = "https://opengeo.ncep.noaa.gov/geoserver/{ws}/ows?service=WMS&version=1.3.0&request=GetCapabilities"
# Environment and Climate Change Canada's GeoMet (Data Server End-use Licence: free use, credited
# "Data Source: Environment and Climate Change Canada"). Its full capabilities run to 40 MB, so each layer is read alone.
GEOMET_WMS = "https://geo.weather.gc.ca/geomet"
GEOMET_LAYERS = ("RADAR_1KM_RRAI", "RADAR_1KM_RSNO", "Lightning_2.5km_Density")
GEOMET_CAPS = GEOMET_WMS + "?service=WMS&version=1.3.0&request=GetCapabilities&layer={layer}"
WMS_BASE = {"eumetview": EUMETVIEW_WMS, "ncep": NCEP_WMS, "geomet": GEOMET_WMS}

NS = {"w": "http://www.opengis.net/wmts/1.0", "ows": "http://www.opengis.net/ows/1.1",
      "wms": "http://www.opengis.net/wms"}

# Sectors, drawn bottom to top. Each takes the longitudes where its satellite views best (a tile overlapping two
# sectors shows the upper one), so limb imagery stays out of the middle of another satellite's disc.
SECTORS = {
    "himawari": {"name": "Himawari (140.7°E)", "west": 100, "east": 180},
    "iodc": {"name": "Meteosat Indian Ocean (45.5°E)", "west": 30, "east": 100},
    "meteosat": {"name": "Meteosat (0°)", "west": -30, "east": 30},
    "goes_east": {"name": "GOES-East (75.2°W)", "west": -105, "east": -30},
    "goes_west": {"name": "GOES-West (137.2°W)", "west": -180, "east": -105},
    "global": {"name": "Global", "west": -180, "east": 180},
    # Regional radar and lightning, with latitude limits too (fewer empty tiles fetched).
    "canada": {"name": "Canada (ECCC radar)", "west": -170, "east": -48, "south": 38, "north": 84},
    "canada_snow": {"name": "Canada (ECCC radar, snow)", "west": -170, "east": -48, "south": 38, "north": 84},
    "conus": {"name": "US lower 48 (NOAA MRMS)", "west": -130, "east": -60, "south": 20, "north": 55},
    "alaska": {"name": "Alaska (NOAA MRMS)", "west": -180, "east": -125, "south": 50, "north": 73},
    "hawaii": {"name": "Hawaii (NOAA MRMS)", "west": -162, "east": -153, "south": 17, "north": 24},
    "carib": {"name": "Puerto Rico (NOAA MRMS)", "west": -69, "east": -63, "south": 15, "north": 21},
    "guam": {"name": "Guam (NOAA MRMS)", "west": 142, "east": 147, "south": 11, "north": 16},
}

# Candidates are tried in order: an exact name, or a pattern (re.fullmatch) when a service's naming is uncertain.
PRODUCTS = [
    {"id": "ir", "name": "Infrared", "group": "imagery",
     "note": "Clean longwave window (10.3–10.5 µm): cloud-top temperature, day and night. Meteosat's tiles are grey, colder and higher tops brighter; NASA's GOES and Himawari tiles are colour-enhanced, with the coldest tops in colour.",
     "sectors": {
         "goes_west": ("gibs", ["GOES-West_ABI_Band13_Clean_Infrared"]),
         "goes_east": ("gibs", ["GOES-East_ABI_Band13_Clean_Infrared"]),
         "meteosat": ("eumetview", ["mtg_fd:ir105_hrfi", r"mtg_fd:ir_?105\w*", "msg_fes:ir108"]),
         "iodc": ("eumetview", ["msg_iodc:ir108"]),
         "himawari": ("gibs", ["Himawari_AHI_Band13_Clean_Infrared"]),
     }},
    {"id": "wv", "name": "Water vapour", "group": "imagery",
     "note": "Upper-level water vapour (6.2 µm): moisture and dry slots in the middle and upper troposphere, jet streaks and troughs. Meteosat only (Europe, Africa, the Middle East, South Asia): NASA's tile service carries no GOES or Himawari water vapour.",
     "sectors": {
         "goes_west": ("gibs", [r"GOES-West_ABI_Band8_[\w.]*Water_Vapor[\w.]*", r"GOES-West_ABI_Band9_[\w.]*Water_Vapor[\w.]*"]),
         "goes_east": ("gibs", [r"GOES-East_ABI_Band8_[\w.]*Water_Vapor[\w.]*", r"GOES-East_ABI_Band9_[\w.]*Water_Vapor[\w.]*"]),
         "meteosat": ("eumetview", [r"mtg_fd:wv_?63\w*", "msg_fes:wv062"]),
         "iodc": ("eumetview", ["msg_iodc:wv062"]),
         "himawari": ("gibs", [r"Himawari_AHI_Band8_[\w.]*Water_Vapor[\w.]*", r"Himawari_AHI_Band9_[\w.]*Water_Vapor[\w.]*"]),
     }},
    {"id": "airmass", "name": "Air mass RGB", "group": "imagery",
     "note": "Air mass RGB: red and orange for dry, high-ozone stratospheric air (jets, deep troughs, turbulence risk), green for warm tropical air, blue and purple for cold air.",
     "sectors": {
         "goes_west": ("gibs", ["GOES-West_ABI_Air_Mass", r"GOES-West_ABI_Air_?Mass\w*"]),
         "goes_east": ("gibs", ["GOES-East_ABI_Air_Mass", r"GOES-East_ABI_Air_?Mass\w*"]),
         "meteosat": ("eumetview", ["mtg_fd:rgb_airmass", "msg_fes:rgb_airmass"]),
         "iodc": ("eumetview", ["msg_iodc:rgb_airmass"]),
         "himawari": ("gibs", ["Himawari_AHI_Air_Mass", r"Himawari_AHI_Air_?Mass\w*"]),
     }},
    {"id": "geocolor", "name": "GeoColor", "group": "imagery",
     "note": "GeoColor: true colour by day; by night, infrared cloud over city lights.",
     "sectors": {
         "goes_west": ("gibs", ["GOES-West_ABI_GeoColor"]),
         "goes_east": ("gibs", ["GOES-East_ABI_GeoColor"]),
         "meteosat": ("eumetview", ["mtg_fd:rgb_geocolour", r"mtg_fd:rgb_geocolou?r\w*"]),
         "himawari": ("gibs", [r"Himawari_AHI_GeoColor\w*", r"Himawari_AHI_\w*True_Color\w*"]),
     }},
    {"id": "dust", "name": "Dust RGB", "group": "imagery",
     "note": "Dust RGB: airborne dust and sand show pink and magenta, thick high cloud dark red, thin cirrus black. Himawari has no dust layer here, so East Asia and Australia are left out.",
     "sectors": {
         "goes_west": ("gibs", ["GOES-West_ABI_Dust"]),
         "goes_east": ("gibs", ["GOES-East_ABI_Dust"]),
         "meteosat": ("eumetview", ["mtg_fd:rgb_dust", "msg_fes:rgb_dust"]),
         "iodc": ("eumetview", ["msg_iodc:rgb_dust"]),
     }},
    {"id": "fog", "name": "Fog RGB", "group": "imagery",
     "note": "Fog and low cloud RGB from Meteosat: Europe, Africa, the Middle East and South Asia only. NASA's tile service carries no GOES or Himawari fog product.",
     "sectors": {
         "meteosat": ("eumetview", ["mtg_fd:rgb_fog", "msg_fes:rgb_fog"]),
         "iodc": ("eumetview", ["msg_iodc:rgb_fog"]),
     }},
    {"id": "aerosol", "name": "Smoke and haze (daily)", "group": "imagery", "frames": 1,
     "note": "Aerosol optical depth from the VIIRS satellites, once a day: smoke, haze and dust in yellow to red. Gaps are cloud or the satellites' night side; there is no loop.",
     "sectors": {
         "global": ("gibs", ["VIIRS_NOAA20_AOD_Deep_Blue_Land_Ocean", "VIIRS_NOAA21_AOD_Deep_Blue_Land_Ocean",
                             "VIIRS_SNPP_AOD_Deep_Blue_Land_Ocean", "MODIS_Combined_Value_Added_AOD"]),
     }},
    {"id": "precip", "name": "Precipitation (IMERG)", "group": "imagery",
     "note": "NASA IMERG precipitation rate, every 30 minutes, from microwave and infrared satellites. It runs hours behind real time.",
     "sectors": {
         "global": ("gibs", ["IMERG_Precipitation_Rate_30min", "IMERG_Precipitation_Rate"]),
     }},
    {"id": "radar", "name": "Radar", "group": "overlay",
     "note": "Weather radar where open radar data exist: NOAA MRMS for the US, Puerto Rico and Guam, and Environment and Climate Change Canada's composite (rain, and snow in blue). Europe's radar composite is open but comes as raw files, not map tiles.",
     "sectors": {
         "canada": ("geomet", ["RADAR_1KM_RRAI"]),
         "canada_snow": ("geomet", ["RADAR_1KM_RSNO"]),
         "conus": ("ncep", ["conus:conus_bref_qcd"]),
         "alaska": ("ncep", ["alaska:alaska_bref_qcd"]),
         "hawaii": ("ncep", ["hawaii:hawaii_bref_qcd"]),
         "carib": ("ncep", ["carib:carib_bref_qcd"]),
         "guam": ("ncep", ["guam:guam_bref_qcd"]),
     }},
    {"id": "rainrate", "name": "Rain rate (Meteosat)", "group": "overlay",
     "note": "Satellite rain rate every 15 minutes (EUMETSAT H SAF): Europe, Africa, the Middle East and South Asia, where there is no open radar mosaic. Less sharp than radar.",
     "sectors": {
         "meteosat": ("eumetview", ["msg_fes:h60b", "mtg_fd:h40b"]),
         "iodc": ("eumetview", ["msg_iodc:h63"]),
     }},
    {"id": "lightning", "name": "Lightning", "group": "overlay",
     "note": "Lightning from Meteosat's Lightning Imager (Europe, Africa, the Middle East and the Atlantic) and Canada's lightning network (Canada and the northern US). No free live lightning layer covers the rest of the Americas, Asia or the Pacific.",
     "sectors": {
         "goes_west": ("gibs", [r"GOES-West_GLM_\w*(Flash|Event|Group)\w*"]),
         "goes_east": ("gibs", [r"GOES-East_GLM_\w*(Flash|Event|Group)\w*"]),
         "canada": ("geomet", ["Lightning_2.5km_Density"]),
         "meteosat": ("eumetview", ["mtg_fd:li_afa", r"mtg_fd:li_\w*"]),
     }},
    {"id": "storms", "name": "Thunderstorm cells", "group": "overlay",
     "note": "Rapidly developing thunderstorm cells tracked by Meteosat (Europe and Africa): the nearest open stand-in for hail outside the US, where hail reports show.",
     "sectors": {
         "meteosat": ("eumetview", ["msg_fes:rdt"]),
     }},
]

_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def duration_s(text: str) -> int | None:
    """ISO 8601 duration (PT10M, PT1H, P1D) to seconds."""
    m = _DUR.match((text or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def parse_time(text: str) -> datetime | None:
    t = (text or "").strip()
    if not t:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        t += "T00:00:00Z"
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def time_range(values: list[str]) -> tuple[datetime | None, datetime | None, int | None]:
    """The first time, the latest time and the step from WMTS/WMS time values: "start/end/PT10M" intervals or comma
    lists. The first time is what the service still keeps: NOAA's radar server lists about 2 hours of scans and
    ECCC's 3 hours, and both answer a request for an older time with an empty tile or an error."""
    first, latest, step = None, None, None
    for v in values:
        for part in (v or "").split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split("/")
            start = parse_time(bits[0])
            end = parse_time(bits[1] if len(bits) >= 2 else bits[0])
            if start and (first is None or start < first):
                first = start
            if end and (latest is None or end > latest):
                latest = end
                step = duration_s(bits[2]) if len(bits) == 3 else step
    if step is None:   # a plain list: the gap between its last two entries
        times = sorted(t for v in values for t in (parse_time(p) for p in (v or "").split(",")) if t)
        if len(times) >= 2:
            step = int((times[-1] - times[-2]).total_seconds())
    return first, latest, step


def time_extent(values: list[str]) -> tuple[datetime | None, int | None]:
    """The latest time and the step (see time_range)."""
    _, latest, step = time_range(values)
    return latest, step


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def gibs_layers(xml_bytes: bytes) -> dict[str, dict]:
    root = ET.fromstring(xml_bytes)
    contents = root.find("w:Contents", NS)
    if contents is None:
        return {}
    max_zoom = {}
    for tms in contents.findall("w:TileMatrixSet", NS):
        ids = [m.findtext("ows:Identifier", namespaces=NS) for m in tms.findall("w:TileMatrix", NS)]
        nums = [int(i) for i in ids if i and i.isdigit()]
        if nums:
            max_zoom[tms.findtext("ows:Identifier", namespaces=NS)] = max(nums)
    out = {}
    for lyr in contents.findall("w:Layer", NS):
        ident = lyr.findtext("ows:Identifier", namespaces=NS)
        tms = lyr.findtext("w:TileMatrixSetLink/w:TileMatrixSet", namespaces=NS)
        url = None
        for r in lyr.findall("w:ResourceURL", NS):
            if r.get("resourceType") == "tile":
                url = r.get("template")
                break
        dim = None
        for d in lyr.findall("w:Dimension", NS):
            if (d.findtext("ows:Identifier", namespaces=NS) or "").lower() == "time":
                dim = d
        if not ident or not url or tms is None:
            continue
        first, latest, step = (time_range([v.text or "" for v in dim.findall("w:Value", NS)]) if dim is not None
                               else (None, None, None))
        out[ident] = {
            "url": (url.replace("{Time}", "{time}").replace("{TileMatrixSet}", tms)
                    .replace("{TileMatrix}", "{z}").replace("{TileRow}", "{y}").replace("{TileCol}", "{x}")),
            "max_zoom": max_zoom.get(tms),
            "first": first,
            "latest": latest,
            "step_s": step,
            "timed": dim is not None,
        }
    return out


def wms_layers(xml_bytes: bytes, workspace: str = "mtg_fd", qualify: bool = True) -> dict[str, dict]:
    """Named layers from a WMS 1.3.0 capabilities document, keyed "workspace:name" (a GeoServer workspace's own
    capabilities name its layers without the prefix), or by the bare name with qualify=False (GeoMet)."""
    root = ET.fromstring(xml_bytes)
    out = {}
    for lyr in root.iter("{%s}Layer" % NS["wms"]):
        name = lyr.findtext("wms:Name", namespaces=NS)
        if not name:
            continue
        values = [d.text or "" for d in lyr.findall("wms:Dimension", NS) if (d.get("name") or "").lower() == "time"]
        first, latest, step = time_range(values) if values else (None, None, None)
        if latest is None:
            for d in lyr.findall("wms:Dimension", NS):
                if (d.get("name") or "").lower() == "time" and d.get("default"):
                    latest = parse_time(d.get("default"))
        full = name if ":" in name or not qualify else f"{workspace}:{name}"
        out[full] = {"layer": full, "first": first, "latest": latest, "step_s": step, "timed": bool(values)}
    return out


def pick(available: dict[str, dict], candidates: list[str]) -> tuple[str, dict] | None:
    for c in candidates:
        if c in available:
            return c, available[c]
        try:
            rx = re.compile(c)
        except re.error:
            continue
        hits = sorted(k for k in available if rx.fullmatch(k))
        if hits:
            return hits[0], available[hits[0]]
    return None


def build_manifest(gibs: dict[str, dict] | None, eumetview: dict[str, dict] | None,
                   now: datetime | None = None, ncep: dict[str, dict] | None = None,
                   geomet: dict[str, dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    year = now.year
    services = {"gibs": gibs, "eumetview": eumetview, "ncep": ncep, "geomet": geomet}
    products, dropped = [], []
    for p in PRODUCTS:
        layers = []
        for sector_id, (svc, candidates) in p["sectors"].items():
            avail = services.get(svc)
            if avail is None:
                dropped.append({"product": p["id"], "sector": sector_id, "why": f"{svc} capabilities not read"})
                continue
            hit = pick(avail, candidates)
            if not hit:
                dropped.append({"product": p["id"], "sector": sector_id, "why": f"no {svc} layer matched"})
                continue
            name, info = hit
            if not info.get("timed") or not info.get("latest") or not info.get("step_s"):
                dropped.append({"product": p["id"], "sector": sector_id, "why": f"{name}: no usable time dimension"})
                continue
            sec = SECTORS[sector_id]
            entry = {"sector": sector_id, "sector_name": sec["name"], "west": sec["west"], "east": sec["east"],
                     "south": sec.get("south", -85), "north": sec.get("north", 85),
                     "source": svc, "name": name, "step_s": info["step_s"], "latest": iso(info["latest"]),
                     "lag_s": max(0, int((now - info["latest"]).total_seconds()))}
            # How far back the service keeps frames (its first listed time to its latest). The page never asks
            # for a time older than that: radar servers keep 2 to 3 hours and answer older times with nothing.
            first = info.get("first")
            if first and first <= info["latest"]:
                entry.update(first=iso(first), keep_s=int((info["latest"] - first).total_seconds()))
            if svc == "gibs":
                entry.update(url=info["url"], max_zoom=info["max_zoom"] if info["max_zoom"] is not None else 6)
            else:
                entry.update(wms=WMS_BASE[svc], layer=info["layer"])
            layers.append(entry)
        if layers:
            products.append({k: p[k] for k in ("id", "name", "group", "note")} | {"frames": p.get("frames")}
                            | {"layers": layers})
    credits = []
    if any(l["source"] == "gibs" for p in products for l in p["layers"]):
        credits.append({"text": "Imagery: NASA GIBS (ESDIS), NOAA GOES, JMA Himawari, NASA GPM IMERG",
                        "url": "https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api"})
    if any(l["source"] == "eumetview" for p in products for l in p["layers"]):
        credits.append({"text": f"Contains modified EUMETSAT Meteosat data {year}", "url": "https://view.eumetsat.int/"})
    if any(l["source"] == "ncep" for p in products for l in p["layers"]):
        credits.append({"text": "Radar: NOAA MRMS", "url": "https://opengeo.ncep.noaa.gov/"})
    if any(l["source"] == "geomet" for p in products for l in p["layers"]):
        credits.append({"text": "Data Source: Environment and Climate Change Canada",
                        "url": "https://eccc-msc.github.io/open-data/licence/readme_en/"})
    return {"generated_at": iso(now), "ok": bool(products), "products": products, "dropped": dropped,
            "credits": credits}


def probe_url(layer: dict, now: datetime) -> str:
    """One tile (zoom 2, over the sector's middle) at the newest frame the page would ask for."""
    step = layer["step_s"]
    t = (int(now.timestamp()) - layer["lag_s"]) // step * step - step
    when = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mid = (layer["west"] + layer["east"]) / 2
    x = min(3, max(0, int((mid + 180) / 90)))
    if layer["source"] == "gibs":
        return layer["url"].replace("{time}", when).replace("{z}", "2").replace("{y}", "1").replace("{x}", str(x))
    def merc(lon, lat):
        lat = max(-85.0, min(85.0, lat))
        return 6378137 * math.radians(lon), 6378137 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    x0, y0 = merc(layer["west"], max(layer.get("south", -85), 0 if layer.get("south", -85) < 0 < layer.get("north", 85) else layer.get("south", -85)))
    x1, y1 = merc(layer["east"], layer.get("north", 85))
    q = urllib.parse.urlencode({"service": "WMS", "request": "GetMap", "version": "1.3.0", "layers": layer["layer"],
                                "styles": "", "format": "image/png", "transparent": "true", "crs": "EPSG:3857",
                                "width": 256, "height": 256, "time": when,
                                "bbox": f"{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}"})
    return f"{layer['wms']}?{q}"


TIMEOUT_S = 20      # per socket operation; a host that times out once is not asked again this run
_HUNG: set[str] = set()


def fetch(url: str, timeout: int = TIMEOUT_S) -> bytes:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in _HUNG:
        raise TimeoutError(f"{host} timed out earlier in this run; not asked again")
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (TimeoutError, urllib.error.URLError) as e:
        if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
            _HUNG.add(host)
        raise


def _read(path_or_url: str | None, url: str, label: str, reader) -> dict | None:
    try:
        raw = Path(path_or_url).read_bytes() if path_or_url else fetch(url)
        layers = reader(raw)
        print(f"{label}: {len(layers)} layers read")
        return layers
    except Exception as e:  # noqa: BLE001 - one service down must not stop the build
        print(f"{label}: capabilities not read ({type(e).__name__}: {e})")
        return None


def read_eumetview(path: str | None) -> dict | None:
    if path:
        return _read(path, "", "EUMETView", wms_layers)
    found: dict[str, dict] = {}
    for ws in EUMETVIEW_WORKSPACES:
        got = _read(None, EUMETVIEW_CAPS.format(ws=ws), f"EUMETView {ws}", lambda raw, ws=ws: wms_layers(raw, ws))
        found.update(got or {})
    return found or None


def read_ncep() -> dict | None:
    found: dict[str, dict] = {}
    for ws in NCEP_WORKSPACES:
        got = _read(None, NCEP_CAPS.format(ws=ws), f"NOAA opengeo {ws}", lambda raw, ws=ws: wms_layers(raw, ws))
        found.update(got or {})
    return found or None


def read_geomet() -> dict | None:
    found: dict[str, dict] = {}
    for name in GEOMET_LAYERS:
        got = _read(None, GEOMET_CAPS.format(layer=name), f"ECCC GeoMet {name}",
                    lambda raw: wms_layers(raw, "geomet", qualify=False))
        found.update({k: v for k, v in (got or {}).items() if k == name})
    return found or None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "site" / "data" / "wxmap.json"))
    ap.add_argument("--gibs", help="read GIBS WMTS capabilities from this file")
    ap.add_argument("--eumetview", help="read EUMETView WMS capabilities from this file")
    ap.add_argument("--offline", action="store_true", help="with --gibs/--eumetview: skip the NOAA and ECCC services")
    ap.add_argument("--probe", action="store_true", help="fetch one tile per layer and print what came back")
    ap.add_argument("--find", help="also list every layer name matching this pattern (to fix a product's candidates)")
    a = ap.parse_args(argv)
    gibs = _read(a.gibs, GIBS_CAPS, "NASA GIBS", gibs_layers)
    eumetview = read_eumetview(a.eumetview)
    ncep = None if a.offline else read_ncep()
    geomet = None if a.offline else read_geomet()
    doc = build_manifest(gibs, eumetview, ncep=ncep, geomet=geomet)
    if a.find:
        rx = re.compile(a.find, re.I)
        for label, avail in (("gibs", gibs), ("eumetview", eumetview), ("ncep", ncep), ("geomet", geomet)):
            names = sorted(k for k in (avail or {}) if rx.search(k))
            print(f"  {label} names matching {a.find!r} ({len(names)}): " + ", ".join(names[:400]))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for p in doc["products"]:
        print(f"  {p['id']}: " + "; ".join(
            f"{l['sector']} {l['name']} every {l['step_s'] // 60} min, latest {l['latest']} ({l['lag_s'] // 60} min "
            f"behind)" + (f", kept {l['keep_s'] // 60} min" if l.get("keep_s", 0) < 2 * 86400 and "keep_s" in l else "")
            for l in p["layers"]))
    if a.probe:
        for p in doc["products"]:
            for l in p["layers"]:
                u = probe_url(l, datetime.now(timezone.utc))
                try:
                    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=TIMEOUT_S) as r:
                        body = r.read()
                        print(f"  probe {p['id']} {l['sector']}: HTTP {r.status} {r.headers.get('Content-Type')} {len(body)} B")
                except Exception as e:  # noqa: BLE001
                    print(f"  probe {p['id']} {l['sector']}: {type(e).__name__}: {e}")
    for d in doc["dropped"]:
        print(f"  left out: {d['product']} {d['sector']}: {d['why']}")
    print(f"wrote {out} (ok={doc['ok']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
