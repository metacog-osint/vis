"""Back-test of GEFS ceiling estimates against METAR ceilings (control member, 0.5 deg, short lead).

    python -I analyse.py <data dir> <vis tools dir> <out json> [nearest|best4]     # needs numpy and eccodes

Reads <data dir>/obs (fetch_obs.py) and <data dir>/gefs (fetch_gefs.py). Not part of the build.

Candidates, each as a height above the model's own ground (m), or None for "no ceiling below about 1.5 km":
  CUR   HGT:cloud ceiling (the field Vis reads today) minus the model ground
  LCB   TCDC:low cloud layer >= 50% (3-h mean) with PRES:low cloud bottom level (3-h mean) taken to a height with
        the hypsometric equation from PRES:surface and TMP:2 m
  RH<R> lowest of hybrid levels 1-4 (HGT minus the ground), then the 60-30, 90-60, 120-90 mb above-ground layers
        (mid-points, hypsometric), where RH >= R%
  FOG   VIS:surface < 1000 m read as a ceiling at the ground (0 m)
Combinations take the lowest height.
"""
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import eccodes
import numpy as np

scratch, tools, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
MODE = sys.argv[4] if len(sys.argv) > 4 else "nearest"
sys.path.insert(0, tools)
import verify  # noqa: E402  (Vis's own METAR ceiling parser)

RD_G = 287.05 / 9.80665
NONE = None


def read(path):
    f = {}
    with open(path, "rb") as fh:
        while True:
            h = eccodes.codes_grib_new_from_file(fh)
            if h is None:
                break
            sn, tl, lv = (eccodes.codes_get(h, k) for k in ("shortName", "typeOfLevel", "level"))
            if tl in ("heightAboveGroundLayer", "pressureFromGroundLayer"):
                lv = (eccodes.codes_get(h, "topLevel"), eccodes.codes_get(h, "bottomLevel"))
            v = eccodes.codes_get_values(h).reshape(eccodes.codes_get(h, "Nj"), eccodes.codes_get(h, "Ni"))
            if eccodes.codes_get(h, "bitmapPresent"):
                v = np.where(v == eccodes.codes_get(h, "missingValue"), np.nan, v)
            f[(sn, tl, lv)] = v
            eccodes.codes_release(h)
    return f


def pick(f, sn, tl, lv=None):
    for (a, b, c), v in f.items():
        if a == sn and b == tl and (lv is None or c == lv):
            return v
    raise KeyError((sn, tl, lv, sorted(f)))


stations = {s["id"]: s for s in json.load(open(scratch / "obs" / "stations.json", encoding="utf-8"))}
reports = defaultdict(list)
with open(scratch / "obs" / "metar.csv", encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        if r["station"] in stations and r["metar"]:
            reports[r["station"]].append((datetime.strptime(r["valid"], "%Y-%m-%d %H:%M"), r["metar"]))


def nearest_report(sid, t):
    best = None
    for vt, raw in reports.get(sid, ()):
        d = abs((vt - t).total_seconds())
        if d <= 1800 and (best is None or d < best[0]):
            best = (d, raw)
    return best[1] if best else None


rows = []
files = sorted(scratch.glob("gefs/*_b.grb2"))
for bpath in files:
    cyc, step = bpath.name.split("_")[0], int(bpath.name.split("_")[1][1:])
    valid = datetime.strptime(cyc, "%Y%m%d%H") + timedelta(hours=step)
    b = read(bpath)
    a = read(bpath.with_name(bpath.name.replace("_b.", "_a.")))
    ground = pick(b, "orog", "surface")
    land = pick(b, "lsm", "surface")
    ceil = pick(b, "gh", "cloudCeiling")
    cape = pick(b, "cape", "surface")
    vis = pick(b, "vis", "surface")
    lowc = pick(b, "tcc", "lowCloudLayer")
    lcb_p = pick(b, "avg_pres", "lowCloudBottom")
    blc = pick(b, "tcc", "boundaryLayerCloudLayer")
    psfc = pick(a, "sp", "surface")
    t2 = pick(a, "2t", "heightAboveGround")
    hyb_z = [pick(b, "gh", "hybrid", k) - ground for k in (1, 2, 3, 4)]
    hyb_rh = [pick(b, "r", "hybrid", k) for k in (1, 2, 3, 4)]
    lay_rh = {}
    for (sn, tl, lv), v in b.items():
        if sn == "r" and tl == "pressureFromGroundLayer":
            lay_rh[lv] = v
    for sid, s in stations.items():
        raw = nearest_report(sid, valid)
        if raw is None:
            continue
        obs = verify.ceiling_m({"raw": raw})
        if obs is None:
            continue
        j = int(round((90 - s["lat"]) / 0.5))
        i = int(round((s["lon"] % 360) / 0.5)) % 720
        if MODE == "best4":   # of the 4 cells around the station, the one whose ground is nearest its height
            j0, i0 = int((90 - s["lat"]) // 0.5), int((s["lon"] % 360) // 0.5)
            cells = [(jj, ii % 720) for jj in (j0, j0 + 1) for ii in (i0, i0 + 1)]
            j, i = min(cells, key=lambda c: abs(float(ground[c[0], c[1]]) - s["elev"]))
        g = float(ground[j, i])
        c = float(ceil[j, i])
        cur = None if c >= 15000 else max(0.0, c - g)
        lc = float(lowc[j, i])
        pb = float(lcb_p[j, i])
        ps = float(psfc[j, i])
        tk = float(t2[j, i])
        lcb = None
        if lc >= 50 and not math.isnan(pb) and pb > 0:
            lcb = max(0.0, RD_G * tk * math.log(ps / pb)) if pb < ps else 0.0
        prof = [(float(hyb_z[k][j, i]), float(hyb_rh[k][j, i])) for k in range(4)]
        for key in sorted(lay_rh):           # (top, bottom) in Pa above ground, e.g. (6000, 3000)
            top, bot = key
            if bot < 3000:                    # 30-0 mb is under hybrid level 4
                continue
            pm = ps - (top + bot) / 2.0
            prof.append((RD_G * tk * math.log(ps / pm), float(lay_rh[key][j, i])))
        rows.append({
            "sid": sid, "region": s["region"], "valid": valid.strftime("%Y-%m-%dT%H"), "cycle": cyc, "step": step,
            "obs": obs, "raw": raw, "elev": s["elev"], "ground": round(g, 1), "land": float(land[j, i]),
            "cur": cur, "cur_station": None if c >= 15000 else max(0.0, c - s["elev"]), "cape": float(cape[j, i]),
            "lowc": lc, "blc": float(blc[j, i]), "lcb": lcb, "vis": float(vis[j, i]),
            "prof": [(round(z, 1), round(r, 1)) for z, r in prof],
        })
    print(bpath.name, len(rows), flush=True)

json.dump(rows, open(out, "w", encoding="utf-8"))
print("rows", len(rows))
