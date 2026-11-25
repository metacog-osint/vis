"""Thunderstorm trial (method vis-ensemble-1.2): how often the thunderstorm hazard would have fired, and
how that compares with the thunderstorms the stations reported.

    python tools/tstorm_trial.py metar [--metar history/metar] [--first YYYY-MM-DD] [--last YYYY-MM-DD]
    python tools/tstorm_trial.py fetch [--past-days 10] [--out out/tstorm_trial]
    python tools/tstorm_trial.py measure [--answers out/tstorm_trial] [--metar history/metar]

metar    What the METAR store says, per station and full UTC day, under the scoring rule of tools/verify.py
         (vis-verify/2): days with a thunderstorm reported (TS, VCTS or RETS), days with cumulonimbus cloud or
         lightning remarks only, days unresolved, and how long each run of thunderstorm reports lasted.
fetch    The two ensembles' CAPE and precipitation for every point from Open-Meteo's Ensemble API, with `past_days`:
         for past hours Open-Meteo serves what successive runs said about them, so this is a short-lead hindcast, not
         the archived 7-day forecasts. Two models, one call per cell selection (land, sea) each: 4 calls for the 31
         points, with about the weight of one forecast run on Open-Meteo's free tier (CC BY 4.0, non-commercial; 600 calls a
         minute, 5,000 an hour, 10,000 a day): run it once, a minute away from any other Open-Meteo call. Raw answers
         are saved next to the report so the measurement can be re-run offline.
measure  For each full past UTC day, point and candidate rule (CAPE line, rain line, consecutive 3-hourly steps), the
         share of members that meet it, each model weighted equally as in the forecast; then how many point-days
         would have shown at least Yellow (20%) or Amber (45%, without the trial cap), and the Brier score and hit and
         false-alarm counts against the METAR outcome where the station's reports resolve the day.

Writes out/tstorm_trial/ (git-ignored). Standard library only.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import forecast  # noqa: E402
import metar  # noqa: E402
import verify  # noqa: E402

MODELS = ("ncep_gefs025", "ecmwf_ifs025")
HOURLY = "cape,precipitation"
CAPE_LINES = (500.0, 1000.0, 1500.0, 2500.0)
RAIN_LINES = (0.5, 1.0, 2.5)
STEP_RULES = (1, 2)
CHOSEN = (1000.0, 1.0, 1)   # the forecast's amber line and persistence (forecast.HAZARDS "tstorm")
EPISODE_GAP_MIN = 90        # thunderstorm reports this close together are one episode


def _day(t):
    return t.strftime("%Y-%m-%d")


def station_days(reports, first, last):
    """Per full UTC day from first to last: (amber outcome, red outcome, why, cb_only, thunder reports)."""
    out = {}
    d = datetime.strptime(first, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    while d <= end:
        steps = verify.steps_for(_day(d), None)
        a, _, n, why = verify.resolve_tstorm("amber", steps, reports)
        r, _, _, _ = verify.resolve_tstorm("red", steps, reports)
        inday = [verify.thunder(x) for x in reports if _day(verify._t(x["obs_time"])) == _day(d)]
        cb = any(x["cb"] for x in inday) and not any(x["ts"] for x in inday)
        out[_day(d)] = {"amber": a, "red": r, "why": why, "cb_only": cb, "thunder_reports": n}
        d += timedelta(days=1)
    return out


def episodes(reports):
    """Runs of thunderstorm reports (gaps under EPISODE_GAP_MIN): [(first, last, minutes between them)]."""
    ts = sorted(verify._t(r["obs_time"]) for r in reports if verify.thunder(r)["ts"] and verify.thunder(r)["known"])
    runs, cur = [], []
    for t in ts:
        if cur and (t - cur[-1]).total_seconds() > EPISODE_GAP_MIN * 60:
            runs.append(cur)
            cur = []
        cur.append(t)
    if cur:
        runs.append(cur)
    return [(r[0].strftime("%Y-%m-%dT%H:%MZ"), r[-1].strftime("%Y-%m-%dT%H:%MZ"),
             int((r[-1] - r[0]).total_seconds() // 60)) for r in runs]


def metar_summary(obs_base, first, last, points):
    days = []
    d = datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)
    while d <= datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1):
        days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    store = metar.load_range(obs_base, days)
    rows = []
    for icao in sorted({p["icao"] for p in points}):
        reps = store.get(icao, [])
        sd = station_days(reps, first, last)
        inrange = [r for r in reps if first <= r["obs_time"][:10] <= last]
        rows.append({
            "icao": icao, "points": [p["id"] for p in points if p["icao"] == icao], "reports": len(inrange),
            "days": len(sd),
            "days_resolved": sum(1 for v in sd.values() if v["amber"] in (0, 1)),
            "days_thunder": sorted(k for k, v in sd.items() if v["amber"] == 1),
            "days_strong": sorted(k for k, v in sd.items() if v["red"] == 1),
            "days_cb_only": sorted(k for k, v in sd.items() if v["cb_only"]),
            "unresolved": {k: v["why"] for k, v in sd.items() if v["amber"] is None},
            "episodes": episodes(inrange),
        })
    return {"first_day": first, "last_day": last, "rule": verify.METHOD, "stations": rows}


def fetch(points, past_days, out_dir, opener=None, wait=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    kw = {"hourly": HOURLY, "extra": {"past_days": int(past_days)}}
    if opener:
        kw["opener"] = opener
    if wait:
        kw["wait"] = wait
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for model in MODELS:
        answers = forecast.fetch_model(points, model, 1, **kw)
        doc = {"model": model, "fetched_at": stamp, "past_days": int(past_days), "hourly": HOURLY,
               "points": [p["id"] for p in points], "answers": answers,
               "source": "Open-Meteo Ensemble API (open-meteo.com), CC BY 4.0"}
        (out / f"{model}.json").write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _rule(cape, rain, steps):
    return {"id": "tstorm", "agg": "joint", "dir": "above", "amber": cape, "red": cape, "min_steps": steps,
            "with": {"var": "precipitation", "dir": "above", "amber": rain, "red": rain}}


def day_probs(answers_by_model, idx, day):
    """{(cape, rain, steps): p} for one point and full UTC day, models weighted equally; None if no model covers it."""
    per = {}
    for model, doc in answers_by_model.items():
        a = (doc["answers"][idx] or {}).get("hourly") or {}
        times = a.get("time") or []
        try:
            i0 = times.index(day + "T00:00")
        except ValueError:
            continue
        if i0 + 24 > len(times):
            continue
        capes = forecast.member_series(a, "cape")
        rains = forecast.member_series(a, "precipitation")
        if not capes or len(capes) != len(rains):
            continue
        native = [int(t[11:13]) % forecast.NATIVE_STEP_HOURS == 0 for t in times[i0:i0 + 24]]
        for c in CAPE_LINES:
            for r in RAIN_LINES:
                for s in STEP_RULES:
                    h = _rule(c, r, s)
                    hits = sum(1 for m1, m2 in zip(capes, rains)
                               if forecast.member_hits_day(h, m1[i0:i0 + 24], m2[i0:i0 + 24], native)[0])
                    per.setdefault((c, r, s), []).append(hits / len(capes))
    return {k: sum(v) / len(v) for k, v in per.items()} if per else None


def measure(answers_dir, obs_base, points):
    adir = Path(answers_dir)
    by_model = {}
    for model in MODELS:
        f = adir / f"{model}.json"
        if f.exists():
            by_model[model] = json.loads(f.read_text(encoding="utf-8"))
    if not by_model:
        raise SystemExit(f"no answers in {adir}; run the fetch step first")
    fetched = min(d["fetched_at"] for d in by_model.values())
    order = next(iter(by_model.values()))["points"]
    pts = {p["id"]: p for p in points}
    days = sorted({t[:10] for d in by_model.values() for a in d["answers"] for t in (a or {}).get("hourly", {}).get("time", [])
                   if t[:10] < fetched[:10]})
    if not days:
        raise SystemExit("the answers hold no full past UTC day")
    store = metar.load_range(obs_base, [(datetime.strptime(days[0], "%Y-%m-%d") + timedelta(days=k)).strftime("%Y-%m-%d")
                                        for k in range(-1, len(days) + 1)])
    cells = {}
    per_point = {}
    for idx, pid in enumerate(order):
        p = pts.get(pid, {"id": pid, "icao": None})
        reps = store.get(p.get("icao"), [])
        for day in days:
            probs = day_probs(by_model, idx, day)
            if probs is None:
                continue
            o = verify.resolve_tstorm("amber", verify.steps_for(day, None), reps)[0]
            for k, pv in probs.items():
                c = cells.setdefault(k, {"point_days": 0, "yellow_or_more": 0, "amber_or_more": 0, "resolved": 0,
                                         "events": 0, "brier_sum": 0.0, "hits": 0, "misses": 0, "false_alarms": 0,
                                         "p_sum": 0.0})
                c["point_days"] += 1
                c["p_sum"] += pv
                c["yellow_or_more"] += pv >= 0.20
                c["amber_or_more"] += pv >= 0.45
                if o in (0, 1):
                    c["resolved"] += 1
                    c["events"] += o
                    c["brier_sum"] += (pv - o) ** 2
                    c["hits"] += bool(o and pv >= 0.45)
                    c["misses"] += bool(o and pv < 0.45)
                    c["false_alarms"] += bool(not o and pv >= 0.45)
            pp = per_point.setdefault(pid, {"icao": p.get("icao"), "days": 0, "p_chosen": [], "observed": []})
            pp["days"] += 1
            pp["p_chosen"].append(round(probs[CHOSEN], 3))
            pp["observed"].append(o)
    rules = []
    for (cape, rain, steps), c in sorted(cells.items()):
        rules.append({"cape_j_kg": cape, "rain_mm_h": rain, "steps": steps, "point_days": c["point_days"],
                      "mean_p": round(c["p_sum"] / c["point_days"], 4),
                      "share_yellow_or_more": round(c["yellow_or_more"] / c["point_days"], 4),
                      "share_amber_or_more": round(c["amber_or_more"] / c["point_days"], 4),
                      "resolved": c["resolved"], "events": c["events"],
                      "brier": round(c["brier_sum"] / c["resolved"], 4) if c["resolved"] else None,
                      "hits_at_45": c["hits"], "misses_at_45": c["misses"], "false_alarms_at_45": c["false_alarms"],
                      "chosen": (cape, rain, steps) == CHOSEN})
    return {"method": "vis-ensemble-1.2 trial", "fetched_at": fetched, "first_day": days[0], "last_day": days[-1],
            "models": sorted(by_model), "lead": "short (Open-Meteo past hours from successive runs)",
            "observations": verify.METHOD, "rules": rules, "points": per_point}


def _print_metar(doc):
    print(f"METAR thunderstorms, {doc['first_day']} to {doc['last_day']} ({doc['rule']})")
    print(f"{'station':8} {'reports':>7} {'resolved':>8} {'thunder':>7} {'strong':>6} {'CB only':>7}  longest episode")
    for r in doc["stations"]:
        longest = max((e[2] for e in r["episodes"]), default=None)
        print(f"{r['icao']:8} {r['reports']:7d} {r['days_resolved']:5d}/{r['days']:<2d} {len(r['days_thunder']):7d} "
              f"{len(r['days_strong']):6d} {len(r['days_cb_only']):7d}  "
              f"{'' if longest is None else str(longest) + ' min'}")


def _print_measure(doc):
    print(f"Trial {doc['first_day']} to {doc['last_day']} ({', '.join(doc['models'])}; lead {doc['lead']})")
    print(f"{'CAPE':>5} {'rain':>4} {'steps':>5} {'pt-days':>7} {'>=Y':>6} {'>=A':>6} {'res':>4} {'ev':>3} "
          f"{'Brier':>6} {'hit':>3} {'miss':>4} {'FA':>3}")
    for r in doc["rules"]:
        print(f"{r['cape_j_kg']:5.0f} {r['rain_mm_h']:4.1f} {r['steps']:5d} {r['point_days']:7d} "
              f"{r['share_yellow_or_more']:6.1%} {r['share_amber_or_more']:6.1%} {r['resolved']:4d} {r['events']:3d} "
              f"{'' if r['brier'] is None else format(r['brier'], '.3f'):>6} {r['hits_at_45']:3d} "
              f"{r['misses_at_45']:4d} {r['false_alarms_at_45']:3d}{'  <- forecast rule' if r['chosen'] else ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("metar")
    m.add_argument("--metar", default=str(ROOT / "history" / "metar"))
    m.add_argument("--first", default=None)
    m.add_argument("--last", default=None)
    m.add_argument("--out", default=str(ROOT / "out" / "tstorm_trial" / "metar.json"))
    f = sub.add_parser("fetch")
    f.add_argument("--past-days", type=int, default=10)
    f.add_argument("--out", default=str(ROOT / "out" / "tstorm_trial"))
    s = sub.add_parser("measure")
    s.add_argument("--answers", default=str(ROOT / "out" / "tstorm_trial"))
    s.add_argument("--metar", default=str(ROOT / "history" / "metar"))
    s.add_argument("--out", default=str(ROOT / "out" / "tstorm_trial" / "report.json"))
    a = ap.parse_args(argv)
    points = forecast.expand_points(forecast.load_zones())
    if a.cmd == "metar":
        today = datetime.now(timezone.utc)
        first = a.first or _day(today - timedelta(days=8))
        last = a.last or _day(today - timedelta(days=1))
        doc = metar_summary(a.metar, first, last, points)
        _print_metar(doc)
    elif a.cmd == "fetch":
        out = fetch(points, a.past_days, a.out)
        print(f"wrote {out}/<model>.json for {len(points)} points")
        return 0
    else:
        doc = measure(a.answers, a.metar, points)
        _print_measure(doc)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
