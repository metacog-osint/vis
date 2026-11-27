"""Back-test model fields: GEFS v12 control member (gec00), 0.5 deg a and b files, byte ranges through each .idx.

    python -I fetch_gefs.py <out dir> <first YYYYMMDDHH> <last YYYYMMDDHH> [steps, default 3,9]
One file per cycle, step and set: <out>/<cycle>_f<step>_<a|b>.grb2 holding only the records below.
"""
import concurrent.futures as cf
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

UA = "Vis-backtest/1.0 (+https://vis.theintelligence.company)"
BASE = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.{ymd}/{hh}/atmos/pgrb2{set}p5/gec00.t{hh}z.pgrb2{set}.0p50.f{step:03d}"
WANT = {
    "b": ["HGT:surface:", "LAND:surface:", "HGT:cloud ceiling:", "CAPE:surface:", "VIS:surface:",
          "TCDC:low cloud layer:", "PRES:low cloud bottom level:", "TCDC:boundary layer cloud layer:",
          "HGT:1 hybrid level:", "HGT:2 hybrid level:", "HGT:3 hybrid level:", "HGT:4 hybrid level:",
          "RH:1 hybrid level:", "RH:2 hybrid level:", "RH:3 hybrid level:", "RH:4 hybrid level:",
          "RH:30-0 mb above ground:", "RH:60-30 mb above ground:", "RH:90-60 mb above ground:",
          "RH:120-90 mb above ground:", "HPBL:surface:"],
    "a": ["PRES:surface:", "TMP:2 m above ground:", "RH:2 m above ground:"],
}


def get(url, rng=None, tries=4):
    for k in range(tries):
        try:
            h = {"User-Agent": UA}
            if rng:
                h["Range"] = f"bytes={rng[0]}-{rng[1]}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                raise
            time.sleep(3 + 5 * k)


def ranges(idx_text, want):
    lines = [ln.split(":") for ln in idx_text.strip().splitlines()]
    out = []
    for i, p in enumerate(lines):
        key = f"{p[3]}:{p[4]}:"
        if key in want:
            start = int(p[1])
            end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
            out.append((key, start, end))
    return out


def one(job):
    out, cyc, step, s = job
    path = out / f"{cyc:%Y%m%d%H}_f{step:03d}_{s}.grb2"
    if path.exists() and path.stat().st_size > 0:
        return path.name, path.stat().st_size, "kept"
    url = BASE.format(ymd=f"{cyc:%Y%m%d}", hh=f"{cyc:%H}", set=s, step=step)
    rr = ranges(get(url + ".idx").decode("ascii"), set(WANT[s]))
    got = {k for k, _, _ in rr}
    missing = set(WANT[s]) - got
    parts = [get(url, (a, b)) for _, a, b in rr]
    tmp = path.with_suffix(".part")
    tmp.write_bytes(b"".join(parts))
    tmp.replace(path)
    return path.name, path.stat().st_size, ("missing " + ",".join(sorted(missing))) if missing else "ok"


def main(out, first, last, steps="3,9"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = datetime.strptime(first, "%Y%m%d%H")
    t1 = datetime.strptime(last, "%Y%m%d%H")
    jobs = []
    c = t0
    while c <= t1:
        for st in [int(x) for x in steps.split(",")]:
            for s in ("a", "b"):
                jobs.append((out, c, st, s))
        c += timedelta(hours=12)
    total = 0
    with cf.ThreadPoolExecutor(6) as ex:
        for name, size, state in ex.map(one, jobs):
            total += size
            print(name, size, state, flush=True)
    print("total MB", round(total / 1e6, 1))


if __name__ == "__main__":
    main(*sys.argv[1:])
