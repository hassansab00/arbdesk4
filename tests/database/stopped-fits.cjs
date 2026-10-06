// ===========================================================================
// FIVE NIGHTLY FITS WHOSE OUTPUT REACHES NO PRICE STOP (WXPredict build, A.3).
//
// pipeline_daily switches off model promotion, the calibration refit,
// strategy learning, the trajectory fit and the hit tournament. Migration
// 20261005190000 does what stopping them needs: their jobs are no longer
// expected to log (else every run reads "missing"), their tables' freshness
// limits go, the calibration status says "stopped" rather than "stale", and
// what two of them last wrote stops pricing (Codex on #316): a calibration map
// claiming `applies` and trajectory cells marked applied are switched off, with
// the row saying so. "Stopped" is never shown while a stored map claims
// `applies`. This holds each, and that nothing else moves.
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
// The engine's door to the trajectory, as sql/ad4_86_trajectory.sql builds it.
const TRAJECTORY = fs.readFileSync(path.join(__dirname, '..', '..', 'sql', 'ad4_86_trajectory.sql'), 'utf-8');
const APPLIED_VIEW = TRAJECTORY.match(/create or replace view v_trajectory_applied as[\s\S]*?;/)[0];
const PREFIX = 'Stopped (WXPredict build, wave A.3): applied when its nightly fit was switched off, so no longer priced. ';

const FILES = ['archive_observations.yml', 'forecasts.yml', 'observations.yml', 'paper_trade_log.yml',
               'pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml', 'weather_model.yml'];
const STOPPED = ['model_promotion', 'calibration', 'P5.8_strategy_learn', 'trajectory', 'hit_tournament'];
const TABLES = ['derived_hit_recipe', 'derived_hit_tournament', 'derived_hit_summary',
                'derived_trajectory', 'derived_model_promotion', 'strategy_params'];

