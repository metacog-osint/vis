"""Offline unit tests for Vis (Operational weather & illumination): astro, forecast scoring, doctrine registers,
contract v2 feed, and static site build."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))

import astro  # noqa: E402
import build_site  # noqa: E402
import feed_v2  # noqa: E402
import forecast  # noqa: E402
from tic_doctrine import contract, registers  # noqa: E402


def _synthetic_model(hours=72, members=4, gust=40.0, cloud=50.0, vis=10000.0, rain=0.0, temp=18.0, snow=0.0):
    t0 = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
    times = [(t0.replace(day=1 + h // 24, hour=h % 24)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    hourly = {"time": times}
    for m in range(members):
        sfx = f"_member{m + 1:02d}"
        hourly["wind_gusts_10m" + sfx] = [gust] * hours
        hourly["cloud_cover_low" + sfx] = [cloud] * hours
        hourly["visibility" + sfx] = [vis] * hours
        hourly["precipitation" + sfx] = [rain] * hours
        hourly["temperature_2m" + sfx] = [temp] * hours
        hourly["snowfall" + sfx] = [snow] * hours
    return {"hourly": hourly}


class VisTests(unittest.TestCase):
    def test_doctrine_registers_pass(self):
        self.assertEqual(registers.check(ROOT / "doctrine"), [])

    def test_points_catalog_has_icao_stations_for_all_16_zones(self):
        zones = forecast.load_zones()
        self.assertEqual(len(zones), 16)
        for z in zones:
            self.assertRegex(z["icao"], r"^[A-Z]{4}$")
            self.assertTrue(z["countries"] and all(len(c) == 2 for c in z["countries"]))

    def test_duration_rule_filters_single_hour_spike_and_flags_sustained_hazard(self):
        # 1 hour of 75 km/h gusts does NOT trigger Amber (needs >= 3 hours)
        m_spike = _synthetic_model(hours=24, members=4, gust=30.0)
        for m in range(1, 5):
            m_spike["hourly"][f"wind_gusts_10m_member{m:02d}"][5] = 75.0
        d_spike = forecast.score_zone_days([m_spike, m_spike], days=1)[0]
        self.assertEqual(d_spike["hazards"]["wind"]["amber_hits"], 0)
        self.assertEqual(d_spike["colour"], "green")

        # 3 hours of 75 km/h gusts across all members triggers Amber
        m_sust = _synthetic_model(hours=24, members=4, gust=30.0)
        for m in range(1, 5):
            for h in (5, 6, 7):
                m_sust["hourly"][f"wind_gusts_10m_member{m:02d}"][h] = 75.0
        d_sust = forecast.score_zone_days([m_sust, m_sust], days=1)[0]
        self.assertEqual(d_sust["hazards"]["wind"]["p_amber"], 1.0)
        self.assertEqual(d_sust["colour"], "amber")

    def test_feed_v2_and_site_build_pass_kit_linter(self):
        zones = forecast.load_zones()[:3]
        # Give zone 0 sustained strong wind (Amber) and zones 1-2 quiet weather (Green)
        m_amber = _synthetic_model(hours=72, members=4, gust=70.0, cloud=85.0)
        m_green = _synthetic_model(hours=72, members=4, gust=25.0, cloud=20.0)
        by_model = {
            "ncep_gefs025": [m_amber, m_green, m_green],
            "ecmwf_ifs025": [m_amber, m_green, m_green],
        }
        doc = forecast.build_forecast_doc(zones, by_model, days=3, generated_at="2026-10-31T18:00:00Z")
        fc = feed_v2.build_feed(doc)
        self.assertEqual(fc["dropped"], [])
        self.assertEqual(fc["program"], "vis")
        self.assertEqual(len(fc["features"]), 4)  # 1 summary + 3 zone forecasts
        regs = registers.ids(ROOT / "doctrine")
        for f in fc["features"]:
            errs = [p for p in contract.check(f, regs) if p[1] == "error"]
            self.assertEqual(errs, [], f["properties"]["id"])
        z0 = fc["features"][1]["properties"]
        self.assertEqual((z0["kind"], z0["tier"], z0["confidence"]), ("forecast", "warning", "moderate"))
        self.assertIn("UKBB", z0["resolution"]["rule"])

        files = build_site.build(doc)
        self.assertIn("index.html", files)
        self.assertIn("Vis", files["index.html"])

    def test_low_cloud_cover_shows_at_most_yellow(self):
        m = _synthetic_model(hours=24, members=4, cloud=100.0)
        d = forecast.score_zone_days([m, m], days=1)[0]
        self.assertEqual(d["hazards"]["lowcloud"]["p_red"], 1.0)
        self.assertEqual(d["hazards"]["lowcloud"]["colour"], "yellow")
        self.assertEqual(d["colour"], "yellow")
        # Another hazard can still make the day Red
        m["hourly"] = {k: ([95.0] * 24 if k.startswith("wind_gusts_10m") else v) for k, v in m["hourly"].items()}
        self.assertEqual(forecast.score_zone_days([m, m], days=1)[0]["colour"], "red")

    def test_alternates_are_forecast_as_their_own_rows(self):
        zones = forecast.load_zones()
        rows = forecast.expand_points(zones)
        self.assertEqual(len(rows), len(zones) + sum(len(z.get("alternates", [])) for z in zones))
        self.assertEqual(len(rows), 31)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        zone_ids = {z["id"] for z in zones}
        for r in rows:
            self.assertIn(r["zone_id"], zone_ids)
            self.assertIn(r["role"], ("reference", "alternate"))
            self.assertRegex(r["icao"], r"^[A-Z]{4}$")
            self.assertTrue(r["countries"] and all(len(c) == 2 for c in r["countries"]))
            self.assertNotIn("alternates", r)
        odesa = next(r for r in rows if r["icao"] == "UKOO")
        self.assertEqual((odesa["id"], odesa["role"], odesa["countries"]), ("01_Ukraine_Conflict-UKOO", "alternate", ["UA"]))

    def test_feed_counts_zones_not_points(self):
        rows = forecast.expand_points(forecast.load_zones()[:1])  # Kyiv Boryspil and Odesa
        m_amber = _synthetic_model(hours=72, members=4, gust=70.0)
        by_model = {"ncep_gefs025": [m_amber, m_amber], "ecmwf_ifs025": [m_amber, m_amber]}
        doc = forecast.build_forecast_doc(rows, by_model, days=3, generated_at="2026-10-31T18:00:00Z")
        fc = feed_v2.build_feed(doc)
        self.assertEqual(fc["dropped"], [])
        head = fc["features"][0]["properties"]
        self.assertIn("1 of 1 conflict zones", head["title"])
        self.assertIn("2 forecast points", head["title"])
        ids = [f["properties"]["id"] for f in fc["features"]]
        self.assertEqual(len(set(ids)), len(ids))
        alt = fc["features"][2]["properties"]
        self.assertIn("alternate point", alt["title"])
        self.assertEqual(alt["measures"]["watchfloor_zone"], "01_Ukraine_Conflict")
        self.assertIn("AGPL-3.0", fc["licence"])

    def test_low_cloud_resolution_rule_uses_metar_cloud_layers(self):
        h = next(h for h in forecast.HAZARDS if h["id"] == "lowcloud")
        self.assertIn("BKN or OVC", feed_v2._resolution_rule("Kyiv Boryspil", "UKBB", h, "amber", "2026-11-09"))
        self.assertIn("(OVC)", feed_v2._resolution_rule("Kyiv Boryspil", "UKBB", h, "red", "2026-11-09"))

    def test_fetch_waits_and_retries_when_open_meteo_refuses(self):
        import io
        import urllib.error
        calls, waits = [], []

        def fake_urlopen(req, timeout=60):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}"))
            return io.BytesIO(b'[{"hourly": {"time": []}}]')

        real = forecast.urllib.request.urlopen
        forecast.urllib.request.urlopen = fake_urlopen
        try:
            out = forecast.fetch_model([{"lat": 1.0, "lon": 2.0}], "ecmwf_ifs025", 7, wait=waits.append)
        finally:
            forecast.urllib.request.urlopen = real
        self.assertEqual((len(calls), waits, out), (2, [65], [{"hourly": {"time": []}}]))

    def test_stale_model_run_is_judged_from_open_meteo_metadata(self):
        import io
        now = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)

        def opener_for(init_ts):
            def fake(req, timeout=30):
                self.assertIn("ecmwf_ifs025_ensemble/static/meta.json", req.full_url)
                return io.BytesIO(json.dumps({"last_run_initialisation_time": init_ts,
                                              "last_run_availability_time": init_ts + 9 * 3600}).encode())
            return fake

        old = int(datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp())  # 66 h before `now`
        r = forecast.model_run("ecmwf_ifs025", now, opener_for(old))
        self.assertEqual((r["state"], r["age_hours"], r["run_time"]), ("stale", 66.0, "2026-11-13T12:00:00Z"))
        fresh = int(datetime(2026, 11, 15, 18, tzinfo=timezone.utc).timestamp())
        self.assertEqual(forecast.model_run("ecmwf_ifs025", now, opener_for(fresh))["state"], "used")

        def broken(req, timeout=30):
            raise OSError("down")
        self.assertEqual(forecast.model_run("ecmwf_ifs025", now, broken)["state"], "unknown")

    def test_left_out_model_is_named_and_not_cited(self):
        zones = forecast.load_zones()[:1]
        m = _synthetic_model(hours=72, members=4, gust=70.0)
        runs = {"ncep_gefs025": {"run_time": "2026-10-31T12:00:00Z", "age_hours": 6.0, "state": "used"},
                "ecmwf_ifs025": {"run_time": "2026-10-29T12:00:00Z", "age_hours": 54.0, "state": "stale"}}
        doc = forecast.build_forecast_doc(zones, {"ncep_gefs025": [m]}, days=3,
                                          generated_at="2026-10-31T18:00:00Z", runs=runs)
        self.assertEqual(doc["members_used"], 31)
        self.assertEqual([x["used"] for x in doc["models"]], [True, False])
        fc = feed_v2.build_feed(doc)
        self.assertEqual(fc["dropped"], [])
        head = fc["features"][0]["properties"]
        self.assertIn("31-member GEFS ensemble; ecmwf_ifs025 left out", head["title"])
        self.assertNotIn("open-meteo-ecmwf", [s["id"] for s in head["sources"]])
        self.assertIn("ecmwf_ifs025", [r["id"] for r in head["measures"]["model_runs"]])

    def test_first_day_counts_only_hours_still_ahead(self):
        # Gusts of 70 km/h for the first 12 hours of 1 Nov only. Issued at 18:00 UTC that day, those hours are past.
        m = _synthetic_model(hours=72, members=4, gust=25.0)
        for k in m["hourly"]:
            if k.startswith("wind_gusts_10m"):
                m["hourly"][k] = [70.0] * 12 + [25.0] * 60
        before = forecast.score_zone_days([m, m], days=3)[0]
        self.assertEqual(before["colour"], "amber")
        now = datetime(2026, 11, 1, 18, 20, tzinfo=timezone.utc)
        after = forecast.score_zone_days([m, m], days=3, now=now)
        self.assertEqual((after[0]["day"], after[0]["from"], after[0]["hours"]), ("2026-11-01", "2026-11-01T18:00Z", 6))
        self.assertEqual(after[0]["colour"], "green")
        self.assertEqual((after[1]["hours"], len(after)), (24, 3))

    def test_confidence_counts_only_models_that_supply_the_hazard(self):
        # Visibility below 1 km in every member of one model, while the other model has no visibility series.
        zones = forecast.load_zones()[:1]
        gefs = _synthetic_model(hours=72, members=4, vis=500.0)
        ecmwf = _synthetic_model(hours=72, members=4)
        ecmwf["hourly"] = {k: v for k, v in ecmwf["hourly"].items() if not k.startswith("visibility")}
        doc = forecast.build_forecast_doc(zones, {"ncep_gefs025": [gefs], "ecmwf_ifs025": [ecmwf]}, days=3,
                                          generated_at="2026-10-31T18:00:00Z")
        self.assertEqual(doc["zones"][0]["days"][0]["hazards"]["vis"]["models"], 1)
        z0 = feed_v2.build_feed(doc)["features"][1]["properties"]
        self.assertEqual((z0["measures"]["driver_hazard"], z0["measures"]["models_for_hazard"]), ("vis", 1))

    def test_page_carries_the_house_layer(self):
        html = build_site.INDEX_HTML
        for needle in ('id="about"', 'id="sources"', "https://github.com/metacog-osint/vis", "GNU AGPL-3.0",
                       "Not an official forecast", 'id="vis-stale"', 'id="vis-asof"', 'data-current="vis"'):
            self.assertIn(needle, html)
        self.assertNotIn("in <b>Verdict</b>", html)  # scoring against METARs is not built yet

    def test_astro_night_returns_twilight_and_dark_minutes(self):
        n = astro.night(50.345, 30.895, datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertIn("civil_dusk", n)
        self.assertIn("civil_dawn", n)
        self.assertTrue(0.0 <= n["moon_lit"] <= 1.0)
        self.assertTrue(0 <= n["dark_low_light_min"] <= n["dark_min"])


if __name__ == "__main__":
    unittest.main()
