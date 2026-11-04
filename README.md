# Vis

Operational weather and illumination for each of Watchfloor's conflict zones, from The Intelligence Company:
the likelihood, over the next seven days, that weather reaches levels that degrade operations at each zone's
reference airfield or port, and tonight's light. Planned at https://vis.theintelligence.company.

- Forecast: NOAA GEFS (31 members) and ECMWF IFS (51 members) ensembles through the Open-Meteo Ensemble API;
  the share of members reaching each threshold is the probability, with its ICD 203 term, on a likelihood-by-impact
  stoplight. Thresholds are indicative: JP 3-59 publishes no numeric platform limits.
- Illumination: sun and moon computed (Meeus low-precision formulas), checked against the US Naval Observatory.
- Method: `doctrine/method.md`. Run: `python tools/forecast.py`, then `python tools/build_site.py`.

Data: Open-Meteo (CC BY 4.0, free for non-commercial use), NOAA GEFS (public domain), ECMWF IFS ensemble (CC BY 4.0).
Code: GNU AGPL-3.0 (`LICENSE`). The vendored doctrine kit in `doctrine/kit/` keeps its own licence.
