"""Build Vis's contract v2 feed (tic-feed/2, GeoJSON) from forecast.json.

    python tools/feed_v2.py [--in out/forecast.json] [--out site/data/feed.geojson]

Emits:
- 1 daily summary item (kind=observation, tier=routine)
- 1 72-hour operational weather forecast per conflict zone (kind=forecast, tier=warning when the 72-hour stoplight
  is Amber or Red with ensemble probability >= 0.45, else tier=routine), with ICD 203 likelihood term, confidence
  chip from tic_doctrine.confidence, competing hypotheses, and an objective resolution rule against the zone's ICAO
  METAR / SYNOP station.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
sys.path.insert(0, str(ROOT / "tools"))
from tic_doctrine import CONTRACT, __version__ as KIT_VERSION, confidence, contract, likelihood, registers, vocab  # noqa: E402
import verify  # noqa: E402  (the resolution rule states verify's own test)
import gefs  # noqa: E402  (the ceiling source's limits)

SITE = "https://vis.theintelligence.company"
METHOD = "vis-ensemble-1.1"
LANGUAGE_KEY = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/language-key.md"
DOCTRINE = ROOT / "doctrine"
CONTRACT_REV = "2.1"  # feed contract revision 2.1; last_change is filled by the kit's history step

SRC_GEFS = {
    "id": "open-meteo-gefs",
    "publisher": "Open-Meteo / NOAA NCEP",
    "title": "NOAA GEFS 0.25 deg ensemble (31 members)",
    "url": "https://open-meteo.com/en/docs/ensemble-api",
    "licence": "CC BY 4.0 (Open-Meteo); NOAA NCEP public domain",
    "grade": "A2",
    "independence_group": "noaa-ncep",
    "source_type": "model",
    "descriptor": "31-member global ensemble forecast (ncep_gefs025) at 0.25 deg, 3-hourly steps (served hourly, interpolated) out to 7 days.",
}
SRC_ECMWF = {
    "id": "open-meteo-ecmwf",
    "publisher": "Open-Meteo / ECMWF",
    "title": "ECMWF IFS 0.25 deg ensemble (51 members)",
    "url": "https://open-meteo.com/en/docs/ensemble-api",
    "licence": "CC BY 4.0 (Open-Meteo and ECMWF open data)",
    "grade": "A2",
    "independence_group": "ecmwf",
    "source_type": "model",
    "descriptor": "51-member global ensemble forecast (ecmwf_ifs025) at 0.25 deg, 3-hourly steps (served hourly, interpolated) out to 7 days.",
}
# The low ceiling trial reads NOAA's own GEFS files (tools/gefs.py), since Open-Meteo carries no cloud base.
SRC_CEILING = {
    "id": "noaa-gefs-aws",
    "publisher": "NOAA NCEP (AWS Open Data)",
    "title": "NOAA GEFS 0.25 deg cloud ceiling (31 members, trial)",
    "url": "https://registry.opendata.aws/noaa-gefs/",
    "licence": "NOAA NCEP, US public domain",
    "grade": "A2",
    "independence_group": "noaa-ncep",
    "source_type": "model",
    "descriptor": "HGT:cloud ceiling from the GEFS pgrb2sp25 files, 3-hourly, nearest 0.25 deg cell to each point.",
}
SRC_ASTRO = {
    "id": "astro-meeus",
    "publisher": "Vis, The Intelligence Company",
    "title": "Computed solar and lunar ephemeris (Meeus)",
    "url": SITE,
    "licence": "Computed by Vis (code GNU AGPL-3.0)",
    "grade": "A1",
    "independence_group": "ephemeris",
    "source_type": "derived",
    "descriptor": "Deterministic solar twilight, moonrise/moonset, lunar disc fraction and low-light dark minutes.",
}

COLOUR_RANK = {"green": 0, "yellow": 1, "amber": 2, "red": 3}


def _clamp_prob(p: float) -> float:
    return round(max(0.01, min(0.99, float(p))), 3)


def _ncf_for_hazard(hid: str) -> list[str]:
    rules = json.loads((DOCTRINE / "ncf_map.json").read_text(encoding="utf-8")).get("rules", [])
    for r in rules:
        if r.get("match", {}).get("hazard") == hid:
            return r.get("ncf", [])
    return []


_COUNT = {2: "two", 3: "three", 4: "four"}


def _steps(hdef: dict, day_str: str, day_from: str | None = None) -> str:
    """The persistence the forecast counts and tools/verify.py scores: `min_steps` consecutive 3-hourly model steps
    (00, 03 ... 21 UTC) in the UTC day, from the first scored step when the day's window opens after 00 UTC."""
    n = hdef.get("min_steps", 1)
    h = verify.STEP_H
    marks = f"00, {h:02d} ... {24 - h:02d} UTC"
    start = day_from[11:16] if day_from and len(day_from) >= 16 and day_from[11:16] != "00:00" else None
    when = f"from {start} on {day_str} UTC" if start else f"on {day_str} UTC"
    if n <= 1:
        return f"at any {h}-hourly step ({marks}) {when}"
    return f"on {_COUNT.get(n, str(n))} consecutive {h}-hourly steps ({marks}) {when}"


