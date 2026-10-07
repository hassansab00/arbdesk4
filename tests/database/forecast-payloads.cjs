// ===========================================================================
// A WEEK-OLD FORECAST'S PAYLOAD GOES TO THE REPOSITORY; THE ROW AND ITS
// NUMBERS STAY (WXPredict build 2.A,
// 20261007170000_forecast_payloads_go_to_the_repo.sql).
//
// Each export view offers exactly the payloads still on their rows; each
// strip takes only rows dated at least 7 days back, only the exact count the
// archive read back from its committed file, never a row the mirror has not
// had, and changes nothing but the payload: every other column of every row
// is identical after it, and so is every reader that does not open the
// payload (v_model_forecast_current reads it, from yesterday on). The
// reclaim takes derived_model_forecast, the views and functions are the
// service role's, the migration re-runs, and a database without
// derived_model_forecast still takes it.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261007170000_forecast_payloads_go_to_the_repo.sql'), 'utf-8');

const BASE = `
  create role anon; create role authenticated; create role service_role;
  create schema cron;
  create table cron.calls (jobname text, schedule text, command text);
  create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
    language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
  -- The live shape (information_schema and pg_constraint, 7 Oct).
  create table public.weather_forecasts (forecast_id bigserial primary key, city_key text, model text,
    run_at timestamptz, observed_at timestamptz not null default now(), for_date date, lead_days int,
    forecast_max_c numeric, variables jsonb, source text,
    unique (city_key, model, run_at, for_date));
`;
const MODEL_TABLE = `
  create table public.derived_model_forecast (city_key text not null, for_date date not null,
    run_at timestamptz not null, lead_days int, predicted_max_c numeric, nws_max_c numeric,
    prev_max_c numeric, prev_source text, contributions jsonb, inputs jsonb, model_mae_c numeric,
    persistence_mae_c numeric, beats_persistence boolean, predicted_at timestamptz not null default now(),
    model_version text, primary key (city_key, for_date, run_at));
  grant select on public.derived_model_forecast to anon, authenticated;
  -- The one reader of the payloads (live definition, 7 Oct).
  create view public.v_model_forecast_current as
    select distinct on (city_key, for_date) city_key, for_date, run_at, lead_days, predicted_max_c,
           contributions, inputs, predicted_at
      from public.derived_model_forecast where for_date >= current_date - 1
     order by city_key, for_date, run_at desc;
`;

