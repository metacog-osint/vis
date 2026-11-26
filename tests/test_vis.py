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
import archive  # noqa: E402
import build_site  # noqa: E402
import feed_v2  # noqa: E402
import forecast  # noqa: E402
import metar  # noqa: E402
import verify  # noqa: E402
import gfs  # noqa: E402
import wxmap  # noqa: E402
import wxobs  # noqa: E402
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
        # 1 hour of 75 km/h gusts does NOT trigger Amber (needs two consecutive 3-hourly model steps)
        m_spike = _synthetic_model(hours=24, members=4, gust=30.0)
        for m in range(1, 5):
            m_spike["hourly"][f"wind_gusts_10m_member{m:02d}"][5] = 75.0
        d_spike = forecast.score_zone_days([m_spike, m_spike], days=1)[0]
        self.assertEqual(d_spike["hazards"]["wind"]["amber_hits"], 0)
        self.assertEqual(d_spike["colour"], "green")

        # 3 interpolated hours around one step (05-07, one step at 06) is a single step: not Amber
        m_one = _synthetic_model(hours=24, members=4, gust=30.0)
        for m in range(1, 5):
            for h in (5, 6, 7):
                m_one["hourly"][f"wind_gusts_10m_member{m:02d}"][h] = 75.0
        self.assertEqual(forecast.score_zone_days([m_one, m_one], days=1)[0]["hazards"]["wind"]["amber_hits"], 0)

        # Steps 06 and 09 both over the line across all members: Amber
        m_sust = _synthetic_model(hours=24, members=4, gust=30.0)
        for m in range(1, 5):
            for h in (6, 7, 8, 9):
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
        ok = {"cell": "land", "lat": 50.25, "lon": 31.0, "cell_elevation_m": 120.0, "station_elevation_m": 127.0,
              "diff_m": -7.0, "ok": True}
        doc = forecast.build_forecast_doc(zones, by_model, days=3, generated_at="2026-10-31T18:00:00Z",
                                          grids=[ok, ok, ok])
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
        # Contract 2.1: the subject carries no date, so the same zone on another day is the same subject.
        self.assertEqual((fc["contract_rev"], fc["kit"]), ("2.1", feed_v2.KIT_VERSION))
        self.assertEqual(z0["subject_id"], f"vis:zone:{zones[0]['id']}")
        self.assertNotIn("2026", z0["subject_id"])
        self.assertEqual(fc["features"][0]["properties"]["subject_id"], "vis:summary:daily")
        for f in fc["features"]:
            self.assertTrue(f["properties"]["event_start"].endswith("Z"))
        self.assertLessEqual(z0["event_start"], z0["event_end"])
        self.assertEqual(set(fc["health"]), {"open-meteo-gefs", "open-meteo-ecmwf", "astro-meeus"})
        self.assertIn("UKBB", z0["resolution"]["rule"])

        files = build_site.build(doc)
        self.assertIn("index.html", files)
        self.assertIn("Vis", files["index.html"])

    def test_models_weigh_equally_and_warnings_need_two_models_and_a_checked_cell(self):
        # GEFS 4 of 4 members over the wind line, ECMWF 0 of 8: equal weight gives 50%, not 4 of 12.
        gefs = _synthetic_model(hours=72, members=4, gust=70.0)
        ecmwf = _synthetic_model(hours=72, members=8, gust=20.0)
        d = forecast.score_zone_days([gefs, ecmwf], days=1, model_ids=["ncep_gefs025", "ecmwf_ifs025"])[0]
        w = d["hazards"]["wind"]
        self.assertEqual(w["p_amber"], 0.5)
        self.assertEqual(w["by_model"]["ncep_gefs025"]["amber_hits"], 4)
        self.assertEqual(w["by_model"]["ecmwf_ifs025"]["members"], 8)

        zones = forecast.load_zones()[:1]
        both = {"ncep_gefs025": [gefs], "ecmwf_ifs025": [_synthetic_model(hours=72, members=8, gust=70.0)]}
        off = {"cell": "land", "lat": 50.25, "lon": 31.0, "cell_elevation_m": 936.0, "station_elevation_m": 57.0,
               "diff_m": 879.0, "ok": False}
        p = feed_v2.build_feed(forecast.build_forecast_doc(zones, both, days=3, generated_at="2026-10-31T18:00:00Z",
                                                             grids=[off]))["features"][1]["properties"]
        self.assertEqual(p["tier"], "routine")
        self.assertIn("grid cell sits 879 m above the station", p["summary"])
        self.assertIn("GEFS 4 of 4, ECMWF 8 of 8, weighted equally", p["summary"])
        p = feed_v2.build_feed(forecast.build_forecast_doc(zones, both, days=3, generated_at="2026-10-31T18:00:00Z"))[
            "features"][1]["properties"]
        self.assertIn("could not be checked", p["summary"])   # no grid record: no warning

    def test_grid_check_compares_cell_and_station_elevation(self):
        class R:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(self.body).encode()
        zones = [{"lat": 10.601, "lon": -66.991, "icao": "SVMI"}, {"lat": 50.345, "lon": 30.895, "icao": "UKBB"}]
        answers = [{"latitude": 10.5, "longitude": -67.0}, {"latitude": 50.25, "longitude": 31.0}]
        seen = []
        def opener(req, timeout=None):
            seen.append(req.full_url)
            return R({"elevation": [57.0, 127.0, 936.0, 120.0]})
        g = forecast.grid_check(zones, answers, opener=opener)
        self.assertEqual((g[0]["diff_m"], g[0]["ok"]), (879.0, False))
        self.assertEqual((g[1]["diff_m"], g[1]["ok"]), (-7.0, True))
        self.assertEqual(len(seen), 1)

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

    def _fetch_with(self, failures):
        """fetch_model against a fake urlopen that raises each of `failures` in turn, then answers."""
        import io
        calls, waits = [], []

        def fake_urlopen(req, timeout=60):
            calls.append(req.full_url)
            if len(calls) <= len(failures):
                raise failures[len(calls) - 1]
            return io.BytesIO(b'[{"hourly": {"time": []}}]')

        real = forecast.urllib.request.urlopen
        forecast.urllib.request.urlopen = fake_urlopen
        try:
            out = forecast.fetch_model([{"lat": 1.0, "lon": 2.0}], "ecmwf_ifs025", 7, wait=waits.append)
        finally:
            forecast.urllib.request.urlopen = real
        return len(calls), waits, out

    def test_fetch_retries_a_network_failure_or_timeout(self):
        import urllib.error
        got = self._fetch_with([urllib.error.URLError(TimeoutError("_ssl.c:983: The handshake operation timed out")),
                                TimeoutError("The read operation timed out")])
        self.assertEqual(got, (3, [65, 65], [{"hourly": {"time": []}}]))

    def test_fetch_gives_up_after_three_network_failures(self):
        import urllib.error
        with self.assertRaises(urllib.error.URLError):
            self._fetch_with([urllib.error.URLError("down")] * 3)

    def test_fetch_raises_other_http_errors_at_once(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError("u", 500, "Server Error", {}, io.BytesIO(b"{}"))
        with self.assertRaises(urllib.error.HTTPError):
            self._fetch_with([err])

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


class ArchiveTests(unittest.TestCase):
    DOC = {"generated_at": "2026-11-18T20:25:12Z", "models": [
        {"id": "gfs", "used": True, "run_time": "2026-11-18T12:00Z"},
        {"id": "ecmwf", "used": True, "run_time": "2026-11-18T06:00Z"},
        {"id": "old", "used": False, "run_time": "2026-11-20T00:00Z"}]}

    def test_name_and_tag(self):
        self.assertEqual(archive.asset_name(self.DOC), "20261118T1200Z.forecast.gen-20261118T202512Z.json.gz")
        self.assertEqual(archive.tag_for(self.DOC), "vis-data-2026-11")

    def test_no_used_run_raises(self):
        with self.assertRaises(ValueError):
            archive.run_stamp({"generated_at": "2026-11-18T20:25:12Z", "models": [{"used": False, "run_time": "x"}]})

    def test_gzip_is_deterministic_and_lossless(self):
        import gzip
        raw = json.dumps(self.DOC).encode()
        self.assertEqual(archive.pack(raw), archive.pack(raw))
        self.assertEqual(gzip.decompress(archive.pack(raw)), raw)

    def test_prepare_writes_unchanged_forecast_and_site_output_is_unaffected(self):
        import gzip
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "forecast.json"
            src.write_text(json.dumps(self.DOC), encoding="utf-8")
            before = src.read_bytes()
            tag, name, p = archive.prepare(src, Path(d) / "archive")
            self.assertEqual(src.read_bytes(), before)  # the live input is untouched
            self.assertEqual(gzip.decompress(p.read_bytes()), before)
            self.assertEqual(p.name, name)
            tag2, name2, p2 = archive.prepare(src, Path(d) / "archive")  # same input -> same name and bytes
            self.assertEqual((tag, name, p.read_bytes()), (tag2, name2, p2.read_bytes()))


class VerifyTests(unittest.TestCase):
    """Plan step W3: METAR store, resolution and scores (tools/metar.py, tools/verify.py)."""
    HZ = {h["id"]: h for h in forecast.HAZARDS}

    @staticmethod
    def rep(t, **kw):
        r = {"icao": "TEST", "obs_time": t, "type": "METAR", "temp_c": 10, "wspd_kt": 5, "wgst_kt": None,
             "visib": "6+", "raw": "METAR TEST 000000Z 00005KT 9999 FEW030 10/05 Q1015"}
        r.update(kw)
        return r

    def test_store_is_append_only_and_never_duplicates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = self.rep("2026-11-20T00:00:00Z")
            self.assertEqual(metar.append(d, [a]), 1)
            path = metar.day_path(d, a["obs_time"])
            first = path.read_bytes()
            self.assertEqual(metar.append(d, [a, a]), 0)  # already stored: nothing written
            b = self.rep("2026-11-20T00:30:00Z")
            self.assertEqual(metar.append(d, [b]), 1)
            self.assertTrue(path.read_bytes().startswith(first))  # earlier lines untouched
            self.assertEqual(len(metar.load_range(d, ["2026-11-20"])["TEST"]), 2)

    def test_visibility_reads_the_metric_group_before_awc_miles(self):
        self.assertEqual(verify.visib_m(self.rep("x", visib=1.86, raw="METAR VISR 200230Z VRB02KT 3000 BR BKN200 01/M01 Q1031 TEMPO 1500")), 3000.0)
        self.assertEqual(verify.visib_m(self.rep("x", raw="METAR X 200230Z 00000KT 9999 NSC 10/05 Q1015")), 10000.0)
        self.assertEqual(verify.visib_m(self.rep("x", raw="METAR X 200230Z 00000KT CAVOK 10/05 Q1015")), 10000.0)
        self.assertAlmostEqual(verify.visib_m(self.rep("x", visib="1 1/2", raw="METAR KXXX 200230Z 00000KT 1 1/2SM BR")), 1.5 * verify.SM_M)

    def _steps(self, vals, hazard, field):
        steps = verify.steps_for("2026-11-20", "2026-11-20T00:00Z")
        reps = [self.rep(s.strftime("%Y-%m-%dT%H:%M:%SZ"), **{field: v}) for s, v in zip(steps, vals) if v is not None]
        return verify.resolve_level(self.HZ[hazard], self.HZ[hazard]["amber"], steps, reps)

    def test_two_consecutive_steps_make_an_event_and_one_does_not(self):
        self.assertEqual(self._steps([5, 5, 40, 40, 5, 5, 5, 5], "wind", "wgst_kt")[0], 1)  # 74 km/h twice
        self.assertEqual(self._steps([5, 5, 40, 5, 40, 5, 5, 5], "wind", "wgst_kt")[0], 0)
        self.assertEqual(self._steps([-20, -20, 0, 0, 0, 0, 0, 0], "cold", "temp_c")[0], 1)

    def test_missing_reports_are_counted_never_filled(self):
        o, seen, _, why = self._steps([5, None, None, None, 5, 5, 5, 5], "wind", "wgst_kt")
        self.assertIsNone(o)
        self.assertEqual((seen, why), (5, "too few reports in the window"))
        o, _, _, why = self._steps([5, 40, None, 5, 5, 5, 5, 5], "wind", "wgst_kt")
        self.assertIsNone(o)  # a hit beside a gap could have been an event
        self.assertEqual(why, "a hit sits next to a missing report")
        steps = verify.steps_for("2026-11-20", None)
        self.assertEqual(verify.resolve_level(self.HZ["wind"], 65.0, steps, [])[3], "no reports from the station")

    def test_score_counts_one_forecast_per_run_and_marks_small_samples(self):
        rows = []
        for i in range(120):
            for gen in ("2026-11-20T08:25:00Z", "2026-11-20T09:00:00Z"):  # two builds of one run
                rows.append({"forecast": f"f{gen}", "run": "20261120T0000Z", "generated_at": gen, "zone": f"z{i % 5}",
                             "day": f"d{i}", "lead_days": 1, "hazard": "vis", "level": "amber",
                             "p": 0.9 if i < 12 else 0.0, "outcome": 1 if i < 12 else 0, "why": None,
                             "threshold": 3000.0, "unit": "m"})
        doc = verify.score(rows)
        cell = [c for c in doc["scores"] if c["hazard"] == "vis" and c["level"] == "amber" and c["lead_days"] == "all"][0]
        self.assertEqual((cell["n"], cell["events"], cell["points_with_events"]), (120, 12, 5))
        self.assertTrue(cell["readable"])
        self.assertAlmostEqual(cell["brier"], 12 * 0.01 / 120, places=4)
        wind = [c for c in doc["scores"] if c["hazard"] == "wind" and c["lead_days"] == "all"][0]
        self.assertFalse(wind["readable"])
        html = build_site.verification_html(doc)
        self.assertIn("too few to read", html)
        self.assertIn("indicative", html)
        self.assertNotIn("<script", html)


GIBS_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1" version="1.0.0">
 <Contents>
  <Layer>
   <ows:Identifier>GOES-East_ABI_Band13_Clean_Infrared</ows:Identifier>
   <Format>image/png</Format>
   <Dimension><ows:Identifier>Time</ows:Identifier><Default>2026-11-22T01:50:00Z</Default>
    <Value>2026-11-21T00:00:00Z/2026-11-22T01:50:00Z/PT10M</Value></Dimension>
   <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible_Level7</TileMatrixSet></TileMatrixSetLink>
   <ResourceURL format="image/png" resourceType="tile" template="https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_Band13_Clean_Infrared/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png"/>
  </Layer>
  <Layer>
   <ows:Identifier>GOES-East_ABI_Band8_6.2um_Upper_Level_Water_Vapor</ows:Identifier>
   <Dimension><ows:Identifier>Time</ows:Identifier><Value>2026-11-22T00:00:00Z/2026-11-22T02:00:00Z/PT10M</Value></Dimension>
   <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible_Level7</TileMatrixSet></TileMatrixSetLink>
   <ResourceURL format="image/png" resourceType="tile" template="https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/WV/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png"/>
  </Layer>
  <Layer>
   <ows:Identifier>BlueMarble_NextGeneration</ows:Identifier>
   <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible_Level7</TileMatrixSet></TileMatrixSetLink>
   <ResourceURL format="image/jpeg" resourceType="tile" template="https://example.invalid/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg"/>
  </Layer>
  <TileMatrixSet><ows:Identifier>GoogleMapsCompatible_Level7</ows:Identifier>
   <TileMatrix><ows:Identifier>0</ows:Identifier></TileMatrix><TileMatrix><ows:Identifier>7</ows:Identifier></TileMatrix>
  </TileMatrixSet>
 </Contents>
</Capabilities>"""

EUMETVIEW_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0"><Capability><Layer>
 <Layer><Name>ir105_hrfi</Name><Dimension name="time" default="2026-11-22T02:00:00Z" units="ISO8601">2024-09-23T00:00:00.000Z/2026-11-22T02:00:00.000Z/PT10M</Dimension></Layer>
 <Layer><Name>li_afa</Name><Dimension name="time" units="ISO8601">2026-11-22T01:50:00.000Z,2026-11-22T01:55:00.000Z,2026-11-22T02:00:00.000Z</Dimension></Layer>
 <Layer><Name>rgb_airmass</Name></Layer>
</Layer></Capability></WMS_Capabilities>"""


NCEP_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms"><Capability><Layer><Title>conus</Title>
<Layer queryable="1"><Name>conus_bref_qcd</Name><Title>radar_reflectivity</Title>
<Dimension name="time" default="2026-11-22T02:20:04Z" units="ISO8601" nearestValue="1">2026-11-22T02:16:10.000Z,2026-11-22T02:18:06.000Z,2026-11-22T02:20:04.000Z</Dimension>
</Layer></Layer></Capability></WMS_Capabilities>"""

GEOMET_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms"><Capability><Layer><Title>MSC GeoMet</Title>
<Layer><Name>RADAR</Name><Title>Radar</Title>
<Layer queryable="1"><Name>RADAR_1KM_RRAI</Name><Title>Radar precipitation rate for rain</Title>
<Dimension name="time" units="ISO8601" default="2026-11-22T02:24:00Z" nearestValue="0">2026-11-21T23:24:00Z/2026-11-22T02:24:00Z/PT6M</Dimension>
</Layer></Layer></Layer></Capability></WMS_Capabilities>"""


class GfsTests(unittest.TestCase):
    NOW = datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)

    @staticmethod
    def field(short, level, fn, ni=360, nj=181):
        return {"short": short, "level": level, "ni": ni, "nj": nj, "lat0": 90.0, "lon0": 0.0, "dlat": -1.0,
                "dlon": 1.0, "values": [float(fn(90 - j, i)) for j in range(nj) for i in range(ni)]}

    def test_candidates_try_the_newest_run_first_at_the_nearest_three_hours(self):
        got = list(gfs.candidates(self.NOW))[:3]
        self.assertEqual([(c.hour, f) for c, f in got], [(0, 3), (18, 9), (12, 15)])

    def test_grid_thins_to_two_degrees_and_rounds_to_whole_metres_per_second(self):
        msgs = [self.field("u", 250, lambda la, lo: 40.4 if la == 40 else 5.0),
                self.field("v", 250, lambda la, lo: -3.6), self.field("u", 500, lambda la, lo: 99)]
        g = gfs.grid(msgs)
        self.assertEqual((g["nx"], g["ny"], g["dlat"], g["dlon"]), (180, 91, -2.0, 2.0))
        self.assertEqual(g["u"][25 * 180], 40)     # row 25 is 40 N
        self.assertEqual(g["u"][0], 5)
        self.assertEqual(set(g["v"]), {-4})
        with self.assertRaises(ValueError):
            gfs.grid(msgs[:1])

    def test_build_tries_older_runs_and_writes_snow_depth_in_centimetres(self):
        msgs = [self.field("u", 250, lambda la, lo: 30), self.field("v", 250, lambda la, lo: 0),
                self.field("sde", 0, lambda la, lo: 0.42 if la > 60 else 0.0)]
        msgs[2]["values"][-1] = None                  # masked (sea) point
        calls = []

        def get(url):
            calls.append(url)
            if "gfs.20261122/00" in url:
                raise OSError("not yet published")
            return b":UGRD:250 mb:\n:VGRD:250 mb:" if url.endswith(".idx") else b"GRIB"
        wind, snow = gfs.build(self.NOW, get=get, decoder=lambda raw: msgs, log=lambda *_: None)
        self.assertTrue(wind["ok"] and snow["ok"])
        self.assertEqual((wind["run"], wind["lead_h"], wind["valid"]), ("2026-11-21T18:00:00Z", 9, "2026-11-22T03:00:00Z"))
        self.assertIn("var_SNOD=on", calls[-1])
        self.assertEqual(max(snow["cm"]), 42)
        self.assertEqual(snow["cm"][-1], 0)

    def test_build_without_a_decoder_says_so_and_does_not_raise(self):
        def no_decoder(raw):
            raise ModuleNotFoundError("No module named 'eccodes'")
        wind, snow = gfs.build(self.NOW, get=lambda url: b":UGRD:250 mb:\n:VGRD:250 mb:", decoder=no_decoder,
                               log=lambda *_: None)
        self.assertFalse(wind["ok"] or snow["ok"])
        self.assertIn("eccodes", wind["error"])


