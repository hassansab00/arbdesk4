// Main, S10 and the challengers apart (web/lib/lineup.ts; P2.2 part 2, 4 Oct).
// The learning status in the plan's order, the lineup's columns and lines, and
// a blinded version that is never tallied whatever its rows carry.
// Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { learningStatus, lineupDates, lineupTable, shortVersion, utcDate, STAGES } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'lineup.js'));

// ------------------------------------------------------------ the status
const st = (family, version, state, stage, blind = false) =>
  ({ family, version, horizon: 'same day', state, stage, blind, decided_at: '2026-10-04T17:00:00Z',
     decided_by: 'test', evidence: 'test', rollback_to: null, note: null });
const status = [
  st('s10', 'rd1:2026-09-25:389620c0d9', 'retired', 'retired'),
  st('calibration', 'temperature:T=1.141', 'fitted', 'candidate fitting'),
  st('engine_variant', 'da_floor:v1', 'shadow', 'evaluation', true),
  st('s10', 'rd3:2026-09-25:555719d4a1', 'shadow', 'evaluation', true),
  st('s10', 'rd1:2026-09-25:f5372ebb05', 'shadow', 'evaluation'),
  st('engine', 'station-corrected forecast path', 'served', 'serving'),
];
const ordered = learningStatus(status);
assert.deepEqual(ordered.map((s) => s.stage),
  ['serving', 'evaluation', 'evaluation', 'evaluation', 'candidate fitting', 'retired'], 'serving first, retired last');
assert.deepEqual(ordered.slice(1, 4).map((s) => s.version),
  ['rd1:2026-09-25:f5372ebb05', 'rd3:2026-09-25:555719d4a1', 'da_floor:v1'], 'within a stage: engine, S10, the variants');
assert.deepEqual(STAGES.slice(0, 4), ['serving', 'evaluation', 'candidate fitting', 'data capture'], "the plan's four words");
assert.equal(status[0].stage, 'retired', 'sorting copies; the query result is not reordered');

// ------------------------------------------------------------ the dates
assert.deepEqual(lineupDates(new Date('2026-10-04T23:59:00Z')),
  ['2026-10-05', '2026-10-04', '2026-10-03', '2026-10-02', '2026-10-01', '2026-09-30', '2026-09-29', '2026-09-28', '2026-09-27'],
  "tomorrow (the evening-before call's date), today and the 7 before it, on the database's clock (the view's current_date - 7 on)");
assert.equal(lineupDates(new Date('2026-10-04T18:00:00Z'))[0], '2026-10-05',
  'at 18:00Z the evening-before calls for tomorrow are already written (31 live on 4 Oct, review of #305)');
assert.equal(lineupDates(new Date('2026-10-05T00:30:00Z'))[1], '2026-10-05', 'UTC, not the browser zone');
assert.equal(lineupDates(new Date('2026-03-01T12:00:00Z'))[2], '2026-02-28', 'across a month end');
assert.equal(utcDate(new Date('2026-10-04T23:59:00Z'), -1), '2026-10-03', 'the default date: yesterday, mostly settled');

assert.equal(shortVersion('rd1:2026-09-25:f5372ebb05'), 'rd1 f5372eb');
assert.equal(shortVersion('da_floor:v1'), 'da_floor v1');

// ------------------------------------------------------------ the lineup
const row = (city, family, version, extra) => ({
  city_key: city, target_date: '2026-10-03', checkpoint: 'noon', model_family: family, version,
  serving_role: family === 'engine' ? 'served' : 'shadow', as_of: '2026-10-03T11:36:00Z', station: 'EGLL',
  blind: false, withheld: false, top_band_id: 'b2', top_label: '21°C', top_prob: '0.6',
  priced_centre_c: '21.0', uncertainty_c: '0.9', winner_band_id: 'b2', winner_label: '21°C',
  hit: true, prob_on_winner: '0.6', ...extra });
const ENGINE_A = 'forecast:station-correction:2026-10-03';
const rows = [
  // london: everyone right
  row('london', 's10', 'rd1:2026-09-25:f5372ebb05'),
  row('london', 'engine', ENGINE_A),
  // a blinded version whose rows carry an outcome anyway: never counted
  row('london', 's10', 'rd3:2026-09-25:555719d4a1', { blind: true, withheld: true, top_label: null, hit: true }),
  row('london', 'engine_variant', 'da_floor:v1', { blind: true, withheld: true, top_label: null, hit: null,
                                                   winner_label: null, winner_band_id: null }),
  // paris: the engine wrong, S10 right; the engine's version differs by city
  row('paris', 'engine', 'forecast:station-correction:2026-10-02', { top_label: '19°C', hit: false, winner_label: '20°C' }),
  row('paris', 's10', 'rd1:2026-09-25:f5372ebb05', { top_label: '20°C', winner_label: '20°C' }),
  // tokyo: not settled, and S10 not captured
  row('tokyo', 'engine', ENGINE_A, { hit: null, winner_label: null, winner_band_id: null, prob_on_winner: null }),
  // an older S10 file on the same day
  row('athens', 's10', 'rd1:2026-09-25:389620c0d9', { hit: false, winner_label: '30°C' }),
];
const t = lineupTable(rows, status);
assert.deepEqual(t.columns.map((c) => c.key),
  ['engine', 's10|rd1:2026-09-25:389620c0d9', 's10|rd1:2026-09-25:f5372ebb05', 's10|rd3:2026-09-25:555719d4a1',
   'engine_variant|da_floor:v1'], 'the engine first, then S10 by version, then the variants');
