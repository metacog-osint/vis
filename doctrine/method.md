# Vis — Operational weather & illumination (`vis.theintelligence.company`)

Vis answers two operational questions for each of Watchfloor's 16 conflict zones over the next 7 days:

1. **How likely is weather that degrades air, ISR, maritime or ground operations, and which hazard drives it?**
2. **When is it dark, and how much of the night has the moon down or under a quarter lit?**

## Reference stations (`config/points.json`)

Each conflict zone is anchored at a named operational airfield or port with an ICAO station identifier (for example, `UKBB` Kyiv Boryspil for Ukraine, `RCTP` Taipei Taoyuan for the Taiwan Strait, `OIKB` Bandar Abbas for Iran & Red Sea, `HSPN` Port Sudan for Sudan). Using an ICAO station rather than a geographic polygon centroid means:
- the forecast samples an actual operational hub rather than an uninhabited desert or mountain midpoint; and
- every forecast can be checked against the station's METAR reports (see "Verification" below).

Since 8 Nov 2026 (Tom's decision) each zone's alternates are forecast too, as rows of their own: 16 reference points and 15 alternates, 31 in all. An alternate's row id is `<zone id>-<ICAO>`.

## Multi-model ensemble (`tools/forecast.py`)

Vis queries two independent global ensemble systems through the Open-Meteo Ensemble API (CC BY 4.0):
- **NOAA NCEP GEFS (`ncep_gefs025`)**: 31 members at 0.25° resolution.
- **ECMWF IFS (`ecmwf_ifs025`)**: 51 members at 0.25° resolution.

Across the members of every model whose run is current (all 82 when both are; a model whose newest run on Open-Meteo is more than 24 hours old, read from Open-Meteo's `static/meta.json`, is left out and named in the feed and on the page), each UTC day and hazard is scored by the share of members that reach the **Amber** (moderate operational degradation) or **Red** (severe operational degradation) threshold, **each model weighted equally** (the mean of the two models' own shares, each kept beside it as `by_model`; since `vis-ensemble-1.1`, 16 Nov 2026, Tom's yes: pooled by count, ECMWF's 51 members had carried 62% of the weight). Both ensembles are **3-hourly** on Open-Meteo (`temporal_resolution_seconds` 10800); the hours between steps are interpolated. So the transient hazards (`wind`, `lowcloud`, `vis`, `heat`, `cold`, `icing`) must hold on **two consecutive 3-hourly model steps** (00, 03, ... 21 UTC) in the UTC day; until 1.1 the rule counted 3 interpolated hours (2 for icing), which one strong step could meet on its own.

**Grid cells (1.1).** Each point names the Open-Meteo cell to read (`cell` in `config/points.json`: land by default, sea for Caracas SVMI and Beirut OLBA, whose land cells sat 879 m and 333 m above the runway on 16 Nov 2026), and every run compares the elevation of the cell's centre with the station's (Open-Meteo's elevation API, one call; `grid` on each point). **Amber or Red goes to COP as a warning only when two models supply the driving hazard and the cell is within 300 m of the station;** otherwise the item is routine and its summary says why. On 16 Nov 2026, 30 of 31 points passed; Asmara (HHAS) sits 446 m above its cell on land or sea, so its warnings are held. Visibility (GEFS only) never makes a warning. The first day counts only the hours from the current UTC hour on, so hours already past never raise a forecast. Confidence counts as independent lines only the models that supply the driving hazard (visibility comes from GEFS alone, low cloud from ECMWF alone).

Two hazards use one model only, because each is published by only one ensemble on Open-Meteo: visibility (GEFS, 31 members) and low cloud cover (ECMWF, 51 members).

**Low cloud cover shows at most Yellow** (Tom, 8 Nov 2026). It is the cover of the lowest 3 km (`cloud_cover_low`), not ceiling height, and neither ensemble on Open-Meteo carries cloud base (checked 8 Nov 2026). Before the cap, low cloud drove all 31 Amber and Red zone-days in the 8 Nov 17Z run, and Kyiv read Red all week. A true ceiling needs another source.

The thresholds are one generic, indicative set (Tom, 8 Nov 2026). Platform profiles (rotary-wing, small UAS, maritime with sea state) and a heat-stress measure (WBGT) are later work. A thunderstorm hazard (CAPE with rain in the same hour; both ensembles carry CAPE) comes back only after a week of test runs.

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

## Verification (plan step W3, method `vis-verify/1`, 24 Nov 2026)

Every archived forecast (release `vis-data-YYYY-MM`, kept since 19 Nov 2026; nothing earlier was stored) is checked
against the station's METARs once its UTC day has ended plus 6 hours (`tools/verify.py`).

- **Observations:** `tools/metar.py` stores every report from the NOAA Aviation Weather Center data API (US public
  domain) in `history/metar/YYYY/MM/DD.jsonl`, append-only. Each build asks for the last 200 hours, one call per
  station. 11 of the 31 stations returned no reports on 24 Nov 2026 (UKBB, UKOO, OLBA, OIKB, OYHD, HSSS, HSPN, HHAS,
  HDAM, FZNA, ZKPY); their forecasts are counted as unresolved, never filled.
- **Scored hazards:** wind (the larger of mean wind and gust), visibility (the report's metric group when it has one,
  else AWC's statute miles), heat and cold (2 m temperature). Each 3-hourly model step in the forecast's window takes
  the nearest report within 60 minutes, and the hazard counts as observed when the forecast's own test holds on two
  consecutive steps. "Not observed" needs reports at 75% of the steps and no hit beside a gap; anything else is stored
  as unresolved with its reason.
- **Not scored:** heavy rain and snow (24 h totals most of these stations do not report), low cloud cover (a model
  cloud fraction, not a ceiling) and icing (needs a precipitation rate).
- **Stores:** `history/verify/observed.jsonl` (what the station showed, once per point, UTC day and window start) and
  `history/verify/pairs.jsonl` (each forecast's amber and red probabilities for that point and day). Both are
  append-only.
- **Scores** (`site/data/verification.json`, the page's "Measured skill" card): Brier score, skill against the
  sample's own observed frequency, and reliability in the ICD 203 likelihood bands, by hazard, threshold and lead day,
  with one forecast per model run. A row is marked too few to read unless it has at least 100 forecasts and 10
  observed events at 3 or more points.
- **First read (24 Nov 2026, 19 to 23 Nov):** 5,929 forecasts resolved. No wind, heat or cold event was observed. Poor
  visibility (3 km) was observed 39 times, all at Srinagar (VISR), on days the forecast gave almost no chance, while
  none of the 20 forecasts at "Likely" or above verified. That is one station and one week, too small to read, but
  it agrees with the known weakness of GEFS's diagnosed visibility.
