"""Forecast verification (plan step W3): resolve each archived Vis forecast against the station's METARs, then score.

    python tools/verify.py resolve --forecasts DIR [--metar history/metar] [--dir history/verify]
    python tools/verify.py score [--dir history/verify] [--out out/verification.json]

Input forecasts are the per-build `*.forecast.gen-*.json.gz` assets of the `vis-data-YYYY-MM` releases (kept since
19 Nov 2026; nothing earlier was stored). Observations come from `tools/metar.py`'s append-only store.

What METARs can resolve, and how (method `vis-verify/1`):
- wind (gusts), vis (visibility), heat and cold (2 m temperature). Each of the model's 3-hourly steps (00, 03 ... 21
  UTC) inside the forecast's window for that UTC day takes the station report nearest the step, within 60 minutes;
  wind uses the larger of the mean wind and the gust. The hazard is observed when the same test the forecast used
  (`forecast.hit`) holds on `min_steps` consecutive steps, as in the forecast.
- ceiling (the "Low ceiling" trial, from 27 Nov 2026): the report's lowest BKN, OVC or VV base, from the raw text
  before any TEMPO, BECMG, NOSIG or RMK group, in metres above the aerodrome. FEW or SCT only, CAVOK, NSC, SKC, CLR and
  NCD mean no ceiling (stored as gefs.NO_CEILING_M); CAVOK and NSC only say nothing below 1,500 m, so ceiling
  thresholds stay under that. A ceiling layer whose height is `///`, or a report with no cloud group, is unresolved,
  never "no ceiling".
- An event is "not observed" only when at least 75% of the steps have a report and no hit sits next to a missing step.
  Otherwise the forecast is stored as unresolved, with the reason, and counted; a missing report is never filled.
- Not resolvable from METARs and counted as such: heavy rain and heavy snow (24 h totals, which most of these stations
  do not report), low cloud cover (a model cloud fraction, not a ceiling) and icing (needs a precipitation rate).

Stores (append-only, written after the day ends plus 6 hours; a stored line is never rewritten):
`history/verify/observed.jsonl` holds what the station showed, once per point, UTC day and window start;
`history/verify/pairs.jsonl` holds each forecast's probabilities for that point and day. Scores use one forecast per model run (the latest build of it), so repeat builds of the same
run are not counted twice. Standard library only.
"""
import argparse
import gzip
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import forecast  # noqa: E402
import metar  # noqa: E402

METHOD = "vis-verify/1"
SCORED = ("wind", "vis", "heat", "cold", "ceiling")
NOT_RESOLVABLE = {
    "rain": "24-hour rainfall: most of these stations report no precipitation amount in METARs",
    "snow": "24-hour snowfall: METARs carry no snowfall amount",
    "lowcloud": "model low-cloud fraction has no direct METAR equivalent (METARs give layer amount and base)",
    "icing": "needs a precipitation rate, which METARs do not give",
}
STEP_H = forecast.NATIVE_STEP_HOURS
NEAR_MIN = 60
MIN_COVERAGE = 0.75
GRACE_H = 6
# Readable when a cell has at least this many forecasts and this many observed events (stated on the page).
READ_MIN_N = 100
READ_MIN_EVENTS = 10
READ_MIN_POINTS = 3  # points with at least one observed event, so one station cannot decide a row
# Reliability bins: the ICD 203 likelihood bands Vis's terms use.
BINS = [(0.0, 0.05, "Almost no chance"), (0.05, 0.20, "Very unlikely"), (0.20, 0.45, "Unlikely"),
        (0.45, 0.55, "Roughly even chance"), (0.55, 0.80, "Likely"), (0.80, 0.95, "Very likely"),
        (0.95, 1.0001, "Almost certain")]
NAME_RE = re.compile(r"^(\d{8}T\d{4}Z)\.forecast\.gen-(\d{8}T\d{6}Z)\.json\.gz$")
SM_M = 1609.344
UNITS = {"wind": "km/h", "vis": "m", "heat": "C", "cold": "C", "ceiling": "m"}
FT_M = 0.3048
NO_CEILING_M = 20000.0   # the same "none" value as tools/gefs.py
CLOUD_RE = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\d{3}|///)(?:CB|TCU|///)?$")
NO_CLOUD = {"CAVOK", "NSC", "SKC", "CLR", "NCD"}


def _t(iso):
    iso = iso.rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S" if iso.count(":") == 2 else "%Y-%m-%dT%H:%M"
    return datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc)


