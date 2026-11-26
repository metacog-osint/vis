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
from tic_doctrine import contract, registers  # noqa: E402


def _synthetic_model(hours=72, members=4, gust=40.0, cloud=50.0, vis=10000.0, rain=0.0, temp=18.0, snow=0.0, cape=0.0):
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
        hourly["cape" + sfx] = [cape] * hours
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


FIXTURES = ROOT / "tests" / "fixtures"


class ThunderstormTests(unittest.TestCase):
    """Method vis-ensemble-1.2 and vis-verify/2: the thunderstorm hazard, its scoring and its trial. The METAR lines
    in tests/fixtures/metar_tstorm.jsonl are cut from history/metar: Kigali (HRYR) 17 Nov 2026 with -TSRA, Incheon
    (RKSI) 23 Nov with TSRA, Kaliningrad (UMKK) 22 Nov with cumulonimbus but no thunder, and Mandalay (VYMD) 20 Nov,
    which reports by day only."""
    import tstorm_trial  # noqa: E402  (here, so the module's import block stays as it is)

    HZ = {h["id"]: h for h in forecast.HAZARDS}

    @classmethod
    def setUpClass(cls):
        cls.reports = {}
        for line in (FIXTURES / "metar_tstorm.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            cls.reports.setdefault(r["icao"], []).append(r)

    def test_hazard_is_cape_with_rain_at_one_step_and_on_trial(self):
        h = self.HZ["tstorm"]
        self.assertEqual((h["var"], h["with"]["var"], h["agg"], h["min_steps"]), ("cape", "precipitation", "joint", 1))
        self.assertEqual((h["amber"], h["red"], h["with"]["amber"], h["with"]["red"]), (1000.0, 2500.0, 1.0, 2.5))
        self.assertEqual((h["max_colour"], h["trial"]), ("yellow", True))
        self.assertIn("cape", forecast.VARS.split(","))

    def _model(self, cape_at, rain_at, members=4):
        """24 h of quiet weather, with CAPE and rain set at the given hours (all members)."""
        m = _synthetic_model(hours=24, members=members)
        for k in m["hourly"]:
            if k.startswith("cape"):
                m["hourly"][k] = [cape_at.get(h, 100.0) for h in range(24)]
            if k.startswith("precipitation"):
                m["hourly"][k] = [rain_at.get(h, 0.0) for h in range(24)]
        return m

    def test_one_native_step_with_both_fires_and_either_alone_does_not(self):
        both = self._model({12: 1500.0}, {12: 2.0})
        d = forecast.score_zone_days([both, both], days=1)[0]["hazards"]["tstorm"]
        self.assertEqual((d["p_amber"], d["p_red"]), (1.0, 0.0))
        self.assertEqual((d["colour"], d["capped_from"]), ("yellow", "amber"))   # Amber, shown Yellow on trial
        self.assertEqual(forecast.score_zone_days([both, both], days=1)[0]["colour"], "yellow")
        cape_only = self._model({12: 3000.0}, {})
        self.assertEqual(forecast.score_zone_days([cape_only, cape_only], days=1)[0]["hazards"]["tstorm"]["p_amber"], 0.0)
        rain_only = self._model({}, {12: 5.0})
        self.assertEqual(forecast.score_zone_days([rain_only, rain_only], days=1)[0]["hazards"]["tstorm"]["p_amber"], 0.0)
        # CAPE and rain in the same interpolated hour between steps is not a model step: no hit
        between = self._model({13: 3000.0}, {13: 5.0})
        self.assertEqual(forecast.score_zone_days([between, between], days=1)[0]["hazards"]["tstorm"]["p_amber"], 0.0)
        severe = self._model({9: 3000.0}, {9: 3.0})
        d = forecast.score_zone_days([severe, severe], days=1)[0]["hazards"]["tstorm"]
        self.assertEqual((d["p_red"], d["colour"], d["capped_from"]), (1.0, "yellow", "red"))
        self.assertNotIn("capped_from", forecast.score_zone_days([cape_only, cape_only], days=1)[0]["hazards"]["tstorm"])

    def test_thunder_codes_count_only_observed_thunder(self):
        th = verify.thunder
        line = {r["obs_time"][11:16]: r for r in self.reports["HRYR"]}
        self.assertTrue(th(line["11:30"])["ts"])                          # -TSRA
        tempo = th(line["10:30"])                                         # "... TEMPO TS": a forecast, not seen
        self.assertEqual((tempo["ts"], tempo["cb"]), (False, True))
        self.assertTrue(all(not th(r)["ts"] and th(r)["known"] for r in self.reports["UMKK"]))   # CB, no thunder
        self.assertTrue(any(th(r)["cb"] for r in self.reports["UMKK"]))
        rep = lambda raw, g=None: {"raw": raw, "wspd_kt": 10, "wgst_kt": g}  # noqa: E731
        self.assertTrue(th(rep("METAR XXXX 231200Z 18010KT 9999 VCTS SCT030CB 30/20 Q1010"))["ts"])
        self.assertTrue(th(rep("METAR XXXX 231200Z 18010KT 9999 -RA SCT030 25/20 Q1010 RETSRA"))["ts"])
        self.assertTrue(th(rep("METAR XXXX 231200Z 18010KT 2000 +TSRA SCT030CB 25/20 Q1010"))["strong"])
        self.assertTrue(th(rep("METAR XXXX 231200Z 18010KT 2000 TSRAGR SCT030CB 25/20 Q1010"))["strong"])
        self.assertTrue(th(rep("METAR XXXX 231200Z 18020G40KT 9999 TSRA 25/20 Q1010", 40))["strong"])
        self.assertFalse(th(rep("METAR XXXX 231200Z 18010KT 9999 TSRA 25/20 Q1010"))["strong"])
        self.assertFalse(th(rep("METAR XXXX 231200Z 18010KT 9999 VCSH 25/20 Q1010 RMK OCNL LTG W"))["ts"])
        self.assertFalse(th(rep("METAR KXXX 231200Z AUTO 18010KT 10SM CLR 25/20 A3000 RMK AO2 TSNO"))["known"])

    def test_resolution_on_real_reports_and_missing_is_never_a_no(self):
        st = lambda day: verify.steps_for(day, None)  # noqa: E731
        self.assertEqual(verify.resolve_tstorm("amber", st("2026-11-17"), self.reports["HRYR"])[0], 1)
        self.assertEqual(verify.resolve_tstorm("red", st("2026-11-17"), self.reports["HRYR"])[0], 0)
        self.assertEqual(verify.resolve_tstorm("amber", st("2026-11-23"), self.reports["RKSI"])[0], 1)
        o, seen, _, why = verify.resolve_tstorm("amber", st("2026-11-22"), self.reports["UMKK"])
        self.assertEqual((o, why), (0, None))                            # CB all day, no thunder: a no
        o, seen, _, why = verify.resolve_tstorm("amber", st("2026-11-20"), self.reports["VYMD"])
        self.assertEqual((o, why), (None, "too few reports in the window"))   # no night reports: unresolved
        self.assertEqual(verify.resolve_tstorm("amber", st("2026-11-20"), [])[3], "no reports from the station")
        # The window starts at the forecast's first step: the 11:30 storm is outside a window from 15:00
        late = verify.steps_for("2026-11-17", "2026-11-17T15:00Z")
        self.assertEqual(verify.resolve_tstorm("amber", late, self.reports["HRYR"])[0], 0)

    def _doc(self, cape=1500.0, rain=2.0):
        zones = forecast.load_zones()[:1]
        m = _synthetic_model(hours=72, members=4, cape=cape, rain=rain)
        ok = {"cell": "land", "lat": 50.25, "lon": 31.0, "cell_elevation_m": 120.0, "station_elevation_m": 127.0,
              "diff_m": -7.0, "ok": True}
        return forecast.build_forecast_doc(zones, {"ncep_gefs025": [m], "ecmwf_ifs025": [m]}, days=3,
                                           generated_at="2026-10-31T18:00:00Z", grids=[ok])

    def test_feed_item_passes_the_linter_with_a_metar_rule_and_no_warning_on_trial(self):
        # rain 2 mm/h all day also reaches nothing else (heavy rain needs 25 mm in 24 h: 48 mm does)
        doc = self._doc(rain=0.9)
        self.assertEqual(doc["zones"][0]["days"][1]["hazards"]["tstorm"]["p_amber"], 0.0)  # under the rain line
        doc = self._doc(rain=1.0)
        fc = feed_v2.build_feed(doc)
        self.assertEqual(fc["dropped"], [])
        regs = registers.ids(ROOT / "doctrine")
        p = fc["features"][1]["properties"]
        self.assertEqual([e for e in contract.check(fc["features"][1], regs) if e[1] == "error"], [])
        self.assertEqual((p["measures"]["driver_hazard"], p["tier"], p["method_version"]),
                         ("tstorm", "routine", "vis-ensemble-1.2"))
        self.assertIn("CAPE >= 1000 J/kg with precipitation >= 1 mm/h", p["summary"])
        self.assertIn("on trial, so this Amber shows as Yellow", p["summary"])
        rule = p["resolution"]["rule"]
        for needle in ("UKBB", "VCTS", "RETS", "TSNO", "missing report is not a no", "75%"):
            self.assertIn(needle, rule)
        self.assertIn("METARs at UKBB report no thunderstorm", p["alternatives"][0]["tell_apart"])
        red = feed_v2._resolution_rule("Kyiv Boryspil", "UKBB", self.HZ["tstorm"], "red", "2026-11-09")
        self.assertIn("+TS", red)
        self.assertEqual(feed_v2._ncf_for_hazard("tstorm"), ["transport_by_air", "transport_by_vessel"])

    def test_verify_resolves_supplements_and_scores_thunderstorms(self):
        import gzip
        import tempfile
        doc = self._doc()
        # One forecast for 23 Nov at the Incheon point, issued the day before
        doc["generated_at"] = "2026-11-22T08:25:00Z"
        z = dict(doc["zones"][0], id="13_Korea", icao="RKSI", station="Incheon")
        z["days"] = [dict(z["days"][1], day="2026-11-23", **{"from": "2026-11-23T00:00Z"})]
        doc["zones"] = [z]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            metar.append(d / "metar", self.reports["RKSI"])
            (d / "fc").mkdir()
            name = "20261122T0000Z.forecast.gen-20261122T082500Z.json.gz"
            (d / "fc" / name).write_bytes(gzip.compress(json.dumps(doc).encode()))
            # A window first observed by vis-verify/1, before thunderstorms were scored
            old = {"method": "vis-verify/1", "zone": "13_Korea", "icao": "RKSI", "day": "2026-11-23",
                   "start": "2026-11-23T00:00Z", "steps": 8, "reports": 48,
                   "obs": {"wind": {"amber": {"outcome": 0, "threshold": 65.0, "why": None},
                                    "red": {"outcome": 0, "threshold": 90.0, "why": None},
                                    "steps_seen": 8, "extreme": 35.2}}}
            verify._append(d / "v" / "observed.jsonl", [old])
            before = (d / "v" / "observed.jsonl").read_bytes()
            now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
            obs, pairs = verify.resolve(d / "fc", d / "metar", d / "v", now=now)
            self.assertTrue((d / "v" / "observed.jsonl").read_bytes().startswith(before))   # append-only
            self.assertEqual(len(obs), 1)
            self.assertTrue(obs[0]["supplement"])
            self.assertNotIn("wind", obs[0]["obs"])                       # the stored wind line is kept as it was
            self.assertEqual(obs[0]["obs"]["tstorm"]["amber"]["outcome"], 1)
            self.assertEqual(obs[0]["obs"]["tstorm"]["thunder_reports"], 2)
            self.assertEqual(pairs[0]["p"]["tstorm"][:2], [1.0, 0.0])
            self.assertEqual(verify.resolve(d / "fc", d / "metar", d / "v", now=now), ([], []))   # nothing twice
            rows = verify.joined(d / "v")
            ts = {r["level"]: r for r in rows if r["hazard"] == "tstorm"}
            self.assertEqual((ts["amber"]["outcome"], ts["red"]["outcome"]), (1, 0))
            self.assertEqual({r["outcome"] for r in rows if r["hazard"] == "wind"}, {0})
            sc = verify.score(rows, now=now)
            cell = next(c for c in sc["scores"] if c["hazard"] == "tstorm" and c["level"] == "amber"
                        and c["lead_days"] == 1)
            self.assertEqual((cell["n"], cell["events"], cell["brier"]), (1, 1, 0.0))
            self.assertIn("TS, VCTS or RETS", cell["criterion"])
            self.assertEqual(sc["method"], "vis-verify/2")
            html = build_site.verification_html(sc)
            self.assertIn("Thunderstorms (amber)", html)
            self.assertIn("TS, VCTS or RETS reported", html)
            self.assertIn("a missing report is never a no", html)

    def test_trial_measures_rules_against_the_stations(self):
        import tempfile
        tt = self.tstorm_trial
        points = [{"id": "13_Korea", "icao": "RKSI"}, {"id": "20_DRC-HRYR", "icao": "HRYR"}]

        def answer(cape, rain, days=("2026-11-22", "2026-11-23")):
            times = [f"{day}T{h:02d}:00" for day in days for h in range(24)]
            hourly = {"time": times}
            for k in range(1, 4):
                hourly[f"cape_member{k:02d}"] = [cape] * len(times)
                hourly[f"precipitation_member{k:02d}"] = [rain] * len(times)
            return {"hourly": hourly}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            metar.append(d / "metar", self.reports["RKSI"] + self.reports["HRYR"])
            for model in tt.MODELS:
                (d / f"{model}.json").write_text(json.dumps({
                    "model": model, "fetched_at": "2026-11-24T09:00:00Z", "past_days": 2, "points": [p["id"] for p in points],
                    "answers": [answer(1200.0, 1.5), answer(200.0, 0.0)]}), encoding="utf-8")
            rep = tt.measure(d, d / "metar", points)
            chosen = next(r for r in rep["rules"] if r["chosen"])
            self.assertEqual((chosen["cape_j_kg"], chosen["rain_mm_h"], chosen["steps"]), (1000.0, 1.0, 1))
            self.assertEqual(chosen["point_days"], 4)
            self.assertEqual(chosen["share_amber_or_more"], 0.5)          # Incheon both days, Kigali never
            # Resolved: Incheon 23 Nov (thunder) and 22 Nov (no reports in the fixture: unresolved); Kigali none
            self.assertEqual((chosen["resolved"], chosen["events"], chosen["hits_at_45"]), (1, 1, 1))
            high = next(r for r in rep["rules"] if r["cape_j_kg"] == 1500.0 and r["rain_mm_h"] == 1.0 and r["steps"] == 1)
            self.assertEqual(high["share_amber_or_more"], 0.0)
            self.assertEqual(rep["points"]["13_Korea"]["p_chosen"], [1.0, 1.0])
            summary = tt.metar_summary(d / "metar", "2026-11-17", "2026-11-23", points)
            st = {r["icao"]: r for r in summary["stations"]}
            self.assertEqual(st["HRYR"]["days_thunder"], ["2026-11-17"])
            self.assertEqual(st["RKSI"]["days_thunder"], ["2026-11-23"])
            self.assertEqual(st["RKSI"]["episodes"][0][2], 30)             # 06:00 to 06:30 UTC

    def test_page_shows_the_hazard_and_its_trial(self):
        self.assertIn('split(" + ")', build_site.VIS_JS)                 # J/kg + mm/h, not "C with"
        self.assertIn("on trial:", build_site.VIS_JS)
        self.assertNotIn("There is no thunderstorm hazard yet", build_site.INDEX_HTML)
        self.assertIn("Only wind, visibility, thunderstorms, heat and cold", build_site.INDEX_HTML)
        files = build_site.build(self._doc())
        hz = {h["id"]: h for h in json.loads(files["data/forecast.json"])["hazards"]}
        self.assertEqual(hz["tstorm"]["unit"], "J/kg + mm/h")


if __name__ == "__main__":
    unittest.main()
