// One city selection for all of Predictive (web/lib/focus.ts; WXPredict build
// wave F, Hassan 6 Oct: "Filtering the visible rows must also update the
// summary totals"). Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { selectedKeys, summariseByMoment, cityEvidence, compareFocus, wilson, mulberry32, MOMENTS } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'focus.js'));

// ------------------------------------------------------------ the selection
const active = ['amsterdam', 'chicago', 'lucknow', 'madrid', 'miami', 'tokyo'];
const focus = ['lucknow', 'miami', 'chicago', 'taipei'];      // taipei is retired: never shown
assert.deepEqual(selectedKeys('all', active, focus, []), active);
assert.deepEqual(selectedKeys('focus', active, focus, []), ['chicago', 'lucknow', 'miami'],
  'the focus set in roster order, and never a city outside the active roster');
assert.deepEqual(selectedKeys('custom', active, focus, ['tokyo', 'nowhere', 'amsterdam']), ['amsterdam', 'tokyo']);
assert.deepEqual(selectedKeys('custom', active, focus, []), []);
assert.equal(MOMENTS.filter((m) => m.afterPeak).map((m) => m.key).join(), 'postpeak_1h',
  'after the peak is one moment, labelled apart');

// ------------------------------------------------ the totals, from the rows
// The view's filters (v_prediction_hindsight_summary): days need hit AND
// predicted_pct; claimed averages predicted_pct where hit is known; market
// days are market_hit known; the error averages every known error.
const R = (city, date, moment, pct, hit, err, mkt, extra = {}) =>
  ({ city_key: city, for_date: date, called_when: moment, predicted_pct: pct, hit, forecast_error_c: err,
     market_hit: mkt, call_order: moment === 'day_ahead' ? 0 : 3, after_peak: false, ...extra });
const rows = [
  R('lucknow', '2026-10-08', 'day_ahead', 40, true, 0.5, true),
  R('lucknow', '2026-10-09', 'day_ahead', '30', false, '-1.5', true),     // PostgREST numerics come as strings
  R('lucknow', '2026-10-10', 'day_ahead', null, true, null, null),        // no stated pct: not a graded day
  R('miami', '2026-10-08', 'day_ahead', 50, null, 1.0, null),             // pending: never a miss
  R('miami', '2026-10-09', 'day_ahead', 20, false, 2.0, false),
  R('tokyo', '2026-10-08', 'day_ahead', 60, true, -0.5, false),
  R('tokyo', '2026-10-08', 'noon', 70, true, 0.0, true),
];
const all = summariseByMoment(rows);
const da = all.find((s) => s.called_when === 'day_ahead');
assert.equal(da.days, 4);
assert.equal(da.hits, 2);
assert.equal(da.claimed_pct, (40 + 30 + 20 + 60) / 4);
assert.equal(da.market_days, 4);
assert.equal(da.market_hits, 2);
assert.equal(da.model_hits_on_market_days, 2, 'lucknow 8 Oct and tokyo 8 Oct');
assert.equal(da.mae_c, (0.5 + 1.5 + 1.0 + 2.0 + 0.5) / 5);
assert.equal(da.bias_c, (0.5 - 1.5 + 1.0 + 2.0 - 0.5) / 5);
assert.equal(da.first_day, '2026-10-08');
assert.equal(da.last_day, '2026-10-10');
assert.deepEqual(all.map((s) => s.called_when), ['day_ahead', 'noon'], 'in call order');

// A filtered total is the sum of its cities: the page's "Was it right?"
// follows the selection by adding up exactly the rows it shows.
const pick = new Set(['lucknow', 'tokyo']);
const part = summariseByMoment(rows.filter((r) => pick.has(r.city_key))).find((s) => s.called_when === 'day_ahead');
const perCity = [...pick].map((c) => summariseByMoment(rows.filter((r) => r.city_key === c))
  .find((s) => s.called_when === 'day_ahead'));
for (const k of ['days', 'hits', 'market_days', 'market_hits', 'model_hits_on_market_days']) {
  assert.equal(part[k], perCity.reduce((a, s) => a + s[k], 0), `${k} adds up over the selection`);
}

