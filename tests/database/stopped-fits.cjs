// ===========================================================================
// FIVE NIGHTLY FITS WHOSE OUTPUT REACHES NO PRICE STOP (WXPredict build, A.3).
//
// pipeline_daily switches off model promotion, the calibration refit,
// strategy learning, the trajectory fit and the hit tournament. Migration
// 20261005190000 does what stopping them needs: their jobs are no longer
// expected to log (else every run reads "missing"), their tables' freshness
// limits go, and the calibration status says "stopped" rather than "stale".
// This holds each of the three, and that nothing else moves.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const SEED = MIG('20260930001000_a_dispatched_run_that_never_logged.sql');
const ADDED = ['20261003170000_the_ladder_queue_is_expected_to_log.sql',
               '20261004170000_the_minutes_fit_the_pro_plan.sql'];
const STOP = MIG('20261005190000_five_fits_that_reach_no_price_stop.sql');

const FILES = ['archive_observations.yml', 'forecasts.yml', 'observations.yml', 'paper_trade_log.yml',
               'pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml', 'weather_model.yml'];
const STOPPED = ['model_promotion', 'calibration', 'P5.8_strategy_learn', 'trajectory', 'hit_tournament'];
const TABLES = ['derived_hit_recipe', 'derived_hit_tournament', 'derived_hit_summary',
                'derived_trajectory', 'derived_model_promotion', 'strategy_params'];

const fixture = (withSpec) => `
  create role anon; create role authenticated; create role service_role;
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
    ${TABLES.map((t) => `('${t}', 'computed_at', 48, 'model', 'What ${t} holds.')`).join(',\n    ')},
    ('derived_forecast_skill', 'computed_at', 200, 'model', 'Skill: still refitted nightly.');` : ''}
`;

(async () => {
  const db = new PGlite();
  await db.exec(fixture(true));
  await db.exec(SEED);
  for (const f of ADDED) await db.exec(MIG(f));
  const one = async (sql, params = []) => (await db.query(sql, params)).rows;
  const expected = async () => (await one(
    `select file, job, within_minutes from public.clock_expected_jobs order by file, job`));

  const before = await expected();
  for (const job of STOPPED) {
    assert.ok(before.some((r) => r.file === 'pipeline_daily.yml' && r.job === job), `${job} was expected`);
  }

  await db.exec(STOP);
  const after = await expected();
  // 1. Exactly the five rows go; every other expectation is untouched.
  assert.deepEqual(after, before.filter((r) => !(r.file === 'pipeline_daily.yml' && STOPPED.includes(r.job))));
  assert.equal(before.length - after.length, 5);
  for (const job of ['measure_skill', 'engine_replay', 'forecast_postprocess', 'databank', 'ingest_forecasts']) {
    assert.ok(after.some((r) => r.file === 'pipeline_daily.yml' && r.job === job), `${job} is still expected`);
  }

  // 2. The freshness spec: no limit for the six tables, a description that says
  //    so, once; every other row unchanged.
  const spec = await one(`select table_name, fresh_hours, plain_english from public.data_freshness_spec order by 1`);
  for (const r of spec) {
    if (TABLES.includes(r.table_name)) {
      assert.equal(r.fresh_hours, null, r.table_name);
      assert.equal(r.plain_english,
        `Stopped (WXPredict build, wave A.3): no longer refitted nightly, so these are its last rows. What ${r.table_name} holds.`);
    } else {
      assert.equal(Number(r.fresh_hours), 200);
      assert.equal(r.plain_english, 'Skill: still refitted nightly.');
    }
  }

  // Re-runnable: a second run changes nothing anywhere.
  await db.exec(STOP);
  assert.deepEqual(await expected(), after);
  assert.deepEqual(await one(`select table_name, fresh_hours, plain_english from public.data_freshness_spec order by 1`), spec);

  // 3. The calibration status reads 'stopped' over the last real run...
  const state = async () => (await one('select state, run_status, applies from public.v_calibration_status'));
  assert.deepEqual(await state(), [{ state: 'never_run', run_status: 'never_run', applies: false }],
    'no run on record still returns its row');
  await db.query(`insert into public.ingest_log (job, status, rows_written, detail, logged_at)
                  values ('calibration', 'ok', 386, '{"applies": false, "method": "temperature", "gate_unmet": []}',
                          now() - interval '40 hours')`);
  assert.deepEqual(await state(), [{ state: 'stopped', run_status: 'ok', applies: false }]);

  // ...and only because the step is no longer expected: put the row back and
  // the same run reads 'stale' again, as before this migration; a fresh run
  // reads what the fit said.
  await db.query(`insert into public.clock_expected_jobs values ('pipeline_daily.yml', 'calibration', 105, null)`);
  assert.equal((await state())[0].state, 'stale');
  await db.query(`update public.ingest_log set logged_at = now() - interval '1 hour' where job = 'calibration'`);
  assert.equal((await state())[0].state, 'fitted_not_applied');
  await db.exec(STOP);
  assert.equal((await state())[0].state, 'stopped');

  // The browser reads the status (the view runs as its owner) but not the
  // expectations under it.
  await db.exec('set role anon');
  assert.equal((await one('select state from public.v_calibration_status'))[0].state, 'stopped');
  await assert.rejects(db.query('select * from public.clock_expected_jobs'), /permission denied/);
  await db.exec('reset role');

  // A database without the freshness spec (the paper contracts' fixture)
  // takes the migration too.
  const bare = new PGlite();
  await bare.exec(fixture(false));
  await bare.exec(SEED);
  await bare.exec(STOP);
  await bare.exec(STOP);
  assert.equal((await bare.query(`select count(*)::int n from public.clock_expected_jobs
                                   where file = 'pipeline_daily.yml' and job = 'calibration'`)).rows[0].n, 0);

  console.log('PASS: stopped-fits: the five stopped fits are no longer expected to log and nothing else is; their tables lose their freshness limit and say why, once; the calibration status reads stopped only while its job is not expected (stale, then fitted, when it is); anon reads the status, not the expectations; re-runnable, and safe without the freshness spec');
})().catch((e) => { console.error(e); process.exit(1); });
