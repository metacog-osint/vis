"""NOAA GEFS cloud ceiling for Vis's points, read straight from NOAA's public files (the "Low ceiling" trial hazard).

    python tools/gefs.py [--points config/points.json] [--days 7]     # prints the newest usable run and its ceilings

Open-Meteo's ensemble API carries no cloud base, so `forecast.py` takes the ceiling from NOAA's own GEFS files on AWS
Open Data (bucket `noaa-gefs-pds`, no account, US public domain): the 0.25-degree "s" files
`gefs.YYYYMMDD/HH/atmos/pgrb2sp25/{gec00,gep01..gep30}.tHHz.pgrb2s.0p25.fNNN`, 3-hourly to 240 h, one file per member
and step. Each file's `.idx` gives the byte range of the `HGT:cloud ceiling` record, so one range request per file
reads about 1 MB of the file's 19.5 MB.

What the field is (NOAA UPP, `CALCEILING` and `CLDRAD`): the height above mean sea level of the mid-point of the
lowest model layer holding cloud water or ice, set when the column's largest layer cloud fraction is at least 50%,
and 20,000 m when there is none. It is not the METAR or ICAO ceiling (the lowest layer covering more than half the
sky), so it is scored against METARs on trial (`tools/verify.py`). Vis subtracts the station's elevation, since a
METAR gives heights above the aerodrome.

The records use GRIB2 data template 5.3 (complex packing with second-order spatial differencing), decoded here with
the standard library: WMO Manual on Codes, FM 92 GRIB edition 2, templates 3.0, 5.2/5.3 and 7.2/7.3. Other templates,
a bitmap or another scanning mode are refused rather than guessed. Checked against ecCodes 2.50.1 at every grid point
of real records (26 Nov 2026): no difference.

A failure anywhere here (bucket unreachable, run missing, a record that will not decode, the time budget spent)
leaves the ceiling out of that build; it never stops the forecast.
"""
import argparse
import concurrent.futures as cf
import itertools
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = "https://noaa-gefs-pds.s3.amazonaws.com"
FILE = "{bucket}/gefs.{ymd}/{hh}/atmos/pgrb2sp25/{member}.t{hh}z.pgrb2s.0p25.f{step:03d}"
UA = "Vis/1.0 (+https://vis.theintelligence.company)"
RECORD = "HGT:cloud ceiling"
MEMBERS = ["gec00"] + ["gep%02d" % i for i in range(1, 31)]
CYCLES = (0, 12)          # 00Z and 12Z runs reach 240 h; the build reads the newest whose files are all up
STEP_HOURS = 3
FIRST_STEP, LAST_STEP = 3, 240
NO_CEILING_M = 20000.0    # UPP writes 20,000 m where no layer qualifies: "no ceiling", not a missing value
MAX_RUN_AGE_HOURS = 24.0  # same limit as forecast.py's Open-Meteo runs
GRID_MAX_DIFF_M = 300.0
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
THREADS = 32
RETRIES = 3              # per request, on a dropped connection only
PROCS = 4
BUDGET_S = 300.0          # the whole ceiling stage; past this the build goes on without it
SOURCE = {
    "id": "noaa-gefs-aws",
    "publisher": "NOAA NCEP (AWS Open Data)",
    "title": "NOAA GEFS 0.25 deg cloud ceiling (31 members)",
    "url": "https://registry.opendata.aws/noaa-gefs/",
    "licence": "NOAA NCEP, US public domain",
    "grade": "A2",
    "independence_group": "noaa-ncep",
    "source_type": "model",
    "descriptor": "HGT:cloud ceiling from the GEFS pgrb2sp25 files, 3-hourly, nearest 0.25 deg cell to each point.",
}


# ---- GRIB2 decoding (standard library) ----

def _sm(b):
    """GRIB2 signed integer: sign bit then magnitude (not two's complement)."""
    v = int.from_bytes(b, "big")
    top = 1 << (8 * len(b) - 1)
    return -(v - top) if v & top else v


