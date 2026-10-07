// ===========================================================================
// STRATEGY LEARNING IS SWITCHED ON (migration 20261007100000; Hassan, 7 Oct:
// "turn learning on").
//
// After the 5 Oct stop (20261005190000): the learning loop is expected to log
// again, exactly as seeded, and nothing else the stop did comes back; its
// table is fresh within 30 h again; settings.strategy_learning is enabled
// and says why. Re-runnable, and safe on a database without the freshness
// spec (the paper contracts' fixture).
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const SEED = MIG('20260930001000_a_dispatched_run_that_never_logged.sql');
const STOP = MIG('20261005190000_five_fits_that_reach_no_price_stop.sql');
const ON = MIG('20261007100000_strategy_learning_is_switched_on.sql');
const FILES = ['archive_observations.yml', 'forecasts.yml', 'observations.yml', 'paper_trade_log.yml',
               'pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml', 'weather_model.yml'];
const STILL_STOPPED = ['model_promotion', 'calibration', 'trajectory', 'hit_tournament'];

const fixture = (withSpec) => `
  create role anon; create role authenticated; create role service_role;
  create table public.settings (key text primary key, value jsonb not null,
                                updated_at timestamptz not null default now());
  insert into public.settings (key, value) values
    ('strategy_learning', '{"enabled": false, "why": "Plan v2 P5.8: until the replay."}'),
    ('risk_rails', '{"applies": true, "max_price": 0.97}');
  create table public.clock_schedule (file text primary key, hours_utc int[], weekdays_utc int[], inputs jsonb,
                                     updated_at timestamptz not null default now());
  insert into public.clock_schedule (file) values ${FILES.map((f) => `('${f}')`).join(', ')};
  create table public.ingest_log (log_id bigserial primary key, job text, status text, rows int,
                                  rows_written int, started_at timestamptz, detail jsonb,
                                  logged_at timestamptz not null default now());
  create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
    language sql as $$ insert into public.ingest_log (job, status, rows, detail) values (p_job, p_status, p_rows, p_detail) $$;
  create function public.should_run(p_job text, p_trigger text) returns jsonb
    language sql as $$ select '{"run": true}'::jsonb $$;
  create table public.book_snapshots (observed_at timestamptz);
  create table public.weather_forecasts (run_at timestamptz);
  create table public.anomalies (detected_at timestamptz);
  create table public.city_volume (volume_usd numeric, n_trades bigint);
  create view public.v_city_volume as select * from public.city_volume;
  ${withSpec ? `
  create table public.data_freshness_spec (table_name text primary key, ts_column text, fresh_hours numeric,
                                           layer text not null, plain_english text);
  insert into public.data_freshness_spec values
    ('strategy_params', 'fitted_at', 30, 'model', 'What the nightly learning loop fitted.'),
    ('derived_hit_summary', 'computed_at', 48, 'model', 'The hit tournament.'),
    ('derived_forecast_skill', 'computed_at', 200, 'model', 'Skill: still refitted nightly.');` : ''}
`;

(async () => {
  const db = new PGlite();
  await db.exec(fixture(true));
  await db.exec(SEED);
  const one = async (sql) => (await db.query(sql)).rows;
  const expected = async () => one(`select file, job, within_minutes, note from public.clock_expected_jobs order by file, job`);
  const seeded = await expected();
  const learn = seeded.find((r) => r.file === 'pipeline_daily.yml' && r.job === 'P5.8_strategy_learn');
  assert.ok(learn, 'the seed expects the learning loop');

  await db.exec(STOP);
  const stopped = await expected();
  assert.ok(!stopped.some((r) => r.job === 'P5.8_strategy_learn'));
  const riskBefore = (await one(`select value from public.settings where key = 'risk_rails'`))[0].value;

  await db.exec(ON);
  // 1. The loop is expected again exactly as seeded; nothing else the stop
  //    removed comes back, and no other expectation moves.
  const on = await expected();
  assert.deepEqual(on, [...stopped, learn].sort((a, b) => (a.file + a.job < b.file + b.job ? -1 : 1)));
  for (const job of STILL_STOPPED) {
    assert.ok(!on.some((r) => r.file === 'pipeline_daily.yml' && r.job === job), `${job} stays stopped`);
  }
  // 2. Its table is fresh within 30 h again and says learning is on; the
  //    other stopped tables keep their stop.
  const spec = Object.fromEntries((await one(`select table_name, fresh_hours, plain_english from public.data_freshness_spec`))
    .map((r) => [r.table_name, r]));
  assert.equal(Number(spec.strategy_params.fresh_hours), 30);
  assert.match(spec.strategy_params.plain_english, /^What the nightly learning loop fitted/);
  assert.match(spec.strategy_params.plain_english, /Learning is on \(7 Oct\)/);
  assert.equal(spec.derived_hit_summary.fresh_hours, null);
  assert.match(spec.derived_hit_summary.plain_english, /^Stopped \(WXPredict build, wave A\.3\)/);
  assert.equal(Number(spec.derived_forecast_skill.fresh_hours), 200);
  // 3. Learning is enabled and says why and who; no other setting moves.
  const sl = (await one(`select value from public.settings where key = 'strategy_learning'`))[0].value;
  assert.equal(sl.enabled, true);
  assert.match(sl.why, /Hassan: "turn learning on"/);
  assert.match(sl.why, /Nothing reaches capital/);
  assert.deepEqual((await one(`select value from public.settings where key = 'risk_rails'`))[0].value, riskBefore);

  // Re-runnable: a second run leaves the same rows.
  await db.exec(ON);
  assert.deepEqual(await expected(), on);
  assert.equal((await one(`select value from public.settings where key = 'strategy_learning'`))[0].value.enabled, true);

  // A database without the freshness spec, and one where the setting row was
  // never created, take it too.
  const bare = new PGlite();
  await bare.exec(fixture(false));
  await bare.exec(`delete from public.settings where key = 'strategy_learning'`);
  await bare.exec(SEED);
  await bare.exec(STOP);
  await bare.exec(ON);
  assert.equal((await bare.query(`select value from public.settings where key = 'strategy_learning'`)).rows[0].value.enabled, true);
  assert.equal((await bare.query(`select count(*)::int n from public.clock_expected_jobs where job = 'P5.8_strategy_learn'`)).rows[0].n, 1);

  console.log('PASS: strategy-learning-on: the learning loop is expected again exactly as seeded and the other four stopped fits stay stopped; strategy_params is fresh within 30 h again, the other stopped tables keep their stop; settings.strategy_learning is enabled with why, no other setting moves; re-runnable, and safe without the freshness spec or the setting row');
})().catch((e) => { console.error(e); process.exit(1); });
