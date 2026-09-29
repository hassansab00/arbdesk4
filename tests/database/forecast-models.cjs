// ===========================================================================
// EACH MODEL'S PAST FORECASTS GO TO THE REPOSITORY (plan v2 P1.6 phase 2,
// step 4, 29 Sep, 20260929120000_model_forecasts_go_to_the_repo.sql).
//
// The export view gives the four-column key one text key the archive can
// keyset-page on: unique, totally ordered, and a walk page by page returns
// every row exactly once. prune_forecast_models deletes only rows dated at
// least 30 days back, only the exact count the archive read back from its
// committed file, and never a row observed since yesterday's UTC midnight -
// the repo mirror copies those after the prune runs. request_reclaim takes the
// table, the weekly backstop is scheduled, the view and the function are the
// service role's alone, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const read = (f) => fs.readFileSync(path.join(MIGRATIONS, f), 'utf-8');
const MIG = read('20260929120000_model_forecasts_go_to_the_repo.sql');

(async () => {
  const db = new PGlite();
  // The table as its own migration creates it (the live shape, 29 Sep), RLS
  // on and the service role's alone, as it is live. cron as a recorder.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
  `);
  await db.exec(read('20260923190000_every_forecast_model.sql'));
  await db.exec(`
    alter table public.weather_forecast_models enable row level security;
    revoke all on public.weather_forecast_models from anon, authenticated;
    grant all on public.weather_forecast_models to service_role;`);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // Eight rows: two runs a microsecond apart and a second model for one old
  // city-day, the same run's forecast of the next day (a previous-runs run
  // covers several days, so the key needs for_date), two more old days, the
  // first day the 30-day window keeps, and a models-current row for tomorrow.
  const day = (d) => `current_date - ${d}`;
  const obs = (d) => `now() - interval '${d} days'`;
  await db.exec(`insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, source, observed_at) values
    ('nyc',    'open_meteo_ecmwf_ifs025', timestamptz '2026-08-01 00:00:00.000001+00', ${day(40)}, 1, 21.5, 'open-meteo-previous-runs', ${obs(3)}),
    ('nyc',    'open_meteo_ecmwf_ifs025', timestamptz '2026-08-01 00:00:00.000002+00', ${day(40)}, 1, 21.6, 'open-meteo-previous-runs', ${obs(3)}),
    ('nyc',    'open_meteo_gfs_seamless', timestamptz '2026-08-01 00:00:00+00',        ${day(40)}, 1, 22.0, 'open-meteo-previous-runs', ${obs(3)}),
    ('nyc',    'open_meteo_ecmwf_ifs025', timestamptz '2026-08-01 00:00:00.000001+00', ${day(39)}, 2, 20.9, 'open-meteo-previous-runs', ${obs(3)}),
    ('london', 'open_meteo_ecmwf_ifs025', timestamptz '2026-08-05 00:00:00+00',        ${day(35)}, 2, 15.0, 'open-meteo-previous-runs', ${obs(3)}),
    ('london', 'open_meteo_icon_seamless', timestamptz '2026-08-09 00:00:00+00',       ${day(31)}, 1, 16.0, 'open-meteo-previous-runs', ${obs(3)}),
    ('london', 'open_meteo_icon_seamless', timestamptz '2026-08-10 00:00:00+00',       ${day(30)}, 1, 16.5, 'open-meteo-previous-runs', ${obs(3)}),
    ('nyc',    'open_meteo_ecmwf_ifs025', now(),                                        current_date + 1, 1, 23.0, 'open-meteo-models-current', now())`);
  const count = async () => (await db.query('select count(*)::int as n from public.weather_forecast_models')).rows[0].n;
  const prune = async (args) => (await db.query(`select public.prune_forecast_models(${args}) as r`)).rows[0].r;

  // THE KEY: one per row, in the primary key's order, the run to the
  // microsecond in UTC - the two runs a microsecond apart are two keys, and
  // two models on one city-day are two keys.
  const keys = (await db.query(`select model_key from public.v_forecast_models_export order by model_key`)).rows.map((r) => r.model_key);
  assert.equal(keys.length, 8);
  assert.equal(new Set(keys).size, 8, 'two rows share an export key');
  for (const k of keys) {
    assert.match(k, /^[a-z_]+\|[a-z0-9_]+\|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\|\d{4}-\d{2}-\d{2}$/, k);
  }
  assert.ok(keys.some((k) => k.includes('|2026-08-01T00:00:00.000001|')));
  assert.ok(keys.some((k) => k.includes('|2026-08-01T00:00:00.000002|')));

  // The archive's walk: order by the key, a page at a time, the next page
  // strictly after the last key. Every row once, none twice, none skipped.
  const seen = [];
  let after = null;
  for (;;) {
    const page = (await db.query(
      `select model_key from public.v_forecast_models_export
        where ($1::text is null or model_key > $1::text)
        order by model_key limit 2`, [after])).rows;
    if (!page.length) break;
    seen.push(...page.map((r) => r.model_key));
    after = page[page.length - 1].model_key;
  }
  assert.deepEqual(seen, keys, 'the paged walk missed or repeated a row');

  // Every column of the table rides along, unchanged.
  const one = (await db.query(`select * from public.v_forecast_models_export where city_key = 'london' and for_date = ${day(35)}`)).rows[0];
  assert.equal(Number(one.forecast_max_c), 15);
  assert.equal(one.model, 'open_meteo_ecmwf_ifs025');
  assert.equal(one.lead_days, 2);
  const viewCols = (await db.query(`select count(*)::int as n from information_schema.columns where table_name = 'v_forecast_models_export'`)).rows[0].n;
  assert.equal(viewCols, 9, 'the export view should be the 8 table columns plus the key');

  // The floor is thirty days; a committed prune needs the verified count.
  const short = await prune('29');
  assert.equal(short.ok, false);
  assert.match(short.error, /at least 30/);
  const blind = await prune('30, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the six rows dated before thirty days ago, nothing deleted.
  const dry = await prune('30, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 6);
  assert.equal(Number(dry.would_keep), 2);
  assert.equal(await count(), 8);

  // A cutoff newer than the floor is moved back to it, never forward; an
  // older one is honoured.
  assert.equal(Number((await prune(`30, true, current_date`)).would_delete), 6, 'a cutoff inside the window was honoured');
  assert.equal(Number((await prune(`30, true, ${day(36)}`)).would_delete), 4);

  // A count that differs from the file deletes nothing.
  const wrong = await prune(`30, false, null, 5`);
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await count(), 8, 'a mismatched prune deleted rows');

  // THE MIRROR. A hole filled for an old date, observed since yesterday's UTC
  // midnight, is not in the repo mirror yet: nothing goes, dry run or not.
  const yesterdayMidnight = `(date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC'`;
  await db.exec(`insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, observed_at)
                 values ('paris', 'open_meteo_ecmwf_ifs025', timestamptz '2026-08-06 00:00:00+00', ${day(33)}, 1, 18.0, ${yesterdayMidnight})`);
  const young = await prune(`30, true`);
  assert.equal(young.ok, false, JSON.stringify(young));
  assert.match(young.error, /not in the repo mirror yet/);
  assert.equal(Number(young.not_yet_mirrored), 1);
  const youngCommit = await prune(`30, false, null, 7`);
  assert.equal(youngCommit.ok, false);
  assert.equal(await count(), 9, 'a row the mirror has not had was deleted');
  // A microsecond earlier, the mirror had it last night.
  await db.exec(`update public.weather_forecast_models set observed_at = observed_at - interval '1 microsecond' where city_key = 'paris'`);
  assert.equal(Number((await prune(`30, true`)).would_delete), 7);

  // The exact count: the seven old rows go, the two recent stay.
  const done = await prune(`30, false, null, 7`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 7);
  const left = (await db.query(`select string_agg(city_key || ':' || (for_date - current_date), ',' order by for_date) as l from public.weather_forecast_models`)).rows[0].l;
  assert.equal(left, 'london:-30,nyc:1');

  // Nothing old left: a no-op, not an error.
  const none = await prune(`30, false, null, 0`);
  assert.equal(none.ok, true);
  assert.equal(Number(none.deleted), 0);

  // request_reclaim takes the table and schedules its VACUUM FULL; a table
  // the archive does not prune is still refused. The backstop is scheduled
  // weekly, as weather_forecasts'.
  const rr = (await db.query(`select public.request_reclaim('weather_forecast_models') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_weather_forecast_models');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = (await db.query(`select jobname, schedule, command from cron.calls`)).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_weather_forecast_models' && c.schedule === '50 6 * * 1'
    && c.command === 'VACUUM (FULL, ANALYZE) public.weather_forecast_models'), JSON.stringify(calls));
  // Every table before it keeps its slot: the new one is appended.
  for (const t of ['research_captures', 'weather_forecast_features', 'signals']) {
    assert.equal((await db.query(`select public.request_reclaim('${t}') as r`)).rows[0].r.ok, true, t);
  }

  // The view is the service role's alone, and so are both functions.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}', 'public.v_forecast_models_export', 'select') as ok`)).rows[0].ok,
      false, `${role} can read the export view`);
  }
  assert.equal((await db.query(`select has_table_privilege('service_role', 'public.v_forecast_models_export', 'select') as ok`)).rows[0].ok, true);
  await db.exec('set role anon');
  await assert.rejects(db.query('select count(*) from public.v_forecast_models_export'), /permission denied/);
  await db.exec('reset role');
  for (const fn of ['public.prune_forecast_models(integer,boolean,date,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log('PASS: forecast-models: one unique key per row in the primary key\'s order that pages every row exactly once, a thirty-day floor, the exact verified count or nothing, never a row the repo mirror has not had, request_reclaim and the weekly backstop take the table with every earlier slot kept, service role only, re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