class Bits:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos * 8

    def take(self, n, count):
        """`count` unsigned integers of `n` bits each, then skip to the next octet."""
        if n == 0:
            self.p = ((self.p + 7) // 8) * 8
            return [0] * count
        m = (1 << n) - 1
        out = []
        p = self.p
        for c0 in range(0, count, 256):   # in chunks, so each shift works on a small integer
            c = min(256, count - c0)
            nb = n * c
            b0, b1 = p // 8, (p + nb + 7) // 8
            x = int.from_bytes(self.d[b0:b1], "big") >> ((b1 * 8) - (p + nb))
            out.extend([(x >> (n * (c - 1 - k))) & m for k in range(c)])
            p += nb
        self.p = ((p + 7) // 8) * 8
        return out


def sections(msg):
    if msg[:4] != b"GRIB" or msg[7] != 2:
        raise ValueError("not a GRIB2 message")
    i, out = 16, {}
    while i < len(msg) - 4:
        if msg[i:i + 4] == b"7777":
            break
        n, s = struct.unpack(">IB", msg[i:i + 5])
        if n < 5:
            raise ValueError("bad section length")
        out[s] = msg[i:i + n]
        i += n
    return out


def grid(sec3):
    t = struct.unpack(">H", sec3[12:14])[0]
    if t != 0:
        raise ValueError("grid template 3.%d not handled" % t)
    ni, nj = struct.unpack(">II", sec3[30:38])
    la1, lo1 = _sm(sec3[46:50]) / 1e6, _sm(sec3[50:54]) / 1e6
    la2, lo2 = _sm(sec3[55:59]) / 1e6, _sm(sec3[59:63]) / 1e6
    di, dj = struct.unpack(">II", sec3[63:71])
    return {"ni": ni, "nj": nj, "la1": la1, "lo1": lo1, "la2": la2, "lo2": lo2, "di": di / 1e6, "dj": dj / 1e6,
            "scan": sec3[71]}


def grid_index(g, lat, lon):
    """Nearest grid point -> (index, cell lat, cell lon) on a regular grid scanning west to east from La1 southward
    (scanning mode 0)."""
    if g["scan"] != 0:
        raise ValueError("scan mode %d not handled" % g["scan"])
    j = min(g["nj"] - 1, max(0, int(round((g["la1"] - lat) / g["dj"]))))
    i = int(round(((lon - g["lo1"]) % 360.0) / g["di"])) % g["ni"]
    clon = (g["lo1"] + i * g["di"]) % 360.0
    return j * g["ni"] + i, round(g["la1"] - j * g["dj"], 4), round(clon - 360.0 if clon > 180.0 else clon, 4)


def decode(msg, need_upto=None):
    """Template 5.2/5.3 values for grid points 0..need_upto (all if None).

    Spatial differencing makes each value depend on every earlier one, so a point can only be read after decoding
    the field up to it; `need_upto` stops there."""
    s = sections(msg)
    s5, s7 = s[5], s[7]
    npts, tmpl = struct.unpack(">IH", s5[5:11])
    if tmpl not in (2, 3):
        raise ValueError("data template 5.%d not handled" % tmpl)
    if 6 in s and s[6][5] != 255:
        raise ValueError("bitmap present; not handled")
    R = struct.unpack(">f", s5[11:15])[0]
    E, D = _sm(s5[15:17]), _sm(s5[17:19])
    nbits = s5[19]
    miss_mgmt = s5[22]
    ng = struct.unpack(">I", s5[31:35])[0]
    w_ref, w_bits = s5[35], s5[36]
    l_ref = struct.unpack(">I", s5[37:41])[0]
    l_inc = s5[41]
    l_last = struct.unpack(">I", s5[42:46])[0]
    l_bits = s5[46]
    order = s5[47] if tmpl == 3 else 0
    oct_sd = s5[48] if tmpl == 3 else 0
    data = s7[5:]
    pos = 0
    ival = []
    minsd = 0
    if tmpl == 3 and order:
        for _ in range(order):
            ival.append(_sm(data[pos:pos + oct_sd]))
            pos += oct_sd
        minsd = _sm(data[pos:pos + oct_sd])
        pos += oct_sd
    bits = Bits(data, pos)
    g_ref = bits.take(nbits, ng)
    g_w = [w + w_ref for w in bits.take(w_bits, ng)]
    g_l = [l_ref + n * l_inc for n in bits.take(l_bits, ng)]
    g_l[-1] = l_last
    if sum(g_l) != npts:
        raise ValueError("group lengths sum to %d, expected %d" % (sum(g_l), npts))
    limit = npts if need_upto is None else min(npts, need_upto + 1)
    vals = []       # packed values of non-missing points, in order
    is_miss = []    # per grid point: 0 present, 1 primary missing, 2 secondary missing
    p = bits.p
    d = bits.d
    m1_ref, m2_ref = (1 << nbits) - 1, (1 << nbits) - 2
    for g in range(ng):
        L, W, ref = g_l[g], g_w[g], g_ref[g]
        if W == 0:
            if miss_mgmt and ref == m1_ref:
                is_miss.extend([1] * L)
            elif miss_mgmt == 2 and ref == m2_ref:
                is_miss.extend([2] * L)
            else:
                vals.extend([ref] * L)
                is_miss.extend([0] * L)
        else:
            nb = W * L
            b0, b1 = p // 8, (p + nb + 7) // 8
            x = int.from_bytes(d[b0:b1], "big") >> ((b1 * 8) - (p + nb))
            m = (1 << W) - 1
            raw = [(x >> (W * k)) & m for k in range(L - 1, -1, -1)]
            p += nb
            if miss_mgmt:
                for v in raw:
                    if v == m:
                        is_miss.append(1)
                    elif miss_mgmt == 2 and v == m - 1:
                        is_miss.append(2)
                    else:
                        vals.append(v + ref)
                        is_miss.append(0)
            else:
                vals.extend([v + ref for v in raw])
                is_miss.extend([0] * L)
        if len(is_miss) >= limit:
            break
    # Undo the spatial differencing over the present values, as running sums.
    if order == 1 and vals:
        vals = list(itertools.accumulate(itertools.chain([ival[0]], (v + minsd for v in vals[1:]))))
    elif order == 2 and len(vals) >= 2:
        first = ival[1] - ival[0]
        diffs = itertools.accumulate(itertools.chain([first], (v + minsd for v in vals[2:])))
        vals = list(itertools.accumulate(itertools.chain([ival[0]], diffs)))
    return {"vals": vals, "is_miss": is_miss, "miss_mgmt": miss_mgmt, "scale": (R, 2.0 ** E, 10.0 ** (-D)),
            "npts": npts}


def values_at(dec, indices):
    """Grid indices -> real values (None where missing)."""
    R, se, sd = dec["scale"]
    if not dec["miss_mgmt"]:
        return [(R + dec["vals"][i] * se) * sd for i in indices]
    miss = dec["is_miss"]
    out, pos, j = {}, 0, 0
    for idx in sorted(set(indices)):
        while j < idx:
            if miss[j] == 0:
                pos += 1
            j += 1
        out[idx] = None if miss[idx] else (R + dec["vals"][pos] * se) * sd
    return [out[i] for i in indices]


def point_values(msg, latlons):
    """One record -> (values at the nearest cell of each (lat, lon), cell centres)."""
    g = grid(sections(msg)[3])
    where = [grid_index(g, la, lo) for la, lo in latlons]
    dec = decode(msg, max(w[0] for w in where))
    return values_at(dec, [w[0] for w in where]), [(w[1], w[2]) for w in where]


# ---- the bucket ----

def _get(url, rng=None, opener=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if rng:
        req.add_header("Range", "bytes=%d-%s" % (rng[0], "" if rng[1] is None else rng[1]))
    for attempt in range(RETRIES + 1):
        try:
            with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise   # an answer (a missing file is 403 or 404 on this bucket): never retried
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            # A dropped or reset connection (seen on a 2-member live check, 27 Nov 2026) is tried again.
            if attempt == RETRIES:
                raise
            time.sleep(1.0 + attempt * 2.0)


def record_range(idx_text, want=RECORD):
    """wgrib2-style inventory -> (first byte, last byte) of the wanted record (last byte None if it ends the file)."""
    rows = []
    for line in idx_text.splitlines():
        p = line.split(":")
        if len(p) >= 5:
            rows.append((int(p[1]), p[3] + ":" + p[4]))
    for i, (start, name) in enumerate(rows):
        if name == want:
            return start, (rows[i + 1][0] - 1 if i + 1 < len(rows) else None)
    raise KeyError(want)


def file_url(run, member, step):
    return FILE.format(bucket=BUCKET, ymd=run[:8], hh=run[8:10], member=member, step=step)


def run_time(run):
    return datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def needed_steps(run, valid_times):
    """Forecast hours of `run` for the wanted valid times, within the files' range."""
    t0 = run_time(run)
    out = []
    for v in valid_times:
        h = int((v - t0).total_seconds() // 3600)
        if FIRST_STEP <= h <= LAST_STEP and h % STEP_HOURS == 0:
            out.append(h)
    return out


def latest_run(now, last_valid, opener=None, max_age=MAX_RUN_AGE_HOURS):
    """Newest 00Z/12Z run, at most `max_age` hours old, whose file for `last_valid` is up for every member (checked
    on the `.idx`, 2.5 KB each). Returns the run as YYYYMMDDHH, or None."""
    c = now.replace(minute=0, second=0, microsecond=0)
    while c.hour not in CYCLES:
        c -= timedelta(hours=1)
    while (now - c).total_seconds() / 3600.0 <= max_age:
        run = c.strftime("%Y%m%d%H")
        step = min(LAST_STEP, int((last_valid - c).total_seconds() // 3600))
        step -= step % STEP_HOURS
        try:
            with cf.ThreadPoolExecutor(THREADS) as ex:
                list(ex.map(lambda m: _get(file_url(run, m, step) + ".idx", opener=opener, timeout=20), MEMBERS))
            return run
        except Exception:  # noqa: BLE001 - a missing file (403/404 on this bucket) means the run is not complete
            c -= timedelta(hours=12)
    return None


def _decode_job(args):
    msg, latlons = args
    return point_values(msg, latlons)


def fetch_points(run, latlons, steps, opener=None, procs=PROCS, threads=THREADS, budget=BUDGET_S):
    """{(member, step): [ceiling MSL m per point]} and the cell centres, for every member and step.

    Each thread reads a file's `.idx`, then the ceiling record's bytes, and hands the record to a decoding process,
    waiting for it, so at most `threads` records are held at once. Raises on any failure or when the budget runs out:
    the caller then leaves the ceiling out of the build."""
    t_end = time.monotonic() + budget
    pool = cf.ProcessPoolExecutor(procs) if procs > 1 else None
    cells = []

    def one(job):
        member, step = job
        if time.monotonic() > t_end:
            raise TimeoutError("ceiling time budget spent")
        url = file_url(run, member, step)
        rng = record_range(_get(url + ".idx", opener=opener).decode("ascii"))
        msg = _get(url, rng, opener=opener)
        if pool:
            vals, cc = pool.submit(_decode_job, (msg, latlons)).result(timeout=max(1.0, t_end - time.monotonic()))
        else:
            vals, cc = point_values(msg, latlons)
        if not cells:
            cells.extend(cc)
        return job, vals

    try:
        with cf.ThreadPoolExecutor(threads) as ex:
            got = dict(ex.map(one, [(m, s) for m in MEMBERS for s in steps]))
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    return got, cells


def check_cells(latlons, cells, opener=None):
    """Elevation of each station and of its GEFS cell centre, one Open-Meteo call; the 300 m rule as forecast.py's."""
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{x:.4f}" for x in [a for a, _ in latlons] + [a for a, _ in cells]),
        "longitude": ",".join(f"{x:.4f}" for x in [b for _, b in latlons] + [b for _, b in cells])})
    elev = json.loads(_get(f"{ELEVATION_URL}?{q}", opener=opener, timeout=30).decode("utf-8"))["elevation"]
    n = len(latlons)
    rows = []
    for k in range(n):
        st, ce = elev[k], elev[n + k]
        diff = None if st is None or ce is None else round(ce - st, 1)
        rows.append({"lat": cells[k][0], "lon": cells[k][1], "station_elevation_m": st, "cell_elevation_m": ce,
                     "diff_m": diff, "ok": None if diff is None else abs(diff) <= GRID_MAX_DIFF_M})
    return rows


def agl(msl, station_elevation_m):
    """Ceiling above the station: 20,000 m stays "none" (NO_CEILING_M); a base under the station's height reads 0."""
    if msl is None or station_elevation_m is None:
        return None
    if msl >= NO_CEILING_M - 1.0:
        return NO_CEILING_M
    return round(max(0.0, msl - station_elevation_m), 1)


def ceiling_series(points, times, now, opener=None, procs=PROCS, budget=BUDGET_S):
    """The trial hazard's input for forecast.py.

    `times` is Open-Meteo's hourly time list ("YYYY-MM-DDTHH:MM", UTC). Returns (series, info): `series[i]` is
    {"ceiling_agl": [...], "ceiling_agl_member01": [...], ...} aligned with `times`, holding values at the 3-hourly
    model steps from the current hour on and None elsewhere (or None for a point whose station elevation is unknown
    or whose cell fails the 300 m check);
    `info` names the run, its age, the cells and why anything is missing. On any failure series is None."""
    info = {"source": SOURCE["id"], "record": RECORD, "run_time": None, "age_hours": None, "members": len(MEMBERS),
            "state": "unavailable", "why": None, "steps": 0, "seconds": None, "cells": None}
    t_start = time.monotonic()
    cut = now.replace(minute=0, second=0, microsecond=0)
    valid = [datetime.strptime(t[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc) for t in times]
    want = [v for v in valid if v.hour % STEP_HOURS == 0 and v >= cut]
    if not want:
        info["why"] = "no 3-hourly step left in the forecast window"
        return None, info
    try:
        run = latest_run(now, want[-1], opener=opener)
        if not run:
            info["why"] = f"no complete 00Z or 12Z run in the last {MAX_RUN_AGE_HOURS:g} hours"
            return None, info
        info["run_time"] = run_time(run).strftime("%Y-%m-%dT%H:%M:%SZ")
        info["age_hours"] = round((now - run_time(run)).total_seconds() / 3600.0, 1)
        steps = needed_steps(run, want)
        latlons = [(p["lat"], p["lon"]) for p in points]
        got, cells = fetch_points(run, latlons, steps, opener=opener, procs=procs,
                                  budget=budget - (time.monotonic() - t_start))
        checks = check_cells(latlons, cells, opener=opener)
    except Exception as e:  # noqa: BLE001 - never a stop for the forecast
        info["why"] = f"{type(e).__name__}: {e}"[:300]
        info["seconds"] = round(time.monotonic() - t_start, 1)
        return None, info
    t0 = run_time(run)
    at = {}
    for k, v in enumerate(valid):
        h = int((v - t0).total_seconds() // 3600)
        if h in steps and v >= cut:
            at[k] = h
    series = []
    for i, chk in enumerate(checks):
        st = chk["station_elevation_m"]
        # A cell over 300 m from the station's height is left out of the trial rather than scored: its cloud sits
        # over other terrain (27 Nov 2026: SVMI's cell +879 m in the coastal range, HHAS -446 m, OLBA +333 m).
        if st is None or chk["ok"] is not True:
            series.append(None)
            continue
        row = {}
        for mi, m in enumerate(MEMBERS):
            key = "ceiling_agl" if mi == 0 else "ceiling_agl_member%02d" % mi
            col = [None] * len(times)
            for k, h in at.items():
                col[k] = agl(got[(m, h)][i], st)
            row[key] = col
        series.append(row)
    info.update({"state": "used", "steps": len(steps), "seconds": round(time.monotonic() - t_start, 1),
                 "cells": checks, "points_used": sum(1 for s in series if s),
                 "left_out": [p.get("icao") or p.get("id") for p, s in zip(points, series) if not s]})
    return series, info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--points", default=os.path.join(ROOT, "config", "points.json"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--procs", type=int, default=PROCS)
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import forecast  # noqa: E402
    pts = forecast.expand_points(forecast.load_zones(a.points))
    now = datetime.now(timezone.utc)
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    times = [(d0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(24 * a.days)]
    series, info = ceiling_series(pts, times, now, procs=a.procs)
    print(json.dumps({k: v for k, v in info.items() if k != "cells"}, indent=1))
    if series:
        for p, s, c in zip(pts, series, info["cells"]):
            if s is None:
                print(f"{p['icao']}: left out (cell {c['diff_m']} m from the station, or elevation unknown)")
                continue
            low = [min(v for v in s[k] if v is not None) for k in s if any(v is not None for v in s[k])]
            low.sort()
            print(f"{p['icao']}: cell {c['lat']},{c['lon']} diff {c['diff_m']} m; lowest member ceiling AGL "
                  f"median {low[len(low) // 2]:.0f} m, min {low[0]:.0f} m")
    return 0 if series else 1


if __name__ == "__main__":
    sys.exit(main())