(async () => {
  // A database without derived_model_forecast (the paper contracts' fixture).
  const bare = new PGlite();
  await bare.exec(BASE);
  await bare.exec(MIG);
  await bare.exec(MIG);
  assert.equal((await bare.query(`select to_regclass('public.v_model_payloads_export') is null as none`)).rows[0].none, true);
  assert.equal((await bare.query(`select to_regclass('public.v_forecast_variables_export') is not null as ok`)).rows[0].ok, true);

  const db = new PGlite();
  await db.exec(BASE + MODEL_TABLE);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  const q = async (sql) => (await db.query(sql)).rows;
  const d = (n) => `current_date - ${n}`;
  const obs = (n) => `now() - interval '${n} days'`;
  const vars = (k) => `'{"issued_at": "x", "max_at_local": "14:00", "min_c": ${k}, "n_hours": 24, "peak_window": "12-16"}'::jsonb`;

  // Forecasts: two payloads 10 and 8 days back, one 7 back (kept: the cut is
  // dated before current_date - 7), today's, a previous-runs row that never
  // had a payload, and one already moved.
  await db.exec(`insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, variables, source) values
    ('nyc',    'open_meteo_forecast', now() - interval '11 days', ${obs(11)}, ${d(10)}, 1, 20.5, ${vars(11)}, 'open-meteo'),
    ('london', 'open_meteo_forecast', now() - interval '9 days',  ${obs(9)},  ${d(8)},  1, 15.0, ${vars(9)},  'open-meteo'),
    ('london', 'nws',                 now() - interval '9 days',  ${obs(9)},  ${d(8)},  1, 15.2, ${vars(8)},  'api.weather.gov'),
    ('paris',  'open_meteo_forecast', now() - interval '8 days',  ${obs(8)},  ${d(7)},  1, 18.0, ${vars(7)},  'open-meteo'),
    ('paris',  'open_meteo_forecast', now(),                      now(),      current_date, 0, 19.0, ${vars(1)}, 'open-meteo'),
    ('nyc',    'open_meteo_best_match', now() - interval '12 days', ${obs(2)}, ${d(10)}, 1, 21.0, null, 'open-meteo-previous-runs'),
    ('tokyo',  'open_meteo_forecast', now() - interval '20 days', ${obs(20)}, ${d(19)}, 1, 25.0, '{"variables_in_repo": true}'::jsonb, 'open-meteo')`);
  // Model forecasts: 9 and 8 days back, 7 back (kept), yesterday and today
  // (v_model_forecast_current's), one already moved.
  const pay = (k) => `'{"intercept": ${k}}'::jsonb, '{"cloud_mean": ${k}}'::jsonb`;
  await db.exec(`insert into public.derived_model_forecast (city_key, for_date, run_at, lead_days, predicted_max_c, contributions, inputs, predicted_at, model_version) values
    ('nyc',    ${d(9)}, timestamptz '2026-09-01 00:00:00.000001+00', 1, 20.1, ${pay(1)}, ${obs(10)}, 'wm-1'),
    ('nyc',    ${d(9)}, timestamptz '2026-09-01 00:00:00.000002+00', 1, 20.2, ${pay(2)}, ${obs(10)}, 'wm-1'),
    ('london', ${d(8)}, timestamptz '2026-09-02 00:00:00+00',        1, 15.1, ${pay(3)}, ${obs(9)},  'wm-1'),
    ('paris',  ${d(7)}, timestamptz '2026-09-03 00:00:00+00',        1, 18.1, ${pay(4)}, ${obs(8)},  'wm-1'),
    ('paris',  ${d(1)}, timestamptz '2026-09-04 00:00:00+00',        1, 18.5, ${pay(5)}, ${obs(2)},  'wm-1'),
    ('paris',  current_date, timestamptz '2026-09-05 00:00:00+00',   0, 18.9, ${pay(6)}, now(),      'wm-1'),
    ('tokyo',  ${d(20)}, timestamptz '2026-08-20 00:00:00+00',       1, 24.0, null, '{"payload_in_repo": true}'::jsonb, ${obs(21)}, 'wm-1')`);

  const numbers = {
    wf: `select forecast_id, city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source from public.weather_forecasts`,
    dmf: `select city_key, for_date, run_at, lead_days, predicted_max_c, predicted_at, model_version from public.derived_model_forecast`,
  };
  const diff = async (a, b) => (await q(`select count(*)::int as n from (${a} except all ${b}) x`))[0].n;
  const wfPrune = async (args) => (await q(`select public.prune_forecast_variables(${args}) as r`))[0].r;
  const dmfPrune = async (args) => (await q(`select public.prune_model_payloads(${args}) as r`))[0].r;

  // THE VIEWS: the payloads still on their rows, every age; not the row that
  // never had one, nor the one already moved.
  assert.equal((await q(`select count(*)::int as n from public.v_forecast_variables_export`))[0].n, 5);
  assert.equal((await q(`select count(*)::int as n from public.v_model_payloads_export`))[0].n, 6);
  const keys = (await q(`select payload_key from public.v_model_payloads_export order by payload_key`)).map((r) => r.payload_key);
  assert.equal(new Set(keys).size, keys.length, 'two rows share an export key');
  for (const k of keys) assert.match(k, /^[a-z_]+\|\d{4}-\d{2}-\d{2}\|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$/, k);

  // FLOORS AND THE VERIFIED COUNT.
  for (const [fn, prune] of [['prune_forecast_variables', wfPrune], ['prune_model_payloads', dmfPrune]]) {
    for (const k of ['6', 'null']) {
      const r = await prune(k);
      assert.equal(r.ok, false, `${fn} ${k}`);
      assert.match(r.error, /at least 7/);
    }
    const blind = await prune('7, false');
    assert.equal(blind.ok, false);
    assert.match(blind.error, /p_expected_rows is required/);
  }

  // Dry runs: the payloads dated before current_date - 7.
  let dry = await wfPrune('7, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 3);
  assert.equal(Number(dry.would_keep), 2);
  dry = await dmfPrune('7, true');
  assert.equal(Number(dry.would_delete), 3);
  assert.equal(Number(dry.would_keep), 3);
  // A cutoff newer than the floor is moved back to it; an older one honoured.
  assert.equal(Number((await wfPrune('7, true, current_date')).would_delete), 3);
  assert.equal(Number((await wfPrune(`7, true, ${d(9)}`)).would_delete), 1);
  // A count that differs from the file strips nothing.
  const wrong = await wfPrune('7, false, null, 2');
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal((await q(`select count(*)::int as n from public.v_forecast_variables_export`))[0].n, 5);

  // THE MIRROR: a payload written since yesterday's UTC midnight for an old
  // date is not in the repo mirror yet; nothing goes until it is.
  const yesterdayMidnight = `(date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC'`;
  await db.exec(`update public.weather_forecasts set observed_at = ${yesterdayMidnight} where city_key = 'nyc' and source = 'open-meteo'`);
  const young = await wfPrune('7, false, null, 3');
  assert.equal(young.ok, false, JSON.stringify(young));
  assert.match(young.error, /not in the repo mirror yet/);
  await db.exec(`update public.weather_forecasts set observed_at = ${obs(11)} where city_key = 'nyc' and source = 'open-meteo'`);
  await db.exec(`update public.derived_model_forecast set predicted_at = ${yesterdayMidnight} where city_key = 'london'`);
  const youngModel = await dmfPrune('7, false, null, 3');
  assert.equal(youngModel.ok, false);
  assert.match(youngModel.error, /not in the repo mirror yet/);
  await db.exec(`update public.derived_model_forecast set predicted_at = ${obs(9)} where city_key = 'london'`);

  // THE STRIPS: the exact count, then nothing but the payload has changed.
  await db.exec(`create table before_wf as ${numbers.wf}; create table before_dmf as ${numbers.dmf};
                 create table before_current as select * from public.v_model_forecast_current;`);
  const wfDone = await wfPrune('7, false, null, 3');
  assert.equal(wfDone.ok, true, JSON.stringify(wfDone));
  assert.equal(Number(wfDone.deleted), 3);
  const dmfDone = await dmfPrune('7, false, null, 3');
  assert.equal(dmfDone.ok, true, JSON.stringify(dmfDone));
  assert.equal(Number(dmfDone.deleted), 3);

  assert.equal(await diff(numbers.wf, 'select * from before_wf'), 0, 'a forecast number changed');
  assert.equal(await diff('select * from before_wf', numbers.wf), 0, 'a forecast row went');
  assert.equal(await diff(numbers.dmf, 'select * from before_dmf'), 0, 'a model forecast number changed');
  assert.equal(await diff('select * from before_dmf', numbers.dmf), 0, 'a model forecast row went');
  assert.equal(await diff('select * from public.v_model_forecast_current', 'select * from before_current'), 0);
  assert.equal(await diff('select * from before_current', 'select * from public.v_model_forecast_current'), 0);
  // The marks, and the kept payloads whole.
  const marked = await q(`select city_key, for_date - current_date as n, variables from public.weather_forecasts where variables ? 'variables_in_repo' order by city_key, model`);
  assert.equal(marked.length, 4);
  assert.deepEqual((await q(`select variables->>'min_c' as m from public.weather_forecasts where city_key = 'paris' and for_date = ${d(7)}`))[0], { m: '7' });
  assert.equal((await q(`select count(*)::int as n from public.derived_model_forecast where inputs ? 'payload_in_repo' and contributions is null`))[0].n, 4);
  assert.equal((await q(`select count(*)::int as n from public.derived_model_forecast where contributions is not null`))[0].n, 3);
  // The views no longer offer what moved; a second strip has nothing to do.
  assert.equal((await q(`select count(*)::int as n from public.v_forecast_variables_export`))[0].n, 2);
  assert.equal((await q(`select count(*)::int as n from public.v_model_payloads_export`))[0].n, 3);
  assert.equal(Number((await wfPrune('7, false, null, 0')).deleted), 0);
  assert.equal(Number((await dmfPrune('7, false, null, 0')).deleted), 0);

  // THE RECLAIM takes derived_model_forecast; weather_forecasts already was.
  for (const t of ['derived_model_forecast', 'weather_forecasts', 'derived_city_correlation', 'band_probabilities']) {
    assert.equal((await q(`select public.request_reclaim('${t}') as r`))[0].r.ok, true, t);
  }
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = await q(`select jobname, schedule, command from cron.calls`);
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_derived_model_forecast' && c.schedule === '15 7 * * 1'
    && c.command === 'VACUUM (FULL, ANALYZE) public.derived_model_forecast'), JSON.stringify(calls));

  // The views and functions are the service role's alone; the table reads as before.
  for (const v of ['v_forecast_variables_export', 'v_model_payloads_export']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await q(`select has_table_privilege('${role}', 'public.${v}', 'select') as ok`))[0].ok, false, `${role} reads ${v}`);
    }
    assert.equal((await q(`select has_table_privilege('service_role', 'public.${v}', 'select') as ok`))[0].ok, true);
  }
  for (const fn of ['public.prune_forecast_variables(integer,boolean,date,bigint)',
                    'public.prune_model_payloads(integer,boolean,date,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await q(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`))[0].ok, false, `${role} runs ${fn}`);
    }
    assert.equal((await q(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`))[0].ok, true);
  }
  assert.equal((await q(`select has_table_privilege('anon', 'public.derived_model_forecast', 'select') as ok`))[0].ok, true);

  console.log('PASS: forecast-payloads: each export view offers exactly the payloads still on their rows, one unique key each; a 7-day floor, the exact verified count or nothing, never a payload the mirror has not had; after the strips every other column of every row is identical, v_model_forecast_current reads the same rows, the moved payloads are marked and the kept ones whole; the reclaim takes derived_model_forecast with its weekly backstop; service role only; re-runnable, and safe without derived_model_forecast');
})().catch((e) => { console.error(e); process.exit(1); });
