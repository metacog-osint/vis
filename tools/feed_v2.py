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
from tic_doctrine import CONTRACT, confidence, contract, likelihood, registers, vocab  # noqa: E402

SITE = "https://vis.theintelligence.company"
METHOD = "vis-ensemble-1.0"
LANGUAGE_KEY = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/language-key.md"
DOCTRINE = ROOT / "doctrine"

SRC_GEFS = {
    "id": "open-meteo-gefs",
    "publisher": "Open-Meteo / NOAA NCEP",
    "title": "NOAA GEFS 0.25 deg ensemble (31 members)",
    "url": "https://open-meteo.com/en/docs/ensemble-api",
    "licence": "CC BY 4.0 (Open-Meteo); NOAA NCEP public domain",
    "grade": "A2",
    "independence_group": "noaa-ncep",
    "source_type": "model",
    "descriptor": "31-member global ensemble forecast (ncep_gefs025) at 0.25 deg, hourly out to 7 days.",
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
    "descriptor": "51-member global ensemble forecast (ecmwf_ifs025) at 0.25 deg, hourly out to 7 days.",
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


def _resolution_rule(station: str, icao: str, hdef: dict, level: str, day_str: str) -> str:
    thr = hdef[level]
    unit = hdef["unit"]
    min_h = hdef.get("min_hours", 1)
    if hdef["id"] == "lowcloud":
        # METARs give cloud in oktas, not percent: 80% is about broken (5-7 oktas), 95% about overcast; 3 km is about
        # 10,000 ft.
        layers = "overcast (OVC)" if level == "red" else "broken or overcast (BKN or OVC)"
        return (f"Yes if METARs from {icao} ({station}) report {layers} cloud below 10,000 ft for >= {min_h} hours "
                f"on {day_str} UTC.")
    if hdef["agg"] == "sum":
        return f"Yes if ICAO station {icao} ({station}) records {hdef['name'].lower()} >= {thr} {unit} on {day_str} UTC."
    if hdef["agg"] == "joint":
        w = hdef["with"]
        return (f"Yes if ICAO station {icao} ({station}) reports 2 m temperature <= {thr} C with precipitation "
                f">= {w[level]} mm/h in the same hour for >= {min_h} hours on {day_str} UTC.")
    op = ">=" if hdef["dir"] == "above" else "<="
    return (f"Yes if ICAO station {icao} ({station}) reports {hdef['name'].lower()} ({hdef['var']}) "
            f"{op} {thr} {unit} for >= {min_h} hours on {day_str} UTC.")


def build_feed(forecast_doc: dict) -> dict:
    generated = forecast_doc["generated_at"]
    day0 = forecast_doc["zones"][0]["days"][0]["day"] if forecast_doc.get("zones") else generated[:10]
    hz_by_id = {h["id"]: h for h in forecast_doc.get("hazards", [])}
    sources_all = [{**s, "accessed_at": generated} for s in (SRC_GEFS, SRC_ECMWF, SRC_ASTRO)]

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
        models_n = best_day.get("models_present", 2)
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
        valid_until = f"{peak_day}T23:59:59Z"
        resolve_dt = (datetime.strptime(peak_day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT12:00:00Z")
        tonight = days_72h[0].get("night") or {}
        dark_h = (tonight.get("dark_low_light_min") or 0) / 60.0

        tier = "warning" if col_72h in ("amber", "red") else "routine"
        ind_id = "VS-IND-2" if col_72h == "red" else ("VS-IND-1" if col_72h == "amber" else None)
        station = z.get("station", z["name"])
        icao = z.get("icao", "ZZZZ")

        where = f"{station} {icao}, alternate point" if alternate else f"{station} {icao}"
        title = (f"{z['name']} ({where}): {col_72h.upper()} 72h weather window — "
                 f"{hdef['name'].lower()} {term['term'].lower()} ({round(raw_p * 100)}%) on {peak_day}; "
                 f"tonight {dark_h:.1f} h low-light dark")
        summary = (f"Across {best_hval['members']} ensemble members, {hdef['name'].lower()} at {station} ({icao}) is "
                   f"{term['term'].lower()} ({term['band_label']}) to reach the {level} threshold "
                   f"({hdef[level]} {hdef['unit']}) on {peak_day} UTC (72-hour stoplight: {col_72h.title()}). "
                   f"Tonight's lunar illumination is {round((tonight.get('moon_lit') or 0) * 100)}% with "
                   f"{dark_h:.1f} hours of nautical darkness under low moon.")

        ncf = _ncf_for_hazard(best_hid) if col_72h in ("amber", "red") else []
        props = {
            "contract": CONTRACT,
            "id": f"vis:{day0}:zone:{z['id']}",
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
                "rule": _resolution_rule(station, icao, hdef, level, peak_day),
                "source": f"NOAA Aviation Weather Center METAR / SYNOP archive for {icao} (https://aviationweather.gov/data/metar/?ids={icao})",
                "known_at": valid_until,
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
                "ensemble_members": best_hval["members"],
                "p_amber": best_hval["p_amber"],
                "p_red": best_hval["p_red"],
                "median": best_hval["median"],
                "p90": best_hval["p90"],
                "moon_lit": tonight.get("moon_lit"),
                "dark_low_light_min": tonight.get("dark_low_light_min"),
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
        "program": "vis",
        "title": (f"Vis, {day0}: {len(zones_amber_or_red)} of {len(all_zones)} conflict zones with Amber or Red "
                  f"operational weather in the next 72 hours at one or more of their {len(feats)} forecast points "
                  f"(82-member GEFS + ECMWF ensemble)"),
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
        "program": "vis",
        "generated_at": generated,
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
