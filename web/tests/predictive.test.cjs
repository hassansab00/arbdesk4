// The Predictive page's tables (web/lib/predictive.ts; handoff 30 Sep, F2).
// Measured 30 Sep as anon: the per-city scorecard rendered 120 of 797 unordered
// rows (8 of 48 cities), the forward table 60 of 96 city-days, the lean chart
// 8 cities unlabelled. Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { forwardRows, groupScorecard, pendingDays, localDayEnd, tzOffsetMs, largestLeans, PROB_SUM_TOLERANCE } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'predictive.js'));

// ------------------------------------------------------------ the scorecard
// 48 active cities, 11 of them US, three models and up to six leads each, in
// the scrambled order an unordered query can return.
const US = ['atlanta', 'austin', 'chicago', 'dallas', 'denver', 'houston', 'los_angeles', 'miami', 'nyc', 'san_francisco', 'seattle'];
const C = Array.from({ length: 37 }, (_, i) => `c_city_${String(i).padStart(2, '0')}`);
const roster = [...C, ...US].sort();
const rows = [];
for (const city of roster) {
  if (city === 'c_city_36') continue;                     // a city with too few settled days
  for (const model of ['nws', 'open_meteo_best_match', 'open_meteo_forecast']) {
    if (model === 'nws' && !US.includes(city)) continue;   // NWS covers the US only
    for (let lead = 0; lead <= 5; lead++) {
      rows.push({ city_key: city, model, lead_days: lead, n_days: 10, mae_c: 1, bias_c: 0,
        error_sd_c: 1, worst_c: 3, hit_rate_pct: 30, within_1c_pct: 60 });
    }
  }
}
rows.reverse();
const all = groupScorecard(rows, roster);
assert.equal(all.groups.length, 47, 'every active city with rows is reachable, not the first 8');
assert.ok(US.every((c) => all.groups.some((g) => g.city_key === c)), 'all 11 US cities');
assert.deepEqual(all.missing, ['c_city_36'], 'a city with no row is named, not scored zero');
assert.equal(all.shownRows, rows.length);
assert.equal(all.totalRows, rows.length);
assert.deepEqual(all.groups.map((g) => g.city_key), roster.filter((c) => c !== 'c_city_36'), 'roster order');
const nyc = all.groups.find((g) => g.city_key === 'nyc').rows;
assert.deepEqual(nyc.slice(0, 3).map((r) => `${r.model}:${r.lead_days}`),
  ['nws:0', 'nws:1', 'nws:2'], 'rows by model then lead, whatever order they arrived in');
const one = groupScorecard(rows, roster, 'seattle');
assert.equal(one.groups.length, 1);
assert.equal(one.groups[0].rows.length, 18);
assert.deepEqual(groupScorecard(rows, roster, 'c_city_36').missing, ['c_city_36']);

// ------------------------------------------------------------ the forward table
const band = (o) => ({ band_index: 1, closed: false, side: 'YES', market_price: 0.3, forecast_max_c: 20,
  centre_c: 21, sigma_c: 1, edge_net_pp: null, tradeable: true, ...o });
const ladder = [];
// 96 open city-days: 48 cities x 2 days, each a 3-band ladder that sums to 1
for (const city of roster) {
  for (const day of ['2026-09-30', '2026-10-01']) {
    ladder.push(band({ city_key: city, for_date: day, band_id: `${city}${day}a`, band_index: 1, band_label: 'a', model_prob: 0.2 }));
    ladder.push(band({ city_key: city, for_date: day, band_id: `${city}${day}b`, band_index: 2, band_label: 'b', model_prob: 0.5 }));
    ladder.push(band({ city_key: city, for_date: day, band_id: `${city}${day}c`, band_index: 3, band_label: 'c', model_prob: 0.3 }));
  }
}
// yesterday and a closed ladder are not forward
ladder.push(band({ city_key: 'nyc', for_date: '2026-09-29', band_id: 'old', band_label: 'x', model_prob: 1 }));
const fw = forwardRows(ladder, '2026-09-30');
assert.equal(fw.length, 96, 'every open city-day, not the first 60');
assert.ok(fw.every((r) => r.best_band === 'b' && !r.incomplete && r.n_priced === 3 && r.n_bands === 3));
assert.deepEqual(fw.slice(0, 2).map((r) => r.city_key), [roster[0], roster[1]], 'equal edges fall back to the city');
assert.equal(fw[0].for_date, '2026-09-30');

// The winning bucket is the distribution's peak, NOT the bucket holding the
// centre: a centre clamped to the observed maximum can sit in a bucket that
// has less mass than the one above it.
const clamped = forwardRows([
  band({ city_key: 'nyc', for_date: '2026-09-30', band_id: 'n1', band_index: 1, band_label: '70-71°F', model_prob: 0.35, centre_c: 21.6 }),
  band({ city_key: 'nyc', for_date: '2026-09-30', band_id: 'n2', band_index: 2, band_label: '72-73°F', model_prob: 0.45, centre_c: 21.6 }),
  band({ city_key: 'nyc', for_date: '2026-09-30', band_id: 'n3', band_index: 3, band_label: '74-75°F', model_prob: 0.20, centre_c: 21.6 }),
  // the NO row repeats the YES probability and carries the NO price; it must not be the quote
  band({ city_key: 'nyc', for_date: '2026-09-30', band_id: 'n2', band_index: 2, band_label: '72-73°F', model_prob: 0.45, side: 'NO', market_price: 0.61 }),
], '2026-09-30')[0];
assert.equal(clamped.best_band, '72-73°F');
assert.equal(clamped.best_price, 0.3, 'the YES price is what the market charges for the bucket');

