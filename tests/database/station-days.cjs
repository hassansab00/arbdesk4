// ===========================================================================
// THE STATION'S DAYS AND THE FORECAST STANDING AT EACH LEAD OUTLAST THE
// WEATHER TABLES' KEEP (plan v2 P1.6 phase 2, step 5 part (a), 29 Sep,
// 20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql).
//
// Forty days of New York readings from two sources (the station's own reports
// and a warmer five-minute feed), a retired Wellington whose four days are all
// older than 30, and forecasts with two runs per lead from 40 days back to
// 3 days ahead. The views as they stood before step 5 are built first and
// read; then the real migration, the nightly refresh and the three prunes at
// 30 days, through the prune functions themselves:
//
//   * v_station_day_max, v_forecast_convergence and the page cache's
//     v_forecast_convergence_all_live return exactly what they returned
//     before - every row and column, the day the prune cut part-way included;
//     so does v_station_day_max with the other source as the primary one,
//     because the cache keeps each source's day;
//   * before the first refresh the new views already equal the old ones;
//   * each prune refuses while the cache it needs does not cover what it
//     deletes; the backtest window and readiness see the same days after;
//   * the cut day keeps its cached values when the refresh runs again;
//   * v_forecast_convergence stays security_invoker and anon can read it;
//   * the caches and their functions are the service role's; re-runnable.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIGRATIONS = path.join(ROOT, 'supabase', 'migrations');
const read = (f) => fs.readFileSync(path.join(MIGRATIONS, f), 'utf-8');
const MIG = read('20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql');
const MIG2 = read('20260929190000_the_backtest_counts_what_it_counted.sql')
  + '\n' + read('20260929200000_the_cut_day_is_counted_whole.sql');

