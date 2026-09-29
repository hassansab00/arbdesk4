// ===========================================================================
// THE TRAJECTORY'S HOURS AND THE HIT TOURNAMENT'S FORECASTS OUTLAST THE
// WEATHER TABLES' KEEP (plan v2 P1.6 phase 2, step 6 part (b), 29 Sep,
// 20260929160000_the_evidence_outlasts_the_weather_tables.sql).
//
// Forty days of hourly readings for New York, a price published for each of
// the last 34, and a settled hit day with four kinds of forecast for each of
// days 40 to 20 back. Then the weather tables are pruned at 30 days, through
// the prune functions themselves:
//
//   * v_trajectory_evidence and v_hit_forecasts return exactly what they
//     returned before the prune, every row and column;
//   * each prune refuses while the cache it needs does not cover what it
//     deletes, and goes through once the nightly refresh has run;
//   * the day the prune cut part-way keeps its cached hours when the refresh
//     runs again, and a whole day takes a late reading;
//   * the as-of climb is over the 30 days before each day, not all of them;
//   * the frozen days survive a later freeze; the tables and functions are
//     the service role's alone; the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const read = (f) => fs.readFileSync(path.join(MIGRATIONS, f), 'utf-8');
const MIG = read('20260929160000_the_evidence_outlasts_the_weather_tables.sql');