// settings and derived_trajectory carry the live column types and NOT NULLs
// (information_schema, 5 Oct). The paper contracts' fixture has settings and
// no derived_trajectory, which `bare` below mirrors.
const fixture = (withSpec, withTrajectory) => `
  create role anon; create role authenticated; create role service_role;
  create table public.settings (key text primary key, value jsonb not null,
                                updated_at timestamptz not null default now());
  ${withTrajectory ? `
  create table public.derived_trajectory (city_key text not null, local_hour int not null, n_days int not null,
    climb_n_days int, sd_ratio numeric not null, crps_trajectory numeric, crps_forecast numeric,
    crps_gain numeric, applied boolean not null, reason text not null, computed_at timestamptz not null,
    primary key (city_key, local_hour));
  ${APPLIED_VIEW}` : ''}
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
  await db.exec(fixture(true, true));
  await db.exec(SEED);
  for (const f of ADDED) await db.exec(MIG(f));
  const one = async (sql, params = []) => (await db.query(sql, params)).rows;
  const expected = async () => (await one(
    `select file, job, within_minutes from public.clock_expected_jobs order by file, job`));

  // What two of the fits last wrote, switched ON, as a night before this
  // lands could leave it: a map claiming `applies`, two applied cells.
  await db.exec(`
    insert into public.settings (key, value, updated_at) values
      ('calibration_map', '{"applies": true, "method": "temperature", "T": 1.42, "evidence_scope": "frozen_day_ahead_v1", "fitted_at": "2026-10-06"}',
       now() - interval '2 days'),
      ('risk_rails', '{"applies": true, "max_price": 0.97}', now() - interval '9 days');
    insert into public.derived_trajectory values
      ('nyc', 14, 31, 30, 0.91, 0.40, 0.44, 0.04, true,  'beat the floored forecast on 31 days', now() - interval '1 day'),
      ('nyc', 15, 31, 30, 1.02, 0.30, 0.31, 0.01, true,  'beat the floored forecast on 31 days', now() - interval '1 day'),
      ('lon', 14, 12, 12, 1.00, 0.50, 0.45, -0.05, false, 'shadow: the forecast is better', now() - interval '1 day');`);
  const settings = async () => (await one(`select key, value, updated_at from public.settings order by key`));
  const cells = async () => (await one(`select * from public.derived_trajectory order by city_key, local_hour`));
  const settingsBefore = await settings();
  const cellsBefore = await cells();
  assert.equal((await one('select count(*)::int n from public.v_trajectory_applied'))[0].n, 2);

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

  // 4. The map no longer applies; it keeps every field it had and says who
  //    stopped it and what it said before. Every other setting is untouched.
  const settingsAfter = await settings();
  const map = settingsAfter.find((r) => r.key === 'calibration_map');
  const mapBefore = settingsBefore.find((r) => r.key === 'calibration_map');
  const { stopped, ...rest } = map.value;
  assert.deepEqual(rest, { ...mapBefore.value, applies: false });
  assert.equal(stopped.applies_before, true);
  assert.match(stopped.by, /^WXPredict build, wave A\.3 \(migration 20261005190000\)/);
  assert.ok(stopped.at, 'the stop is dated');
  assert.ok(map.updated_at > mapBefore.updated_at, 'the row says it changed');
  assert.deepEqual(settingsAfter.filter((r) => r.key !== 'calibration_map'),
                   settingsBefore.filter((r) => r.key !== 'calibration_map'));

  // 5. No cell is applied, so the engine's view of the trajectory is empty;
  //    each cell that was applied says so; nothing else in any row moves.
  const cellsAfter = await cells();
  assert.equal((await one('select count(*)::int n from public.v_trajectory_applied'))[0].n, 0);
  assert.equal(cellsAfter.length, cellsBefore.length, 'no cell is deleted');
  cellsBefore.forEach((b, i) => {
    assert.deepEqual(cellsAfter[i], b.applied ? { ...b, applied: false, reason: PREFIX + b.reason } : b);
  });

  // Re-runnable: a second run changes nothing anywhere.
  await db.exec(STOP);
  assert.deepEqual(await expected(), after);
  assert.deepEqual(await one(`select table_name, fresh_hours, plain_english from public.data_freshness_spec order by 1`), spec);
  assert.deepEqual(await settings(), settingsAfter);
  assert.deepEqual(await cells(), cellsAfter);

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

  // NEVER "STOPPED" OVER A MAP IN FORCE. A run that said `applies` reads
  // stopped with applies false once the stored map no longer claims it...
  await db.query(`update public.ingest_log set detail = detail || '{"applies": true}' where job = 'calibration'`);
  assert.deepEqual(await state(), [{ state: 'stopped', run_status: 'ok', applies: false }]);
  // ...and while the stored map claims `applies` (any value but false or
  // null), the status reads what the last run said, as before the stop:
  // applied while fresh, stale after 36 hours.
  for (const claim of ['true', '1']) {
    await db.query(`update public.settings set value = value || jsonb_build_object('applies', $1::jsonb)
                    where key = 'calibration_map'`, [claim]);
    assert.deepEqual(await state(), [{ state: 'applied', run_status: 'ok', applies: true }], claim);
  }
  await db.query(`update public.ingest_log set logged_at = now() - interval '40 hours' where job = 'calibration'`);
  assert.equal((await state())[0].state, 'stale');
  await db.exec(STOP);
  assert.equal((await one(`select value -> 'applies' a from public.settings where key = 'calibration_map'`))[0].a, false);
  assert.deepEqual(await state(), [{ state: 'stopped', run_status: 'ok', applies: false }]);

  // The browser reads the status (the view runs as its owner) but not the
  // expectations or the settings under it.
  await db.exec('set role anon');
  assert.equal((await one('select state from public.v_calibration_status'))[0].state, 'stopped');
  await assert.rejects(db.query('select * from public.clock_expected_jobs'), /permission denied/);
  await assert.rejects(db.query('select * from public.settings'), /permission denied/);
  await db.exec('reset role');

  // A database without the freshness spec or the trajectory (the paper
  // contracts' fixture) takes the migration too, and a map claiming
  // `applies` with any value but false or null is switched off.
  const bare = new PGlite();
  await bare.exec(fixture(false, false));
  await bare.exec(SEED);
  await bare.exec(`insert into public.settings (key, value) values ('calibration_map', '{"applies": 1, "method": "platt"}')`);
  await bare.exec(STOP);
  await bare.exec(STOP);
  assert.equal((await bare.query(`select count(*)::int n from public.clock_expected_jobs
                                   where file = 'pipeline_daily.yml' and job = 'calibration'`)).rows[0].n, 0);
  const bareMap = (await bare.query(`select value from public.settings where key = 'calibration_map'`)).rows[0].value;
  assert.equal(bareMap.applies, false);
  assert.equal(bareMap.stopped.applies_before, 1);
  assert.equal(bareMap.method, 'platt');

  console.log('PASS: stopped-fits: the five stopped fits are no longer expected to log and nothing else is; their tables lose their freshness limit and say why, once; a calibration map claiming applies and every applied trajectory cell are switched off and say so, nothing else moves and nothing is deleted; the calibration status reads stopped only while its job is not expected and no stored map claims applies (stale, applied or fitted otherwise); anon reads the status, not the expectations or settings; re-runnable, and safe without the freshness spec or the trajectory');
})().catch((e) => { console.error(e); process.exit(1); });
