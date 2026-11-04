# Vis — Operational weather & illumination (`vis.theintelligence.company`)

Vis answers two operational questions for each of Watchfloor's 16 conflict zones over the next 7 days:

1. **How likely is weather that degrades air, ISR, maritime or ground operations, and which hazard drives it?**
2. **When is it dark, and how much of the night has the moon down or under a quarter lit?**

## Reference stations (`config/points.json`)

Each conflict zone is anchored at a named operational airfield or port with an ICAO station identifier (for example, `UKBB` Kyiv Boryspil for Ukraine, `RCTP` Taipei Taoyuan for the Taiwan Strait, `OIKB` Bandar Abbas for Iran & Red Sea, `HSPN` Port Sudan for Sudan). Using an ICAO station rather than a geographic polygon centroid ensures:
- the forecast samples an actual operational hub rather than an uninhabited desert or mountain midpoint; and
- every forecast has an objective resolution source in Verdict (archived ICAO METAR / SYNOP observations).

## Multi-model ensemble (`tools/forecast.py`)

Vis queries two independent global ensemble systems through the Open-Meteo Ensemble API (CC BY 4.0):
- **NOAA NCEP GEFS (`ncep_gefs025`)**: 31 members at 0.25° resolution.
- **ECMWF IFS (`ecmwf_ifs025`)**: 51 members at 0.25° resolution.

Across all 82 members, each UTC day and hazard is scored by the fraction of available members that reach the **Amber** (moderate operational degradation) or **Red** (severe operational degradation) threshold. Transient hourly hazards (`wind`, `ceiling`, `vis`, `heat`, `cold`) require **at least 3 hours** in the UTC day (`2 hours` for `icing`) so a single passing shower or gust does not turn a 24-hour operational window red.

## Likelihood, impact matrix and confidence

- **Likelihood**: The raw ensemble fraction $p \in [0, 1]$ is stored as a number and mapped to its ICD 203 verbal probability term and percentage band via `tic-doctrine`.
- **Stoplight**: Following the UK Met Office National Severe Weather Warning Service likelihood-by-impact matrix:
  - **Red** (*Take action*): severe threshold is *Roughly even chance* or higher ($p_{\text{red}} \ge 0.45$).
  - **Amber** (*Be prepared*): moderate threshold is *Roughly even chance* or higher ($p_{\text{amber}} \ge 0.45$), or severe threshold is *Unlikely* or higher ($p_{\text{red}} \ge 0.20$).
  - **Yellow** (*Be aware*): moderate threshold is *Unlikely* or higher ($p_{\text{amber}} \ge 0.20$), or severe threshold is *Very unlikely* or higher ($p_{\text{red}} \ge 0.05$).
  - **Green** (*No significant impact*): below Yellow on every hazard.
- **Confidence**: Scored separately by the house confidence rubric (`tic_doctrine.confidence`). With two independent numerical weather prediction centres (NOAA GEFS and ECMWF IFS) agreeing, confidence is **Moderate**; per house doctrine, confidence cannot reach **High** until the ensemble's calibration is scored in **Verdict**.

## Night illumination (`tools/astro.py`)

Sun and moon positions are computed deterministically from Meeus (*Astronomical Algorithms*): sunset/sunrise, civil ($-6^\circ$), nautical ($-12^\circ$) and astronomical ($-18^\circ$) twilight, moonrise, moonset, lunar illuminated fraction at local midnight, and **low-light dark minutes** (sun below $-12^\circ$ with the moon either below the horizon or under 25% illuminated).
