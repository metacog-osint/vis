"""Private archive lanes: every file a run of build.yml makes outside the tracked code (out/, archive/, history/,
site/) lands in the private archive (tools/tic_archive.py, metacog-osint/tic-archive) and restores byte for byte. A
file that a later run removes or replaces stays restorable from the snapshot that held it.

The test makes a real offline run in a temporary folder: the repository's committed history/, a forecast built from
synthetic ensemble members and written as tools/forecast.py writes it, the verification score, the location detail
(network replaced by a stand-in), the site build, the kit's history log and the public release step's
tools/archive.py call, each as build.yml gives it. It then runs the private archive step's own commands, read from
build.yml, against a local DirStore. Nothing reaches GitHub: GhStore is replaced by the DirStore, and any subprocess
call during an archive command fails the test.

out/climate.json is not a file the build makes: build.yml downloads it from release vis-climate, where climate.yml
keeps a dated copy of every upload. The offline run has none."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
sys.path.insert(0, str(ROOT / "tests"))
import archive  # noqa: E402
import forecast  # noqa: E402
import locations  # noqa: E402
import tic_archive as ta  # noqa: E402
from test_vis import _fake_open_meteo_and_awc, _synthetic_model  # noqa: E402

WF_PATH = ROOT / ".github" / "workflows" / "build.yml"
WF = WF_PATH.read_text(encoding="utf-8")
TESTS_WF = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
PROGRAM = "vis"
PRIVATE_STEP = "Private archive (history, forecasts and site, to tic-archive)"
PUBLIC_STEP = "Archive forecast.json and locations.json to release vis-data-YYYY-MM"
RUN_OUTPUTS = ("out", "archive", "history", "site")   # what a run writes beside the tracked code
RUN_INPUTS = ("tools", "doctrine", "config")          # tracked code the offline run is given
FIRST, SECOND = "20261127T080000Z", "20261127T200000Z"
BUILD_1, BUILD_2 = "2026-10-31T18:00:00Z", "2026-10-31T20:25:00Z"


def steps() -> dict[str, str]:
    """The build job's steps by name (unnamed `uses:` steps by their action)."""
    job = WF.split("\n  build:\n", 1)[1]
    out = {}
    for chunk in re.split(r"(?m)^      - ", job.split("\n    steps:\n", 1)[1])[1:]:
        m = re.match(r"name: (.+)|uses: (\S+)", chunk)
        out[(m.group(1) or m.group(2)).strip()] = chunk
    return out


STEPS = steps()


def step(prefix: str) -> str:
    (name,) = [n for n in STEPS if n.startswith(prefix)]
    return STEPS[name]


def flat(text: str) -> str:
    return text.replace("\\\n", " ")


def script_calls(text: str, script: str, cmd: str) -> list[list[str]]:
    """Every `python tools/<script> <cmd> ...` in a step, as argv after the script name (up to && || ; | > or a
    line end)."""
    calls = []
    for m in re.finditer(r"python tools/" + re.escape(script) + r" (" + cmd + r")\b(.*?)(?=&&|\|\||[;|>\n]|$)",
                         flat(text)):
        calls.append([m.group(1), *shlex.split(m.group(2))])
    return calls


def arg(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, t in enumerate(argv) if t == flag]


def put_calls() -> list[list[str]]:
    return script_calls(STEPS[PRIVATE_STEP], "tic_archive.py", "put")


def tree(root: Path) -> dict[str, str]:
    """{posix path relative to root: sha256} for every file."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def expand(argv: list[str], runner_temp: Path, root: Path) -> list[str]:
    """$RUNNER_TEMP and the step's $extra, as the shell would give them in that run."""
    out = []
    for t in argv:
        if t == "$extra":
            if (root / "out" / "locations.json").exists():
                out += ["--extra", "out/locations.json"]
            continue
        out.append(t.replace("$RUNNER_TEMP", str(runner_temp)))
    return out


def in_dir(cwd: Path, fn, *a):
    old = os.getcwd()
    os.chdir(cwd)
    try:
        return fn(*a)
    finally:
        os.chdir(old)


def run_archive(argv: list[str], cwd: Path, store: Path, stamp: str = FIRST) -> int:
    """One tic_archive.py command as the workflow gives it, in cwd, against a DirStore at store."""
    def no_subprocess(*a, **k):
        raise AssertionError("an archive command tried to run a subprocess (gh): nothing may leave the test")

    env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(ta, "GhStore", lambda *a, **k: ta.DirStore(store)), \
            mock.patch.object(ta, "utc_stamp", lambda now=None: stamp), \
            mock.patch.object(subprocess, "run", no_subprocess):
        return in_dir(cwd, ta.main, argv)


