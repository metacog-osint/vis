"""Small HTTP helper for Vis's build tools (22 Nov 2026): GET a JSON document with Vis's User-Agent, waiting and
trying again on HTTP 429 or a dropped connection, the same way forecast.fetch_model does. Standard library only."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Vis/1.0 (+https://vis.theintelligence.company)"


class Refused(Exception):
    """The server refused the call (HTTP 429) on every try."""


def get_json(url, params=None, timeout=60, tries=3, wait=time.sleep, wait_s=65, opener=None, empty_ok=False):
    """GET url?params and parse JSON. HTTP 429 and network failures wait `wait_s` seconds and try again; after the
    last try a 429 raises Refused and a network failure re-raises. HTTP 204 (no content) returns None when
    `empty_ok`, since the Aviation Weather Center answers 204 when a valid query has no reports."""
    opener = opener or urllib.request.urlopen
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(url + q, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(tries):
        try:
            with opener(req, timeout=timeout) as r:
                if getattr(r, "status", 200) == 204:
                    return None
                body = r.read()
            if not body.strip() and empty_ok:
                return None
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            if attempt == tries - 1:
                raise Refused(f"{url}: HTTP 429 on {tries} tries") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        wait(wait_s)
    return None