def ceiling_limits(forecast_doc: dict) -> str:
    """The ceiling source's two measured limits, with the points the second one hits in this build (as on the page:
    tools/build_site.py ceiling_limits_html)."""
    both, red = gefs.unreachable(forecast_doc)
    where = []
    if both:
        where.append("Amber and Red cannot be reached at " + ", ".join(z.get("icao") for z in both))
    if red:
        where.append("Red cannot be reached at " + ", ".join(z.get("icao") for z in red))
    return (" Trial, scored by Vis against METAR ceilings (lowest BKN, OVC or VV layer). Two known limits: GEFS v12 "
            "sets this field only where surface-based CAPE is at least about 0.5 J/kg, so stratus and fog in stable "
            "air read as no ceiling; and the field cannot sit lower than about 17 m above the model's own ground, so "
            "where that ground is well above the station a threshold can be out of reach"
            + (f" ({'; '.join(where)})." if where else "."))


def _ft_rule(thr_m: float) -> str:
    """What a METAR ceiling (100 ft steps) must read to fall at or below `thr_m` metres."""
    ft = int(thr_m / 0.3048 // 100) * 100
    return f"{ft:,} ft or lower"


def _metar_element(hdef: dict, level: str) -> str:
    """What the station's report must show at each step, as tools/verify.py reads it."""
    thr, hid = hdef[level], hdef["id"]
    if hid == "ceiling":
        return (f"a ceiling (lowest broken, overcast or obscured layer) at or below {thr:g} m "
                f"({_ft_rule(thr)} in the report's 100 ft steps) above the aerodrome")
    op = ">=" if hdef["dir"] == "above" else "<="
    if hid == "wind":
        return f"a mean wind or gust {op} {thr:g} km/h (about {round(thr / 1.852)} kt)"
    if hid == "vis":
        return f"visibility {op} {thr:g} m"
    if hid in ("heat", "cold"):
        return f"a temperature {op} {thr:g} C"
    return f"{hdef['name'].lower()} {op} {thr:g} {hdef['unit']}"


def _resolution_rule(station: str, icao: str, hdef: dict, level: str, day_str: str,
                     day_from: str | None = None) -> str:
    """The rule a forecast is resolved by: for the hazards tools/verify.py scores, exactly its test (the report nearest
    each 3-hourly step, the persistence the forecast used, and when the answer is No or unresolved); for the others,
    the event in station terms and why Vis does not score it from METARs."""
    thr = hdef[level]
    steps = _steps(hdef, day_str, day_from)
    if hdef["id"] in verify.SCORED:
        return (f"Yes if the {icao} ({station}) METAR nearest each step, within {verify.NEAR_MIN} minutes, reports "
                f"{_metar_element(hdef, level)} {steps}. No if reports cover at least "
                f"{round(verify.MIN_COVERAGE * 100)}% of the steps and no hit sits beside a missing report; "
                f"otherwise unresolved.")
    not_scored = verify.NOT_RESOLVABLE.get(hdef["id"])
    tail = f" Vis does not score this from METARs. {not_scored[0].upper()}{not_scored[1:]}." if not_scored else ""
    if hdef["id"] == "lowcloud":
        # METARs give cloud in oktas, not percent: 80% is about broken (5-7 oktas), 95% about overcast; 3 km is about
        # 10,000 ft.
        layers = "overcast (OVC)" if level == "red" else "broken or overcast (BKN or OVC)"
        return f"Yes if METARs from {icao} ({station}) report {layers} cloud below 10,000 ft {steps}.{tail}"
    if hdef["agg"] == "sum":
        return (f"Yes if ICAO station {icao} ({station}) records {hdef['name'].lower()} >= {thr:g} {hdef['unit']} "
                f"on {day_str} UTC.{tail}")
    if hdef["agg"] == "joint":
        w = hdef["with"]
        return (f"Yes if ICAO station {icao} ({station}) reports 2 m temperature <= {thr:g} C with precipitation "
                f">= {w[level]:g} mm/h at the same step, {steps}.{tail}")
    return f"Yes if ICAO station {icao} ({station}) reports {_metar_element(hdef, level)} {steps}.{tail}"


def _whole_utc(ts: str) -> str:
    """'2026-11-24T00:00Z' -> '2026-11-24T00:00:00Z'."""
    return ts[:-1] + ":00Z" if len(ts) == 17 and ts.endswith("Z") else ts


def forecast_period(days: list[dict]) -> dict:
    """Contract 2.1 event times of a forecast: the period it covers, from the first scored hour to the last day's end."""
    first = days[0]
    start = _whole_utc(first["from"]) if first.get("from") else f"{first['day']}T00:00:00Z"
    return {"event_start": start, "event_end": f"{days[-1]['day']}T23:59:59Z"}


def feed_health(forecast_doc: dict, generated: str) -> dict:
    """Contract 2.1 health: one entry per source lane. A model left out of this run is listed as not read."""
    models = {m["id"]: m for m in forecast_doc.get("models", [])}
    out = {}
    for mid, src in (("ncep_gefs025", SRC_GEFS), ("ecmwf_ifs025", SRC_ECMWF)):
        m = models.get(mid, {"used": True})
        used = m.get("used", True)
        run = m.get("run_time")
        out[src["id"]] = {
            "state": "fresh" if used else "stale",
            "checked_at": generated,
            "last_ok": generated if used else None,
            "last_error": None if used else f"left out of this run; newest run {run or 'unknown'}",
            "failures": 0 if used else 1,
            "data_as_of": run,
            "detail": f"{mid}: newest run {run or 'unknown'}" + ("" if used else ", not used"),
        }
    c = forecast_doc.get("ceiling")
    if c:
        ok = c.get("state") == "used"
        out[SRC_CEILING["id"]] = {
            "state": "fresh" if ok else "stale", "checked_at": generated, "last_ok": generated if ok else None,
            "last_error": None if ok else (c.get("why") or "not read"), "failures": 0 if ok else 1,
            "data_as_of": c.get("run_time"),
            "detail": f"GEFS cloud ceiling (trial): run {c.get('run_time') or 'none'}" + ("" if ok else ", not used"),
        }
    out[SRC_ASTRO["id"]] = {"state": "up", "checked_at": generated, "last_ok": generated, "last_error": None,
                            "failures": 0, "data_as_of": generated, "detail": "computed on this run"}
    return out


def build_feed(forecast_doc: dict) -> dict:
    generated = forecast_doc["generated_at"]
    first_days = forecast_doc["zones"][0].get("days") if forecast_doc.get("zones") else None
    day0 = first_days[0]["day"] if first_days else generated[:10]
    hz_by_id = {h["id"]: h for h in forecast_doc.get("hazards", [])}
    models = {m["id"]: m for m in forecast_doc.get("models", [])}
    used = [mid for mid in ("ncep_gefs025", "ecmwf_ifs025") if models.get(mid, {"used": True}).get("used", True)]
    src_by_model = {"ncep_gefs025": SRC_GEFS, "ecmwf_ifs025": SRC_ECMWF}
    sources_all = []
    for mid in used:
        src = {**src_by_model[mid], "accessed_at": generated}
        run = models.get(mid, {}).get("run_time")
        if run:
            src["descriptor"] = src["descriptor"] + f" Run used: {run}."
        sources_all.append(src)
    ceiling = forecast_doc.get("ceiling") or {}
    if ceiling.get("state") == "used":
        src = {**SRC_CEILING, "accessed_at": generated}
        src["descriptor"] = src["descriptor"] + f" Run used: {ceiling.get('run_time')}." + ceiling_limits(forecast_doc)
        sources_all.append(src)
    sources_all.append({**SRC_ASTRO, "accessed_at": generated})
    members_used = forecast_doc.get("members_used") or 82
    left_out = [m for m in forecast_doc.get("models", []) if not m.get("used", True)]

    feats = []
    zones_amber_or_red = set()
    all_zones = set()

    for z in forecast_doc.get("zones", []):
        zone_id = z.get("zone_id", z["id"])
        alternate = z.get("role") == "alternate"
        all_zones.add(zone_id)
        days_72h = z.get("days", [])[:3]
        if not days_72h:
            continue
        best_day = days_72h[0]
        best_hid = "wind"
        best_hval = None
        best_score = (-1, -1.0)
        for d in days_72h:
            for hid, hval in d.get("hazards", {}).items():
                score = (COLOUR_RANK.get(hval["colour"], 0), max(hval["p_amber"], hval["p_red"]))
                if score > best_score:
                    best_score = score
                    best_day = d
                    best_hid = hid
                    best_hval = hval

        if best_hval is None:
            continue

        col_72h = max((d["colour"] for d in days_72h), key=lambda c: COLOUR_RANK.get(c, 0))
        if col_72h in ("amber", "red"):
            zones_amber_or_red.add(zone_id)

        level = "red" if best_hval["colour"] == "red" else "amber"
        raw_p = best_hval["p_red"] if level == "red" else best_hval["p_amber"]
        prob = _clamp_prob(raw_p)
        term = likelihood.term_for(prob)
        # Independent lines are the models that supply the driving hazard, not every model in the run (review
        # 15 Nov 2026: visibility comes from GEFS alone and low cloud from ECMWF alone, yet both counted two).
        models_n = best_hval.get("models", best_day.get("models_present", 2))
        cov = round(min(1.0, models_n / 2.0), 3)
        conf_fields = confidence.fields(confidence.Evidence(
            independent_lines=max(1, models_n),
            contradicted=False,
            coverage_fraction=cov,
            grades=["A2", "A1"],
            alternatives_rejected=2,
            method_scored=False,
            assumption_status="holds",
            ease_of_faking="low",
            horizon_days=3.0,
        ))
        hdef = hz_by_id[best_hid]
        peak_day = best_day["day"]
        # The item forecasts its whole 72-hour window (event_start to event_end), so its information holds until the
        # window ends; the next build replaces it before then. The probability, the resolution rule and known_at stay
        # on the driving day.
        period = forecast_period(days_72h)
        valid_until = period["event_end"]
        known_at = f"{peak_day}T23:59:59Z"
        resolve_dt = (datetime.strptime(peak_day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT12:00:00Z")
        tonight = days_72h[0].get("night") or {}
        sun0 = days_72h[0].get("sun") or {}
        dark_h = (tonight.get("dark_low_light_min") or 0) / 60.0

        # Amber or Red goes to COP as a warning only when two models supply the hazard and the point's grid cell was
        # checked against the station (16 Nov 2026, from the 15 Nov review: 9 of 18 archived warnings were
        # GEFS-only visibility, and Caracas's came from a cell 936 m up in the coastal range).
        grid = z.get("grid") or {}
        held = []
        if col_72h in ("amber", "red"):
            if models_n < 2:
                held.append("only one model supplies this hazard")
            if grid.get("ok") is False:
                held.append(f"the model's grid cell sits {abs(grid['diff_m']):.0f} m "
                            f"{'above' if grid['diff_m'] > 0 else 'below'} the station")
            elif grid.get("ok") is not True:
                held.append("the grid cell could not be checked against the station")
        tier = "warning" if col_72h in ("amber", "red") and not held else "routine"
        ind_id = "VS-IND-2" if col_72h == "red" else ("VS-IND-1" if col_72h == "amber" else None)
        station = z.get("station", z["name"])
        icao = z.get("icao", "ZZZZ")

        where = f"{station} {icao}, alternate point" if alternate else f"{station} {icao}"
        title = (f"{z['name']} ({where}): {col_72h.upper()} 72h weather window — "
                 f"{hdef['name'].lower()} {term['term'].lower()} ({round(prob * 100)}%) on {peak_day}; "
                 f"tonight {dark_h:.1f} h low-light dark")
        summary = (f"Across {best_hval['members']} ensemble members, {hdef['name'].lower()} at {station} ({icao}) is "
                   f"{term['term'].lower()} ({term['band_label']}) to reach the {level} threshold "
                   f"({hdef[level]} {hdef['unit']}) on {peak_day} UTC (72-hour stoplight: {col_72h.title()}). "
                   f"Tonight's lunar illumination is {round((tonight.get('moon_lit') or 0) * 100)}% with "
                   f"{dark_h:.1f} hours of nautical darkness under low moon.")
        per = best_hval.get("by_model") or {}
        if per:
            names = {"ncep_gefs025": "GEFS", "ecmwf_ifs025": "ECMWF"}
            hits = "red_hits" if level == "red" else "amber_hits"
            summary += (" By model: " + ", ".join(f"{names.get(k, k)} {v[hits]} of {v['members']}" for k, v in per.items())
                        + (", weighted equally." if len(per) > 1 else "."))
        if held:
            summary += f" Not sent to COP as a warning: {'; '.join(held)}."

        ncf = _ncf_for_hazard(best_hid) if col_72h in ("amber", "red") else []
        props = {
            "contract": CONTRACT,
            "id": f"vis:{day0}:zone:{z['id']}",
            "subject_id": f"vis:zone:{z['id']}",
            **period,
            "program": "vis",
            "title": title,
            "summary": summary,
            "kind": "forecast",
            "tier": tier,
            "status": "actual",
            "msg_type": "new",
            "human_review": "none",
            "us_person_data": "none",
            "status_class": "assessed",
            "product_category": "estimative",
            "data_as_of": generated,
            "published_at": generated,
            "valid_until": valid_until,
            "countries": z.get("countries", []),
            "precision_class": "point",
            "probability": prob,
            "likelihood_term": term["id"],
            **conf_fields,
            "change_from_prior": "new",
            "warning_problem_id": "VS-WP-1",
            "requirement_ids": ["VS-PIR-1", "VS-PIR-2", "VS-EEI-1"],
            "method_version": METHOD,
            "coverage_fraction": cov,
            "ie_aspect": ["physical"],
            "pmesii_pt": ["military", "infrastructure", "physical_environment"],
            "ascope": ["areas", "capabilities"],
            "alternatives": [
                {
                    "hypothesis": "Weather remains below the operational threshold or passes in brief (< 3 h) local showers",
                    "tell_apart": f"Subsequent 00Z/12Z ensemble runs drop below 20% member agreement or METAR at {icao} stays under {hdef[level]} {hdef['unit']}",
                },
                {
                    "hypothesis": "Single-model bias between NOAA GEFS and ECMWF IFS inflates the tail probability",
                    "tell_apart": "Spread between GEFS (31) and ECMWF (51) member fractions exceeds 35 percentage points",
                },
            ],
            "resolution": {
                "rule": _resolution_rule(station, icao, hdef, level, peak_day, best_day.get("from")),
                "source": f"NOAA Aviation Weather Center METAR / SYNOP archive for {icao} (https://aviationweather.gov/data/metar/?ids={icao})",
                "known_at": known_at,
                "resolve_by": resolve_dt,
            },
            "sources": sources_all,
            "measures": {
                "icao": icao,
                "station": station,
                "point_role": z.get("role", "reference"),
                "watchfloor_zone": zone_id,
                "stoplight_72h": col_72h,
                "driver_hazard": best_hid,
                "driver_day": peak_day,
                "driver_day_from": best_day.get("from"),
                "driver_day_hours": best_day.get("hours", 24),
                "models_for_hazard": models_n,
                "ensemble_members": best_hval["members"],
                "p_amber": best_hval["p_amber"],
                "p_red": best_hval["p_red"],
                "median": best_hval["median"],
                "p90": best_hval["p90"],
                "moon_lit": tonight.get("moon_lit"),
                "dark_low_light_min": tonight.get("dark_low_light_min"),
                # Sun and moon for the point's local solar day 0 (Vis's own computation, like the moon fields), the
                # station elevation from the grid check, and where the full location detail lives (22 Nov 2026):
                # data/locations/<id>.json, schema vis-location/1 (docs/location-data.md).
                "moon_phase": tonight.get("moon_phase"),
                "sunrise": sun0.get("sunrise"),
                "sunset": sun0.get("sunset"),
                "day_length_min": sun0.get("day_length_min"),
                "station_elevation_m": (z.get("grid") or {}).get("station_elevation_m"),
                "detail_url": f"{SITE}/data/locations/{z['id']}.json",
                "detail_page": f"{SITE}/#loc-{z['id']}",
            },
            "visual": {"type": "table", "url": f"{SITE}/#zone-{z['id']}"},
            "link": f"{SITE}/#zone-{z['id']}",
        }
        if ind_id:
            props.update({
                "indicator_id": ind_id,
                "hypothesis_id": "VS-WP-1-H1",
                "direction": "supports",
                "indicator_state": "present",
            })
        if ncf:
            props["ncf"] = ncf
            props["sector"] = vocab.derive_sectors(ncf)
        geom = {"type": "Point", "coordinates": [z["lon"], z["lat"]]}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})

    head_props = {
        "contract": CONTRACT,
        "id": f"vis:{day0}:summary",
        "subject_id": "vis:summary:daily",
        "event_start": generated,
        "program": "vis",
        "title": (f"Vis, {day0}: {len(zones_amber_or_red)} of {len(all_zones)} conflict zones with Amber or Red "
                  f"operational weather in the next 72 hours at one or more of their {len(feats)} forecast points "
                  f"({members_used}-member {' + '.join('GEFS' if m == 'ncep_gefs025' else 'ECMWF' for m in used)} "
                  f"ensemble"
                  + "".join(f"; {m['id']} left out, its newest run {m.get('run_time') or 'unknown'} was "
                            f"{m.get('age_hours')} h old" for m in left_out)
                  + ")"),
        "kind": "observation",
        "tier": "routine",
        "status": "actual",
        "msg_type": "new",
        "human_review": "none",
        "us_person_data": "none",
        "status_class": "reported",
        "product_category": "current",
        "data_as_of": generated,
        "published_at": generated,
        "countries": [],
        "precision_class": "none",
        "requirement_ids": ["VS-PIR-1", "VS-PIR-2"],
        "method_version": METHOD,
        "coverage_fraction": 1.0,
        "ie_aspect": ["physical"],
        "pmesii_pt": ["military", "physical_environment"],
        "ascope": ["areas"],
        "sources": sources_all,
        "measures": {
            "members_used": members_used,
            "model_runs": [{k: m.get(k) for k in ("id", "run_time", "age_hours", "state", "used")}
                           for m in forecast_doc.get("models", [])],
            "ceiling_run": {k: (forecast_doc.get("ceiling") or {}).get(k) for k in ("source", "run_time", "age_hours", "state")},
        },
        "visual": {"type": "table", "url": SITE},
        "link": SITE,
    }
    feats.insert(0, {"type": "Feature", "geometry": None, "properties": head_props})

    regs = registers.ids(DOCTRINE)
    kept, dropped = [], []
    for f in feats:
        errs = [f"{std}: {m}" for std, sev, m in contract.check(f, regs) if sev == "error"]
        if errs:
            dropped.append({"id": f["properties"].get("id"), "errors": errs})
        else:
            kept.append(f)

    return {
        "type": "FeatureCollection",
        "contract": CONTRACT,
        "contract_rev": CONTRACT_REV,
        "kit": KIT_VERSION,
        "program": "vis",
        "generated_at": generated,
        "health": feed_health(forecast_doc, generated),
        "licence": ("Vis code GNU AGPL-3.0; ensemble data from Open-Meteo (CC BY 4.0), carrying NOAA NCEP GEFS "
                    "(US public domain) and ECMWF open data (CC BY 4.0)"),
        "attribution": "Vis, The Intelligence Company. Ensemble weather: Open-Meteo (NOAA GEFS + ECMWF IFS).",
        "language_key": LANGUAGE_KEY,
        "dropped": dropped,
        "features": kept,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inp", default=str(ROOT / "out" / "forecast.json"))
    ap.add_argument("--out", default=str(ROOT / "site" / "data" / "feed.geojson"))
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    fc = build_feed(doc)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(fc['features'])} features ({len(fc['dropped'])} dropped)")
    return 1 if fc["dropped"] else 0


if __name__ == "__main__":
    sys.exit(main())
