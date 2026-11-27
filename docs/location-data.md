# Vis location data (`vis-location/1`)

*22 Nov 2026. The detail behind each forecast point's pop-up on the page, what COP can read of it, and where it is
kept.* Times are UTC throughout, written `YYYY-MM-DDTHH:MMZ` or with seconds; local calendar dates (`daily.date`,
`today.date`) are the point's own days, from its time zone.

## Where it is

| File | What | Schema |
|---|---|---|
| `https://vis.theintelligence.company/data/locations.json` | one compact row per point, for a list or a map | `vis-locations-index/1` |
| `https://vis.theintelligence.company/data/locations/<id>.json` | one point in full (about 40 KB) | `vis-location/1` |
| `https://vis.theintelligence.company/#loc-<id>` | the page with that point's pop-up open | |
| `feed.geojson`, each point item's `measures` | the few fields COP shows, and links to the two above | feed contract v2 |
| release `vis-data-YYYY-MM`, `<run>.locations.gen-<build>.json.gz` | every build's full location file, never overwritten | `vis-locations/1` |
| release `vis-climate`, `climate.json` | the climate cache (normals, records, Köppen), the live copy | `vis-climate/1` |
| release `vis-climate`, `climate.<YYYYMMDDTHHMMZ>.json` | a dated copy of each upload, never overwritten | `vis-climate/1` |

