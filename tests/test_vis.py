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

    def test_astro_night_returns_twilight_and_dark_minutes(self):
        n = astro.night(50.345, 30.895, datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertIn("civil_dusk", n)
        self.assertIn("civil_dawn", n)
        self.assertTrue(0.0 <= n["moon_lit"] <= 1.0)
        self.assertTrue(0 <= n["dark_low_light_min"] <= n["dark_min"])


if __name__ == "__main__":
    unittest.main()