def private_step(root: Path, store: Path, stamp: str, runner_temp: Path) -> dict[str, int]:
    """The private archive step's commands in order: the data lane packs with tools/archive.py first."""
    (prep,) = script_calls(STEPS[PRIVATE_STEP], "archive.py", "prepare")
    in_dir(root, archive.main, expand(prep, runner_temp, root))
    return {arg(c, "--lane")[0]: run_archive(expand(c, runner_temp, root), root, store, stamp) for c in put_calls()}


def write_forecast(root: Path, generated_at: str, gust: float = 70.0) -> None:
    """out/forecast.json for three points from synthetic members, written the way tools/forecast.py writes it."""
    zones = forecast.expand_points(forecast.load_zones())[:3]
    hot = _synthetic_model(hours=72, members=4, gust=gust, cloud=85.0)
    quiet = _synthetic_model(hours=72, members=4, gust=25.0, cloud=20.0)
    by_model = {"ncep_gefs025": [hot, quiet, quiet], "ecmwf_ifs025": [hot, quiet, quiet]}
    runs = {m: {"id": m, "run_time": "2026-10-31T12:00Z", "available_at": "2026-10-31T17:40Z", "age_hours": 6.0,
                "state": "used"} for m in by_model}
    ok = {"cell": "land", "lat": 50.25, "lon": 31.0, "cell_elevation_m": 120.0, "station_elevation_m": 127.0,
          "diff_m": -7.0, "ok": True}
    doc = forecast.build_forecast_doc(zones, by_model, days=3, generated_at=generated_at, runs=runs,
                                      grids=[ok, ok, ok])
    (root / "out").mkdir(exist_ok=True)
    with open(root / "out" / "forecast.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


def run_tool(root: Path, argv: list[str]) -> None:
    r = subprocess.run([sys.executable, *argv], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:2])} failed: {r.stderr[-600:]}")


def build_run(root: Path, generated_at: str) -> None:
    """The build steps that write files, in build.yml's order, offline."""
    write_forecast(root, generated_at)
    run_tool(root, ["tools/verify.py", "score"])
    with mock.patch("urllib.request.urlopen", _fake_open_meteo_and_awc):
        assert locations.main(["--forecast", str(root / "out/forecast.json"), "--climate",
                               str(root / "out/climate.json"), "--out", str(root / "out/locations.json"),
                               "--metar", str(root / "history/metar")]) in (0, None)
    run_tool(root, ["tools/build_site.py"])
    (kit,) = re.findall(r"python (doctrine/kit/tic\.py history [^|;\n]*)", flat(step("History")))
    run_tool(root, shlex.split(kit))
    (pub,) = script_calls(STEPS[PUBLIC_STEP], "archive.py", "prepare")
    in_dir(root, archive.main, expand(pub, root / "unused", root))


class RunTree:
    """A real offline run, made once."""
    tmp: Path | None = None

    @classmethod
    def make(cls) -> Path:
        if cls.tmp is not None:
            return cls.tmp
        tmp = Path(tempfile.mkdtemp(prefix="vis-lanes-"))
        for d in ("tools", "doctrine", "config", "history"):
            shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
        # The tracked site files only: site/data/ is what the build writes (git-ignored).
        shutil.copytree(ROOT / "site", tmp / "site", ignore=lambda d, names: ["data"] if Path(d) == ROOT / "site"
                        else [n for n in names if n == "__pycache__"])
        build_run(tmp, BUILD_1)
        cls.tmp = tmp
        return tmp


def tearDownModule():
    if RunTree.tmp is not None:
        shutil.rmtree(RunTree.tmp, ignore_errors=True)


