"""Back-test observations: METARs from the Iowa Environmental Mesonet (IEM) archive for a spread of stations.

    python -I fetch_obs.py <out dir>
Writes stations.json and metar.csv (station, valid UTC, lon, lat, elevation, raw METAR) for 17-27 Nov 2026.
"""
import csv
import io
import json
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Vis-backtest/1.0 (+https://vis.theintelligence.company)"
# network: region. Regions follow Vis's zones, plus two mid-latitude controls with dense reporting.
NETWORKS = {
    "LT__ASOS": "Baltics-Poland", "LV__ASOS": "Baltics-Poland", "EE__ASOS": "Baltics-Poland",
    "PL__ASOS": "Baltics-Poland", "MD__ASOS": "Black Sea-Balkans", "RO__ASOS": "Black Sea-Balkans",
    "BG__ASOS": "Black Sea-Balkans", "RS__ASOS": "Black Sea-Balkans", "HU__ASOS": "Black Sea-Balkans",
    "TR__ASOS": "Black Sea-Balkans",
    "DE__ASOS": "Western Europe", "FR__ASOS": "Western Europe", "GB__ASOS": "Western Europe",
    "NL__ASOS": "Western Europe",
    "IL__ASOS": "Middle East", "JO__ASOS": "Middle East", "LB__ASOS": "Middle East", "SA__ASOS": "Middle East",
    "AE__ASOS": "Middle East", "IR__ASOS": "Middle East", "KW__ASOS": "Middle East", "OM__ASOS": "Middle East",
    "IN__ASOS": "South Asia", "PK__ASOS": "South Asia", "BD__ASOS": "South Asia", "NP__ASOS": "South Asia",
    "TW__ASOS": "East Asia", "KR__ASOS": "East Asia", "JP__ASOS": "East Asia", "CN__ASOS": "East Asia",
    "PH__ASOS": "Southeast Asia", "TH__ASOS": "Southeast Asia", "MM__ASOS": "Southeast Asia",
    "VN__ASOS": "Southeast Asia",
    "ET__ASOS": "East Africa", "KE__ASOS": "East Africa", "RW__ASOS": "East Africa", "UG__ASOS": "East Africa",
    "TZ__ASOS": "East Africa", "DJ__ASOS": "East Africa", "SD__ASOS": "East Africa", "CD__ASOS": "East Africa",
    "NE__ASOS": "West Africa", "ML__ASOS": "West Africa", "SN__ASOS": "West Africa", "NG__ASOS": "West Africa",
    "GH__ASOS": "West Africa", "BF__ASOS": "West Africa",
    "VE__ASOS": "Caribbean-N South America", "CO__ASOS": "Caribbean-N South America",
    "CU__ASOS": "Caribbean-N South America", "DO__ASOS": "Caribbean-N South America",
    "PA__ASOS": "Caribbean-N South America",
    "CA_ON_ASOS": "North America", "CA_QC_ASOS": "North America", "WA_ASOS": "North America",
    "OR_ASOS": "North America", "PA_ASOS": "North America", "NY_ASOS": "North America",
}
PER_NETWORK = 12


def get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                raise
            print("retry", url[:80], e, flush=True)
            time.sleep(10)


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(20261127)
    stations = []
    for net, region in NETWORKS.items():
        try:
            gj = json.loads(get(f"https://mesonet.agron.iastate.edu/geojson/network/{net}.geojson"))
        except Exception as e:  # noqa: BLE001
            print("no network", net, e, flush=True)
            continue
        cand = [f for f in gj["features"] if f["properties"].get("online") and len(f["id"]) == 4
                and f["id"].isalpha() and f["properties"].get("elevation") is not None]
        rnd.shuffle(cand)
        for f in cand[:PER_NETWORK]:
            lon, lat = f["geometry"]["coordinates"]
            stations.append({"id": f["id"], "lat": lat, "lon": lon, "elev": f["properties"]["elevation"],
                             "network": net, "region": region, "name": f["properties"].get("sname")})
        print(net, len(cand), "online;", min(len(cand), PER_NETWORK), "taken", flush=True)
        time.sleep(1)
    (out / "stations.json").write_text(json.dumps(stations, indent=1), encoding="utf-8")
    rows = []
    for k in range(0, len(stations), 30):
        batch = stations[k:k + 30]
        q = [("station", s["id"]) for s in batch] + [
            ("data", "metar"), ("year1", "2026"), ("month1", "11"), ("day1", "16"), ("hour1", "23"),
            ("year2", "2026"), ("month2", "11"), ("day2", "27"), ("hour2", "1"), ("tz", "Etc/UTC"),
            ("format", "onlycomma"), ("latlon", "yes"), ("elev", "yes"), ("missing", "empty"),
            ("report_type", "3"), ("report_type", "4")]
        txt = get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(q)).decode("utf-8", "replace")
        rd = list(csv.reader(io.StringIO(txt)))
        rows.extend(rd[1:])
        print("batch", k, len(rd) - 1, "rows", flush=True)
        time.sleep(3)
    with open(out / "metar.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["station", "valid", "lon", "lat", "elevation", "metar"])
        w.writerows(rows)
    print("stations", len(stations), "reports", len(rows))


if __name__ == "__main__":
    main(sys.argv[1])
