"""Archive helper: deterministic name and gzip for one run's forecast.json (retention, 18 Nov 2026).

    python tools/archive.py prepare out/forecast.json archive/   # writes the .gz, prints tag=, name=, path=

The asset is named from the newest model-run time and the build's own generated_at, so every build gets a unique
name (nothing is ever overwritten) and the same input always gives the same name and the same bytes (gzip mtime 0).
Standard library only. The feed is derived from forecast.json alone (feed_v2.build_feed), so it is not archived.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path


def _stamp(iso: str, seconds: bool = False) -> str:
    """'2026-11-18T12:00Z' / '2026-11-18T20:25:12Z' -> '20261118T1200Z' / '20261118T202512Z'."""
    s = iso.replace("-", "").replace(":", "")
    if not s.endswith("Z") or "T" not in s:
        raise ValueError(f"not a UTC ISO time: {iso!r}")
    d, t = s[:-1].split("T")
    return f"{d}T{t if seconds else t[:4]}Z"


def run_stamp(doc: dict) -> str:
    """Newest model run time among the models used in this forecast, e.g. 20261118T1200Z."""
    runs = [m.get("run_time") for m in doc.get("models", []) if m.get("used") and m.get("run_time")]
    if not runs:
        raise ValueError("forecast.json has no used model with a run_time")
    return _stamp(max(runs))


def tag_for(doc: dict) -> str:
    """Monthly release tag from generated_at (UTC): vis-data-YYYY-MM."""
    return "vis-data-" + doc["generated_at"][:7]


def asset_name(doc: dict) -> str:
    return f"{run_stamp(doc)}.forecast.gen-{_stamp(doc['generated_at'], True)}.json.gz"


def pack(raw: bytes) -> bytes:
    """Deterministic gzip: fixed mtime, no embedded filename, fixed level."""
    return gzip.compress(raw, compresslevel=9, mtime=0)


def prepare(src: Path, out_dir: Path) -> tuple[str, str, Path]:
    raw = src.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    name = asset_name(doc)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / name
    p.write_bytes(pack(raw))
    return tag_for(doc), name, p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["prepare"])
    ap.add_argument("src")
    ap.add_argument("out_dir")
    a = ap.parse_args(argv)
    tag, name, p = prepare(Path(a.src), Path(a.out_dir))
    print(f"tag={tag}\nname={name}\npath={p.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