(async () => {
  const db = new PGlite();
  // What the migration's views and functions read, at the columns they use.
  // v_hit_ladders and v_forecast_issued stand in for the real views (sql/ad4_88,
  // 20260923160000) with the columns v_hit_forecasts_live reads; paper-contracts
  // builds the migration over the real ones.
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
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
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // New York, days 40 back to yesterday: 24 readings a day, peaking at 15:00
  // local, a little warmer each day and wobbling so the as-of climb differs by
  // window. A price published for each of the last 34 days; a verified maximum
  // for each; a climb profile for every hour.
  const TZ = 'America/New_York';
  await db.exec(`
    insert into public.cities values ('nyc', '${TZ}');
    insert into public.weather_observations (city_key, valid_at, temp_c)
    select 'nyc', ((current_date - d)::timestamp + make_interval(hours => h)) at time zone '${TZ}',
           round((15 + 0.1 * (40 - d) - 0.6 * abs(h - 15) + ((d * 7 + h) % 5) * 0.3)::numeric, 1)
      from generate_series(1, 40) d, generate_series(0, 23) h;
    -- A second city with nothing priced - retired, say - whose readings the
    -- prune cuts all the same: its days must be kept too, or the prune refuses.
    insert into public.cities values ('wellington', 'Pacific/Auckland');
    insert into public.weather_observations (city_key, valid_at, temp_c)
    select 'wellington', ((current_date - d)::timestamp + make_interval(hours => h)) at time zone 'Pacific/Auckland', 12
      from generate_series(32, 35) d, generate_series(0, 23) h;
    insert into public.derived_city_day_features
    select distinct o.city_key, (o.valid_at at time zone c.timezone)::date
      from public.weather_observations o join public.cities c using (city_key);
    insert into public.derived_climb_profile (city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c)
    select 'nyc', h, 30, greatest(0, 15 - h) * 0.5, 0.8 from generate_series(0, 23) h;
    insert into public.markets
    select md5('m' || d)::uuid, 'nyc', current_date - d from generate_series(1, 34) d;
    insert into public.bands select md5('b' || d)::uuid, md5('m' || d)::uuid from generate_series(1, 34) d;
    insert into public.band_probabilities (band_id, computed_at, forecast_max_c, bias_applied_c, sigma_c, forecast_sigma_c)
    select md5('b' || d)::uuid, (current_date - d - 1)::timestamp at time zone 'UTC', 20 + d * 0.1, 0.2, 1.3, 1.4
      from generate_series(1, 34) d;
    insert into public.v_verified_weather_outcomes
    select 'nyc', current_date - d, 21 + d * 0.1 from generate_series(2, 34) d;`);

  // A settled hit day for each of days 40 to 20 back: the evening-before
  // cutoff, and each kind of forecast the view reads.
  await db.exec(`
    insert into public.v_hit_ladders
    select 'nyc', current_date - d, (((current_date - d - 1)::timestamp + interval '18 hours') at time zone '${TZ}')
      from generate_series(20, 40) d;
    insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
    select 'nyc', 'open_meteo_forecast', (current_date - d - 1)::timestamp at time zone 'UTC',
           (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1, 20 + d * 0.1, 'open-meteo'
      from generate_series(20, 40) d
    union all
    select 'nyc', 'best_match', (current_date - d - 1)::timestamp at time zone 'UTC',
           (current_date - d)::timestamp at time zone 'UTC', current_date - d, 1, 21 + d * 0.1, 'open-meteo-previous-runs'
      from generate_series(20, 40) d;
    insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, source, observed_at)
    select 'nyc', 'open_meteo_ecmwf_ifs025', (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1,
           22 + d * 0.1, 'open-meteo-models-current', (current_date - d - 1)::timestamp at time zone 'UTC'
      from generate_series(20, 40) d
    union all
    select 'nyc', 'open_meteo_gfs_seamless', (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1,
           23 + d * 0.1, 'open-meteo-previous-runs', (current_date - d)::timestamp at time zone 'UTC'
      from generate_series(20, 40) d;
    insert into public.derived_forecast_skill values (now());`);

  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];
  const TRAJ = `select * from public.v_trajectory_evidence order by local_date, local_hour`;
  const HITS = `select * from public.v_hit_forecasts order by for_date, lane, model, known_at`;
  const snap = async (sql) => JSON.stringify(await q(sql));

  // THE AS-OF WINDOW: the 30 days before each day, as the served profile.
  const traj0 = await q(TRAJ);
  assert.equal(traj0.length, 34 * 24, 'every hour of every priced day is evidence');
  const last = traj0.filter((r) => r.local_date.toISOString().slice(0, 10)
    === traj0[traj0.length - 1].local_date.toISOString().slice(0, 10));
  assert.ok(last.every((r) => r.climb_n_asof === 30), `the newest day's as-of counts ${last[0].climb_n_asof} days, not 30`);
  const first = traj0.slice(0, 24);
  assert.ok(first.every((r) => r.climb_n_asof === 6 && r.climb_left_asof_c === null),
    'a day with 6 earlier days has no as-of climb (the floor is 20)');
  const before = await snap(TRAJ);
  const hits0 = await q(HITS);
  assert.equal(hits0.length, 21 * 4, 'four forecasts for each of the 21 hit days');
  assert.deepEqual([...new Set(hits0.map((r) => r.lane))].sort(), ['asof', 'research']);
  const hitsBefore = await snap(HITS);
  assert.equal(hitsBefore, await snap(`select * from public.v_hit_forecasts_live order by for_date, lane, model, known_at`),
    'while both tables hold every day, the view is the live computation');

  // THE PRUNES REFUSE WHAT IS NOT KEPT. The observations are cut at an
  // instant 30 days back (13:xx in New York: part-way through a local day).
  const cutAt = `(date_trunc('hour', now()) - interval '30 days')`;
  const doomedObs = (await one(`select count(*)::int as n from public.weather_observations where valid_at < ${cutAt}`)).n;
  assert.ok(doomedObs > 200);
  const obsDry = await one(`select public.prune_observations(30, true, ${cutAt}) as r`);
  assert.equal(obsDry.r.ok, false, JSON.stringify(obsDry.r));
  assert.match(obsDry.r.error, /not in derived_city_day_hours/);
  const doomedFc = (await one(`select count(*)::int as n from public.weather_forecasts where for_date < current_date - 30`)).n;
  const fcDry = await one(`select public.prune_forecasts(30, true, current_date - 30) as r`);
  assert.equal(fcDry.r.ok, false, JSON.stringify(fcDry.r));
  assert.match(fcDry.r.error, /not in derived_hit_forecasts/);
  const fmDry = await one(`select public.prune_forecast_models(30, true) as r`);
  assert.equal(fmDry.r.ok, false, JSON.stringify(fmDry.r));
  assert.match(fmDry.r.error, /not in derived_hit_forecasts/);

  // THE NIGHTLY REFRESH, as common.refresh_feature_cache runs it: the hours
  // for every city in one call, then the freeze. One city is not enough -
  // the prune still refuses Wellington's days.
  await one(`select public.refresh_city_day_hours('nyc') as r`);
  const oneCity = await one(`select public.prune_observations(30, true, ${cutAt}) as r`);
  assert.equal(oneCity.r.ok, false, 'a city the refresh skipped was pruned anyway');
  assert.equal(Number(oneCity.r.unkept_city_days), 4, JSON.stringify(oneCity.r));
  const hours = await one(`select public.refresh_city_day_hours() as r`);
  assert.equal(hours.r.ok, true, JSON.stringify(hours.r));
  assert.equal(hours.r.cities, 2);
  assert.equal(hours.r.days_written, 44, 'the forty New York days and four Wellington days the readings hold');
  const row = await one(`select temp_c, n_hours from public.derived_city_day_hours where city_key = 'nyc' and obs_date = current_date - 5`);
  assert.equal(row.temp_c.length, 24);
  assert.equal(row.n_hours, 24);
  const frozen = await one(`select public.freeze_hit_forecasts() as r`);
  assert.equal(frozen.r.ok, true, JSON.stringify(frozen.r));
  assert.equal(frozen.r.rows_written, 84);
  assert.equal(await snap(TRAJ), before, 'the refresh changed the evidence');
  assert.equal(await snap(HITS), hitsBefore, 'the freeze changed the forecasts');

  // Now each prune goes through, with the exact count.
  const obsDone = await one(`select public.prune_observations(30, false, ${cutAt}, ${doomedObs}) as r`);
  assert.equal(obsDone.r.ok, true, JSON.stringify(obsDone.r));
  assert.equal(Number(obsDone.r.deleted), doomedObs);
  const fcDone = await one(`select public.prune_forecasts(30, false, current_date - 30, ${doomedFc}) as r`);
  assert.equal(fcDone.r.ok, true, JSON.stringify(fcDone.r));
  const doomedFm = (await one(`select count(*)::int as n from public.weather_forecast_models where for_date < current_date - 30`)).n;
  const fmDone = await one(`select public.prune_forecast_models(30, false, null, ${doomedFm}) as r`);
  assert.equal(fmDone.r.ok, true, JSON.stringify(fmDone.r));
  assert.equal((await one(`select min(for_date) = current_date - 30 as ok from public.weather_forecast_models`)).ok, true);

  // THE READERS ARE UNCHANGED: every row and column, the cut day included.
  assert.equal(await snap(TRAJ), before, 'the trajectory evidence changed with the prune');
  assert.equal(await snap(HITS), hitsBefore, 'the hit tournament\'s forecasts changed with the prune');
  assert.ok((await q(`select 1 from public.v_hit_forecasts_live where for_date < current_date - 30`)).length === 0,
    'the pruned days are no longer computable live - only the frozen rows serve them');

  // THE NEXT NIGHT. The cut day keeps its whole cached hours; a whole day
  // takes a reading that arrived late; the frozen days stay.
  const cutDay = (await one(`select (min(valid_at) at time zone '${TZ}')::date::text as d from public.weather_observations`)).d;
  const cutLive = (await one(`select count(distinct extract(hour from valid_at at time zone '${TZ}'))::int as n
                                from public.weather_observations where (valid_at at time zone '${TZ}')::date = '${cutDay}'`)).n;
  assert.ok(cutLive < 24, `the cut day should be partial in the readings (${cutLive} hours)`);
  await db.exec(`insert into public.weather_observations (city_key, valid_at, temp_c)
                 values ('nyc', ((current_date - 3)::timestamp + interval '3 hours 30 minutes') at time zone '${TZ}', 40)`);
  await one(`select public.refresh_city_day_hours() as r`);
  const cut = await one(`select n_hours from public.derived_city_day_hours where city_key = 'nyc' and obs_date = '${cutDay}'`);
  assert.equal(cut.n_hours, 24, 'the refresh overwrote the cut day with what the prune left');
  const late = await one(`select temp_c[4] as t from public.derived_city_day_hours where city_key = 'nyc' and obs_date = current_date - 3`);
  assert.equal(Number(late.t), 40, 'a whole day did not take its late reading');
  await one(`select public.freeze_hit_forecasts() as r`);
  assert.equal(await snap(HITS), hitsBefore, 'a later freeze lost or changed a frozen day');
  const afterLate = await q(TRAJ);
  const changed = afterLate.filter((r, i) => JSON.stringify(r) !== JSON.stringify(traj0[i]));
  assert.ok(changed.length > 0 && changed.every((r) => r.local_date >= traj0[0].local_date),
    'the late reading reached the evidence');
  assert.equal(JSON.stringify(afterLate.filter((r) => r.local_date.toISOString().slice(0, 10) <= cutDay)),
    JSON.stringify(traj0.filter((r) => r.local_date.toISOString().slice(0, 10) <= cutDay)),
    'a day from the cache moved');

  // The caches and their functions are the service role's alone.
  for (const role of ['anon', 'authenticated']) {
    for (const t of ['public.derived_city_day_hours', 'public.derived_hit_forecasts', 'public.v_hit_forecasts_live']) {
      assert.equal((await one(`select has_table_privilege('${role}', '${t}', 'select') as ok`)).ok, false, `${role} reads ${t}`);
    }
    for (const fn of ['public.refresh_city_day_hours(text)', 'public.freeze_hit_forecasts()']) {
      assert.equal((await one(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).ok, false, `${role} runs ${fn}`);
    }
  }
  for (const fn of ['public.refresh_city_day_hours(text)', 'public.freeze_hit_forecasts()']) {
    assert.equal((await one(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).ok, true);
  }

  console.log('PASS: evidence-cache: after the weather tables are pruned at 30 days the trajectory evidence and the hit tournament\'s forecasts return every row and column they returned before; each prune refuses until the nightly refresh has kept what it deletes; the cut day keeps its cached hours, a whole day takes a late reading, frozen days survive a later freeze; the as-of climb is the 30 days before; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