def visib_m(rep):
    """Visibility in metres from AWC's statute-mile field ('6+', '1 1/2', '0.25'); CAVOK counts as 10 km."""
    raw = rep.get("raw") or ""
    if " CAVOK" in raw:
        return 10000.0
    # The metric group in the report itself, when there is one: AWC's mile figure is rounded (3000 m reads 1.86 SM).
    body = re.split(r" (?:TEMPO|BECMG|NOSIG|RMK)\b", raw)[0]
    mm = re.search(r" (\d{4})(?:NDV)? ", body + " ")
    if mm:
        v = float(mm.group(1))
        return 10000.0 if v == 9999 else v
    v = rep.get("visib")
    if isinstance(v, (int, float)):
        return float(v) * SM_M
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().rstrip("+").replace("M", "").replace("P", "")
    try:
        total = 0.0
        for part in s.split():
            if "/" in part:
                a, b = part.split("/")
                total += float(a) / float(b)
            else:
                total += float(part)
        return total * SM_M
    except (ValueError, ZeroDivisionError):
        return None


def ceiling_m(rep):
    """Ceiling above the aerodrome in metres from the raw METAR; NO_CEILING_M for none, None when unresolved."""
    raw = rep.get("raw") or ""
    body = re.split(r" (?:TEMPO|BECMG|NOSIG|RMK)\b", raw)[0].split()
    layers = [CLOUD_RE.match(t) for t in body]
    layers = [(m.group(1), m.group(2)) for m in layers if m]
    cig = [(a, h) for a, h in layers if a in ("BKN", "OVC", "VV")]
    if any(h == "///" for _, h in cig):
        return None
    if cig:
        return round(min(int(h) for _, h in cig) * 100 * FT_M, 1)
    if layers or NO_CLOUD.intersection(body):
        return NO_CEILING_M
    return None


def value(hazard, rep):
    if hazard == "ceiling":
        return ceiling_m(rep)
    if hazard == "wind":
        w = [x for x in (rep.get("wspd_kt"), rep.get("wgst_kt")) if isinstance(x, (int, float))]
        return max(w) * 1.852 if w else None
    if hazard == "vis":
        return visib_m(rep)
    t = rep.get("temp_c")
    return float(t) if isinstance(t, (int, float)) else None


def steps_for(day, start_iso):
    d0 = _t(day + "T00:00")
    start = _t(start_iso) if start_iso else d0
    return [d0 + timedelta(hours=h) for h in range(0, 24, STEP_H) if d0 + timedelta(hours=h) >= start]


def nearest(reports, at, hazard):
    best, gap = None, None
    for r in reports:
        if value(hazard, r) is None:
            continue
        g = abs((_t(r["obs_time"]) - at).total_seconds())
        if g <= NEAR_MIN * 60 and (gap is None or g < gap):
            best, gap = r, g
    return best


def resolve_level(hz, threshold, steps, reports):
    """(outcome 1/0/None, steps seen, extreme value, why)."""
    vals = []
    for s in steps:
        r = nearest(reports, s, hz["id"])
        vals.append(value(hz["id"], r) if r else None)
    seen = [v for v in vals if v is not None]
    ext = None
    if seen:
        ext = round(min(seen) if hz["dir"] == "below" else max(seen), 1)
    flags = [None if v is None else bool(forecast.hit(v, threshold, hz["dir"])) for v in vals]
    run = best = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    need = hz.get("min_steps", 1)
    if best >= need:
        return 1, len(seen), ext, None
    if len(steps) < need:
        return None, len(seen), ext, "window shorter than the persistence rule"
    if not reports:
        return None, 0, None, "no reports from the station"
    if len(seen) < MIN_COVERAGE * len(steps):
        return None, len(seen), ext, "too few reports in the window"
    for i, f in enumerate(flags):
        if f and ((i > 0 and flags[i - 1] is None) or (i + 1 < len(flags) and flags[i + 1] is None)):
            return None, len(seen), ext, "a hit sits next to a missing report"
    return 0, len(seen), ext, None