const col = Object.fromEntries(t.columns.map((c) => [c.key, c]));
assert.equal(col.engine.version, null, "one engine column whatever version each call recorded");
assert.equal(col.engine.state, 'served');
assert.equal(col['s10|rd1:2026-09-25:389620c0d9'].state, 'retired');
assert.deepEqual([col['s10|rd3:2026-09-25:555719d4a1'].blind, col['engine_variant|da_floor:v1'].blind], [true, true]);
assert.equal(col['s10|rd1:2026-09-25:f5372ebb05'].title, 'S10 remaining-day rd1 f5372eb');

assert.deepEqual(t.lines.map((l) => l.city_key), ['athens', 'london', 'paris', 'tokyo'], 'a line per city, by name');
const line = Object.fromEntries(t.lines.map((l) => [l.city_key, l]));
assert.equal(line.paris.winner_label, '20°C');
assert.equal(line.tokyo.winner_label, null, 'not settled');
assert.equal(line.tokyo.cells['s10|rd1:2026-09-25:f5372ebb05'], undefined, 'a call not captured is absent, not a miss');

const tl = t.tallies;
assert.deepEqual([tl.engine.graded, tl.engine.hits], [2, 1], 'london right, paris wrong, tokyo not graded');
assert.deepEqual(tl['s10|rd1:2026-09-25:f5372ebb05'],
  { graded: 2, hits: 2, common: 2, hitsOnCommon: 2, engineHitsOnCommon: 1 }, 'S10 against the engine on the same city-days');
assert.deepEqual(tl['s10|rd1:2026-09-25:389620c0d9'],
  { graded: 1, hits: 0, common: 0, hitsOnCommon: 0, engineHitsOnCommon: 0 }, 'athens had no engine call to compare');
for (const k of ['s10|rd3:2026-09-25:555719d4a1', 'engine_variant|da_floor:v1']) {
  assert.deepEqual(tl[k], { graded: 0, hits: 0, common: 0, hitsOnCommon: 0, engineHitsOnCommon: 0 },
    `${k}: a blinded version is never tallied, whatever its rows carry`);
}

// A version blinded in the registry is a blinded column even if a row arrives unflagged.
const late = lineupTable([row('rome', 's10', 'rd3:2026-09-25:555719d4a1')], status);
assert.equal(late.columns[0].blind, true);
assert.deepEqual([late.tallies[late.columns[0].key].graded], [0]);
// ...and a blinded row never lends the line its winner.
const only = lineupTable([row('oslo', 's10', 'rd3:2026-09-25:555719d4a1', { blind: true })], status);
assert.equal(only.lines[0].winner_label, null);

assert.deepEqual(lineupTable([], status), { columns: [], lines: [], tallies: {} });

console.log('lineup: the plan\'s stages in order; tomorrow to 7 days back, UTC; the engine once, S10 by version, the variants; blinded columns never tallied');

// ------------------------------------------------------------ retired versions (P2.2 part 3)
{
  const { compactRetired } = require(path.join(__dirname, '..', '.route-test', 'lib', 'lineup.js'));
  const at = (d) => `2026-10-${d}T05:00:00Z`;
  const rows = [
    { ...st('station_correction', 'station-correction:2026-10-07:c', 'served', 'serving'), decided_at: at('07') },
    { ...st('station_correction', 'station-correction:2026-10-06:b', 'retired', 'retired'), decided_at: at('07') },
    { ...st('station_correction', 'station-correction:2026-10-05:a', 'retired', 'retired'), decided_at: at('06') },
    { ...st('station_mos', 'station-mos:2026-10-06:y', 'retired', 'retired'), decided_at: at('07') },
    { ...st('station_mos', 'station-mos:2026-10-05:x', 'retired', 'retired'), decided_at: at('06') },
    st('s10', 'rd1:2026-09-25:389620c0d9', 'retired', 'retired'),
    st('engine_variant', 'da_floor:v1', 'shadow', 'evaluation', true),
  ];
  const c = compactRetired(rows);
  assert.equal(c.hidden, 2, 'one retired line per family; the older ones counted');
  assert.deepEqual(c.rows.filter((r) => r.stage === 'retired').map((r) => r.version),
    ['station-correction:2026-10-06:b', 'station-mos:2026-10-06:y', 'rd1:2026-09-25:389620c0d9'], 'the newest retired of each');
  assert.equal(c.rows.filter((r) => r.stage !== 'retired').length, 2, 'nothing standing is ever hidden');
  assert.deepEqual(compactRetired([]), { rows: [], hidden: 0 });
}
console.log('lineup: retired versions compacted to the newest per family, the rest counted');