SMOKE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>HMS Smoke Mapping-20261121</name>
<Folder><name>Smoke (Light)</name>
<Placemark><description><![CDATA[Start Time: 2026325 1300UTC<br>Density: Light<br>Satellite: GOES-WEST]]></description>
<styleUrl>#Smoke_Light_style</styleUrl><Polygon><outerBoundaryIs><LinearRing><coordinates>
-64.615070,42.945717,0 -67.484865,45.230924,0 -70.354662,47.037833,0 -64.615070,42.945717,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>
<Folder><name>Smoke (Heavy)</name><Placemark><styleUrl>#Smoke_Heavy_style</styleUrl><Polygon><outerBoundaryIs><LinearRing>
<coordinates>-120,40,0 -121,41,0 -119,41,0 -120,40,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>
</Document></kml>"""


class WxObsTests(unittest.TestCase):
    def test_hail_reports_carry_inches_and_times_across_midnight(self):
        day = wxobs.convective_day(datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc), 0)
        self.assertEqual(wxobs.iso(day), "2026-05-20T12:00:00Z")
        rows = wxobs.parse_hail("Time,Size,Location,County,State,Lat,Lon,Comments\n"
                                "1824,200,JACKSONVILLE,Morgan,IL,39.73,-90.23,(ILX)\n"
                                "0105,125,South Jacksonville,Morgan,IL,39.71,-90.23,Largest stone 1.18 in\n"
                                "bad,row\n", day)
        self.assertEqual([(r["in"], r["time"], r["place"]) for r in rows],
                         [(2.0, "2026-05-20T18:24:00Z", "Jacksonville, IL"), (1.25, "2026-05-21T01:05:00Z", "South Jacksonville, IL")])

    def test_smoke_polygons_by_density(self):
        polys = wxobs.parse_smoke(SMOKE_KML)
        self.assertEqual([p["density"] for p in polys], ["Light", "Heavy"])
        self.assertEqual(polys[0]["ring"][0], [-64.62, 42.95])

    def test_smoke_falls_back_to_yesterday(self):
        def get(url):
            if "20261122" in url:
                raise OSError("404")
            return SMOKE_KML
        doc = wxobs.smoke(datetime(2026, 11, 22, 3, tzinfo=timezone.utc), get=get, log=lambda *_: None)
        self.assertEqual((doc["ok"], doc["date"], len(doc["polygons"])), (True, "2026-11-21", 2))


class WxMapTests(unittest.TestCase):
    NOW = datetime(2026, 11, 22, 2, 30, tzinfo=timezone.utc)

    def test_durations_and_time_extents(self):
        self.assertEqual(wxmap.duration_s("PT10M"), 600)
        self.assertEqual(wxmap.duration_s("PT1H30M"), 5400)
        self.assertEqual(wxmap.duration_s("P1D"), 86400)
        self.assertIsNone(wxmap.duration_s(""))
        latest, step = wxmap.time_extent(["2026-11-20T00:00:00Z/2026-11-21T00:00:00Z/PT30M",
                                          "2026-11-21T06:00:00Z/2026-11-22T01:30:00Z/PT30M"])
        self.assertEqual((wxmap.iso(latest), step), ("2026-11-22T01:30:00Z", 1800))
        latest, step = wxmap.time_extent(["2026-11-22T01:50:00Z,2026-11-22T01:55:00Z"])
        self.assertEqual((wxmap.iso(latest), step), ("2026-11-22T01:55:00Z", 300))

    def test_manifest_resolves_layers_and_names_what_it_left_out(self):
        doc = wxmap.build_manifest(wxmap.gibs_layers(GIBS_CAPS), wxmap.wms_layers(EUMETVIEW_CAPS), now=self.NOW)
        self.assertTrue(doc["ok"])
        ir = next(p for p in doc["products"] if p["id"] == "ir")
        by = {l["sector"]: l for l in ir["layers"]}
        self.assertEqual(sorted(by), ["goes_east", "meteosat"])   # GOES-West and Himawari absent from the fixture
        ge = by["goes_east"]
        self.assertEqual(ge["url"], "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_Band13_Clean_Infrared"
                                    "/default/{time}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png")
        self.assertEqual((ge["max_zoom"], ge["step_s"], ge["lag_s"]), (7, 600, 2400))
        self.assertEqual((by["meteosat"]["layer"], by["meteosat"]["lag_s"]), ("mtg_fd:ir105_hrfi", 1800))
        wv = next(p for p in doc["products"] if p["id"] == "wv")
        self.assertEqual(wv["layers"][0]["name"], "GOES-East_ABI_Band8_6.2um_Upper_Level_Water_Vapor")  # by pattern
        lt = next(p for p in doc["products"] if p["id"] == "lightning")
        self.assertEqual([(l["sector"], l["step_s"]) for l in lt["layers"]], [("meteosat", 300)])
        why = {(d["product"], d["sector"]): d["why"] for d in doc["dropped"]}
        self.assertIn("no usable time dimension", why[("airmass", "meteosat")])   # listed, but no time
        self.assertEqual(why[("ir", "himawari")], "no gibs layer matched")
        self.assertEqual(why[("ir", "iodc")], "no eumetview layer matched")
        self.assertIn("Contains modified EUMETSAT Meteosat data 2026", [c["text"] for c in doc["credits"]])

    def test_manifest_without_services_is_not_ok_and_never_raises(self):
        doc = wxmap.build_manifest(None, None, now=self.NOW)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["products"], [])
        self.assertTrue(all("capabilities not read" in d["why"] for d in doc["dropped"]))

    def test_radar_from_noaa_and_eccc_takes_their_bounds_server_and_credit(self):
        ncep = wxmap.wms_layers(NCEP_CAPS, "conus")
        geomet = wxmap.wms_layers(GEOMET_CAPS, "geomet", qualify=False)
        self.assertIn("RADAR_1KM_RRAI", geomet)
        doc = wxmap.build_manifest(None, None, now=self.NOW, ncep=ncep, geomet=geomet)
        radar = next(p for p in doc["products"] if p["id"] == "radar")
        by = {l["sector"]: l for l in radar["layers"]}
        self.assertEqual(sorted(by), ["canada", "conus"])
        self.assertEqual((by["conus"]["wms"], by["conus"]["layer"]), (wxmap.NCEP_WMS, "conus:conus_bref_qcd"))
        self.assertEqual((by["conus"]["south"], by["conus"]["north"]), (20, 55))
        self.assertEqual(by["conus"]["step_s"], 118)      # irregular scans: the gap between the last two
        self.assertEqual((by["canada"]["wms"], by["canada"]["layer"], by["canada"]["step_s"]),
                         (wxmap.GEOMET_WMS, "RADAR_1KM_RRAI", 360))
        texts = [c["text"] for c in doc["credits"]]
        self.assertIn("Data Source: Environment and Climate Change Canada", texts)
        self.assertIn("Radar: NOAA MRMS", texts)
        self.assertIsNone(radar["frames"])

    def test_site_has_map_above_the_table_and_csp_allows_only_the_tile_hosts(self):
        doc = forecast.build_forecast_doc(forecast.load_zones()[:1], {
            "ncep_gefs025": [_synthetic_model(hours=72)], "ecmwf_ifs025": [_synthetic_model(hours=72)]},
            days=3, generated_at="2026-10-31T18:00:00Z")
        files = build_site.build(doc)
        html = files["index.html"]
        self.assertLess(html.index('id="wx-map"'), html.index('id="vis-table"'))
        self.assertIn('src="wxmap.js"', html)
        csp = next(l for l in files["_headers"].splitlines() if "Content-Security-Policy" in l)
        self.assertIn("img-src 'self' data: https://gibs.earthdata.nasa.gov https://view.eumetsat.int "
                      "https://opengeo.ncep.noaa.gov https://geo.weather.gc.ca;", csp)
        self.assertIn('id="wx-overlays"', html)
        self.assertIn("script-src 'self';", csp)
        self.assertTrue((ROOT / "site" / "wxmap.js").exists())
        self.assertTrue((ROOT / "site" / "vendor" / "leaflet" / "leaflet.js").exists())
        outlines = json.loads((ROOT / "site" / "geo" / "outlines.json").read_text(encoding="utf-8"))
        self.assertGreater(len(outlines["land"]), 50)
        self.assertGreater(len(outlines["borders"]), 100)


if __name__ == "__main__":
    unittest.main()
