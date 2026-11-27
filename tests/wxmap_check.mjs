// View-time checks for site/wxmap.js, run by tests/test_vis.py when Node is installed: the points' outlook day and
// each overlay's time come from the reader's clock and what each service keeps, not from the build.
import assert from "node:assert/strict";
import { newestTime, oldestTime, overlayTime, pointOutlook, timeline } from "../site/wxmap.js";

const at = (s) => Date.parse(s);
const iso = (ms) => (ms === null ? null : new Date(ms).toISOString().slice(0, 19) + "Z");

// The evening build of 26 Nov (20:25Z) has rows for 26 Nov (from 21:00Z) to 2 Dec. Read at 06:00Z on 27 Nov, before
// the morning build, the points must take 27 Nov's row, not the first row.
const z = { days: [
  { day: "2026-11-26", from: "2026-11-26T21:00Z", hours: 3, colour: "green" },
  { day: "2026-11-27", from: "2026-11-27T00:00Z", hours: 24, colour: "yellow" },
  { day: "2026-11-28", hours: 24, colour: "amber" },
] };
assert.equal(pointOutlook(z, at("2026-11-26T22:00:00Z")).colour, "green");
assert.equal(pointOutlook(z, at("2026-11-26T22:00:00Z")).hours, 3);
assert.equal(pointOutlook(z, at("2026-11-27T06:00:00Z")).colour, "yellow");
assert.equal(pointOutlook(z, at("2026-11-27T06:00:00Z")).day, "2026-11-27");
assert.equal(pointOutlook(z, at("2026-11-28T23:59:00Z")).colour, "amber");
// A forecast that no longer covers today, a point with no rows, a row with no colour: grey ("none"), never green.
assert.equal(pointOutlook(z, at("2026-11-29T01:00:00Z")).colour, "none");
assert.equal(pointOutlook({}, at("2026-11-27T06:00:00Z")).colour, "none");
assert.equal(pointOutlook({ days: [{ day: "2026-11-27" }] }, at("2026-11-27T06:00:00Z")).colour, "none");

// Layers as tools/wxmap.py writes them at a 20:00Z build: GOES infrared 60 min behind (10-minute steps, kept for
// years); NOAA MRMS radar 2 min behind with about 2 hours of scans (118 s steps); ECCC radar 3 hours (6-minute steps).
const ir = { step_s: 600, lag_s: 3600, keep_s: 86400 * 400 };
const mrms = { step_s: 118, lag_s: 120, keep_s: 7080, sector_name: "US lower 48 (NOAA MRMS)" };
const eccc = { step_s: 360, lag_s: 360, keep_s: 10800, sector_name: "Canada (ECCC radar)" };
const now = at("2026-11-26T21:50:00Z");

// No loop: radar's own newest scan is minutes old, while the imagery's newest frame is over an hour old.
assert.ok(now - newestTime(mrms, now) < 6 * 60 * 1000, iso(newestTime(mrms, now)));
assert.ok(now - newestTime(ir, now) >= 60 * 60 * 1000, iso(newestTime(ir, now)));

// A 12-frame infrared loop spans about 3 hours back. MRMS keeps 2 hours, so its oldest frames get no radar request
// at all (the page says "none kept for this time"); ECCC, keeping 3 hours, covers all but the oldest, which sits at the
// edge of its 3 hours (one step of margin is kept, as its server answers an expired time with an error).
const times = timeline([ir], 12, now);
assert.equal(times.length, 12);
assert.equal(iso(times[11]), "2026-11-26T20:40:00Z");
assert.equal(iso(times[0]), "2026-11-26T18:50:00Z");
const mrmsT = times.map((t) => overlayTime(mrms, t, now));
assert.equal(mrmsT[0], null, "19:00Z is older than the 2 hours MRMS keeps");
assert.ok(mrmsT[11] !== null && mrmsT[11] <= times[11]);
for (const t of mrmsT) if (t !== null) assert.ok(t >= oldestTime(mrms, now));
assert.ok(mrmsT.filter((t) => t === null).length >= 2);
assert.ok(times.slice(1).every((t) => overlayTime(eccc, t, now) !== null));
assert.equal(overlayTime(eccc, times[0], now), null);

// IMERG, about 7 hours behind: no frame of its loop is within what either radar service keeps.
const imerg = { step_s: 1800, lag_s: 7 * 3600, keep_s: 86400 * 400 };
const precip = timeline([imerg], 6, now);
assert.ok(precip.every((t) => overlayTime(mrms, t, now) === null && overlayTime(eccc, t, now) === null));

// An overlay a little behind the imagery takes its newest frame within one of its steps, and nothing beyond that.
const slow = { step_s: 900, lag_s: 2 * 3600, keep_s: 86400 };
assert.equal(overlayTime(slow, times[11], now), null);
assert.equal(overlayTime(slow, newestTime(slow, now) + 600 * 1000, now), newestTime(slow, now));

// A layer with no record of what it keeps (an older wxmap.json) is never cut off at the old end.
assert.equal(oldestTime({ step_s: 600, lag_s: 0 }, now), -Infinity);

console.log("wxmap view-time checks: ok");
