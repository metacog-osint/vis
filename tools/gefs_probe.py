"""Measure what reading NOAA GEFS cloud ceiling at Vis's points would cost (research tool; nothing here feeds the site).

    python -I tools/gefs_probe.py --run 2026112612 --step 24 --member gep01 --data <empty folder outside the repo>

For one member and one forecast step it reads the file's `.idx` from the public `noaa-gefs-pds` bucket (AWS Open Data,
no account), fetches the byte ranges of `HGT:cloud ceiling`, `VIS:surface` and `CFRZR:surface`, decodes them with the
standard-library GRIB2 decoder below, and prints the values at the 31 Vis points with the time each stage took.
`--decode-only` re-reads records already saved in `--data` and skips the network.

GEFS 0.25-degree "s" files (pgrb2sp25) are 3-hourly to 240 h, one file per member (gec00, gep01..gep30) and step.
Their records use GRIB2 data representation template 5.3 (complex packing with spatial differencing), which this
decoder implements: WMO Manual on Codes, FM 92 GRIB edition 2, templates 5.3 and 7.3. Template 5.40 (JPEG 2000) and
others are refused rather than guessed.
"""
import argparse
import itertools
import json
import os
import struct
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = "https://noaa-gefs-pds.s3.amazonaws.com"
FILE = "{bucket}/gefs.{ymd}/{hh}/atmos/pgrb2sp25/{member}.t{hh}z.pgrb2s.0p25.f{step:03d}"
UA = "tic-vis-research/1.0 (+https://vis.theintelligence.company)"
WANT = {"ceil": "HGT:cloud ceiling", "vis": "VIS:surface", "cfrzr": "CFRZR:surface"}


