# Vis

Operational weather and illumination for each of Watchfloor's conflict zones, from The Intelligence Company:
the likelihood, over the next seven days, that weather reaches levels that degrade operations at each zone's
reference airfield or port, and tonight's light. Planned at https://vis.theintelligence.company.

- Forecast: NOAA GEFS (31 members) and ECMWF IFS (51 members) ensembles through the Open-Meteo Ensemble API;
  the share of members reaching each threshold is the probability, with its ICD 203 term, on a likelihood-by-impact
  stoplight. Thresholds are indicative: JP 3-59 publishes no numeric platform limits.
- Illumination: sun and moon computed (Meeus low-precision formulas), checked against the US Naval Observatory.
- Location detail (pop-up per point): hourly charts, METAR and TAF, normals, records and climate class, sunrise and
  sunset; built by `tools/locations.py` and `tools/climate.py`, kept per build and loadable into SQLite with
  `tools/history.py`. Contract: `docs/location-data.md`.
- Method: `doctrine/method.md`. Run: `python tools/forecast.py`, then `python tools/build_site.py`.

- Weather map: live geostationary imagery (infrared, water vapour, air mass, GeoColor, dust, fog), IMERG
  precipitation, daily aerosol (smoke and haze), radar for the US and Canada, Meteosat rain rate, lightning and
  thunderstorm cells, drawn in the browser from NASA GIBS, EUMETSAT EUMETView, NOAA MRMS and ECCC GeoMet tiles
  (layers resolved each build by `tools/wxmap.py`); plus the GFS jet stream and snow depth (`tools/gfs.py`) and North
  American smoke and US hail reports (`tools/wxobs.py`). The map loads when it is opened, not with the page.

Data: Open-Meteo (CC BY 4.0, free for non-commercial use), NOAA GEFS (public domain), ECMWF IFS ensemble (CC BY 4.0),
ERA5 (contains modified Copernicus Climate Change Service information), NOAA Aviation Weather Center (public domain).
Map imagery through NASA GIBS: NOAA GOES (public domain), JMA Himawari (JMA Public Data License 1.0, source cited),
NASA GPM IMERG and VIIRS aerosol (NASA data, CC0). EUMETSAT EUMETView (CC BY 4.0). NOAA MRMS, GFS, SPC and HMS
(public domain). Environment and Climate Change Canada (Data Server End-use Licence). Outlines: Natural Earth (public
domain).
Code: GNU AGPL-3.0 (`LICENSE`). Shared and third-party files keep their own licences, listed in `NOTICE`.

## AI assistance

This repository is built with AI coding assistance (Anthropic's Claude Code). The Intelligence Company is responsible for everything published here.
