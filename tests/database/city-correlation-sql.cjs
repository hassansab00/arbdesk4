// ===========================================================================
// WHAT recompute_correlation RETURNS, for scripts/city_correlation.py to match
// (Fresh Supabase, part 2a, 8 Oct).
//
// The SQL function (sql/ad4_capacity_correlation.sql, byte for byte the live
// one on 8 Oct) runs here over seeded random readings and
// forecasts: several forecasts a city-day, lead 2 rows it must ignore,
// forecasts without a maximum, city-days with no readings and UTC days whose
// readings all lack a temperature. Its rows go to
// tests/fixtures/city_correlation_sql.json beside the inputs;
// tests/test_city_correlation.py runs the Python over the same inputs.
//
//   node tests/database/city-correlation-sql.cjs           checks the fixture
//   node tests/database/city-correlation-sql.cjs --write   rewrites it
//
// City keys are letters only: PGlite orders text bytewise and the live
// database by en_US, and the two agree on letters
// (weather_history.text_key is pinned to the live order by its own tests).
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const FIXTURE = path.join(ROOT, 'tests', 'fixtures', 'city_correlation_sql.json');
const SRC = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_capacity_correlation.sql'), 'utf-8');

function recomputeCorrelation() {
  const start = SRC.search(/create or replace function (public\.)?recompute_correlation\(/);
  assert.ok(start >= 0, 'recompute_correlation in sql/ad4_capacity_correlation.sql');
  const quote = SRC.slice(SRC.indexOf(' as $', start) + 4).match(/^\$\w*\$/)[0];
  const open = SRC.indexOf(quote, start);
  const close = SRC.indexOf(`${quote};`, open + quote.length);
  return SRC.slice(start, close + quote.length + 1);
}

// mulberry32: the same rows every run.
function rng(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

(async () => {
  const rand = rng(20261008);
  const cities = ['amman', 'berlin', 'cairo', 'dakar', 'essen', 'fargo', 'genoa'];
  const anchor = new Date(Date.UTC(2026, 9, 8));                 // 8 Oct 2026
  const day = (k) => new Date(anchor.getTime() - k * 86400000).toISOString().slice(0, 10);

  const forecasts = [];
  const readings = [];
  for (let k = 0; k <= 30; k += 1) {
    for (const c of cities) {
      const base = 15 + 10 * rand();
      // the readings of a UTC day: none (1 in 12), all without a temperature
      // (1 in 25), or hourly with an occasional gap
      const r = rand();
      if (r >= 1 / 12) {
        const blank = r < 1 / 12 + 1 / 25;
        for (let h = 0; h < 24; h += 1) {
          if (!blank && rand() < 0.05) continue;
          const t = blank ? null : Math.round((base + 6 * Math.sin((h - 9) / 24 * 2 * Math.PI) + 2 * rand()) * 10) / 10;
          readings.push({ city_key: c, valid_at: `${day(k)}T${String(h).padStart(2, '0')}:20:00+00:00`, temp_c: t });
        }
      }
      // 0-3 lead-1 forecasts, a lead-2 one, sometimes one without a maximum
      const n = Math.floor(rand() * 4);
      for (let i = 0; i < n; i += 1) {
        forecasts.push({ city_key: c, model: `m${i}`, run_at: `${day(k + 1)}T0${i}:00:00+00:00`, for_date: day(k),
                         lead_days: 1, forecast_max_c: Math.round((base + 6 + 4 * (rand() - 0.5)) * 100) / 100 });
      }
      forecasts.push({ city_key: c, model: 'm9', run_at: `${day(k + 2)}T00:00:00+00:00`, for_date: day(k),
                       lead_days: 2, forecast_max_c: Math.round((base + 5) * 100) / 100 });
      if (rand() < 0.1) {
        forecasts.push({ city_key: c, model: 'm8', run_at: `${day(k + 1)}T05:00:00+00:00`, for_date: day(k),
                         lead_days: 1, forecast_max_c: null });
      }
    }
  }

  const db = new PGlite();
  await db.exec(`
    set timezone = 'UTC';
    create table weather_forecasts (forecast_id bigserial primary key, city_key text, model text, run_at timestamptz,
      for_date date, lead_days int, forecast_max_c numeric);
    create table weather_observations (obs_id bigserial primary key, city_key text, valid_at timestamptz, temp_c numeric);
    create table derived_city_correlation (city_a text, city_b text, computed_at timestamptz default now(),
      n_days integer, err_corr numeric, primary key (city_a, city_b, computed_at));`);
  for (const f of forecasts) {
    await db.query(`insert into weather_forecasts (city_key, model, run_at, for_date, lead_days, forecast_max_c)
                    values ($1, $2, $3, $4, $5, $6)`, [f.city_key, f.model, f.run_at, f.for_date, f.lead_days, f.forecast_max_c]);
  }
  await db.query(`insert into weather_observations (city_key, valid_at, temp_c)
                  select x->>'city_key', (x->>'valid_at')::timestamptz, (x->>'temp_c')::numeric
                    from json_array_elements($1::json) x`, [JSON.stringify(readings)]);
  await db.exec(recomputeCorrelation());
  // The function reads the readings of the last 180 days by now(); these all are.
  const n = (await db.query('select recompute_correlation() as n')).rows[0].n;
  const rows = (await db.query(`select city_a, city_b, n_days, err_corr::text as err_corr
                                  from derived_city_correlation order by city_a, city_b`)).rows;
  assert.equal(Number(n), rows.length);
  assert.ok(rows.length >= 15, `only ${rows.length} pairs: the fixture tests too little`);

  const fixture = { anchor: day(0), forecasts, readings, rows };
  const text = `${JSON.stringify(fixture)}\n`;
  if (process.argv.includes('--write')) {
    fs.writeFileSync(FIXTURE, text);
    console.log(`wrote ${path.relative(ROOT, FIXTURE)}: ${forecasts.length} forecasts, ${readings.length} readings, ${rows.length} pairs`);
    return;
  }
  assert.equal(fs.readFileSync(FIXTURE, 'utf-8'), text,
    'tests/fixtures/city_correlation_sql.json is not what recompute_correlation returns: run with --write');
  console.log(`PASS: city-correlation-sql: recompute_correlation over the fixture's ${forecasts.length} forecasts and ${readings.length} readings returns its ${rows.length} pairs`);
})().catch((e) => { console.error(e); process.exit(1); });
