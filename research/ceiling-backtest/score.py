"""Score ceiling candidates from analyse.py's rows against METAR ceilings.  python -I score.py rows.json out.json

results-2026-11-17-26.json is this script's output for the best4 rows (17-26 Nov 2026). Not part of the build."""
import json
import sys
from collections import defaultdict

rows = json.load(open(sys.argv[1], encoding="utf-8"))
THR = {"amber": 305.0, "red": 152.0}
MISMATCH_M = 100.0


def rh_base(r, R):
    for z, rh in r["prof"]:
        if rh >= R:
            return max(0.0, z)
    return None


def fog(r):
    return 0.0 if r["vis"] < 1000 else None


def lo(*v):
    v = [x for x in v if x is not None]
    return min(v) if v else None


CANDS = {
    "CUR (field Vis reads, above model ground)": lambda r: r["cur"],
    "CUR as Vis applies it (above station)": lambda r: r["cur_station"],
    "LCB (low-cloud cover >=50% and base)": lambda r: r["lcb"],
    "RH90": lambda r: rh_base(r, 90), "RH95": lambda r: rh_base(r, 95), "RH98": lambda r: rh_base(r, 98),
    "FOG (VIS < 1 km)": fog,
}
for R in (90, 95, 98):
    CANDS[f"LCB+RH{R}"] = (lambda R: lambda r: lo(r["lcb"], rh_base(r, R)))(R)
    CANDS[f"LCB+RH{R}+FOG"] = (lambda R: lambda r: lo(r["lcb"], rh_base(r, R), fog(r)))(R)


def table(sub, f, thr):
    h = m = fa = cn = 0
    for r in sub:
        o = r["obs"] <= thr
        v = f(r)
        p = v is not None and v <= thr
        h += o and p
        m += o and not p
        fa += p and not o
        cn += (not o) and (not p)
    n = h + m + fa + cn
    ref = (h + m) * (h + fa) / n if n else 0
    ets = (h - ref) / (h + m + fa - ref) if (h + m + fa - ref) else None
    return {"n": n, "events": h + m, "hits": h, "misses": m, "false_alarms": fa,
            "pod": round(h / (h + m), 3) if h + m else None, "far": round(fa / (h + fa), 3) if h + fa else None,
            "bias": round((h + fa) / (h + m), 2) if h + m else None, "ets": round(ets, 3) if ets is not None else None}


def obs_class(o):
    if o <= 152:
        return "1: <=152 m (400 ft or lower)"
    if o <= 305:
        return "2: 153-305 m (500-1,000 ft)"
    if o <= 914:
        return "3: 305-914 m (1,100-3,000 ft)"
    if o < 20000:
        return "4: above 914 m"
    return "5: no ceiling"


for r in rows:
    r["mismatch"] = abs(r["ground"] - r["elev"]) > MISMATCH_M
    r["half"] = "train" if r["valid"] < "2026-11-22" else "test"

res = {"n_rows": len(rows), "stations": len({r["sid"] for r in rows}),
       "valid_times": len({r["valid"] for r in rows}),
       "mismatch_stations": sorted({r["sid"] for r in rows if r["mismatch"]})}
for half in ("train", "test", "all"):
    for lvl, thr in THR.items():
        for name, f in CANDS.items():
            for terr in ("matched", "all"):
                sub = [r for r in rows if (half == "all" or r["half"] == half) and (terr == "all" or not r["mismatch"])]
                res[f"{half}|{lvl}|{terr}|{name}"] = table(sub, f, thr)

# By region (all times, matched terrain), amber and red, for CUR and the chosen ones.
PICK = ["CUR (field Vis reads, above model ground)", "LCB (low-cloud cover >=50% and base)", "RH95", "LCB+RH95",
        "LCB+RH95+FOG", "LCB+RH98+FOG"]
reg = defaultdict(dict)
for region in sorted({r["region"] for r in rows}):
    sub = [r for r in rows if r["region"] == region and not r["mismatch"]]
    for lvl, thr in THR.items():
        for name in PICK:
            reg[region][f"{lvl}|{name}"] = table(sub, CANDS[name], thr)
res["by_region"] = reg

# By observed class: share of reports at which each candidate gives a ceiling at or below 305 m and 152 m.
cls = defaultdict(dict)
for c in sorted({obs_class(r["obs"]) for r in rows}):
    sub = [r for r in rows if obs_class(r["obs"]) == c and not r["mismatch"]]
    for name in PICK:
        v = [CANDS[name](r) for r in sub]
        cls[c][name] = {"n": len(sub), "le305": round(sum(1 for x in v if x is not None and x <= 305) / len(sub), 3),
                        "le152": round(sum(1 for x in v if x is not None and x <= 152) / len(sub), 3)}
res["by_obs_class"] = cls

# CAPE split of observed <= 305 m events (matched): what the current field and the candidates catch.
cape = {}
for k, cond in (("CAPE 0", lambda r: r["cape"] < 0.5), ("CAPE >= 1", lambda r: r["cape"] >= 0.5)):
    sub = [r for r in rows if r["obs"] <= 305 and not r["mismatch"] and cond(r)]
    cape[k] = {"events": len(sub), **{name: round(sum(1 for r in sub if (lambda v: v is not None and v <= 305)(CANDS[name](r))) / len(sub), 3) if sub else None for name in PICK}}
res["amber_events_by_cape"] = cape

# Misses of the best combination: a sample with the model's own values.
best = CANDS["LCB+RH95+FOG"]
miss = [r for r in rows if r["obs"] <= 305 and not r["mismatch"] and not ((lambda v: v is not None and v <= 305)(best(r)))]
res["misses_sample"] = [{k: r[k] for k in ("sid", "region", "valid", "raw", "lowc", "lcb", "cape", "vis", "prof")}
                        for r in miss[:: max(1, len(miss) // 25)]][:25]
res["misses_n"] = len(miss)
fas = [r for r in rows if r["obs"] > 914 and not r["mismatch"] and ((lambda v: v is not None and v <= 305)(best(r)))]
res["false_alarm_sample"] = [{k: r[k] for k in ("sid", "region", "valid", "raw", "lowc", "lcb", "cape", "vis")}
                             for r in fas[:: max(1, len(fas) // 15)]][:15]
json.dump(res, open(sys.argv[2], "w", encoding="utf-8"), indent=1)
for half in ("train", "test"):
    for lvl in THR:
        print(f"== {half} {lvl} (matched terrain)")
        for name in CANDS:
            t = res[f"{half}|{lvl}|matched|{name}"]
            print(f"  {name:45s} ev {t['events']:4d} hit {t['hits']:4d} miss {t['misses']:4d} fa {t['false_alarms']:4d} pod {t['pod']} far {t['far']} bias {t['bias']} ets {t['ets']}")
