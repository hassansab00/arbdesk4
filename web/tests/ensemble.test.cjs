// The city popup's ensemble (web/lib/ensemble.ts): every member's maximum over
// the city's own day, summarised as the nightly record summarises it
// (scripts/ensemble_record.py daily_rows), and the share of members each
// bucket would settle. Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { memberMaxima, summarise, percentile, bucketShares, localDate, ensembleUrl, runTimes } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'ensemble.js'));

const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;

// THE SAME DAYS AS THE RECORD. tests/test_ensemble_record.py runs daily_rows on
// this fixture and pins these numbers; three members, hourly in GMT, Tokyo.
const js = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'ensemble_tokyo.json'), 'utf8'));
for (const [day, want] of [
  ['2026-10-10', { n: 3, mean: 26.17, sd: 0.236, p10: 26.0, p25: 26.0, p50: 26.0, p75: 26.25, p90: 26.4 }],
  // member 01 reads 40.0 at 10 Oct 16:00Z: 01:00 on Tokyo's 11 Oct, not the 10th
  ['2026-10-11', { n: 3, mean: 30.83, sd: 6.485, p10: 26.1, p25: 26.25, p50: 26.5, p75: 33.25, p90: 37.3 }],
]) {
  const s = summarise(memberMaxima(js, 'Asia/Tokyo', day));
  assert.equal(s.n, want.n, day);
  assert.equal(round(s.mean, 2), want.mean, `${day} mean`);
  assert.equal(round(s.sd, 3), want.sd, `${day} sd`);
  for (const q of ['p10', 'p25', 'p50', 'p75', 'p90']) assert.equal(round(s[q], 2), want[q], `${day} ${q}`);
}
// A day the response does not cover whole (fewer than 20 hours) is no day.
assert.deepEqual(memberMaxima(js, 'Asia/Tokyo', '2026-10-09'), []);
assert.deepEqual(memberMaxima(js, 'Asia/Tokyo', '2026-10-12'), []);
assert.deepEqual(memberMaxima(null, 'Asia/Tokyo', '2026-10-10'), []);
assert.equal(summarise([]), null);

// numpy's default percentile, as the record computes it
assert.equal(round(percentile([1, 2, 3, 4], 10), 6), 1.3);
assert.equal(percentile([1, 2, 3, 4], 50), 2.5);
assert.equal(round(percentile([1, 2, 3, 4], 90), 6), 3.7);
assert.equal(percentile([5], 90), 5);

// The city's own calendar day, across the date line and a daylight-saving zone.
assert.equal(localDate(Date.parse('2026-10-09T15:00:00Z'), 'Asia/Tokyo'), '2026-10-10');
assert.equal(localDate(Date.parse('2026-10-10T03:00:00Z'), 'America/Chicago'), '2026-10-09');

// BUCKET SHARES: read as the venue reads (whole degree, half away from zero),
// placed by the ladder's half-open edges, in the market's unit.
const cBands = [
  { band_id: 'lo', band_hi: 18, open_low: true },
  { band_id: '18', band_lo: 18, band_hi: 19 },
  { band_id: '19', band_lo: 19, band_hi: 20 },
  { band_id: 'hi', band_lo: 20, open_high: true },
];
let r = bucketShares([17.4, 18.49, 18.5, 19.2, 25], 'C', cBands);
assert.deepEqual(Object.fromEntries(r.shares), { lo: 0.2, 18: 0.2, 19: 0.4, hi: 0.2 });
assert.equal(r.unplaced, 0);
// floored at the station's maximum so far: a member below it settles at it
r = bucketShares([17.4, 18.49, 18.5, 19.2, 25], 'C', cBands, 19.0);
assert.deepEqual(Object.fromEntries(r.shares), { 19: 0.8, hi: 0.2 });
// a Fahrenheit market: 21.0 C is 69.8 F, which the venue reads as 70
const fBands = [{ band_id: '68-69', band_lo: 68, band_hi: 70 }, { band_id: '70-71', band_lo: 70, band_hi: 72 }];
r = bucketShares([21.0, 25.0], 'F', fBands);
assert.deepEqual(Object.fromEntries(r.shares), { '70-71': 0.5 });
assert.equal(r.unplaced, 0.5);   // 77 F: no bucket holds it, and it is not given to one
assert.equal(bucketShares([], 'C', cBands).shares.size, 0);

// The request: the record's model and variable, in GMT, with yesterday so a
// city east of UTC has its whole local day.
const u = new URL(ensembleUrl(35.55, 139.75, 'ecmwf_ifs025'));
assert.equal(u.host, 'ensemble-api.open-meteo.com');
assert.equal(u.searchParams.get('models'), 'ecmwf_ifs025');
assert.equal(u.searchParams.get('timezone'), 'GMT');
assert.equal(u.searchParams.get('past_days'), '1');
assert.deepEqual(runTimes({ last_run_initialisation_time: 1790553600 }), { init: '2026-09-28T00:00:00.000Z', available: null });
assert.deepEqual(runTimes(null), { init: null, available: null });

console.log('ensemble: ok');
