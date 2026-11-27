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

**Low ceiling (trial, from 27 Nov 2026; `tools/gefs.py`, `rh_ceiling_series`).** Neither ensemble on Open-Meteo carries
cloud base, so the trial reads NOAA's own GEFS files on AWS Open Data (`noaa-gefs-pds`, 0.5-degree `pgrb2bp5`, 31
members, 3-hourly; US public domain). Each build reads the newest 00Z or 12Z run, at most 24 hours old, whose files reach
the end of the forecast for every member, and fetches only the records it needs through each file's `.idx` byte ranges:
relative humidity (RH) at the four lowest model (hybrid) levels for every member, and the control member's heights of
those levels and its ground (`HGT:surface`). The records are GRIB2 template 5.3, decoded with the standard library (the
decoder matched ecCodes 2.50.1 exactly at every point of real 0.5-degree records, 27 Nov 2026).

The low-cloud base is the lowest of those four levels, about 22, 71, 126 and 190 m above the model's own ground (median
of 11,933 reads, 17-26 Nov 2026), where RH is at least 95%; with none, there is no ceiling. Heights are above the
model's ground, not above the station. Each point reads the one of its four surrounding 0.5-degree cells whose ground is
nearest the station's height (Open-Meteo's elevation for the station, one call), and a point whose best cell is more than
100 m off is left out (on 27 Nov 2026 Srinagar VISR, +548 m; Asmara HHAS, -443 m; Goma FZNA, +141 m). This is not the
METAR or ICAO ceiling (the lowest layer covering more than half the sky). Levels above about 200 m are not read, so in
practice Amber means near-saturated air within about 200 m of the ground and Red within about 130 m: a base between 200
and 305 m counts only when the air below it is also near saturation, and a deck over drier air is missed.

Thresholds, indicative and not for flight planning: Amber at or below 305 m (a reported 1,000 ft ceiling counts; IFR is
below 1,000 ft) and Red at or below 152 m (a reported 500 ft ceiling, 152.4 m, does not count, so 400 ft or lower; LIFR
is below 500 ft) on two consecutive 3-hourly steps. During the trial it shows at most Yellow; it comes from one model,
so it never makes a COP warning. The trial is read from its Measured skill rows: if they score at least as well as the
sample's own climatology, the cap lifts and the ceiling replaces low cloud cover. A failure to read the files leaves
the ceiling out of that build and never stops the forecast; the page and the feed name the run used.

**Back-test (17-26 Nov 2026).** GEFS control member, 0.5 degree, 20 runs (00Z and 12Z) at 3 and 9 h leads, so 40 valid
times; METARs from the Iowa Environmental Mesonet archive at 353 stations in 11 regions, the report nearest each valid
time within 30 minutes, ceiling read by Vis's own parser (`tools/verify.py`). The RH threshold (90, 95 or 98%) was
chosen on 17-21 Nov by equitable threat score and held on 22-26 Nov. All 40 times, stations whose best cell is within
100 m (10,626 pairs at 316 stations):

| Ceiling at or below | METAR events | RH 95% at the lowest levels: hit rate / false alarm ratio / bias | GEFS ceiling field, same cells: hit rate / false alarm ratio / bias |
|---|---|---|---|
| 305 m (Amber) | 582 | 0.46 / 0.52 / 0.95 | 0.16 / 0.64 / 0.43 |
| 152 m (Red) | 198 | 0.40 / 0.81 / 2.1 | 0.06 / 0.89 / 0.54 |

Of the 305 m events where the model's surface CAPE was 0, the GEFS ceiling field caught none (0 of 316); the RH
reading caught 50%. Most events were in Europe (Baltics and Poland, Western Europe, Black Sea and Balkans) and
North America; the Middle East had none and every other region fewer than 15, too few to read there. One model run per
time and short leads only: the trial itself scores every member and lead.