class TestWorkflowLanes(unittest.TestCase):
    def test_lanes_are_history_data_site(self):
        lanes = {arg(c, "--lane")[0]: c for c in put_calls()}
        self.assertEqual(set(lanes), {"history", "data", "site"})
        self.assertEqual(len(put_calls()), 3)
        for c in lanes.values():
            self.assertEqual(arg(c, "--program"), [PROGRAM])
            # Copies of public files, whole: no filter, no redaction, so they match the public copy by hash.
            self.assertFalse([t for t in c if t.startswith(("--redact", "--include", "--exclude"))], c)
        self.assertEqual(arg(lanes["history"], "--src"), ["history"])
        self.assertEqual(arg(lanes["data"], "--src"), ["$RUNNER_TEMP/vis-data"])
        self.assertEqual(arg(lanes["site"], "--src"), ["site"])

    def test_step_is_last_and_loud_on_failure(self):
        names = list(STEPS)
        self.assertEqual(names[-1], PRIVATE_STEP)
        self.assertGreater(names.index(PRIVATE_STEP), names.index("Deploy site/ to Cloudflare Pages"))
        self.assertGreater(names.index(PRIVATE_STEP), names.index(PUBLIC_STEP))
        text = STEPS[PRIVATE_STEP]
        self.assertIn("if: ${{ !cancelled() }}", text)
        self.assertIn("GH_TOKEN: ${{ secrets.TIC_ARCHIVE_TOKEN }}", text)
        self.assertNotIn("github.token", text)
        self.assertNotIn("|| true", text)
        self.assertNotIn("continue-on-error", text)
        self.assertIn('[ -z "$GH_TOKEN" ]', text)
        self.assertEqual(text.count("exit 1"), 2)            # token missing; any lane failed
        self.assertEqual(text.count("|| failed=1"), 3)       # each lane tried even if another failed

    def test_each_lane_only_after_the_step_that_writes_it(self):
        text = STEPS[PRIVATE_STEP]
        self.assertIn("FORECAST: ${{ steps.forecast.outcome }}", text)
        self.assertIn("HISTORY: ${{ steps.history.outcome }}", text)
        self.assertIn("DEPLOYED: ${{ steps.deploy.outcome }}", text)
        self.assertIn("id: forecast", step("Forecast"))
        self.assertIn("id: history", step("History"))
        self.assertIn("id: deploy", STEPS["Deploy site/ to Cloudflare Pages"])
        f = flat(text)
        self.assertRegex(f, r'if \[ "\$HISTORY" = "success" \]; then\s+python tools/tic_archive\.py put '
                            r'--program vis --lane history ')
        self.assertRegex(f, r'if \[ "\$FORECAST" = "success" \] && \[ -f out/forecast\.json \]; then\s+extra=.*\s+'
                            r'python tools/archive\.py prepare .*&& python tools/tic_archive\.py put '
                            r'--program vis --lane data ')
        self.assertRegex(f, r'if \[ "\$DEPLOYED" = "success" \]; then\s+python tools/tic_archive\.py put '
                            r'--program vis --lane site ')

    def test_data_lane_packs_with_the_public_release_steps_command(self):
        (pub,) = script_calls(STEPS[PUBLIC_STEP], "archive.py", "prepare")
        (priv,) = script_calls(STEPS[PRIVATE_STEP], "archive.py", "prepare")
        self.assertEqual(pub, ["prepare", "out/forecast.json", "archive", "$extra"])
        self.assertEqual(priv, ["prepare", "out/forecast.json", "$RUNNER_TEMP/vis-data", "$extra"])
        same_extra = 'extra=""; [ -f out/locations.json ] && extra="--extra out/locations.json"'
        self.assertIn(same_extra, STEPS[PUBLIC_STEP])
        self.assertIn(same_extra, STEPS[PRIVATE_STEP])

    def test_the_folders_the_lanes_read_are_the_ones_the_workflow_writes(self):
        self.assertIn("git add history\n", STEPS["Commit history/"])
        self.assertIn("pages deploy site --project-name vis", flat(STEPS["Deploy site/ to Cloudflare Pages"]))
        self.assertIn("--dir history --ended site/data/ended.geojson", flat(step("History")))

    def test_both_workflows_run_these_tests(self):
        self.assertIn("python tests/test_archive_lanes.py", STEPS["Tests"])
        self.assertIn("python tests/test_archive_lanes.py", TESTS_WF)


