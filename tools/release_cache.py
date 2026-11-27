"""A JSON cache kept as one release asset (the climate cache, climate.json on release vis-climate): download it with
every failure loud, and check a new copy before it replaces the live one. Standard library only.

    python tools/release_cache.py get --tag vis-climate --asset climate.json --out out/climate.json
        # prints state=none (no such release or asset: the first run) or state=ok and points=N; anything else exits 1
    python tools/release_cache.py check --new out/climate.json [--live out/climate.prev.json]
        # prints upload=yes|no and stamp=YYYYMMDDTHHMMZ; exits 1 when the new copy would drop points

Why: the asset is the cache's only copy and an upload replaces it in place, so a download that fails or arrives cut
short must stop the run, never look like an empty cache, and an upload must never put fewer points in its place.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = "tic-vis/1.0 (+https://vis.theintelligence.company)"


class CacheError(Exception):
    """The cache could not be read whole; the run must stop rather than start from nothing."""


def _open(req, opener, timeout=60):
    return (opener or urllib.request.urlopen)(req, timeout=timeout)


def get(repo: str, tag: str, asset: str, schema: str | None = None, token: str | None = None, opener=None):
    """(state, doc): ("none", None) when the release or its asset does not exist; ("ok", doc) for a whole, parsed
    copy. Raises CacheError for anything else: a network failure, another HTTP error, a body shorter or longer than
    the asset's listed size, or JSON that does not parse or has another schema."""
    hdr = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    try:
        with _open(urllib.request.Request(url, headers=hdr), opener) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "none", None
        raise CacheError(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise CacheError(f"{url}: {e.__class__.__name__}: {e}") from e
    a = next((x for x in rel.get("assets") or [] if x.get("name") == asset), None)
    if a is None:
        return "none", None
    # The public download link, without the token: it redirects to storage that refuses a second credential.
    req = urllib.request.Request(a["browser_download_url"], headers={"User-Agent": UA, "Accept": "application/octet-stream"})
    try:
        with _open(req, opener, timeout=120) as r:
            body = r.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise CacheError(f"{asset}: download failed: {e.__class__.__name__}: {e}") from e
    if isinstance(a.get("size"), int) and len(body) != a["size"]:
        raise CacheError(f"{asset}: got {len(body)} bytes, the release lists {a['size']}: cut short")
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise CacheError(f"{asset}: not valid JSON ({e})") from e
    if not isinstance(doc, dict) or (schema and doc.get("schema") != schema):
        raise CacheError(f"{asset}: schema {doc.get('schema') if isinstance(doc, dict) else type(doc).__name__!r}, "
                         f"expected {schema!r}")
    return "ok", doc


def points(doc) -> dict:
    p = (doc or {}).get("points")
    if not isinstance(p, dict):
        raise CacheError("the cache has no points object")
    return p


def check(new_doc: dict, live_doc: dict | None) -> tuple[bool, str]:
    """(upload, why). Refuses (raises CacheError) when the new copy lacks a point the live one has, so a run can only
    add or rebuild points. No upload when the points are unchanged."""
    new = points(new_doc)
    if live_doc is None:
        return (bool(new), f"no live cache; {len(new)} points" if new else "no live cache and nothing built")
    live = points(live_doc)
    lost = sorted(set(live) - set(new))
    if lost or len(new) < len(live):
        raise CacheError(f"the new cache has {len(new)} points, the live one {len(live)}; missing {', '.join(lost[:8])}"
                         f"{' ...' if len(lost) > 8 else ''}")
    if json.dumps(new, sort_keys=True) == json.dumps(live, sort_keys=True):
        return False, f"unchanged ({len(new)} points)"
    return True, f"{len(new)} points (live {len(live)})"


def stamp(doc: dict) -> str:
    t = str(doc.get("updated_at") or "")
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        d = datetime.now(timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise CacheError(f"{path}: {e}") from e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "metacog-osint/vis"))
    g.add_argument("--tag", required=True)
    g.add_argument("--asset", required=True)
    g.add_argument("--schema", default=None)
    g.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--new", required=True)
    c.add_argument("--live", default=None, help="the copy downloaded at the start of the run; omit when there was none")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "get":
            state, doc = get(a.repo, a.tag, a.asset, a.schema, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
            print(f"state={state}")
            if doc is not None:
                os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
                tmp = a.out + ".tmp"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, a.out)
                print(f"points={len(points(doc))}")
            return 0
        new = _read(a.new)
        up, why = check(new, _read(a.live) if a.live else None)
        print(f"upload={'yes' if up else 'no'}\nstamp={stamp(new)}")
        print(why, file=sys.stderr)
        return 0
    except CacheError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
