"""Write site/geo/outlines.json: land, coastline and land-border lines for the weather map, from Natural Earth.

    python tools/outlines.py NE_DIR

NE_DIR holds Natural Earth GeoJSON files (public domain, naturalearthdata.com; the GeoJSON copies in
github.com/nvkelso/natural-earth-vector): ne_110m_land.geojson and ne_50m_admin_0_boundary_lines_land.geojson.
Coordinates are rounded to 0.05 degrees and repeated points dropped, to keep the file small. Run it again only to
change the detail; the output is committed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP = 0.05


def _lines(geom: dict) -> list[list[list[float]]]:
    t, c = geom["type"], geom["coordinates"]
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return c
    if t == "Polygon":
        return c
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    return []


def _squash(line: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for x, y, *_ in line:
        p = [round(round(x / STEP) * STEP, 2), round(round(y / STEP) * STEP, 2)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def build(ne_dir: Path) -> dict:
    doc = {"source": "Natural Earth (public domain), 1:110m land and 1:50m land borders", "step_deg": STEP}
    for key, name in (("land", "ne_110m_land"), ("borders", "ne_50m_admin_0_boundary_lines_land")):
        fc = json.loads((ne_dir / f"{name}.geojson").read_text(encoding="utf-8"))
        lines = [_squash(ln) for f in fc["features"] for ln in _lines(f["geometry"])]
        doc[key] = [ln for ln in lines if len(ln) > 1]
    return doc


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    doc = build(Path(argv[0]))
    out = ROOT / "site" / "geo" / "outlines.json"
    out.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): {len(doc['land'])} land rings, {len(doc['borders'])} borders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