def load_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append(path, rows):
    if rows:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def observe(hazards, zone, day, steps, obs_base):
    """One observation line for a point, UTC day and window start (shared by every forecast with that window)."""
    d0 = _t(day + "T00:00")
    reps = metar.load_range(obs_base, [(d0 + timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)])
    reports = reps.get(zone.get("icao"), [])
    obs = {}
    for hid in SCORED:
        hz = hazards.get(hid)
        if not hz:
            continue
        obs[hid] = {}
        seen = ext = None
        for lvl in ("amber", "red"):
            o, seen, ext, why = resolve_level(hz, hz[lvl], steps, reports)
            obs[hid][lvl] = {"outcome": o, "threshold": hz[lvl], "why": why}
        obs[hid]["steps_seen"], obs[hid]["extreme"] = seen, ext
    return {"method": METHOD, "zone": zone["id"], "icao": zone.get("icao"), "day": day,
            "start": steps[0].strftime("%Y-%m-%dT%H:%MZ") if steps else None, "steps": len(steps),
            "reports": len(reports), "obs": obs}


def resolve(forecast_dir, obs_base, verify_dir, now=None):
    """Append observations (observed.jsonl) and forecast probabilities (pairs.jsonl) for days that have ended.

    observed.jsonl: one line per point, UTC day and window start. pairs.jsonl: one line per forecast, point and day,
    holding the amber and red probabilities of the scored hazards and the window start that joins it to its
    observation. Lines already stored are never rewritten; each run adds only what is new.
    """
    now = now or datetime.now(timezone.utc)
    vdir = Path(verify_dir)
    have_obs = {(r["zone"], r["day"], r["start"]) for r in load_jsonl(vdir / "observed.jsonl")}
    have_pair = {(r["forecast"], r["zone"], r["day"]) for r in load_jsonl(vdir / "pairs.jsonl")}
    new_obs, new_pairs = [], []
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in sorted(Path(forecast_dir).glob("*.forecast.gen-*.json.gz")):
        name = f.name
        m = NAME_RE.match(name)
        doc = json.loads(gzip.decompress(f.read_bytes()).decode("utf-8"))
        hazards = {h["id"]: h for h in doc.get("hazards", [])}
        gen = doc["generated_at"]
        for z in doc.get("zones", []):
            for d in z.get("days", []):
                day = d["day"]
                if (name, z["id"], day) in have_pair or now < _t(day + "T00:00") + timedelta(hours=24 + GRACE_H):
                    continue
                steps = steps_for(day, d.get("from"))
                start = steps[0].strftime("%Y-%m-%dT%H:%MZ") if steps else None
                if (z["id"], day, start) not in have_obs:
                    row = observe(hazards, z, day, steps, obs_base)
                    row["resolved_at"] = stamp
                    new_obs.append(row)
                    have_obs.add((z["id"], day, start))
                p = {}
                for hid in SCORED:
                    fc = (d.get("hazards") or {}).get(hid)
                    if fc:
                        p[hid] = [fc.get("p_amber"), fc.get("p_red"), fc.get("members")]
                new_pairs.append({"forecast": name, "run": m.group(1) if m else None, "generated_at": gen,
                                  "zone": z["id"], "day": day, "start": start,
                                  "lead_days": (_t(day + "T00:00").date() - _t(gen).date()).days, "p": p})
                have_pair.add((name, z["id"], day))
    _append(vdir / "observed.jsonl", new_obs)
    _append(vdir / "pairs.jsonl", new_pairs)
    return new_obs, new_pairs


def joined(verify_dir):
    """Rows of (forecast, point, day, hazard, level, p, outcome) built from the two stores."""
    vdir = Path(verify_dir)
    obs = {(r["zone"], r["day"], r["start"]): r for r in load_jsonl(vdir / "observed.jsonl")}
    rows = []
    for pr in load_jsonl(vdir / "pairs.jsonl"):
        o = obs.get((pr["zone"], pr["day"], pr["start"]))
        for hid, vals in pr["p"].items():
            for lvl, p in (("amber", vals[0]), ("red", vals[1])):
                ob = ((o or {}).get("obs") or {}).get(hid, {}).get(lvl) or {}
                rows.append({"forecast": pr["forecast"], "run": pr.get("run"), "generated_at": pr["generated_at"],
                             "zone": pr["zone"], "day": pr["day"], "lead_days": pr["lead_days"], "hazard": hid,
                             "level": lvl, "p": p, "outcome": ob.get("outcome") if o else None,
                             "why": ob.get("why") if o else "no observation line",
                             "threshold": ob.get("threshold"), "unit": UNITS.get(hid)})
    return rows