def _get(url, rng=None, opener=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if rng:
        req.add_header("Range", "bytes=%d-%d" % rng)
    with (opener or urllib.request.urlopen)(req, timeout=60) as r:
        return r.read()


def parse_idx(text):
    """wgrib2-style inventory -> list of (record, start byte, 'VAR:level', full line)."""
    rows = []
    for line in text.splitlines():
        p = line.split(":")
        if len(p) >= 5:
            rows.append((int(p[0]), int(p[1]), p[3] + ":" + p[4], line))
    return rows


def ranges(rows, total_len=None):
    """'VAR:level' -> (first byte, last byte) for the wanted records. The last record runs to the end of the file."""
    out = {}
    for i, (_, start, name, _) in enumerate(rows):
        for k, want in WANT.items():
            if name == want:
                end = rows[i + 1][1] - 1 if i + 1 < len(rows) else (total_len - 1 if total_len else None)
                out[k] = (start, end)
    return out


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
            return [0] * count
        m = (1 << n) - 1
        out = []
        p = self.p
        # In chunks, so each shift works on a small integer rather than the whole run.
        for c0 in range(0, count, 256):
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


def product(sec4):
    t = struct.unpack(">H", sec4[7:9])[0]
    return {"template": t, "category": sec4[9], "number": sec4[10], "surface": sec4[22],
            "fcst": struct.unpack(">I", sec4[18:22])[0], "unit": sec4[17]}


def decode(msg, need_upto=None):
    """Template 5.3 (or 5.2) values, as floats with None for missing, for grid points 0..need_upto (all if None).

    Spatial differencing makes each value depend on every earlier one, so a point can only be read after decoding
    the field up to it; `need_upto` stops there."""
    s = sections(msg)
    s5, s7 = s[5], s[7]
    npts, tmpl = struct.unpack(">IH", s5[5:11])
    if tmpl not in (2, 3):
        raise ValueError("data template 5.%d not handled by the standard-library decoder" % tmpl)
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
    g_l = [l_ref + l * l_inc for l in bits.take(l_bits, ng)]
    g_l[-1] = l_last
    if sum(g_l) != npts:
        raise ValueError("group lengths sum to %d, expected %d" % (sum(g_l), npts))
    limit = npts if need_upto is None else min(npts, need_upto + 1)
    vals = []       # packed values of non-missing points, in order
    is_miss = []    # per grid point: 0 present, 1 primary missing, 2 secondary missing
    p = bits.p
    d = bits.d
    m1_ref, m2_ref = (1 << nbits) - 1, (1 << nbits) - 2
    if not miss_mgmt:
        # Fast path: no missing values, so every grid point is present and in order.
        ext = vals.extend
        for g in range(ng):
            L, W, ref = g_l[g], g_w[g], g_ref[g]
            if W == 0:
                ext([ref] * L)
            else:
                nb = W * L
                b0, b1 = p // 8, (p + nb + 7) // 8
                x = int.from_bytes(d[b0:b1], "big") >> ((b1 * 8) - (p + nb))
                m = (1 << W) - 1
                ext([((x >> (W * k)) & m) + ref for k in range(L - 1, -1, -1)])
                p += nb
            if len(vals) >= limit:
                break
        is_miss = bytes(len(vals))
        ng_loop_done = True
    else:
        ng_loop_done = False
    for g in ([] if ng_loop_done else range(ng)):
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
            raw = [(x >> (W * (L - 1 - k))) & m for k in range(L)]
            p += nb
            if miss_mgmt:
                mm1, mm2 = m, m - 1
                for v in raw:
                    if v == mm1:
                        is_miss.append(1)
                    elif miss_mgmt == 2 and v == mm2:
                        is_miss.append(2)
                    else:
                        vals.append(v + ref)
                        is_miss.append(0)
            else:
                vals.extend([v + ref for v in raw])
                is_miss.extend([0] * L)
        if len(is_miss) >= limit:
            break
    # Undo the spatial differencing (over present values only), as two running sums for order 2.
    if order == 1 and vals:
        vals[0] = ival[0]
        vals = list(itertools.accumulate((v + minsd if i else v for i, v in enumerate(vals))))
    elif order == 2 and len(vals) >= 2:
        first = ival[1] - ival[0]
        diffs = itertools.accumulate(itertools.chain([first], (v + minsd for v in vals[2:])))
        vals = list(itertools.accumulate(itertools.chain([ival[0]], diffs)))
    scale_e, scale_d = 2.0 ** E, 10.0 ** (-D)
    return {"R": R, "E": E, "D": D, "order": order, "miss_mgmt": miss_mgmt, "ng": ng, "vals": vals,
            "is_miss": is_miss, "scale": (R, scale_e, scale_d), "npts": npts}


def values_at(dec, indices):
    """Grid indices -> real values (None where missing)."""
    R, se, sd = dec["scale"]
    if not dec["miss_mgmt"]:
        return [(R + dec["vals"][i] * se) * sd for i in indices]
    # Map each grid index to its position among present values.
    want = sorted(set(indices))
    pos, k, out = 0, 0, {}
    miss = dec["is_miss"]
    j = 0
    for idx in want:
        while j < idx:
            if miss[j] == 0:
                pos += 1
            j += 1
        out[idx] = None if miss[idx] else (R + dec["vals"][pos] * se) * sd
    return [out[i] for i in indices]


def points(path=None):
    doc = json.load(open(path or os.path.join(ROOT, "config", "points.json"), encoding="utf-8"))
    out = []
    for z in doc["zones"]:
        out.append((z["icao"], z["lat"], z["lon"]))
        for a in z.get("alternates") or []:
            out.append((a["icao"], a["lat"], a["lon"]))
    return out


def grid_index(g, lat, lon):
    """Nearest grid point for a regular lat/lon grid scanning west to east from La1 southward (scan mode 0)."""
    if g["scan"] != 0:
        raise ValueError("scan mode %d not handled" % g["scan"])
    j = int(round((g["la1"] - lat) / g["dj"]))
    i = int(round(((lon - g["lo1"]) % 360.0) / g["di"])) % g["ni"]
    return j * g["ni"] + i, g["la1"] - j * g["dj"], (g["lo1"] + i * g["di"]) % 360.0


def _fetch_one(job):
    """(member, step, fields) -> (member, step, {field: bytes}, seconds, bytes moved)."""
    run, member, step, fields = job
    url = FILE.format(bucket=BUCKET, ymd=run[:8], hh=run[8:10], member=member, step=step)
    t0 = time.perf_counter()
    idx = _get(url + ".idx").decode("ascii")
    moved = len(idx)
    rng = ranges(parse_idx(idx))
    out = {}
    for k in fields:
        out[k] = _get(url, rng[k])
        moved += len(out[k])
    return member, step, out, time.perf_counter() - t0, moved


def _decode_one(args):
    msg, upto_by_grid = args
    s = sections(msg)
    g = grid(s[3])
    where = [grid_index(g, la, lo)[0] for _, la, lo in points()]
    t0 = time.perf_counter()
    dec = decode(msg, max(where))
    vals = values_at(dec, where)
    return time.perf_counter() - t0, vals


def _decode_eccodes(msgs):
    """The same points through ECMWF's ecCodes (pip package `eccodes`), for comparison. Not a Vis dependency."""
    import eccodes
    pts = points()
    out, t_all = [], time.perf_counter()
    for msg in msgs:
        h = eccodes.codes_new_from_message(msg)
        v = eccodes.codes_get_values(h)
        eccodes.codes_release(h)
        g = grid(sections(msg)[3])
        out.append([float(v[grid_index(g, la, lo)[0]]) for _, la, lo in pts])
    return time.perf_counter() - t_all, out


def bench(run, members, steps, fields, threads, procs, also_eccodes=False):
    """Fetch and decode `fields` for every member and step, timing each stage separately."""
    import concurrent.futures as cf
    jobs = [(run, m, s, fields) for m in members for s in steps]
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(threads) as ex:
        got = list(ex.map(_fetch_one, jobs))
    fetch_wall = time.perf_counter() - t0
    moved = sum(g[4] for g in got)
    report = {"files": len(jobs), "records": len(jobs) * len(fields), "bytes": moved,
              "bytes_by_field": {k: sum(len(g[2][k]) for g in got) for k in fields},
              "fetch_wall_s": round(fetch_wall, 2), "fetch_MBps": round(moved / 1e6 / fetch_wall, 1),
              "threads": threads, "procs": procs, "cpus": os.cpu_count(), "python": sys.version.split()[0]}
    for k in fields:
        msgs = [g[2][k] for g in got]
        t0 = time.perf_counter()
        if procs > 1:
            with cf.ProcessPoolExecutor(procs) as ex:
                dec = list(ex.map(_decode_one, [(m, None) for m in msgs], chunksize=4))
        else:
            dec = [_decode_one((m, None)) for m in msgs]
        wall = time.perf_counter() - t0
        per = sorted(d[0] for d in dec)
        row = {"decode_wall_s": round(wall, 2), "decode_cpu_s_median": round(per[len(per) // 2], 3),
               "decode_cpu_s_max": round(per[-1], 3), "decode_cpu_s_sum": round(sum(per), 1)}
        if also_eccodes:
            t_ec, ec = _decode_eccodes(msgs)
            diffs = [abs(a - b) for d, e in zip(dec, ec) for a, b in zip(d[1], e) if a is not None]
            row.update({"eccodes_wall_s_one_proc": round(t_ec, 2), "eccodes_max_abs_diff": max(diffs)})
        report[k] = row
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", action="store_true", help="time fetch and decode for --members over --steps")
    ap.add_argument("--members", default="gep01")
    ap.add_argument("--steps", default="3-168", help="first-last forecast hour, 3-hourly")
    ap.add_argument("--fields", default="ceil")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--eccodes", action="store_true", help="also decode with ecCodes and compare the point values")
    ap.add_argument("--run", required=True, help="YYYYMMDDHH")
    ap.add_argument("--step", type=int, default=24)
    ap.add_argument("--member", default="gep01")
    ap.add_argument("--data", help="empty folder outside the repo for the downloaded records")
    ap.add_argument("--decode-only", action="store_true")
    ap.add_argument("--full", action="store_true", help="decode the whole field, not just up to the last point")
    a = ap.parse_args(argv)
    if a.bench:
        s0, s1 = (int(x) for x in a.steps.split("-"))
        members = [m for m in a.members.split(",") if m]
        if members == ["all"]:
            members = ["gec00"] + ["gep%02d" % i for i in range(1, 31)]
        print(json.dumps(bench(a.run, members, list(range(s0, s1 + 1, 3)), a.fields.split(","), a.threads,
                               a.procs, a.eccodes), indent=1))
        return 0
    if not a.data:
        ap.error("--data is required")
    ymd, hh = a.run[:8], a.run[8:10]
    url = FILE.format(bucket=BUCKET, ymd=ymd, hh=hh, member=a.member, step=a.step)
    tag = "%s.%s.f%03d" % (a.run, a.member, a.step)
    timing = {}
    recs = {}
    if not a.decode_only:
        t0 = time.perf_counter()
        idx = _get(url + ".idx").decode("ascii")
        timing["idx_s"] = round(time.perf_counter() - t0, 3)
        rng = ranges(parse_idx(idx))
        for k, (b0, b1) in rng.items():
            t0 = time.perf_counter()
            body = _get(url, (b0, b1))
            timing["get_%s_s" % k] = round(time.perf_counter() - t0, 3)
            with open(os.path.join(a.data, "%s.%s.grb2" % (tag, k)), "wb") as f:
                f.write(body)
    pts = points()
    report = {"file": url, "timing": timing, "fields": {}}
    for k in WANT:
        path = os.path.join(a.data, "%s.%s.grb2" % (tag, k))
        msg = open(path, "rb").read()
        s = sections(msg)
        g, prod = grid(s[3]), product(s[4])
        where = [grid_index(g, la, lo) for _, la, lo in pts]
        upto = None if a.full else max(w[0] for w in where)
        t0 = time.perf_counter()
        dec = decode(msg, upto)
        vals = values_at(dec, [w[0] for w in where])
        dt = time.perf_counter() - t0
        report["fields"][k] = {
            "bytes": len(msg), "decode_s": round(dt, 3), "decoded_points": len(dec["is_miss"]),
            "grid": g, "product": prod, "groups": dec["ng"], "order": dec["order"], "missing_mgmt": dec["miss_mgmt"],
            "E": dec["E"], "D": dec["D"], "R": dec["R"],
            "missing_in_decoded": sum(1 for m in dec["is_miss"] if m),
            "points": {icao: (None if v is None else round(v, 1)) for (icao, _, _), v in zip(pts, vals)},
        }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