class TestEveryRunFileIsArchived(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_root = RunTree.make()
        cls.work = Path(tempfile.mkdtemp(prefix="vis-store-"))
        cls.store = cls.work / "store"
        cls.before = {top: tree(cls.run_root / top) for top in RUN_OUTPUTS}
        cls.results = private_step(cls.run_root, cls.store, FIRST, cls.work / "runner-1")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def restore(self, lane: str, dest: Path, snapshot: str | None = None) -> dict[str, str]:
        cmd = ["restore", "--program", PROGRAM, "--lane", lane, "--dest", str(dest)]
        if snapshot:
            cmd += ["--snapshot", snapshot]
        self.assertEqual(run_archive(cmd, self.run_root, self.store), 0, lane)
        return tree(dest)

    def snapshot_names(self, stamp: str) -> dict[str, str]:
        out = {}
        for tag in self.store.iterdir():
            for p in tag.iterdir():
                if p.name.startswith(stamp) and p.name.endswith(".files.json"):
                    out[tag.name[len(PROGRAM) + 1:].rsplit("-", 2)[0]] = p.name
        return out

    def test_the_offline_run_writes_only_the_known_folders(self):
        self.assertEqual({p.name for p in self.run_root.iterdir()}, set(RUN_INPUTS) | set(RUN_OUTPUTS))
        self.assertEqual(set(self.before["out"]), {"forecast.json", "locations.json", "verification.json"})
        self.assertEqual(len(self.before["archive"]), 2)
        self.assertTrue(self.before["history"] and self.before["site"])

    def test_every_put_succeeded(self):
        self.assertEqual(self.results, {"history": 0, "data": 0, "site": 0})
        self.assertEqual(set(self.snapshot_names(FIRST)), {"history", "data", "site"})

    def test_history_and_site_restore_byte_for_byte(self):
        snaps = self.snapshot_names(FIRST)
        with tempfile.TemporaryDirectory() as td:
            for lane in ("history", "site"):
                got = self.restore(lane, Path(td) / lane, snaps[lane])
                self.assertEqual(got, self.before[lane], lane)
        for p in ("data/feed.geojson", "data/ended.geojson", "data/forecast.json", "data/locations.json",
                  "data/verification.json", "index.html", "vis.js"):
            self.assertIn(p, self.before["site"])
        for p in ("state.json", "verify/pairs.jsonl", "verify/observed.jsonl"):
            self.assertIn(p, self.before["history"])
        self.assertTrue([p for p in self.before["history"] if p.startswith("metar/")])
        self.assertTrue([p for p in self.before["history"] if p.startswith("log/")])

    def test_data_lane_holds_the_public_release_assets_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            got = self.restore("data", Path(td), self.snapshot_names(FIRST)["data"])
        # Same names and same bytes as the assets the public step uploads to vis-data-YYYY-MM.
        self.assertEqual(got, self.before["archive"])
        names = sorted(got)
        self.assertEqual([n.split(".")[1] for n in names], ["forecast", "locations"])
        self.assertTrue(all(n.startswith("20261031T1200Z.") and ".gen-20261031T180000Z." in n for n in names))

    def test_every_out_file_is_in_a_lane(self):
        snaps = self.snapshot_names(FIRST)
        out = self.run_root / "out"
        with tempfile.TemporaryDirectory() as td:
            self.restore("data", Path(td) / "data", snaps["data"])
            self.restore("site", Path(td) / "site", snaps["site"])
            for kind in ("forecast", "locations"):
                (gz,) = (Path(td) / "data").glob(f"*.{kind}.gen-*.json.gz")
                self.assertEqual(gzip.decompress(gz.read_bytes()), (out / f"{kind}.json").read_bytes(), kind)
            # The score the page shows: the site build writes the same document under data/.
            self.assertEqual(json.loads((Path(td) / "site/data/verification.json").read_text(encoding="utf-8")),
                             json.loads((out / "verification.json").read_text(encoding="utf-8")))

    def test_removed_or_replaced_files_stay_restorable(self):
        # The next run, in a copy of the first: a closed log day no longer in the public tree (a cap), a new build
        # and a new deploy.
        root = self.work / "next-run"
        shutil.copytree(self.run_root, root)
        log_day = sorted(p for p in self.before["history"] if p.startswith("log/"))[0]
        (root / "history" / log_day).unlink()
        write_forecast(root, BUILD_2, gust=90.0)
        run_tool(root, ["tools/build_site.py"])
        self.assertNotEqual(tree(root / "site")["data/feed.geojson"], self.before["site"]["data/feed.geojson"])
        self.assertEqual(private_step(root, self.store, SECOND, self.work / "runner-2"),
                         {"history": 0, "data": 0, "site": 0})
        first, second = self.snapshot_names(FIRST), self.snapshot_names(SECOND)
        self.assertEqual(set(second), {"history", "data", "site"})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            now_h = self.restore("history", td / "now-h")
            then_h = self.restore("history", td / "then-h", first["history"])
            now_s = self.restore("site", td / "now-s")
            then_s = self.restore("site", td / "then-s", first["site"])
            now_d = self.restore("data", td / "now-d")
            then_d = self.restore("data", td / "then-d", first["data"])
        self.assertNotIn(log_day, now_h)
        self.assertEqual(then_h, self.before["history"])               # the dropped day among them
        self.assertEqual(then_s, self.before["site"])
        self.assertEqual(now_s, tree(root / "site"))
        self.assertNotEqual(now_s["data/feed.geojson"], then_s["data/feed.geojson"])
        self.assertEqual(then_d, self.before["archive"])               # the first build's files, unchanged
        self.assertEqual(len(now_d), 2)
        self.assertTrue(all(".gen-20261031T202500Z." in n for n in now_d))


if __name__ == "__main__":
    unittest.main()
