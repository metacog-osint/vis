// View-time checks for site/locdetail.js, run by tests/test_vis.py when Node is installed:
// the METAR's age and the TAF's validity come from the reader's clock, not from the build.
import assert from "node:assert/strict";
import { ageMin, ago, tafState, METAR_OLD_MIN } from "../site/locdetail.js";

const obs = "2026-11-26T21:50:00Z";
const at = (s) => Date.parse(s);

// A Vilnius report of 21:50Z read at 22:09Z, then the next morning (the build was at 22:08Z).
assert.equal(ageMin(obs, at("2026-11-26T22:09:00Z")), 19);
assert.equal(ageMin(obs, at("2026-11-27T09:10:00Z")), 680);
assert.ok(ageMin(obs, at("2026-11-27T09:10:00Z")) > METAR_OLD_MIN, "an 11-hour-old report reads as old");
assert.ok(ageMin(obs, at("2026-11-26T23:00:00Z")) <= METAR_OLD_MIN);
assert.equal(ago(19), "19 min ago");
assert.equal(ago(680), "11 h ago");
assert.equal(ago(150), "2.5 h ago");
assert.equal(ago(3 * 1440), "3 days ago");
assert.equal(ageMin(null), null);
assert.equal(ageMin("2026-11-26T21:50Z", at("2026-11-26T22:50:00Z")), 60); // the short form the files also use

// A TAF valid to 27 Nov 06Z: current the evening before, ended once 06Z passes, even with no new build.
const taf = { raw: "TAF EYVI 261700Z 2618/2706 ...", issued: "2026-11-26T17:00:00Z",
  valid_from: "2026-11-26T18:00:00Z", valid_to: "2026-11-27T06:00:00Z" };
assert.equal(tafState(taf, at("2026-11-26T22:09:00Z")), "current");
assert.equal(tafState(taf, at("2026-11-27T06:00:00Z")), "ended");
assert.equal(tafState(taf, at("2026-11-27T09:10:00Z")), "ended");
assert.equal(tafState(null), "none");
assert.equal(tafState({ raw: "TAF X" }), "ended"); // no validity: never shown as current

console.log("locdetail view-time checks: ok");
