# Vis: plan and prototype

**Name: Vis** (Tom, 4 Nov 2026: "Yes, keep Vis"). Address `vis.theintelligence.company`; public repo
`metacog-osint/vis` (AGPL-3.0), Cloudflare Pages project `vis` (Tom approved all three on 4 Nov 2026).

Roadmap step 6 of The Intelligence Company's program roadmap. Prototype built 31 Oct 2026; extended 1 Nov; published
4 Nov 2026.

## What it is
For each Watchfloor zone, the likelihood of operationally significant weather over the next 7 days and the night's
illumination, as a stoplight per zone and day with the driver named. Doctrine anchors: JP 3-59 parameters (Fig. V-1),
illumination tables (App. C) and green/amber/red impact matrices (App. E, 2012 edition); JP 3-59 gives no numeric
platform limits, so every threshold is labelled indicative.

## The prototype (working now)
- `tools/forecast.py`: zone points from Watchfloor's public feed; Open-Meteo's ensemble API, GFS (31 members) and
  ECMWF IFS (51 members), hourly; for each UTC day and hazard, the share of members reaching the amber and red
  thresholds is the probability, with its ICD 203 term; a Met Office-style likelihood-by-impact matrix gives the
  colour (under 5%, "almost no chance", is green). Hazards: gusts 25/35 kt, visibility 4,800/1,600 m (GFS only),
  rain 2.5/7.6 mm/h, thunderstorms (CAPE 1,000/2,500 J/kg with rain 1/2.5 mm/h in the same hour), heat 35/40 °C,
  cold −10/−20 °C, snowfall 1/2.5 cm/h. Writes `out/forecast.json`.
- `tools/astro.py`: sun and moon (Meeus low-precision, main lunar terms), twilight, moonrise and moonset, lit
  fraction, and dark hours with the moon down or under a quarter lit. Checked 31 Oct 2026 against the US Naval
  Observatory's API at Kyiv, Taiwan, Goma and Seoul: sun events within 1 minute, moonrise and moonset within 2 minutes.
- `tools/preview.py`: `out/preview.html` (and `out/preview.png`), a zones-by-days table for review.
- Data terms: Open-Meteo, CC BY 4.0, free for non-commercial use (600 calls a minute, 5,000 an hour, 10,000 a day;
  open-meteo.com/en/terms, read 31 Oct 2026). One run is 16 calls. Model data: NOAA GFS ensemble (public domain),
  ECMWF IFS ensemble (CC BY 4.0).

## What the first run showed (1 Nov 2026 run)
1. **Zone points are geographic centres, not operating locations.** Watchfloor's Taiwan point (23.70°N, 120.96°E) is
   on the Central Mountain Range, so GFS puts it in cloud: visibility 100–200 m on six days, red all week. Forecast
   points should be named airfields, ports or bases, a few per zone, each with an ICAO code so METARs can verify them.
2. **CAPE alone was amber across the tropics every day**; requiring rain in the same hour fixed most of it.
3. **Fixed heat thresholds** keep Sudan and the Sahel amber all week at 36–40 °C, which is normal there in autumn.
   A wet-bulb globe temperature or a departure from local climatology would say more.
4. GFS visibility is a diagnosed field and often poor; ECMWF's ensemble on Open-Meteo has none.

## Extended on 1 Nov 2026 (not by the session that wrote this plan)
Named ICAO airfields and ports per zone (`config/points.json`), a 3-hour persistence rule (2 for icing), doctrine
files, a contract-v2 feed (`tools/feed_v2.py`, 17 items, kit lint PASS on 4 Nov), a site builder
(`tools/build_site.py` → `site/`) and `tests/test_vis.py` (passes). Checked 4 Nov 2026: the whole chain runs in about
25 seconds. **Settled 8 Nov 2026 (Decisions, 6):** the "Low cloud ceiling" hazard was low-cloud *cover* (80/95% of the 0–3 km layer), not
cloud-base height, so it read red at Kyiv all week in November; a true ceiling would need cloud by pressure level or
another source.

## Decisions (all made; launch prepared 8 Nov 2026)
1. **Name:** Vis (4 Nov 2026).
2. **Repo and site:** public repo `metacog-osint/vis` (AGPL-3.0), Cloudflare Pages project `vis` and
   `vis.theintelligence.company` (Tom approved all three, 4 Nov 2026). The project's own address is
   `vis-2pq.pages.dev`, because `vis.pages.dev` belongs to someone else; the CNAME points there.
3. **Forecast points:** the 16 reference points in `config/points.json`, with their 15 alternates forecast as rows
   of their own, 31 in all (Tom, 8 Nov 2026).
4. **Thresholds and profiles:** one generic, indicative set for launch; profiles (rotary-wing, small UAS, ground
   manoeuvre, maritime with sea state, airdrop) later (Tom, 8 Nov 2026).
5. **Heat:** the fixed 40/45 °C thresholds for launch; WBGT or a climatology departure later (Tom, 8 Nov 2026).
6. **Low cloud:** renamed "Low cloud cover" and shown at most Yellow (Tom, 8 Nov 2026). Neither ensemble on
   Open-Meteo has a cloud-base field (checked 8 Nov 2026), and before the cap low cloud drove every Amber and Red
   zone-day in the 8 Nov 17Z run.
7. **Thunderstorms:** both ensembles carry CAPE; the hazard comes back after launch, once tested on a week of runs
   (Tom, 8 Nov 2026).

## Build steps after the decisions
- W1 repo, kit vendored, points config, daily GitHub Actions run, site with the house design and tic-time, COP layer.
- W2 feed contract v2 forecast items (probability, likelihood term, confidence from model agreement and lead time,
  alternatives, `resolution` rule and `valid_until`) so Verdict can score them from day one.
- W3 verification: resolve each forecast against METARs (aviationweather.gov, public domain) at the points; Brier
  score and reliability by hazard and lead time, published.
- W4 illumination table page and a likelihood-by-impact matrix view per zone.
