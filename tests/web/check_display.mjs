// Assertions for web/lib/units.ts and web/lib/time.ts, run with Node's
// type-stripping so no test-runner dependency is added to the web app.
//
// These two modules decide what every temperature and every timestamp in the
// UI says. The bugs they exist to prevent are the quiet kind: a Fahrenheit
// market printed in Celsius reads as a plausible number, and a delta
// converted with the +32 offset reads as a plausible number too.
import assert from "node:assert/strict";
import * as U from "../../web/lib/units.ts";
import * as T from "../../web/lib/time.ts";

const out = [];
function t(name, fn) {
  try { fn(); out.push(["ok", name]); }
  catch (e) { out.push(["FAIL", name + " :: " + e.message]); }
}

// ---- units ---------------------------------------------------------------
t("US city shows Fahrenheit", () => {
  assert.equal(U.fmtTemp(28.3, "F"), "82.9°F");
});
t("metric city still shows Celsius", () => {
  assert.equal(U.fmtTemp(28.3, "C"), "28.3°C");
});
t("a missing unit does not invent Fahrenheit", () => {
  assert.equal(U.fmtTemp(28.3, null), "28.3°C");
});
t("a DELTA converts by ratio, never by offset", () => {
  // The bug this exists for: 1°C of change is 1.8°F, not 33.8°F. An offset
  // applied to a delta turns every sigma and every hourly change into noise.
  assert.equal(U.deltaToDisplay(1, "F"), 1.8);
  assert.equal(U.fmtTempDelta(1, "F"), "+1.8°F");
  assert.equal(U.fmtTempDelta(-0.5, "F"), "-0.9°F");
  assert.equal(U.fmtTempDelta(1, "C"), "+1.0°C");
});
t("band edges round to how the market quotes them", () => {
  // 21.11C is exactly 70F: an F market must show 70, not 69.998 or 21.1
  assert.equal(U.fmtBandEdge(U.fToC(70), "F"), "70");
  assert.equal(U.fmtBandEdge(U.fToC(78), "F"), "78");
  // C markets quote in halves, and halves are exactly representable, so this
  // is the case that actually occurs - not a contrived .x5 rounding edge.
  assert.equal(U.fmtBandEdge(21.5, "C"), "21.5");
  assert.equal(U.fmtBandEdge(22, "C"), "22.0");
});
t("a band reads as the interval the market states", () => {
  assert.equal(U.fmtBandRange(U.fToC(70), U.fToC(72), "F"), "70–72°F");
  assert.equal(U.fmtBandRange(null, U.fToC(60), "F", true, false), "≤ 60°F");
  assert.equal(U.fmtBandRange(U.fToC(78), null, "F", false, true), "≥ 78°F");
});
t("round trip C -> F -> C", () => {
  assert.ok(Math.abs(U.fToC(U.cToF(23.7)) - 23.7) < 1e-9);
});
t("null temperature is a dash, not NaN", () => {
  assert.equal(U.fmtTemp(null, "F"), "—");
  assert.equal(U.fmtTempDelta(undefined, "F"), "—");
});

// ---- time ----------------------------------------------------------------
t("timestamps render in the viewer's zone, not UTC", () => {
  const iso = "2026-09-03T19:30:00Z";
  assert.equal(T.fmtTime(iso, "Asia/Beirut"), "22:30");   // UTC+3 in September
  assert.equal(T.fmtTime(iso, "UTC"), "19:30");
});
t("a resolution date is a calendar day, never shifted by a zone", () => {
  // Shifting it would rename the day: 2026-09-03 in a UTC-6 zone is not 2 Sep.
  assert.equal(T.fmtResolutionDate("2026-09-03"), "Thu 3 Sep");
});
t("days ahead is what a trader says", () => {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Beirut", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
  const plus = (n) => {
    const d = new Date(today + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };
  assert.equal(T.daysAhead(today, "Asia/Beirut"), 0);
  assert.equal(T.fmtDaysAhead(today, "Asia/Beirut"), "today");
  assert.equal(T.fmtDaysAhead(plus(1), "Asia/Beirut"), "tomorrow");
  assert.equal(T.fmtDaysAhead(plus(2), "Asia/Beirut"), "in 2 days");
  assert.equal(T.fmtDaysAhead(plus(-1), "Asia/Beirut"), "yesterday");
});
t("a city peak hour shows both clocks", () => {
  // Chicago's 15:00 peak, read from Beirut. CDT is UTC-5 in September,
  // Beirut UTC+3 - eight hours, so 23:00 the same evening.
  const s = T.fmtCityHour(15, "America/Chicago", "Asia/Beirut");
  assert.ok(s.startsWith("15:00"), s);
  assert.ok(s.includes("23:00"), s);
  assert.ok(s.includes("your time"), s);
});
t("same zone does not print a redundant second clock", () => {
  const s = T.fmtCityHour(15, "Asia/Beirut", "Asia/Beirut");
  assert.ok(!s.includes("your time"), s);
});
t("a half-hour peak keeps its minutes", () => {
  assert.ok(T.fmtCityHour(15.5, "America/Chicago", "Asia/Beirut").startsWith("15:30"));
});
t("bad input degrades to a dash, never a crash", () => {
  assert.equal(T.fmtTime(null, "UTC"), "—");
  assert.equal(T.fmtTime("not a date", "UTC"), "—");
  assert.equal(T.fmtCityHour(null, "UTC"), "—");
});

const failed = out.filter(([s]) => s === "FAIL");
for (const [s, n] of out) if (s === "FAIL") console.log("  FAIL", n);
console.log(JSON.stringify({ ok: failed.length === 0, passed: out.length - failed.length, failed: failed.length }));
