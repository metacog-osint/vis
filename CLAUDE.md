# Vis - Claude Context

Operational weather and illumination for Watchfloor's conflict zones, from The Intelligence Company. Public repo
`metacog-osint/vis` (GNU AGPL-3.0). Read `PLAN.md` (decisions and build steps) and `doctrine/method.md` (method).
Times: store UTC; the page shows the viewer's zone through the shared `tic-time.js`. Messages to Tom use Central time.

## Where it stands (8 Nov 2026)
- **Live** at https://vis.theintelligence.company since 8 Nov 2026, 11:57 AM CST (also https://vis-2pq.pages.dev;
  Cloudflare Pages project `vis`, since `vis.pages.dev` belongs to someone else), from `9ad677c`. The custom domain
  was added to the Pages project on 4 Nov; Squarespace `CNAME vis → vis-2pq.pages.dev` (4 h TTL) went in on 8 Nov
  with Tom's yes. Certificate: Google Trust Services, renews automatically; HTTP redirects to HTTPS; HSTS one year.
- **Listed as live (beta) across the suite since 8 Nov 2026** (Tom's yes, including the wording): the hub (drawing,
  product list, 404 page, footer Sources link to `/#sources`, "Updated … ago" from the feed's `generated_at`), the
  house bar v1.5 on every site, the doctrine kit 0.4.2 registry (live; fresh 20 h, stale 48 h) and COP, where Vis's
  Amber and Red forecasts show in every view and the rest under its "Weather" preset.
- Decisions Tom made on 8 Nov 2026 (details in `PLAN.md`, "Decisions"): forecast the 15 alternates as their own rows
  (31 points); "Low cloud cover" shows at most Yellow (no ensemble on Open-Meteo has cloud base); one generic,
  indicative threshold set; thunderstorms (CAPE with rain) only after a week of test runs.

## How it runs
- `.github/workflows/build.yml`: on push to `main`, when the hub's `tic-dispatch` Worker starts it (08:25 and 20:25
  UTC, since 8 Nov 2026), on its fallback cron (08:53 and 20:53 UTC; a gate job skips a scheduled run within 6 hours
  of a successful build), or by hand. Tom adds `vis` to the hub's `DISPATCH_TOKEN` token for the dispatch to work. Tests, kit check, forecast
  (`tools/forecast.py`, two Open-Meteo calls for 31 points), site and feed (`tools/build_site.py`), feed lint, then
  `wrangler pages deploy site --project-name vis`. Repo secrets set by Tom on 8 Nov 2026: `CLOUDFLARE_PAGES_TOKEN`
  (token "vis GitHub deploy", Account › Cloudflare Pages › Edit only) and `CLOUDFLARE_ACCOUNT_ID`. The repo is public,
  so Actions minutes are free.
- GitHub starts this account's scheduled runs late (gaps up to about 16 hours, 6–8 Nov), hence the dispatch clock.
  The page warns when the forecast is over 20 hours old (`STALE_HOURS` in the page script).
- `site/index.html`, `style.css`, `vis.js` and `_headers` are **generated** from strings in `tools/build_site.py`:
  edit there, then run `python tools/build_site.py`. `site/data/` and `out/` are git-ignored.
- Local preview: `python tools/forecast.py` (live data), `python tools/build_site.py`, then serve `site/` (launch
  config `vis` in `D:\Projects\.claude\launch.json`, port 8744). Tests: `python tests/test_vis.py`.
- Open-Meteo weights ensemble calls heavily: one 31-point run uses most of the free 600-a-minute allowance. Leave a
  minute between runs; `fetch_model` waits 65 s and retries on HTTP 429.

## Rules
- `doctrine/kit/` is vendored from `D:\Projects\tic-doctrine`; never edit it here.
- Every threshold is indicative (JP 3-59 gives no numeric limits); say so wherever thresholds appear.
- Don't claim verification that isn't built: scoring against METARs is plan step W3, not done.
- House bar and time files are copies of the hub's `site/shared/` files; keep the version lines.
- Commit, push and deploy only with Tom's yes (a push to `main` deploys).
