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

Across the members of every model whose run is current (all 82 when both are; a model whose newest run on Open-Meteo is more than 24 hours old, read from Open-Meteo's `static/meta.json`, is left out and named in the feed and on the page), each UTC day and hazard is scored by the share of members that reach the **Amber** (moderate operational degradation) or **Red** (severe operational degradation) threshold, **each model weighted equally** (the mean of the two models' own shares, each kept beside it as `by_model`; since `vis-ensemble-1.1`, 16 Nov 2026: pooled by count, ECMWF's 51 members had carried 62% of the weight). Both ensembles are **3-hourly** on Open-Meteo (`temporal_resolution_seconds` 10800); the hours between steps are interpolated. So the transient hazards (`wind`, `lowcloud`, `vis`, `heat`, `cold`, `icing`) must hold on **two consecutive 3-hourly model steps** (00, 03, ... 21 UTC) in the UTC day (thunderstorms, since 1.2, on one step: see "Thunderstorms"); until 1.1 the rule counted 3 interpolated hours (2 for icing), which one strong step could meet on its own.

**Grid cells (1.1).** Each point names the Open-Meteo cell to read (`cell` in `config/points.json`: land by default, sea for Caracas SVMI and Beirut OLBA, whose land cells sat 879 m and 333 m above the runway on 16 Nov 2026), and every run compares the elevation of the cell's centre with the station's (Open-Meteo's elevation API, one call; `grid` on each point). **Amber or Red goes to COP as a warning only when two models supply the driving hazard and the cell is within 300 m of the station;** otherwise the item is routine and its summary says why. On 16 Nov 2026, 30 of 31 points passed; Asmara (HHAS) sits 446 m above its cell on land or sea, so its warnings are held. Visibility (GEFS only) never makes a warning. The first day counts only the hours from the current UTC hour on, so hours already past never raise a forecast. Confidence counts as independent lines only the models that supply the driving hazard (visibility comes from GEFS alone, low cloud from ECMWF alone).

Two hazards use one model only, because each is published by only one ensemble on Open-Meteo: visibility (GEFS, 31 members) and low cloud cover (ECMWF, 51 members).

**Low cloud cover shows at most Yellow** (Tom, 8 Nov 2026). It is the cover of the lowest 3 km (`cloud_cover_low`), not ceiling height, and neither ensemble on Open-Meteo carries cloud base (checked 8 Nov 2026). Before the cap, low cloud drove all 31 Amber and Red zone-days in the 8 Nov 17Z run, and Kyiv read Red all week. A true ceiling needs another source.

The thresholds are one generic, indicative set (8 Nov 2026). Platform profiles (rotary-wing, small UAS, maritime with sea state) and a heat-stress measure (WBGT) are later work. Thunderstorms came back in `vis-ensemble-1.2` (25 Nov 2026) on trial, shown at most Yellow: see "Thunderstorms" below.

## Thunderstorms (method `vis-ensemble-1.2`, 25 Nov 2026, on trial)

Both ensembles carry CAPE; the hazard was held back at launch until it had been tested on a week of
runs. The hazard is back in the forecast, the feed and the table, and it is scored against METARs, but **it shows at
most Yellow while on trial** (`max_colour` and `trial` in `tools/forecast.py`, as low cloud cover is capped), so it
cannot send an Amber or Red warning to COP until its evidence has been reviewed. A capped cell keeps its uncapped colour in
`capped_from`, the page says "on trial: amber without the cap", and the feed's summary says so too.

- **Rule (indicative thresholds):** a member reaches Amber when CAPE (convective available potential energy) is at
  least **1,000 J/kg** and precipitation at least **1 mm/h** at the same 3-hourly model step, and Red at **2,500 J/kg**
  with **2.5 mm/h**: the prototype's lines (31 Oct 2026). CAPE alone was Amber across the tropics every day in the
  1 Nov prototype run (plan, "What the first run showed", 2), so rain is required in the same step. JP 3-59 gives no
  numeric limit; these are common forecasting lines for moderate and strong instability, not platform limits.
- **Persistence: one model step**, not two. The other transient hazards need two consecutive steps so that one
  interpolated value cannot colour a day, but two steps would ask for six hours of storm weather at the point. In the
  METAR store (17 to 24 Nov 2026) every run of thunderstorm reports lasted 30 to 150 minutes from first to last
  report (8 runs, median 75 minutes). One native step is still a model value, never an interpolated hour between steps.
- **What it means:** the chance that the model's 25 km cell has storm weather, not that a storm crosses the runway.
  A single storm is smaller than the grid.

### The week's evidence (25 Nov 2026)

**Observations (METAR store, 17 to 24 Nov 2026, eight full UTC days, the `vis-verify/2` rule below).** 17 of the 31
stations resolved at least one day; 131 of 248 station-days resolved (the rest had no reports or too few, such as
Mogadishu HCMM and Mandalay VYMD, which report by day only, and the 11 stations AWC holds no reports for). A
thunderstorm was reported on **7 resolved station-days (5%) at 2 stations**: Kigali (HRYR, the DRC zone's alternate) on
6 of 8 days, and Incheon (RKSI) on 23 Nov (TSRA from 06:00 to 06:30 UTC). None was a strong one (+TS, hail, or a gust of
65 km/h or more). Cumulonimbus cloud without thunder was reported on 24 more resolved station-days at 7 stations,
mostly winter shower cloud over the Baltic (Vilnius, Kaliningrad) and dry-season cloud in Myanmar; had CB counted,
Kaliningrad would have "had thunderstorms" on all five of its resolved days. That is why CB alone does not count.

**Forecasts: not yet measured.** The archive (`vis-data-YYYY-MM`, since 19 Nov) holds each run's hazard
probabilities, not CAPE, so the past week cannot be re-scored from it. `tools/tstorm_trial.py` does the measurement:
`fetch` reads both ensembles' CAPE and precipitation for the past days from Open-Meteo (`past_days`, so a short-lead
hindcast, about one forecast run's worth of the free allowance), and `measure` gives, for CAPE 500, 1,000, 1,500 and
2,500 J/kg, rain 0.5, 1 and 2.5 mm/h and one or two steps, the share of point-days that would have shown Yellow or
Amber, and Brier, hits, misses and false alarms against the METAR outcomes above. It was written and tested offline
on 25 Nov 2026 and **has not yet been run against Open-Meteo**; its table goes here when it has. From the first
build with method 1.2, every run's thunderstorm probabilities are archived and scored by `vis-verify/2`, so the week
of runs the trial needs is also the first seven days of that archive, at every lead time.

**Reading it.** A week of late November is the dry or cool season at most points, so it tests false alarms more than
hits: of the 17 stations that report, only Kigali sits in a wet season now. Thresholds and the one-step rule are
kept as the prototype had them until the trial's table and the first week of scores say otherwise; the cap comes off
only once that evidence has been reviewed.

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

## Verification (method `vis-verify/2`, 25 Nov 2026; `vis-verify/1`, 24 Nov 2026, had no thunderstorms)

Every archived forecast (release `vis-data-YYYY-MM`, kept since 19 Nov 2026; nothing earlier was stored) is checked
against the station's METARs once its UTC day has ended plus 6 hours (`tools/verify.py`).

- **Observations:** `tools/metar.py` stores every report from the NOAA Aviation Weather Center data API (US public
  domain) in `history/metar/YYYY/MM/DD.jsonl`, append-only. Each build asks for the last 200 hours, one call per
  station. 11 of the 31 stations returned no reports on 24 Nov 2026 (UKBB, UKOO, OLBA, OIKB, OYHD, HSSS, HSPN, HHAS,
  HDAM, FZNA, ZKPY); their forecasts are counted as unresolved, never filled.
- **Scored hazards:** wind (the larger of mean wind and gust), visibility (the report's metric group when it has one,
  else AWC's statute miles), heat and cold (2 m temperature), and since `vis-verify/2` thunderstorms (below). Each
  3-hourly model step in the forecast's window takes the nearest report within 60 minutes, and the hazard counts as
  observed when the forecast's own test holds on two consecutive steps. "Not observed" needs reports at 75% of the steps and no hit beside a gap; anything else is stored
  as unresolved with its reason.
- **Thunderstorms (since `vis-verify/2`):** resolved on what the reports say, not on a measured value. The amber
  level is **observed** when any METAR or SPECI timed from the window's first 3-hourly step to the end of the UTC day
  has, after the station and time groups and before the remarks and any trend group (TEMPO, BECMG, NOSIG, PROBnn, FM):
  TS in present weather alone or with precipitation and with any intensity (`TS`, `-TSRA`, `+TSRA`, `TSGR` ...),
  `VCTS` (in the vicinity, within about 16 km), or `RETS` as recent weather (a thunderstorm in the past hour). The red
  level needs a strong one: heavy (`+TS...`), hail with thunder (TS with GR), or a gust of 65 km/h or more in a report
  with thunder (the wind hazard's amber line). Cumulonimbus cloud (`CB` groups) and lightning remarks alone do not
  count, and a report whose remarks say `TSNO` (thunderstorm sensor off) counts as missing. One such report is a yes.
  **Not observed** needs a report within 60 minutes of at least 75% of the window's 3-hourly steps; otherwise the
  forecast is unresolved with its reason. A missing report is never a no: a station that reports only by day leaves
  every day unresolved.
- **Supplements:** a window observed before thunderstorms were scored gets one more line in `observed.jsonl` holding
  only the new hazard (`"supplement": true`); the stored line is never rewritten, and a hazard keeps its first line.
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