// ties go to the lower band, so the call does not depend on row order
const tie = (order) => forwardRows(order, '2026-09-30')[0].best_band;
const t1 = band({ city_key: 'x', for_date: '2026-09-30', band_id: 't1', band_index: 1, band_label: 'low', model_prob: 0.5 });
const t2 = band({ city_key: 'x', for_date: '2026-09-30', band_id: 't2', band_index: 2, band_label: 'high', model_prob: 0.5 });
assert.equal(tie([t1, t2]), 'low');
assert.equal(tie([t2, t1]), 'low');

// a ladder partly priced, or not summing to 1, is flagged rather than trusted silently
const partial = forwardRows([
  band({ city_key: 'p', for_date: '2026-09-30', band_id: 'p1', band_index: 1, band_label: 'p1', model_prob: 0.6 }),
  band({ city_key: 'p', for_date: '2026-09-30', band_id: 'p2', band_index: 2, band_label: 'p2', model_prob: null }),
], '2026-09-30')[0];
assert.equal(partial.incomplete, true);
assert.equal(partial.n_priced, 1);
const off = forwardRows([
  band({ city_key: 'q', for_date: '2026-09-30', band_id: 'q1', band_index: 1, band_label: 'q1', model_prob: 0.5 }),
  band({ city_key: 'q', for_date: '2026-09-30', band_id: 'q2', band_index: 2, band_label: 'q2', model_prob: 0.5 + 2 * PROB_SUM_TOLERANCE }),
], '2026-09-30')[0];
assert.equal(off.incomplete, true);
const unpriced = forwardRows([band({ city_key: 'u', for_date: '2026-09-30', band_id: 'u1', band_label: 'u', model_prob: null })], '2026-09-30')[0];
assert.equal(unpriced.best_band, null);
assert.equal(unpriced.incomplete, false, 'an unpriced ladder is unpriced, not a bad sum');

// ------------------------------------------------------------ the local day
const iso = (ms) => new Date(ms).toISOString();
assert.equal(iso(localDayEnd('2026-09-29', 'America/New_York')), '2026-09-30T04:00:00.000Z');
assert.equal(iso(localDayEnd('2026-09-29', 'America/Los_Angeles')), '2026-09-30T07:00:00.000Z');
assert.equal(iso(localDayEnd('2026-09-29', 'Asia/Tokyo')), '2026-09-29T15:00:00.000Z');
assert.equal(iso(localDayEnd('2026-09-29', 'Asia/Kolkata')), '2026-09-29T18:30:00.000Z');
assert.equal(iso(localDayEnd('2026-11-01', 'America/New_York')), '2026-11-02T05:00:00.000Z', 'DST ends: a 25-hour day');
assert.equal(iso(localDayEnd('2026-03-08', 'America/New_York')), '2026-03-09T04:00:00.000Z', 'DST starts: a 23-hour day');
assert.equal(tzOffsetMs(Date.parse('2026-09-30T12:00:00Z'), 'UTC'), 0);

// ------------------------------------------------------------ pending days
const now = Date.parse('2026-09-30T09:21:00Z');
const markets = [
  { resolution_date: '2026-09-30', resolution_verified_at: null },            // today: not ended
  { resolution_date: '2026-09-29', resolution_verified_at: null },            // ended 04:00Z, venue pending
  { resolution_date: '2026-09-28', resolution_verified_at: '2026-09-29T04:50:00Z' },
  { resolution_date: '2026-09-27', resolution_verified_at: '2026-09-28T04:50:00Z' },
];
const pend = pendingDays(markets, new Set(['2026-09-27']), 'America/New_York', now);
assert.deepEqual(pend.map((p) => `${p.date}:${p.state}`),
  ['2026-09-29:awaiting_venue', '2026-09-28:confirmed_not_scored']);
// Mexico City is a C city on an American clock: its day, not its unit, decides
assert.deepEqual(pendingDays([{ resolution_date: '2026-09-29', resolution_verified_at: null }], new Set(),
  'America/Mexico_City', Date.parse('2026-09-30T05:59:00Z')), []);
assert.equal(pendingDays([{ resolution_date: '2026-09-29', resolution_verified_at: null }], new Set(),
  'America/Mexico_City', Date.parse('2026-09-30T06:00:00Z'))[0].state, 'awaiting_venue');
assert.deepEqual(pendingDays(markets, new Set(), null, now), [], 'no timezone, no guess');

// ------------------------------------------------------------ the lean chart
const leans = largestLeans(roster.map((c, i) => ({ city: c, bias: (i % 7) - 3, n: 5 })), 8);
assert.equal(leans.shown.length, 8);
assert.equal(leans.total, 48, 'the label can say 8 of 48');
assert.equal(largestLeans([{ city: 'a', bias: 1 }], 0).shown.length, 1, 'k = 0 shows all');

console.log('predictive: every city reachable in the scorecard (47 + 1 named without rows), 96 of 96 forward rows, '
  + 'the peak bucket from the distribution, local-day pending states, the lean chart scoped');
