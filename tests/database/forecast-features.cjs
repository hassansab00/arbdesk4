// ===========================================================================
// PAST FORECAST FEATURES GO TO THE REPOSITORY (plan v2 P1.6 phase 1, step 3,
// 28 Sep, 20260928230000_past_forecast_features_go_to_the_repo.sql). Hassan
// approved phase 1 "as long as we dont lose any collected data".
//
// The export view gives the three-column key one text key the archive can
// keyset-page on: unique, totally ordered, and a walk page by page returns
// every row exactly once. prune_forecast_features deletes only rows dated two
// or more days back, only the exact count the archive read back from its
// committed file, and never a row captured since yesterday's UTC midnight -
// the repo mirror copies those after the prune runs. request_reclaim takes the
// table, the daily backstop is scheduled, the view and the function are the
// service role's alone, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260928230000_past_forecast_features_go_to_the_repo.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  // The table as it is live (28 Sep): 24 columns, the three-column key, RLS
  // on, the browser holding SELECT. cron as a recorder.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
    create table public.weather_forecast_features (
      city_key text not null, for_date date not null, run_at timestamptz not null,
      source text not null default 'api.weather.gov', lead_days integer,
      forecast_max_c numeric, forecast_min_c numeric, apparent_max_c numeric,
      morning_temp_c numeric, morning_dewpoint_c numeric, dewpoint_depression_c numeric,
      morning_humidity numeric, cloud_mean numeric, cloud_max numeric,
      wind_mean numeric, wind_max numeric, precip_total numeric, precip_probability numeric,
      n_hours integer, captured_at timestamptz not null default now(),
      morning_pressure_hpa numeric, pressure_change_24h_hpa numeric,
      wind_u_mean numeric, wind_v_mean numeric,
      primary key (city_key, for_date, run_at));
    alter table public.weather_forecast_features enable row level security;
    grant select on public.weather_forecast_features to anon, authenticated;
    grant all on public.weather_forecast_features to service_role;
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // Five rows: two runs for one old city-day (a microsecond apart), one more
  // old day, the day the window keeps, and today.
  const day = (d) => `current_date - ${d}`;
  const cap = (d) => `now() - interval '${d} days'`;
  await db.exec(`insert into public.weather_forecast_features (city_key, for_date, run_at, source, forecast_max_c, captured_at) values
    ('nyc',    ${day(5)}, timestamptz '2026-09-01 12:00:00.000001+00', 'api.weather.gov', 21.5, ${cap(5)}),
    ('nyc',    ${day(5)}, timestamptz '2026-09-01 12:00:00.000002+00', 'open-meteo',      21.7, ${cap(5)}),
    ('london', ${day(3)}, timestamptz '2026-09-03 06:00:00+00',        'open-meteo',      15.0, ${cap(3)}),
    ('london', ${day(2)}, timestamptz '2026-09-04 06:00:00+00',        'open-meteo',      16.0, ${cap(2)}),
    ('nyc',    ${day(0)}, timestamptz '2026-09-06 18:30:00+00',        'api.weather.gov', 23.0, ${cap(0)})`);
  const count = async () => (await db.query('select count(*)::int as n from public.weather_forecast_features')).rows[0].n;
  const prune = async (args) => (await db.query(`select public.prune_forecast_features(${args}) as r`)).rows[0].r;

  // THE KEY: one per row, fixed-width in every part, and the run time to the
  // microsecond in UTC - the two nyc runs a microsecond apart are two keys.
  const keys = (await db.query(`select feature_key, city_key from public.v_forecast_features_export order by feature_key`)).rows;
  assert.equal(keys.length, 5);
  assert.equal(new Set(keys.map((k) => k.feature_key)).size, 5, 'two rows share an export key');
  for (const k of keys) {
    assert.match(k.feature_key, /^[a-z]+\|\d{4}-\d{2}-\d{2}\|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$/, k.feature_key);
  }
  assert.ok(keys.some((k) => k.feature_key.endsWith('|2026-09-01T12:00:00.000001')));
  assert.ok(keys.some((k) => k.feature_key.endsWith('|2026-09-01T12:00:00.000002')));

  // The archive's walk: order by the key, a page at a time, the next page
  // strictly after the last key. Every row once, none twice, none skipped.
  const seen = [];
  let after = null;
  for (;;) {
    const page = (await db.query(
      `select feature_key from public.v_forecast_features_export
        where ($1::text is null or feature_key > $1::text)
        order by feature_key limit 2`, [after])).rows;
    if (!page.length) break;
    seen.push(...page.map((r) => r.feature_key));
    after = page[page.length - 1].feature_key;
  }
  assert.deepEqual(seen, keys.map((k) => k.feature_key), 'the paged walk missed or repeated a row');

  // Every column of the table rides along, unchanged.
  const one = (await db.query(`select * from public.v_forecast_features_export where city_key = 'london' and for_date = ${day(3)}`)).rows[0];
  assert.equal(Number(one.forecast_max_c), 15);
  assert.equal(one.source, 'open-meteo');
  const viewCols = (await db.query(`select count(*)::int as n from information_schema.columns where table_name = 'v_forecast_features_export'`)).rows[0].n;
  assert.equal(viewCols, 25, 'the export view should be the 24 table columns plus the key');

  // The floor is two days; a committed prune needs the verified count.
  const one_day = await prune('1');
  assert.equal(one_day.ok, false);
  assert.match(one_day.error, /at least 2/);
  const blind = await prune('2, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the three rows dated before two days ago, nothing deleted.
  const dry = await prune('2, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 3);
  assert.equal(Number(dry.would_keep), 2);
  assert.equal(await count(), 5);

  // A cutoff newer than the floor is moved back to it, never forward.
  const tooNew = await prune(`2, true, current_date`);
  assert.equal(Number(tooNew.would_delete), 3, 'a cutoff inside the last two days was honoured');

  // A count that differs from the file deletes nothing.
  const wrong = await prune(`2, false, null, 2`);
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await count(), 5, 'a mismatched prune deleted rows');

  // THE MIRROR. A late write for an old date, captured since yesterday's UTC
  // midnight, is not in the repo mirror yet: nothing goes, dry run or not.
  const yesterdayMidnight = `(date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC'`;
  await db.exec(`insert into public.weather_forecast_features (city_key, for_date, run_at, captured_at)
                 values ('paris', ${day(4)}, timestamptz '2026-09-02 00:00:00+00', ${yesterdayMidnight})`);
  const young = await prune(`2, true`);
  assert.equal(young.ok, false, JSON.stringify(young));
  assert.match(young.error, /not in the repo mirror yet/);
  assert.equal(Number(young.not_yet_mirrored), 1);
  const youngCommit = await prune(`2, false, null, 4`);
  assert.equal(youngCommit.ok, false);
  assert.equal(await count(), 6, 'a row the mirror has not had was deleted');
  // A microsecond earlier, the mirror had it last night.
  await db.exec(`update public.weather_forecast_features set captured_at = captured_at - interval '1 microsecond' where city_key = 'paris'`);
  assert.equal(Number((await prune(`2, true`)).would_delete), 4);

  // The exact count: the four old rows go, the two recent stay.
  const done = await prune(`2, false, null, 4`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 4);
  const left = (await db.query(`select string_agg(city_key || ':' || (current_date - for_date), ',' order by for_date) as l from public.weather_forecast_features`)).rows[0].l;
  assert.equal(left, 'london:2,nyc:0');

  // Nothing old left: a no-op, not an error.
  const none = await prune(`2, false, null, 0`);
  assert.equal(none.ok, true);
  assert.equal(Number(none.deleted), 0);

  // request_reclaim takes the table and schedules its VACUUM FULL; a table
  // the archive does not prune is still refused. The backstop is scheduled.
  const rr = (await db.query(`select public.request_reclaim('weather_forecast_features') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_weather_forecast_features');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = (await db.query(`select jobname, schedule, command from cron.calls`)).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_weather_forecast_features' && c.schedule === '15 3 * * *'
    && c.command === 'VACUUM (FULL, ANALYZE) public.weather_forecast_features'), JSON.stringify(calls));
  // The earlier tables keep their slots: the new one is appended.
  const slot = (await db.query(`select public.request_reclaim('research_captures') as r`)).rows[0].r;
  assert.equal(slot.ok, true);

  // The view is the service role's alone - the browser reads the table, not
  // this - and so are both functions.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}', 'public.v_forecast_features_export', 'select') as ok`)).rows[0].ok,
      false, `${role} can read the export view`);
  }
  assert.equal((await db.query(`select has_table_privilege('service_role', 'public.v_forecast_features_export', 'select') as ok`)).rows[0].ok, true);
  await db.exec('set role anon');
  await assert.rejects(db.query('select count(*) from public.v_forecast_features_export'), /permission denied/);
  await db.exec('reset role');
  for (const fn of ['public.prune_forecast_features(integer,boolean,date,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log("PASS: forecast-features: one unique key per row that pages every row exactly once, a two-day floor, the exact verified count or nothing, never a row the repo mirror has not had, request_reclaim and the daily backstop take the table, service role only, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
