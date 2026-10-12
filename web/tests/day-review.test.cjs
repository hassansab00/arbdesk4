// Why each day hit or missed (web/lib/dayReview.ts; Hassan, 12 Oct: "we need
// to nail it before the peak ... each day needs to teach the model ... why
// this weather hit/miss"). Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { beforePeakTally, sumTallies, checkpointsOf, isBeforePeak, DAY_REVIEW_SELECT } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'dayReview.js'));

const call = (checkpoint, engine, wxrd, market) => ({
  checkpoint, engine_top: 'a', engine_hit: engine, wxrd_top: wxrd === null ? null : 'b', wxrd_hit: wxrd,
  market_top: 'c', market_hit: market,
});

// ------------------------------------------- before the peak is what counts
assert.equal(isBeforePeak('prepeak_1h'), true);
assert.equal(isBeforePeak('postpeak_1h'), false, 'after the peak the day is mostly decided');
const day = [call('morning', false, true, true), call('noon', true, true, true), call('prepeak_1h', true, null, false),
  call('postpeak_1h', true, true, true)];
assert.deepEqual(beforePeakTally(day), {
  engine: { hits: 2, n: 3 }, wxrd: { hits: 2, n: 2 }, market: { hits: 2, n: 3 },
}, 'the after-peak call is left out, and a missing wxrd call is not a miss');
assert.deepEqual(beforePeakTally(null), { engine: { hits: 0, n: 0 }, wxrd: { hits: 0, n: 0 }, market: { hits: 0, n: 0 } });

// ------------------------------------------------------------ over the days
const rows = [{ target_date: '2026-10-12', calls: day }, { target_date: '2026-10-11', calls: [call('noon', false, false, true)] },
  { target_date: '2026-10-10', calls: null }];
assert.deepEqual(sumTallies(rows), { engine: { hits: 2, n: 4 }, wxrd: { hits: 2, n: 3 }, market: { hits: 3, n: 4 } });

// ------------------------------------------ the columns, in the day's order
assert.deepEqual(checkpointsOf([{ calls: [call('postpeak_1h'), call('noon')] }, { calls: [call('d1_eve'), call('morning')] }]),
  ['d1_eve', 'morning', 'noon', 'postpeak_1h']);

// --------------------------------- the page reads the columns the job writes
const migration = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261013020000_why_each_day_hit_or_missed.sql'), 'utf8');
for (const col of DAY_REVIEW_SELECT.split(',')) {
  assert.match(migration, new RegExp(`^\\s+${col}\\s`, 'm'), `derived_day_review has ${col}`);
}

console.log('day review: ok');