// ------------------------------------------------------ one card's record
const ev = cityEvidence(rows, 'lucknow', 'day_ahead', '2026-10-08');
assert.equal(ev.n, 2);
assert.equal(ev.hits, 1);
assert.equal(ev.rate, 0.5);
assert.equal(ev.claimed, 0.35);
assert.ok(Math.abs(ev.gap - 0.15) < 1e-12);
assert.equal(ev.marketN, 2);
assert.equal(ev.marketHits, 2);
assert.equal(ev.modelHitsOnMarketDays, 1);
assert.equal(cityEvidence(rows, 'lucknow', 'day_ahead', '2026-10-09').n, 1, 'the window starts where it says');
assert.equal(cityEvidence(rows, 'lucknow', 'noon', '2026-10-01').n, 0, 'one moment at a time');
assert.equal(cityEvidence(rows, 'nowhere', 'day_ahead', '2026-10-01').rate, null);

// ----------------------------------------------------------------- Wilson
const w = wilson(3, 10);
assert.ok(Math.abs(w[0] - 0.108) < 0.001 && Math.abs(w[1] - 0.603) < 0.001, 'the interval the migration quotes');
assert.equal(wilson(0, 0), null);

// ------------------------------------------- the focus against the universe
// docs/FOCUS_PREREG.md: settled calls from the evaluation start; a row counts
// only where BOTH groups have a call for its date, moment and version.
const cmpRows = [];
const dates = ['2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10'];
for (const d of dates) {
  for (const c of active) {
    const inFocus = ['chicago', 'lucknow', 'miami'].includes(c);
    cmpRows.push({ city_key: c, for_date: d, called_when: 'noon', predicted_pct: 40,
      hit: inFocus ? d !== '2026-10-09' || c === 'miami' : c === 'tokyo', forecast_error_c: 0,
      market_hit: true, engine_version: 'git:a', prob_on_winner: inFocus ? 0.4 : 0.2 });
  }
}
// on 10 Oct only the focus cities were captured by a second version: those
// rows have no partner in the rest and must not count
for (const c of ['chicago', 'lucknow']) {
  const r = cmpRows.find((x) => x.city_key === c && x.for_date === '2026-10-10');
  r.engine_version = 'git:b';
}
const cmp = compareFocus(cmpRows, ['chicago', 'lucknow', 'miami'], active, 'noon', '2026-10-08');
assert.equal(cmp.dates, 3, '7 Oct is before the evaluation start');
assert.equal(cmp.focus.n, 3 + 3 + 1, 'the git:b rows on 10 Oct have no partner and are dropped');
assert.equal(cmp.rest.n, 9);
assert.equal(cmp.universe.n, cmp.focus.n + cmp.rest.n);
assert.ok(Math.abs(cmp.focus.probOnWinner - 0.4) < 1e-12);
assert.equal(cmp.focus.marketRate, 1);
// focus rates per date: 8 Oct 3/3, 9 Oct 1/3, 10 Oct 1/1; the rest: 1/3 each day
const want = ((1 - 1 / 3) + (1 / 3 - 1 / 3) + (1 - 1 / 3)) / 3;
assert.ok(Math.abs(cmp.vsRest.mean - want) < 1e-12, `focus minus the rest per date: ${cmp.vsRest.mean} vs ${want}`);
const again = compareFocus(cmpRows, ['chicago', 'lucknow', 'miami'], active, 'noon', '2026-10-08');
assert.deepEqual(again.vsRest.interval, cmp.vsRest.interval, 'seed 11: the same interval twice');
assert.ok(cmp.vsRest.interval[0] <= cmp.vsRest.mean && cmp.vsRest.mean <= cmp.vsRest.interval[1]);
const none = compareFocus(cmpRows, ['chicago'], active, 'noon', '2026-12-01');
assert.equal(none.focus.n, 0);
assert.equal(none.vsRest.mean, null);
assert.equal(none.vsRest.interval, null);

// the generator is deterministic and in [0, 1)
const g1 = mulberry32(11), g2 = mulberry32(11);
for (let i = 0; i < 5; i++) {
  const a = g1(), b = g2();
  assert.equal(a, b);
  assert.ok(a >= 0 && a < 1);
}

console.log('focus: the selection, totals that follow it, one card\'s record, and the focus against the universe');