def _cell(rows):
    n = len(rows)
    ev = sum(r["outcome"] for r in rows)
    brier = sum((r["p"] - r["outcome"]) ** 2 for r in rows) / n if n else None
    base = ev / n if n else None
    clim = base * (1 - base) if n else None
    bss = (1 - brier / clim) if n and clim else None
    rel = []
    for lo, hi, term in BINS:
        b = [r for r in rows if lo <= r["p"] < hi]
        if b:
            rel.append({"band": term, "n": len(b), "mean_p": round(sum(r["p"] for r in b) / len(b), 3),
                        "observed": round(sum(r["outcome"] for r in b) / len(b), 3)})
    pts_ev = len({r["zone"] for r in rows if r["outcome"]})
    readable = n >= READ_MIN_N and ev >= READ_MIN_EVENTS and pts_ev >= READ_MIN_POINTS
    return {"n": n, "events": ev, "points": len({r["zone"] for r in rows}),
            "points_with_events": pts_ev, "brier": None if brier is None else round(brier, 4),
            "base_rate": None if base is None else round(base, 4),
            "brier_skill_vs_sample_climatology": None if bss is None else round(bss, 3),
            "readable": readable,
            "note": None if readable else (f"too few to read (needs {READ_MIN_N} forecasts and {READ_MIN_EVENTS} "
                                           f"observed events at {READ_MIN_POINTS} or more points; has {n}, {ev} "
                                           f"and {pts_ev})"),
            "reliability": rel}


def score(rows, now=None):
    now = now or datetime.now(timezone.utc)
    latest = {}
    for r in rows:
        k = (r.get("run") or r["forecast"], r["zone"], r["day"], r["hazard"], r["level"])
        if k not in latest or r["generated_at"] > latest[k]["generated_at"]:
            latest[k] = r
    use = list(latest.values())
    ok = [r for r in use if r["outcome"] in (0, 1) and isinstance(r.get("p"), (int, float))]
    unresolved = {}
    for r in use:
        if r["outcome"] not in (0, 1):
            k = (r["hazard"], r.get("why") or "unresolved")
            unresolved[k] = unresolved.get(k, 0) + 1
    cells = []
    for hid in SCORED:
        for lvl in ("amber", "red"):
            sub = [r for r in ok if r["hazard"] == hid and r["level"] == lvl]
            if not sub:
                cells.append({"hazard": hid, "level": lvl, "lead_days": "all", **_cell([])})
                continue
            thr = sub[0].get("threshold")
            cells.append({"hazard": hid, "level": lvl, "lead_days": "all", "threshold": thr,
                          "unit": sub[0].get("unit"), **_cell(sub)})
            for lead in sorted({r["lead_days"] for r in sub}):
                cells.append({"hazard": hid, "level": lvl, "lead_days": lead, "threshold": thr,
                              "unit": sub[0].get("unit"), **_cell([r for r in sub if r["lead_days"] == lead])})
    days = sorted({r["day"] for r in use})
    return {
        "program": "vis", "method": METHOD, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
        "forecasts_counted": len(use), "resolved": len(ok), "unresolved": len(use) - len(ok),
        "unresolved_by_reason": [{"hazard": h, "why": w, "n": n} for (h, w), n in sorted(unresolved.items())],
        "rules": {
            "one_forecast_per_model_run": True,
            "readable_when": (f"at least {READ_MIN_N} forecasts and {READ_MIN_EVENTS} observed events, at "
                              f"{READ_MIN_POINTS} or more points"),
            "steps": f"{STEP_H}-hourly model steps, nearest report within {NEAR_MIN} min",
            "not_observed_needs": f"{int(MIN_COVERAGE * 100)}% of steps with a report",
            "thresholds": "indicative (JP 3-59 gives no numeric limits); not for flight planning",
        },
        "observations": "METARs from the NOAA Aviation Weather Center (aviationweather.gov), US public domain",
        "scores": cells,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--forecasts", required=True)
    r.add_argument("--metar", default=str(ROOT / "history" / "metar"))
    r.add_argument("--dir", default=str(ROOT / "history" / "verify"))
    s = sub.add_parser("score")
    s.add_argument("--dir", default=str(ROOT / "history" / "verify"))
    s.add_argument("--out", default=str(ROOT / "out" / "verification.json"))
    a = ap.parse_args(argv)
    if a.cmd == "resolve":
        obs, pairs = resolve(a.forecasts, a.metar, a.dir)
        print(f"verify: {len(obs)} new observation lines, {len(pairs)} new forecast lines")
    else:
        doc = score(joined(a.dir))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"verify: scored {doc['resolved']} of {doc['forecasts_counted']} forecasts ({doc['first_day']} "
              f"to {doc['last_day']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