(async () => {
  const db = new PGlite();
  // What the migrations' views and functions read, at the columns they use.
  // v_hit_ladders, v_forecast_issued, v_verified_* stand in for the real
  // relations; paper-contracts builds the migration over the real ones.
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    grant usage on schema public to anon, authenticated, service_role;
    create table public.cities(city_key text primary key, timezone text);
    create table public.weather_observations(obs_id bigserial primary key, city_key text, station text,
      valid_at timestamptz not null, temp_c numeric, temp_f numeric, source text, observed_at timestamptz default now());
    create table public.derived_city_day_features(city_key text, obs_date date, primary key (city_key, obs_date));
    create table public.derived_climb_profile(city_key text not null, local_hour integer not null, n_days integer,
      typical_climb_left_c numeric, climb_left_sd_c numeric, climb_left_p10_c numeric, climb_left_p90_c numeric,
      pct_already_peaked numeric, computed_at timestamptz not null default now(), primary key (city_key, local_hour));
    create table public.markets(market_id uuid primary key, city_key text, resolution_date date);
    create table public.bands(band_id uuid primary key, market_id uuid);
    create table public.book_snapshots(snapshot_id bigserial primary key, band_id uuid, observed_at timestamptz);
    create table public.band_probabilities(prob_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null, forecast_max_c numeric, bias_applied_c numeric, sigma_c numeric,
      forecast_sigma_c numeric);
    create table public.v_verified_weather_outcomes(city_key text, for_date date, observed_max_c numeric);
    create table public.v_verified_fact_forecast_outcome(city_key text, for_date date, observed_max_c numeric);
    create table public.fact_forecast_outcome(city_key text, for_date date, observed_max_c numeric);
    create table public.weather_forecasts(forecast_id bigserial primary key, city_key text, model text,
      run_at timestamptz, observed_at timestamptz, for_date date, lead_days int, forecast_max_c numeric, source text);
    create view public.v_forecast_issued as
      select city_key, for_date, model, forecast_max_c, run_at as issued_at, 'ingest_time'::text as issued_at_source
        from public.weather_forecasts where source = 'open-meteo';
    create table public.v_hit_ladders(city_key text, for_date date, cutoff_at timestamptz);
    create table public.derived_forecast_skill(computed_at timestamptz);
    grant select on public.v_verified_fact_forecast_outcome to anon, authenticated;
  `);
  // obs_primary_source() is sql/ad4_82's, never a migration's: the real one.
  const sa = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_82_settlement_agreement.sql'), 'utf-8');
  const a = sa.indexOf('create or replace function obs_primary_source()');
  const OBS_PRIMARY = sa.slice(a, sa.indexOf('$$;', a) + 3);
  await db.exec(OBS_PRIMARY);
  await db.exec(read('20260923190000_every_forecast_model.sql'));
  await db.exec(read('20260929160000_the_evidence_outlasts_the_weather_tables.sql'));

  // THE VIEWS AS THEY STOOD BEFORE STEP 5 - the reference every read after it
  // is compared with. v_station_day_max as sql/ad4_82 had it; the
  // convergence views as 20260913100000 and sql/ad4_62 (through ad4_89) had
  // them, with the page cache's materialized view over the _live one.
  await db.exec(`
    create view public.v_station_day_max as
    select o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as for_date,
           max(o.temp_c) as max_c, max(o.temp_f) as max_f, count(*) as n_readings,
           max(o.valid_at) as last_reading_at,
           min(o.station) filter (where o.source = obs_primary_source()) as station,
           max(o.temp_c) filter (where o.source = obs_primary_source()) as max_c_hourly,
           max(o.temp_f) filter (where o.source = obs_primary_source()) as max_f_hourly,
           count(*) filter (where o.source = obs_primary_source()) as n_hourly
      from weather_observations o join cities c on c.city_key = o.city_key
     where o.temp_c is not null
     group by 1, 2;
    create view public.v_forecast_convergence with (security_invoker = true) as
    with latest as (
      select distinct on (city_key, for_date, model, lead_days)
             city_key, for_date, model, lead_days, forecast_max_c, run_at
        from public.weather_forecasts
       where for_date >= current_date - 45 and for_date <= current_date + 16 and forecast_max_c is not null
       order by city_key, for_date, model, lead_days, run_at desc
    ), observed as (
      select city_key, for_date, max(observed_max_c) as observed_max_c
        from public.v_verified_fact_forecast_outcome where for_date >= current_date - 45
       group by city_key, for_date
    )
    select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at, o.observed_max_c,
           case when o.observed_max_c is not null then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
           l.for_date < current_date as is_past, o.observed_max_c is not null as is_settled
      from latest l left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;
    grant select on public.v_forecast_convergence to anon, authenticated, service_role;
    create view public.v_forecast_convergence_all_live as
    with latest as (
      select distinct on (city_key, for_date, model, lead_days)
             city_key, for_date, model, lead_days, forecast_max_c, run_at
        from weather_forecasts
       where for_date >= current_date - 45 and for_date <= current_date + 16 and forecast_max_c is not null
       order by city_key, for_date, model, lead_days, run_at desc
    ), observed as (
      select g.city_key, g.for_date, max(g.observed_max_c) as observed_max_c,
             bool_or(exists (select 1 from v_verified_weather_outcomes w
                              where w.city_key = g.city_key and w.for_date = g.for_date
                                and abs(g.observed_max_c - w.observed_max_c) <= 0.01)) as verified
        from (select distinct f.city_key, f.for_date, f.observed_max_c
                from fact_forecast_outcome f where f.for_date >= current_date - 45) g
       group by g.city_key, g.for_date
    )
    select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at, o.observed_max_c,
           case when o.observed_max_c is not null then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
           l.for_date < current_date as is_past, o.observed_max_c is not null as is_settled,
           coalesce(o.verified, false) as verified
      from latest l left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;
    create materialized view public.mv_forecast_convergence_all as select * from public.v_forecast_convergence_all_live;
    create unique index on public.mv_forecast_convergence_all (city_key, for_date, model, lead_days);
  `);

  // New York, days 40 back to yesterday: the station's report at :51 of every
  // hour and a five-minute feed at :55 half a degree warmer, in both units.
  // One reading from a feed with no source. Wellington, retired, days 32-35.
  // Honolulu's one reading is the oldest held: 00:30Z on day 40 back, which
  // is the day before in Honolulu - the backtest window has always said
  // observations begin on its UTC date. Taipei's day 10 back has readings
  // only before 08:00 local, none on its UTC date, and a market: the
  // backtest has never counted it as observed (Taipei 21 Sep, live).
  // Panama's day 30 back has one reading on its UTC date, at 02:00Z, before
  // the cut at 14:00Z, and its local day's readings fall on the next UTC day
  // (Panama City 30 Aug, live): the cut day must still be counted.
  const TZ = 'America/New_York';
  await db.exec(`
    insert into public.cities values ('nyc', '${TZ}'), ('wellington', 'Pacific/Auckland'),
                                     ('honolulu', 'Pacific/Honolulu'), ('taipei', 'Asia/Taipei'),
                                     ('panama', 'America/Panama');
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    values ('panama', 'MPTO', (current_date - 30)::timestamp at time zone 'UTC' + interval '2 hours', 28, 82.4, 'IEM');
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select 'panama', 'MPTO', (current_date - 29)::timestamp at time zone 'UTC' + make_interval(hours => h), 27, 80.6, 'IEM'
      from generate_series(1, 4) h;
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    values ('honolulu', 'PHNL', (current_date - 40)::timestamp at time zone 'UTC' + interval '30 minutes', 27, 80.6, 'IEM');
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select 'taipei', 'RCSS', ((current_date - 10)::timestamp + make_interval(hours => h)) at time zone 'Asia/Taipei',
           26 + h * 0.2, round((26 + h * 0.2) * 9 / 5 + 32, 1), 'IEM'
      from generate_series(0, 7) h;
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select 'nyc', 'NYC', ((current_date - d)::timestamp + make_interval(hours => h, mins => 51)) at time zone '${TZ}',
           t, round(t * 9 / 5 + 32, 1), 'IEM'
      from generate_series(1, 40) d, generate_series(0, 23) h,
           lateral (select round((15 + 0.1 * (40 - d) - 0.6 * abs(h - 15) + ((d * 7 + h) % 5) * 0.3)::numeric, 1) as t) x
    union all
    select 'nyc', 'KNYC', ((current_date - d)::timestamp + make_interval(hours => h, mins => 55)) at time zone '${TZ}',
           t + 0.5, round((t + 0.5) * 9 / 5 + 32, 1), 'NWS'
      from generate_series(1, 40) d, generate_series(0, 23) h,
           lateral (select round((15 + 0.1 * (40 - d) - 0.6 * abs(h - 15) + ((d * 7 + h) % 5) * 0.3)::numeric, 1) as t) x;
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    values ('nyc', 'X', ((current_date - 36)::timestamp + interval '12 hours 10 minutes') at time zone '${TZ}', 30, 86, null);
    insert into public.weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select 'wellington', 'NZWN', ((current_date - d)::timestamp + make_interval(hours => h, mins => 0)) at time zone 'Pacific/Auckland',
           12 + h * 0.1, round((12 + h * 0.1) * 9 / 5 + 32, 1), 'IEM'
      from generate_series(32, 35) d, generate_series(0, 23) h;
    insert into public.derived_city_day_features
    select distinct o.city_key, (o.valid_at at time zone c.timezone)::date
      from public.weather_observations o join public.cities c using (city_key);
    -- Markets, bands and a book for days 40 back to yesterday, for the backtest.
    insert into public.markets select md5('m' || d)::uuid, 'nyc', current_date - d from generate_series(1, 40) d;
    insert into public.bands select md5('b' || d)::uuid, md5('m' || d)::uuid from generate_series(1, 40) d;
    insert into public.book_snapshots (band_id, observed_at)
    select md5('b' || d)::uuid, (current_date - d - 1)::timestamp at time zone 'UTC' from generate_series(1, 40) d;
    insert into public.markets values (md5('taipei-m')::uuid, 'taipei', current_date - 10),
                                      (md5('panama-m')::uuid, 'panama', current_date - 30);
    insert into public.bands values (md5('taipei-b')::uuid, md5('taipei-m')::uuid), (md5('panama-b')::uuid, md5('panama-m')::uuid);
    insert into public.book_snapshots (band_id, observed_at)
    values (md5('taipei-b')::uuid, (current_date - 11)::timestamp at time zone 'UTC'),
           (md5('panama-b')::uuid, (current_date - 31)::timestamp at time zone 'UTC');
    insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
    values ('taipei', 'best_match', (current_date - 11)::timestamp at time zone 'UTC', now(), current_date - 10, 1, 29, 'open-meteo'),
           ('panama', 'best_match', (current_date - 31)::timestamp at time zone 'UTC', now(), current_date - 30, 1, 30, 'open-meteo');
    -- Forecasts from 40 days back to 3 ahead: two models, leads 0-2, two runs
    -- per lead (the later one stands), and a run with no maximum.
    insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
    select 'nyc', m, ((current_date - d - l)::timestamp + make_interval(hours => r * 6)) at time zone 'UTC', now(),
           current_date - d, l, 20 + d * 0.1 + l * 0.3 + r * 0.2 + (m = 'gfs')::int, 'open-meteo'
      from generate_series(-3, 40) d, generate_series(0, 2) l, generate_series(0, 1) r, unnest(array['best_match', 'gfs']) m;
    insert into public.weather_forecasts (city_key, model, run_at, observed_at, for_date, lead_days, forecast_max_c, source)
    select 'nyc', 'best_match', ((current_date - d)::timestamp + interval '23 hours') at time zone 'UTC', now(),
           current_date - d, 0, null, 'open-meteo'
      from generate_series(1, 40) d;
    insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, source, observed_at)
    select 'nyc', 'open_meteo_ecmwf_ifs025', (current_date - d - 1)::timestamp at time zone 'UTC', current_date - d, 1,
           22 + d * 0.1, 'open-meteo-models-current', (current_date - d - 1)::timestamp at time zone 'UTC'
      from generate_series(1, 40) d;
    insert into public.v_verified_fact_forecast_outcome select 'nyc', current_date - d, 21 + d * 0.1 from generate_series(1, 40) d;
    insert into public.fact_forecast_outcome select 'nyc', current_date - d, 21 + d * 0.1 from generate_series(1, 40) d;
    insert into public.v_verified_weather_outcomes select 'nyc', current_date - d, 21 + d * 0.1 from generate_series(1, 40, 2) d;
    insert into public.derived_forecast_skill values (now());
    refresh materialized view public.mv_forecast_convergence_all;`);

  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];
  const snap = async (sql) => JSON.stringify(await q(sql));
  const SDM = `select * from public.v_station_day_max order by city_key, for_date`;
  const CONV = `select * from public.v_forecast_convergence order by city_key, for_date, model, lead_days`;
  const ALL = `select * from public.v_forecast_convergence_all_live order by city_key, for_date, model, lead_days`;
  const withPrimary = async (src) => db.exec(OBS_PRIMARY.replace(`'IEM'::text`, `'${src}'::text`));

  // Before: the reference, under each source as the primary one.
  const sdm0 = await q(SDM);
  assert.equal(sdm0.length, 40 + 4 + 1 + 1 + 2, 'the forty New York days, Wellington\'s four, Honolulu\'s, Taipei\'s, Panama\'s two');
  const sdmBefore = JSON.stringify(sdm0);
  await withPrimary('NWS');
  const sdmBeforeNws = await snap(SDM);
  assert.notEqual(sdmBeforeNws, sdmBefore, 'the fixture does not tell the two sources apart');
  await withPrimary('IEM');
  const convBefore = await snap(CONV);
  const allBefore = await snap(ALL);
  assert.equal((await q(CONV)).filter((r) => r.observed_max_c !== null).length > 0, true);
  assert.ok((await q(`select 1 from public.v_forecast_convergence where for_date < current_date - 30`)).length > 0,
    'the window must reach past the 30-day cut for this to test anything');

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // THE FIRST READ, BEFORE ANY REFRESH: the caches are empty, and a source's
  // day before the first whole one is read from the readings.
  assert.equal(await snap(SDM), sdmBefore, 'v_station_day_max changed before the cache was filled');
  assert.equal(await snap(CONV), convBefore, 'v_forecast_convergence changed');
  assert.equal(await snap(ALL), allBefore, 'v_forecast_convergence_all_live changed');
  assert.deepEqual((await one(`select reloptions from pg_class where oid = 'public.v_forecast_convergence'::regclass`)).reloptions,
    ['security_invoker=true'], 'v_forecast_convergence lost security_invoker');
  await db.exec(`refresh materialized view public.mv_forecast_convergence_all`);
  // The reference for the backtest: the first version with its caches empty
  // counts only what the tables hold, as the backtest always did.
  const bt0 = await one(`select obs_from, (obs_from = current_date - 40) as utc from public.v_backtest_window`);
  assert.equal(bt0.utc, true, 'observations begin on the UTC date of the first reading');
  const ready0 = (await one(`select public.backtest_readiness(current_date - 40, current_date - 1) as r`)).r;
  assert.equal(ready0.markets, 42);
  assert.equal(ready0.with_observation, 41, 'Taipei\'s morning-only day has no reading on its UTC date; Panama\'s has one');
  assert.equal(ready0.with_forecast, 41);

  // AS IT HAPPENED LIVE: the first version's refresh filled the cache, then
  // 20260929190000 added each day's first reading and filled it in.
  await one(`select public.refresh_city_day_hours() as r`);
  await db.exec(MIG2);
  await db.exec(MIG2);                                           // re-runnable
  assert.equal((await one(`select count(*)::int as n from public.derived_station_day_sources k
     where k.first_reading_at is distinct from (
       select min(o.valid_at) from public.weather_observations o join public.cities c using (city_key)
        where o.city_key = k.city_key and o.temp_c is not null and coalesce(o.source, '') = k.source
          and (o.valid_at at time zone c.timezone)::date = k.obs_date)`)).n, 0,
    'the backfill did not give every cached day its first reading');
  assert.equal((await one(`select obs_from from public.v_backtest_window`)).obs_from.toISOString(),
    bt0.obs_from.toISOString(), 'the filled cache moved where observations begin');
  assert.deepEqual((await one(`select public.backtest_readiness(current_date - 40, current_date - 1) as r`)).r, ready0,
    'the filled cache changed what the backtest counts on days the tables hold');

  // THE PRUNES REFUSE WHAT IS NOT KEPT. Observations are cut at an instant
  // 30 days back (part-way through a New York day); forecasts by date.
  // 14:00Z on day 30 back, whatever the hour the suite runs: Panama's
  // 02:00Z reading always falls before it.
  const cutAt = `((current_date - 30)::timestamp at time zone 'UTC' + interval '14 hours')`;
  const doomedObs = (await one(`select count(*)::int as n from public.weather_observations where valid_at < ${cutAt}`)).n;
  await one(`select public.refresh_city_day_hours() as r`);
  const stationRows = (await one(`select count(*)::int as n from public.derived_station_day_sources`)).n;
  assert.equal(stationRows, 40 * 2 + 1 + 4 + 1 + 1 + 2,
    'New York\'s days for each source, the sourceless one, Wellington\'s four, Honolulu\'s, Taipei\'s, Panama\'s two');
  assert.equal((await one(`select count(*)::int as n from public.derived_station_day_sources where first_reading_at is null`)).n, 0);
  assert.equal((await one(`select count(*)::int as n from public.derived_station_day_sources where source = ''`)).n, 1);
  await db.exec(`delete from public.derived_station_day_sources where city_key = 'nyc' and obs_date = current_date - 35 and source = 'NWS'`);
  const obsDry = await one(`select public.prune_observations(30, true, ${cutAt}) as r`);
  assert.equal(obsDry.r.ok, false, JSON.stringify(obsDry.r));
  assert.match(obsDry.r.error, /not in derived_station_day_sources/);
  assert.equal(Number(obsDry.r.unkept_station_days), 1, JSON.stringify(obsDry.r));
  await one(`select public.freeze_hit_forecasts() as r`);
  const fcDry = await one(`select public.prune_forecasts(30, true, current_date - 30) as r`);
  assert.equal(fcDry.r.ok, false, JSON.stringify(fcDry.r));
  assert.match(fcDry.r.error, /not in derived_forecast_latest/);

  // THE NIGHTLY REFRESH, as common.refresh_feature_cache runs it.
  const hours = await one(`select public.refresh_city_day_hours() as r`);
  assert.equal(hours.r.ok, true, JSON.stringify(hours.r));
  // Every source-day again but Honolulu's: its local day began before the
  // oldest reading held, so it counts as cut and is not rewritten.
  assert.equal(hours.r.station_days_written, stationRows - 1, JSON.stringify(hours.r));
  const latest = await one(`select public.freeze_forecast_latest() as r`);
  assert.equal(latest.r.ok, true, JSON.stringify(latest.r));
  assert.equal(latest.r.rows_written, 40 * 2 * 3 + 2, 'one row per day that has passed, model and lead, Taipei\'s and Panama\'s');
  assert.equal((await one(`select count(*)::int as n from public.derived_forecast_latest where for_date >= current_date`)).n, 0,
    'a day still ahead is read from the table, not frozen');
  await one(`select public.freeze_hit_forecasts() as r`);
  assert.equal(await snap(SDM), sdmBefore, 'the refresh changed v_station_day_max');
  assert.equal(await snap(CONV), convBefore, 'the freeze changed v_forecast_convergence');

  const doomedFc = (await one(`select count(*)::int as n from public.weather_forecasts where for_date < current_date - 30`)).n;
  const doomedFm = (await one(`select count(*)::int as n from public.weather_forecast_models where for_date < current_date - 30`)).n;
  // The cached days the prune reaches (it cuts New York part-way through
  // current_date - 30, local), with computed_at: a rewrite would move it.
  const CUT_ROWS = `select * from public.derived_station_day_sources where obs_date <= current_date - 30
                     order by city_key, obs_date, source`;
  const cutStation = await snap(CUT_ROWS);
  const obsDone = await one(`select public.prune_observations(30, false, ${cutAt}, ${doomedObs}) as r`);
  assert.equal(obsDone.r.ok, true, JSON.stringify(obsDone.r));
  const fcDone = await one(`select public.prune_forecasts(30, false, current_date - 30, ${doomedFc}) as r`);
  assert.equal(fcDone.r.ok, true, JSON.stringify(fcDone.r));
  const fmDone = await one(`select public.prune_forecast_models(30, false, null, ${doomedFm}) as r`);
  assert.equal(fmDone.r.ok, true, JSON.stringify(fmDone.r));
  assert.equal((await one(`select count(*)::int as n from public.weather_observations where city_key = 'wellington'`)).n, 0,
    'Wellington\'s days are all older than the cut');

  // THE READERS ARE UNCHANGED: every row and column, the cut day included.
  assert.equal(await snap(SDM), sdmBefore, 'v_station_day_max changed with the prune');
  await withPrimary('NWS');
  assert.equal(await snap(SDM), sdmBeforeNws, 'the cached days did not follow the primary source');
  await withPrimary('IEM');
  assert.equal(await snap(CONV), convBefore, 'v_forecast_convergence changed with the prune');
  assert.equal(await snap(ALL), allBefore, 'v_forecast_convergence_all_live changed with the prune');
  await db.exec(`refresh materialized view concurrently public.mv_forecast_convergence_all`);
  assert.equal(await snap(`select * from public.mv_forecast_convergence_all order by city_key, for_date, model, lead_days`),
    allBefore, 'the page cache changed');
  assert.equal((await one(`select obs_from from public.v_backtest_window`)).obs_from.toISOString(),
    bt0.obs_from.toISOString(), 'the backtest window lost the days the readings no longer hold');
  assert.deepEqual((await one(`select public.backtest_readiness(current_date - 40, current_date - 1) as r`)).r, ready0,
    'backtest readiness lost the days the tables no longer hold');

  // THE NEXT NIGHT: the cut day keeps what it was cached with, and a later
  // freeze keeps the frozen days.
  await one(`select public.refresh_city_day_hours() as r`);
  assert.ok((await snap(CUT_ROWS)) === cutStation, 'the refresh rewrote a day the prune had cut');
  await one(`select public.freeze_forecast_latest() as r`);
  assert.equal(await snap(CONV), convBefore, 'a later freeze lost or changed a frozen day');
  assert.equal(await snap(SDM), sdmBefore);

  // anon reads the convergence view, through v_forecast_latest to the frozen
  // rows it may not read itself; the caches and functions are the service role's.
  await db.exec(`set role anon`);
  assert.equal((await q(CONV)).length, JSON.parse(convBefore).length, 'anon cannot read v_forecast_convergence');
  await db.exec(`reset role`);
  for (const role of ['anon', 'authenticated']) {
    for (const t of ['public.derived_station_day_sources', 'public.derived_forecast_latest']) {
      assert.equal((await one(`select has_table_privilege('${role}', '${t}', 'select') as ok`)).ok, false, `${role} reads ${t}`);
    }
    assert.equal((await one(`select has_function_privilege('${role}', 'public.freeze_forecast_latest()', 'execute') as ok`)).ok,
      false, `${role} runs freeze_forecast_latest`);
  }
  assert.equal((await one(`select has_function_privilege('service_role', 'public.freeze_forecast_latest()', 'execute') as ok`)).ok, true);

  console.log('PASS: station-days: after the weather tables are pruned at 30 days v_station_day_max (under either primary source), both convergence views and the page cache return every row and column they returned before, and the backtest window and readiness see the same days; the new views equal the old before the first refresh; each prune refuses until the refresh has kept what it deletes; a cut day keeps its cached values; v_forecast_convergence stays security_invoker and anon reads it; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