**Why not the GEFS ceiling field (`HGT:cloud ceiling`), which the trial read on 27 Nov 2026.**
- **The field is set only where the air is at least slightly unstable.** UPP's `CALCEILING` (AVIATION.f) keeps the
  height only where `TCLD >= 50`, meant as a column cloud fraction of at least 50%. In the UPP that GEFS v12 runs
  (tags `gefs_v12.3.0` to `gefs_v12.3.22`; `CLDRAD.f` and `AVIATION.f` are the same in all of them), nothing on the
  GFS path writes a cloud fraction into `TCLD`: it holds the surface-based CAPE (convective available potential
  energy, a measure of how unstable the air near the ground is) times 100, left over from the `CALCAPE` call. So in
  practice the ceiling is set only where surface CAPE is at least about 0.5 J/kg. In six operational GEFS fields
  (all 1,038,240 points each), under 100% total cloud 3.8-4.4% of points with CAPE 0 carried a ceiling, against
  97.5-98.1% with CAPE of 1 J/kg or more. Stratus, fog and drizzle under an inversion have no surface CAPE, so they
  read as "no ceiling" even when GEFS's own low-cloud field reads 90-100%. Where CAPE is present, the field can find a
  real low base, but it also gives a ceiling at 44-49% of points with under 10% total cloud. NOAA's development code
  has since fixed the gate for the deterministic GFS; the GEFS v12 branch keeps it. Sources: UPP at the GEFS tag
  (https://github.com/NOAA-EMC/UPP/blob/gefs_v12.3.22/sorc/ncep_post.fd/CLDRAD.f and `AVIATION.f`), NOAA notice
  SCN 26-57 (GEFS v12.3.20, 15 Jun 2026).
- **At some points the model's ground sits well above the station.** The field cannot sit lower than about 17 m above
  GEFS's own ground (0.1% of 2.6 million values sat lower), and Vis took heights above the station. Where the model's
  ground at the point's cell is well above the station, the lowest above-station value is high: at Srinagar (VISR)
  the ground is about 518 m above the field, so neither Amber nor Red can be reached; Red cannot be reached at Goma
  (FZNA, +270 m), Tel Aviv Ben Gurion (LLBG, +193 m), Taipei Taoyuan (RCTP, +171 m) or Subic Bay (RPLB, +136 m).
  Where the ground is below the station (Islamabad OPIS, 66 m), ceilings read lower and a base in the lowest model
  layer reads 0 m (Red). The model ground is GEFS's 0.25-degree `HGT:surface` at each point's cell
  (`config/gefs_terrain.json`, read 27 Nov 2026); the 300 m rule above compares a different terrain model (Open-Meteo's
  elevation at the cell centre), so it does not see this.

Scores of forecasts built from that field measure these two limits as much as ceiling skill.

The thresholds are one generic, indicative set (Tom, 8 Nov 2026). Platform profiles (rotary-wing, small UAS, maritime with sea state) and a heat-stress measure (WBGT) are later work. A thunderstorm hazard (CAPE with rain in the same hour; both ensembles carry CAPE) comes back only after a week of test runs.

## Likelihood, impact matrix and confidence

- **Likelihood**: The raw ensemble fraction $p \in [0, 1]$ is stored as a number and mapped to its ICD 203 verbal probability term and percentage band via `tic-doctrine`.
- **Stoplight**: Following the UK Met Office National Severe Weather Warning Service likelihood-by-impact matrix:
  - **Red** (*Take action*): severe threshold is *Roughly even chance* or higher ($p_{\text{red}} \ge 0.45$).
  - **Amber** (*Be prepared*): moderate threshold is *Roughly even chance* or higher ($p_{\text{amber}} \ge 0.45$), or severe threshold is *Unlikely* or higher ($p_{\text{red}} \ge 0.20$).
  - **Yellow** (*Be aware*): moderate threshold is *Unlikely* or higher ($p_{\text{amber}} \ge 0.20$), or severe threshold is *Very unlikely* or higher ($p_{\text{red}} \ge 0.05$).
  - **Green** (*No significant impact*): below Yellow on every hazard.
- **Feed items** (`site/data/feed.geojson`, tic-feed/2): one forecast per point covers the 72-hour window from the first scored hour (`event_start` to `event_end`), and `valid_until` is the end of that window; each build replaces the items before then. The probability is for the driving hazard on the driving day, and `resolution.known_at` is the end of that day. The resolution rule states the test `tools/verify.py` applies (the report nearest each 3-hourly step, two consecutive steps), or says that Vis does not score that hazard from METARs.
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
  else AWC's statute miles), heat and cold (2 m temperature), and from 27 Nov 2026 the low ceiling trial: the
  report's lowest broken (BKN), overcast (OVC) or obscured (VV) base, read from the raw text before any TEMPO, BECMG,
  NOSIG or RMK group. FEW or SCT only, CAVOK, NSC, SKC, CLR and NCD count as no ceiling (CAVOK and NSC only say
  nothing below 1,500 m, so ceiling thresholds stay under that); a ceiling layer whose height is `///`, or a report
  with no cloud group, is unresolved. Each 3-hourly model step in the forecast's window takes
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
