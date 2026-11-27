#!/usr/bin/env python3
"""Fetch NOAA GFS upper-level winds and snow depth for the weather map's jet stream and snow layers.

Downloads the 250 hPa U and V wind and the surface snow depth of the newest GFS run (1-degree grid, NOAA NOMADS grib
filter, US public domain), at the forecast hour nearest the build time, and writes site/data/wind250.json (a
2-degree global grid of whole m/s that site/wxmap.js animates as moving streaks) and site/data/snow.json (a 1-degree
grid of whole centimetres, drawn as shaded cells). Decoding needs the eccodes package (pip install eccodes); the
build installs it. Like tools/wxmap.py this never fails the build: with no run or no decoder it writes a file that
says the layer is unavailable, and the map hides the layer. Reads time out after TIMEOUT_S seconds, and once NOMADS
times out the older runs are not tried, so a hung server costs about TIMEOUT_S seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Vis/1.0 (+https://vis.theintelligence.company)"}
NOMADS = "https://nomads.ncep.noaa.gov"
IDX = NOMADS + "/pub/data/nccf/com/gfs/prod/gfs.{d}/{h}/atmos/gfs.t{h}z.pgrb2.1p00.f{f:03d}.idx"
FILTER = (NOMADS + "/cgi-bin/filter_gfs_1p00.pl?dir=%2Fgfs.{d}%2F{h}%2Fatmos&file=gfs.t{h}z.pgrb2.1p00.f{f:03d}"
          "&var_UGRD=on&var_VGRD=on&var_SNOD=on&lev_250_mb=on&lev_surface=on")
LEVEL_HPA = 250
STEP_DEG = 2          # output grid spacing; the 1-degree source is thinned by this factor
CREDIT = {"text": "Winds: NOAA GFS", "url": "https://www.nco.ncep.noaa.gov/pmb/products/gfs/"}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidates(now: datetime, back_cycles: int = 4):
    """(cycle time, forecast hour) pairs to try, newest run first. A run appears on NOMADS about 3.5 to 5 hours after
    its cycle time; the forecast hour is the multiple of 3 nearest `now`."""
    base = now.replace(minute=0, second=0, microsecond=0)
    base -= timedelta(hours=base.hour % 6)
    for k in range(back_cycles + 1):
        cyc = base - timedelta(hours=6 * k)
        lead = max(0, round((now - cyc).total_seconds() / 3600 / 3) * 3)
        if lead <= 24:
            yield cyc, lead


TIMEOUT_S = 20      # per socket operation; a host that times out once is not asked again this run
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


def has_winds(idx_text: str) -> bool:
    return f":UGRD:{LEVEL_HPA} mb:" in idx_text and f":VGRD:{LEVEL_HPA} mb:" in idx_text


def messages(grib: bytes):
    """Split a GRIB2 file into its messages: each starts with "GRIB" and gives its own length in octets 9 to 16."""
    pos = grib.find(b"GRIB")
    while pos >= 0 and pos + 16 <= len(grib):
        n = int.from_bytes(grib[pos + 8:pos + 16], "big")
        if grib[pos + 7] != 2 or n < 16 or pos + n > len(grib):
            raise ValueError(f"not a whole GRIB2 message at byte {pos}")
        yield grib[pos:pos + n]
        pos = grib.find(b"GRIB", pos + n)


def decode(grib: bytes) -> list[dict]:
    """GRIB2 messages as {short, level, ni, nj, lat0, lon0, dlat, dlon, values}. Needs eccodes.

    Each message is decoded from memory: no temporary file, which eccodes would keep open (on Windows an open file
    can be neither reopened nor removed)."""
    import eccodes  # noqa: PLC0415 - optional dependency, only the build installs it

    out = []
    for msg in messages(grib):
        gid = eccodes.codes_new_from_message(msg)
        try:
            g = lambda k: eccodes.codes_get(gid, k)  # noqa: E731
            lat0, lat1 = g("latitudeOfFirstGridPointInDegrees"), g("latitudeOfLastGridPointInDegrees")
            miss = g("missingValue")
            out.append({
                "short": g("shortName"), "level": g("level"), "ni": g("Ni"), "nj": g("Nj"),
                "lat0": lat0, "lon0": g("longitudeOfFirstGridPointInDegrees"),
                "dlat": g("jDirectionIncrementInDegrees") * (-1 if lat1 < lat0 else 1),
                "dlon": g("iDirectionIncrementInDegrees"),
                # Points the GRIB bitmap masks (snow depth over the sea) come back as missingValue.
                "values": [None if v == miss else float(v) for v in eccodes.codes_get_values(gid)],
            })
        finally:
            eccodes.codes_release(gid)
    return out


def grid(messages: list[dict], step_deg: int = STEP_DEG) -> dict:
    """Thin the 250 hPa u and v fields to a regular grid, north to south and west to east from 0 degrees east,
    as whole m/s. Raises ValueError when either field is missing or the grids differ."""
    by = {m["short"]: m for m in messages if m.get("level") == LEVEL_HPA and m["short"] in ("u", "v")}
    if set(by) != {"u", "v"}:
        raise ValueError(f"250 hPa u and v not both present (got {sorted(by)})")
    u, v = by["u"], by["v"]
    keys = ("ni", "nj", "lat0", "lon0", "dlat", "dlon")
    if any(u[k] != v[k] for k in keys):
        raise ValueError("u and v grids differ")
    if abs(abs(u["dlon"]) - abs(u["dlat"])) > 1e-6 or u["dlat"] >= 0:
        raise ValueError("expected a regular north-to-south lat/lon grid")
    res = abs(u["dlon"])
    k = max(1, round(step_deg / res))
    ni, nj = u["ni"], u["nj"]
    rows, cols = range(0, nj, k), range(0, ni, k)
    pick = lambda vals: [round(vals[j * ni + i]) for j in rows for i in cols]  # noqa: E731
    return {"nx": len(cols), "ny": len(rows), "lat0": u["lat0"], "lon0": u["lon0"] % 360,
            "dlat": -res * k, "dlon": res * k, "u": pick(u["values"]), "v": pick(v["values"])}


def snow_grid(messages: list[dict]) -> dict | None:
    """Surface snow depth (GFS SNOD, eccodes short name sde, metres) as whole centimetres on the source grid."""
    m = next((m for m in messages if m["short"] in ("sde", "snod")), None)
    if m is None:
        return None
    return {"nx": m["ni"], "ny": m["nj"], "lat0": m["lat0"], "lon0": m["lon0"] % 360, "dlat": m["dlat"],
            "dlon": m["dlon"], "cm": [0 if v is None or not 0 < v < 100 else round(v * 100) for v in m["values"]]}


def build(now: datetime, get=fetch, decoder=decode, log=print) -> tuple[dict, dict]:
    """(wind document, snow document); each says "ok": false when its field could not be had."""
    out = {"generated_at": iso(now), "ok": False, "level_hpa": LEVEL_HPA, "credits": [CREDIT]}
    snow = {"generated_at": iso(now), "ok": False, "credits": [{**CREDIT, "text": "Snow depth: NOAA GFS"}]}
    for cyc, lead in candidates(now):
        d, h = cyc.strftime("%Y%m%d"), cyc.strftime("%H")
        try:
            if not has_winds(get(IDX.format(d=d, h=h, f=lead)).decode("utf-8", "replace")):
                log(f"GFS {d} {h}Z f{lead:03d}: inventory has no {LEVEL_HPA} hPa winds")
                continue
            raw = get(FILTER.format(d=d, h=h, f=lead))
        except Exception as e:  # noqa: BLE001 - not published yet, or NOMADS busy: try the run before
            log(f"GFS {d} {h}Z f{lead:03d}: not available ({type(e).__name__}: {e})")
            continue
        try:
            msgs = decoder(raw)
            g = grid(msgs)
        except Exception as e:  # noqa: BLE001 - no decoder, or an unexpected file: say so and stop
            log(f"GFS {d} {h}Z f{lead:03d}: {len(raw)} bytes not decoded ({type(e).__name__}: {e})")
            out["error"] = snow["error"] = f"{type(e).__name__}: {e}"
            return out, snow
        sg = snow_grid(msgs)
        stamp = {"run": iso(cyc), "lead_h": lead, "valid": iso(cyc + timedelta(hours=lead))}
        if sg:
            snow.update(ok=True, **stamp, **sg)
            log(f"  snow depth: {sum(1 for c in sg['cm'] if c > 0)} cells with snow, deepest {max(sg['cm'])} cm")
        else:
            snow["error"] = "no snow depth field in the file"
        speed = max((a * a + b * b) ** 0.5 for a, b in zip(g["u"], g["v"]))
        log(f"GFS {d} {h}Z f{lead:03d}: {g['nx']}x{g['ny']} grid, strongest {speed:.0f} m/s")
        out.update(ok=True, **stamp, **g)
        return out, snow
    out["error"] = snow["error"] = "no recent GFS run found"
    return out, snow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "site" / "data" / "wind250.json"))
    ap.add_argument("--snow-out", default=str(ROOT / "site" / "data" / "snow.json"))
    ap.add_argument("--grib", help="decode this GRIB2 file instead of fetching (for testing)")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if a.grib:
        raw = Path(a.grib).read_bytes()
        out, snow = build(now, get=lambda url: (f":UGRD:{LEVEL_HPA} mb:\n:VGRD:{LEVEL_HPA} mb:".encode()
                                                if url.endswith(".idx") else raw))
    else:
        out, snow = build(now)
    for path, doc in ((a.out, out), (a.snow_out, snow)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(doc, separators=(",", ":")))
        print(f"wrote {path} ({'ok' if doc['ok'] else 'unavailable: ' + doc.get('error', '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