`<id>` is the point id in `config/points.json` (the same as the feed item's `measures`-bearing point and the
page's `#zone-<id>` row), for example `01_Ukraine_Conflict`.

## What is built, and from what

| Part | Source | Built by | How often |
|---|---|---|---|
| Hourly and daily forecast, 7 days back and 7 ahead | Open-Meteo Forecast API (best-match deterministic models), CC BY 4.0 | `tools/locations.py` | every build |
| Hazard colours and probabilities | Open-Meteo Ensemble API (GEFS, ECMWF IFS), as on the rest of the page | `tools/forecast.py` | every build |
| Latest METAR | NOAA Aviation Weather Center data API, public domain, through Vis's append-only store `history/metar` (the same reports the forecast scores use) | `tools/metar.py`, then `tools/locations.py` | every build |
| Current TAF | the same API, one call for every station | `tools/locations.py` (through `tools/metar.py`) | every build |
| Normals (1991–2020), records (1991 to last year: now 1991–2025), monthly normals, Köppen–Geiger class | ERA5 via Open-Meteo Historical API, CC BY 4.0 | `tools/climate.py` | a few points a day until all are filled, then once a year |
| Sunrise, sunset, solar noon, day length, moon phase, twilight | computed by Vis (Meeus), like the existing moon fields | `tools/astro.py` | every build |
| Station and model-cell elevation | Open-Meteo Elevation API (Copernicus DEM GLO-90), from the grid check | `tools/forecast.py` | every build |

The deterministic forecast is context only: the hazard colours, the feed's Amber and Red warnings and everything COP
treats as a forecast still come from the ensembles. The charts show the deterministic line with the ensemble's day
colours above it.

Normals and records are ERA5's 0.25° grid cell, not the station's own record, so a coastal or mountain station can
differ from them by a few degrees. Records are "highest/lowest in ERA5 since 1991" (1991–2025), with the year.

**Things that age are read against the viewer's clock.** The METAR is the newest report in the store within 48 hours
before the build; the file gives its `obs_time` and no age, and the page works the age out when it is drawn, marks the
report old past 90 minutes and then hides its flight category. The TAF is left out at build time once its
`valid_to` has passed, and the page checks `valid_to` again when it is drawn, so a TAF that lapses between builds is
not shown either; a shown TAF carries its issue time and validity in the viewer's zone. A reader of these files
should do the same.

Not yet built: **biome**. RESOLVE Ecoregions 2017 (CC BY 4.0, Dinerstein et al. 2017) fits, but no stable direct
download was found to build it from; `site.biome` is `null` until then and the pop-up says "not yet available".

## `vis-location/1`

```
schema, id, zone_id, role ("reference" or "alternate"), name, station, icao, countries[], lat, lon,
generated_at, page
site:   station_elevation_m, ensemble_cell_elevation_m, forecast_cell_elevation_m, cell ("land" | "sea"),
        timezone, timezone_abbreviation, utc_offset_seconds, koppen, koppen_name, normals_period, records_period,
        monthly_normals {tmean_c[12], tmax_c[12], tmin_c[12], prcp_mm[12], annual_tmean_c, annual_prcp_mm}, biome (null)
now:    metar {raw, obs_time, type, temp_c, dewpoint_c, wind_dir_deg (degrees or "VRB"), wind_kt, gust_kt,
               visibility (statute miles, as AWC gives it), altimeter_hpa, weather, clouds[{cover, base_ft}] | null,
               ceiling_ft, flight_category} | null   (fields AWC did not give, or reports stored before
               26 Nov 2026 lack, are null)
        taf {raw, issued, valid_from, valid_to} | null   (UTC; null when there is none or its validity had ended)
        model {time, temperature_2m, relative_humidity_2m, apparent_temperature, weather_code, cloud_cover,
               wind_speed_10m, wind_direction_10m, wind_gusts_10m, pressure_msl, precipitation} | null
today:  date (local), tmax_c, tmin_c, normal_tmax_c, normal_tmin_c, record_tmax_c, record_tmax_year,
        record_tmin_c, record_tmin_year, record_prcp_mm, record_prcp_year,
        sun {day, sunrise, sunset, solar_noon, noon_altitude_deg, day_length_min, polar},
        night {... the page's night fields, moon_phase, moon_waxing}
ensemble_days[]: day, colour, hours, hazards {<hazard>: {colour, p_amber, p_red, median, p90}}, sun, night
daily:  column arrays by local date: date[], weather_code[], temperature_2m_max[], temperature_2m_min[],
        precipitation_sum[], precipitation_probability_max[], wind_speed_10m_max[], wind_gusts_10m_max[],
        wind_direction_10m_dominant[], normal_tmax_c[], normal_tmin_c[], normal_prcp_mm[], record_tmax_c[],
        record_tmax_year[], record_tmin_c[], record_tmin_year[], record_prcp_mm[], record_prcp_year[]
hourly: column arrays: time[] (UTC) and the hourly variables in `units`
trends: past_7_days and next_7_days, each {days, from, to, tmean_c, normal_tmean_c, tmean_anomaly_c, prcp_mm,
        normal_prcp_mm, above_record_high[], below_record_low[], basis}. The past days are the models' own
        analysis, not observations.
units:  {variable: unit}; wind in km/h from Open-Meteo, knots in the METAR as reported
errors: [] or one line per source that failed this build ("climate: not built yet ...")
sources: [{id, name, url, licence, attribution, disclaimer?}]; the ERA5 attribution carries the Copernicus notice
        with its year (the year the climate cache was last built), and its disclaimer the notice that neither the
        European Commission nor ECMWF is responsible for any use made of the information
```

`polar` is `null`, `"midnight_sun"` or `"polar_night"`; then `sunrise` and `sunset` are `null`. A source that fails
leaves its fields `null` and adds a line to `errors`; the file is still written, so a reader should check `errors`
rather than assume a `null` means "none".

## What COP gets

COP reads `feed.geojson` server-side and keeps every item's `measures`. Each point item now also carries:

| Measure | Meaning |
|---|---|
| `moon_phase` | the phase name tonight (beside the existing `moon_lit` and `dark_low_light_min`) |
| `sunrise`, `sunset` | UTC, for the point's local day 0; `null` in polar day or night |
| `day_length_min` | minutes between them |
| `station_elevation_m` | from the grid check |
| `detail_url` | `data/locations/<id>.json` (this document's schema) |
| `detail_page` | the page with the pop-up open |

COP needs no change to keep these. Showing the charts or the full file inside COP would need COP to fetch
`detail_url` (or `data/locations.json` for all points at once), which is a change to the COP repo, not made here.
The feed's `link` still opens the point's row; the page also opens the pop-up from `#loc-<id>`.

## Climate cache

`climate.yml` runs once a day. `tools/release_cache.py get` reads the release copy and tells three cases apart: no
release or asset yet (the run starts a new cache with `climate.py --fresh`), a whole copy (its size matches the
release listing and it parses as `vis-climate/1`), and anything else, which fails the run with the live copy
untouched. `climate.py` itself refuses a missing or unreadable cache file unless `--fresh` is given. Before the
upload, `tools/release_cache.py check` refuses a cache that lacks any point the live copy has, skips the upload when
nothing changed, and otherwise the run uploads a dated copy (`climate.<UTC stamp>.json`, never overwritten) and then
replaces `climate.json`.

## History

Every build's `locations.json` is archived beside its `forecast.json` in the monthly release (`tools/archive.py`
`--extra`). Those assets, and the METAR store `history/metar` in the repository, are the record. `tools/history.py`
loads them into one SQLite file (`out/history.sqlite`, git-ignored), which can be deleted and rebuilt at any time:

```
python tools/history.py build --releases     # pulls every vis-data-* asset not yet loaded (public GitHub API)
python tools/history.py build --from DIR     # or a folder of the .json.gz files (every build also reads history/metar)
python tools/history.py stats
```

Tables: `builds`, `ensemble` and `ensemble_day` (every build's probabilities and colours, with lead days in UTC
days), `metar` (every report in `history/metar`, one row per station, time and raw text), `taf` (every TAF a build
showed), `point_daily` (every build's daily rows, past days included, with lead days counted on the point's own local
calendar) and the view `daily_latest` (the newest build's row for each date: the best estimate of what happened).
Loading the same asset or report twice changes nothing. The forecasts' scores against METARs are
`tools/verify.py`'s (`doctrine/method.md`, "Verification"); this database is for queries beside them, such as lead
times and the pop-up's trends.

For COP's replay (`cop-layer-history/1` in the COP repo), a dataset can be exported from this database with a
manifest; that export is not written yet.
