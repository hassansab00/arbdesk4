// ===========================================================================
// EACH MODEL'S FORECASTS KEEP A WEEK (WXPredict build 2.A, 7 Oct,
// 20261007210000_each_models_forecasts_keep_a_week.sql).
//
// A settled hit day for each of days 3 to 40 back, with both tables' kinds of
// forecast, frozen under the old single boundary. Then:
//
//   * the frozen rows take their table from their model, and a model in
//     neither table's list refuses the migration and changes nothing;
//   * v_hit_forecasts returns exactly what it returned before the migration;
//   * weather_forecast_models is pruned at 7 days through prune_forecast_models
//     (6 refuses) and v_hit_forecasts still returns every row and column;
//   * a weather_forecasts row arriving 10 days after its date is served live
//     (the old single boundary - the later of both tables' oldest days -
//     would have hidden it) and frozen by the next freeze;
//   * the freeze never touches the model rows frozen below that table's
//     oldest day, even with the table empty;
//   * the browser reaches none of it; the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const read = (f) => fs.readFileSync(path.join(MIGRATIONS, f), 'utf-8');
const MIG = read('20261007210000_each_models_forecasts_keep_a_week.sql');
const TZ = 'America/New_York';

// What the migrations' views and functions read, at the columns they use (as
// evidence-cache.cjs); paper-contracts builds every migration over the rest.
async function build() {
  const db = new PGlite();
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
    create table public.cities(city_key text primary key, timezone text);
    create table public.weather_observations(obs_id bigserial primary key, city_key text, station text,
      valid_at timestamptz not null, temp_c numeric, observed_at timestamptz default now());
    create table public.derived_city_day_features(city_key text, obs_date date, primary key (city_key, obs_date));
    create table public.derived_climb_profile(city_key text not null, local_hour integer not null, n_days integer,
      typical_climb_left_c numeric, climb_left_sd_c numeric, climb_left_p10_c numeric, climb_left_p90_c numeric,
      pct_already_peaked numeric, computed_at timestamptz not null default now(), primary key (city_key, local_hour));
    create table public.markets(market_id uuid primary key, city_key text, resolution_date date);
    create table public.bands(band_id uuid primary key, market_id uuid);
    create table public.band_probabilities(prob_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null, forecast_max_c numeric, bias_applied_c numeric, sigma_c numeric,
      forecast_sigma_c numeric);
    create table public.v_verified_weather_outcomes(city_key text, for_date date, observed_max_c numeric);
    create table public.weather_forecasts(forecast_id bigserial primary key, city_key text, model text,
      run_at timestamptz, observed_at timestamptz, for_date date, lead_days int, forecast_max_c numeric, source text);
    create view public.v_forecast_issued as
      select city_key, for_date, model, forecast_max_c, run_at as issued_at, 'ingest_time'::text as issued_at_source
        from public.weather_forecasts where source = 'open-meteo';
    create table public.v_hit_ladders(city_key text, for_date date, cutoff_at timestamptz);
    create table public.derived_forecast_skill(computed_at timestamptz);
  `);
  await db.exec(read('20260923190000_every_forecast_model.sql'));
  await db.exec(read('20260929120000_model_forecasts_go_to_the_repo.sql'));
  await db.exec(read('20260929160000_the_evidence_outlasts_the_weather_tables.sql'));

  // Days 3 to 40 back: the evening-before cutoff, and the four kinds of
  // forecast v_hit_forecasts_live reads, two from each table.
  await db.exec(`
    insert into public.cities values ('nyc', '${TZ}');
    insert into public.v_hit_ladders
    select 'nyc', current_date - d, (((current_date - d - 1)::timestamp + interval '18 hours') at time zone '${TZ}')
      from generate_series(3, 40) d;
    insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
    select 'nyc', 'open_meteo_forecast', (current_date - d - 1)::timestamp at time zone 'UTC',
           (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1, 20 + d * 0.1, 'open-meteo'
      from generate_series(3, 40) d
    union all
    select 'nyc', 'open_meteo_best_match', (current_date - d - 1)::timestamp at time zone 'UTC',
           (current_date - d)::timestamp at time zone 'UTC', current_date - d, 1, 21 + d * 0.1, 'open-meteo-previous-runs'
      from generate_series(3, 40) d;
    insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, source, observed_at)
    select 'nyc', 'open_meteo_ecmwf_ifs025', (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1,
           22 + d * 0.1, 'open-meteo-models-current', (current_date - d - 1)::timestamp at time zone 'UTC'
      from generate_series(3, 40) d
    union all
    select 'nyc', 'open_meteo_gfs_seamless', (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1,
           23 + d * 0.1, 'open-meteo-previous-runs', (current_date - d)::timestamp at time zone 'UTC'
      from generate_series(3, 40) d;`);
  return db;
}

(async () => {
  // A MODEL IN NEITHER LIST REFUSES, AND NOTHING CHANGES.
  {
    const db = await build();
    await db.exec(`insert into public.derived_hit_forecasts (city_key, for_date, lane, model, forecast_max_c, known_at)
                   values ('nyc', current_date - 50, 'asof', 'mystery_model', 20, now())`);
    await assert.rejects(db.exec(MIG), /mystery_model belong to no known forecast table/);
    const col = (await db.query(`select count(*)::int as n from information_schema.columns
                                  where table_name = 'derived_hit_forecasts' and column_name = 'source_table'`)).rows[0].n;
    assert.equal(col, 0, 'a refused migration left the column behind');
  }

  const db = await build();
  const q = async (sql, p) => (await db.query(sql, p)).rows;
  const one = async (sql) => (await q(sql))[0];
  const HITS = `select city_key, for_date, lane, model, forecast_max_c, known_at from public.v_hit_forecasts
                 order by for_date, lane, model, known_at`;
  const snap = async (sql) => JSON.stringify(await q(sql));
  const prune = async (args) => (await one(`select public.prune_forecast_models(${args}) as r`)).r;

  // Frozen under the old single boundary, as live on 7 Oct.
  await db.exec(`select public.freeze_hit_forecasts()`);
  const hits0 = await q(HITS);
  assert.equal(hits0.length, 38 * 4, 'four forecasts for each of the 38 settled days');
  const before = await snap(HITS);

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // 1 - each frozen row knows its table, by its model
  const bySource = await q(`select source_table, string_agg(distinct model, ',' order by model) as models, count(*)::int as n
                              from public.derived_hit_forecasts group by 1 order by 1`);
  assert.deepEqual(bySource, [
    { source_table: 'weather_forecast_models', models: 'open_meteo_ecmwf_ifs025,open_meteo_gfs_seamless', n: 76 },
    { source_table: 'weather_forecasts', models: 'open_meteo_best_match,open_meteo_forecast', n: 76 },
  ]);
  await assert.rejects(db.exec(`update public.derived_hit_forecasts set source_table = 'elsewhere' where model = 'open_meteo_forecast'`));

  // 2 - the same rows as before the migration, and after a freeze under it
  assert.equal(await snap(HITS), before, 'v_hit_forecasts changed when the migration ran');
  const frozen = (await one(`select public.freeze_hit_forecasts() as r`)).r;
  assert.equal(frozen.ok, true);
  assert.equal(Number(frozen.rows_written), 152);
  assert.equal(await snap(HITS), before);

  // 3 - the prune: a week is the floor; the model rows dated over 7 days back go
  assert.equal((await prune('6, true')).ok, false, 'six days is under the floor');
  const dry = await prune('7, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 33 * 2, 'days 8 to 40, two model rows each');
  const done = await prune('7, false, null, 66');
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 66);
  assert.equal((await one(`select min(for_date) = current_date - 7 as ok from public.weather_forecast_models`)).ok, true);

  // 4 - every row and column v_hit_forecasts returned before, the model rows
  // for days 8-40 from the frozen copy, weather_forecasts' rows live
  assert.equal(await snap(HITS), before, 'the hit forecasts lost or changed rows at the 7-day prune');

  // 5 - weather_forecasts still holds day 10 back: a row for it arriving now is
  // served live, and the next freeze keeps it
  await db.exec(`insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
                 values ('nyc', 'open_meteo_best_match', (current_date - 13)::timestamp at time zone 'UTC', now(),
                         current_date - 10, 1, 30.5, 'open-meteo-previous-runs')`);
  const late = await q(`select * from public.v_hit_forecasts where for_date = current_date - 10 and forecast_max_c = 30.5`);
  assert.equal(late.length, 1, 'a weather_forecasts row 10 days after its date is not served');
  const modelRowsFrozen = `select count(*)::int as n from public.derived_hit_forecasts
                            where source_table = 'weather_forecast_models' and for_date < current_date - 7`;
  assert.equal((await one(modelRowsFrozen)).n, 66);
  await db.exec(`select public.freeze_hit_forecasts()`);
  assert.equal((await one(`select count(*)::int as n from public.derived_hit_forecasts
                            where for_date = current_date - 10 and forecast_max_c = 30.5
                              and source_table = 'weather_forecasts'`)).n, 1, 'the late row was not frozen');
  assert.equal((await one(modelRowsFrozen)).n, 66, 'the freeze touched model rows below their table\'s oldest day');
  const hits1 = await q(HITS);
  assert.equal(hits1.length, hits0.length + 1);

  // 6 - an empty model table serves every one of its rows frozen, and the
  // freeze leaves them
  const after = JSON.stringify(hits1);
  await db.exec(`delete from public.weather_forecast_models`);
  assert.equal(await snap(HITS), after, 'with the model table empty its rows are not all served frozen');
  await db.exec(`select public.freeze_hit_forecasts()`);
  assert.equal((await one(modelRowsFrozen)).n, 66);
  assert.equal(await snap(HITS), after, 'a freeze over an empty model table lost frozen rows');

  // 7 - the browser reaches none of it
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`);
    for (const rel of ['public.v_hit_forecasts', 'public.v_hit_forecasts_live', 'public.derived_hit_forecasts']) {
      await assert.rejects(db.query(`select count(*) from ${rel}`), `${role} read ${rel}`);
    }
    await assert.rejects(db.query(`select public.freeze_hit_forecasts()`));
    await assert.rejects(db.query(`select public.prune_forecast_models(7, true)`));
    await db.exec('reset role');
  }

  console.log("PASS: model-forecasts-week: frozen rows take their table from their model (an unknown one refuses, nothing changed); v_hit_forecasts unchanged by the migration and by a 7-day prune of weather_forecast_models (6 refuses); a weather_forecasts row 10 days late is served live and frozen; the freeze never touches the model rows below that table's oldest day, empty or not; closed to the browser; re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
