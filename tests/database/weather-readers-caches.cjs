// ===========================================================================
// THE LONG WEATHER READERS READ THE CACHES (Fresh Supabase, part 2a;
// 20261008200000_the_long_weather_readers_read_the_caches.sql).
//
// Three SQL readers read a month of weather_observations themselves. Before
// the readings' keep falls to three days, each reads what the readings leave
// behind. Run on the real migration over 40 days of half-hourly readings in
// three zones (two sources in one city, readings without a temperature):
//   - with every reading held, the climb profile, the city climate and the
//     peak hour are exactly what the old definitions give;
//   - refresh_city_day_hours keeps each whole day's peak, never the cut day;
//   - after a three-day prune, the climb profile and the climate are still
//     exactly the old ones over every reading, and the peak hour is the old
//     function over the days the readings still hold (the cut day as cut, as
//     today) plus every whole day before it;
//   - v_city_utc_day_max is recompute_correlation's UTC-day grouping, a day
//     with no temperature included;
//   - grants, the peak function's search_path, and a second run.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf-8');
const MIG = read('supabase/migrations/20261008200000_the_long_weather_readers_read_the_caches.sql');
const OLD_CLIMB = read('supabase/migrations/20260929100000_the_climb_profile_reads_thirty_days.sql');

// The definitions live before this migration, from their install files.
function fn(file, name, quote) {
  const text = read(file);
  const start = text.search(new RegExp(`create or replace function (public\\.)?${name}\\(`));
  assert.ok(start >= 0, `${name} in ${file}`);
  const open = text.indexOf(quote, start);
  const close = text.indexOf(`${quote};`, open + quote.length);
  return text.slice(start, close + quote.length + 1);
}
const OLD_PEAK = fn('sql/ad4_56_correlation_speed_and_peak_key.sql', 'refresh_weather_peak_city', '$ad4$');
// As live before this migration (md5 7d84a98b..., the same body as sql/ad4_97 had).
const OLD_HOURS = fn('supabase/migrations/20260929190000_the_backtest_counts_what_it_counted.sql', 'refresh_city_day_hours', '$fn$');
// v_city_climate and v_city_daily_max as live on 8 Oct (pg_get_viewdef).
const OLD_CLIMATE = `
  create view v_city_daily_max as
  select o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as obs_date,
         max(o.temp_c) as max_c, min(o.temp_c) as min_c, count(*)::integer as n_obs
    from weather_observations o left join cities c on c.city_key = o.city_key
   where o.temp_c is not null
   group by o.city_key, ((o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date);
  create view v_city_climate as
  with today as (
    select c.city_key, (now() at time zone coalesce(c.timezone, 'UTC'::text))::date as local_today from cities c
  ), seasonal as (
    select d.city_key, avg(d.max_c)::numeric(6,2) as normal_max_c, stddev_samp(d.max_c)::numeric(6,2) as sd_max_c,
           count(*)::integer as n_days
      from v_city_daily_max d join today t_1 on t_1.city_key = d.city_key
     where least(abs(extract(doy from d.obs_date) - extract(doy from t_1.local_today)),
                 365::numeric - abs(extract(doy from d.obs_date) - extract(doy from t_1.local_today))) <= 10::numeric
       and d.obs_date < t_1.local_today
     group by d.city_key
  ), recent as (
    select d.city_key, avg(d.max_c)::numeric(6,2) as normal_max_c, stddev_samp(d.max_c)::numeric(6,2) as sd_max_c,
           count(*)::integer as n_days
      from v_city_daily_max d join today t_1 on t_1.city_key = d.city_key
     where d.obs_date >= (t_1.local_today - 30) and d.obs_date < t_1.local_today
     group by d.city_key
  )
  select t.city_key, t.local_today,
         case when coalesce(s.n_days, 0) >= 15 then 'seasonal'::text
              when coalesce(r.n_days, 0) >= 5 then 'trailing_30d'::text else 'none'::text end as baseline,
         case when coalesce(s.n_days, 0) >= 15 then s.normal_max_c else r.normal_max_c end as normal_max_c,
         case when coalesce(s.n_days, 0) >= 15 then s.sd_max_c else r.sd_max_c end as sd_max_c,
         case when coalesce(s.n_days, 0) >= 15 then s.n_days else r.n_days end as baseline_days
    from today t left join seasonal s on s.city_key = t.city_key left join recent r on r.city_key = t.city_key;`;

const CITIES = { nyc: 'America/New_York', tokyo: 'Asia/Tokyo', reykjavik: null };
const CLIMB = 'select * from v_city_climb_profile_live order by city_key, local_hour';
const CLIMATE = 'select * from v_city_climate order by city_key';
const PEAKS = 'select city_key, month, peak_hour_local, window_width_h, n_days from derived_weather_peak order by 1, 2';

(async () => {
  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;
  const snap = async (sql) => JSON.stringify(await q(sql));
  const peaks = async () => {
    await db.exec('truncate derived_weather_peak');
    // Two days a month, not twenty, so the partial first day (August) and the
    // cut day (October) each sit in a month the function reports.
    for (const c of Object.keys(CITIES)) await db.exec(`select refresh_weather_peak_city('${c}', 2)`);
    return snap(PEAKS);
  };

  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    create table cities (city_key text primary key, timezone text);
    create table weather_observations (obs_id bigserial primary key, city_key text not null, station text,
      valid_at timestamptz not null, observed_at timestamptz not null default now(), temp_c numeric,
      temp_f numeric, source text not null);
    create table derived_city_day_hours (city_key text not null, obs_date date not null, temp_c numeric[] not null,
      n_hours integer not null, computed_at timestamptz not null default now(), primary key (city_key, obs_date));
    create table derived_station_day_sources (city_key text not null, obs_date date not null, source text not null,
      max_c numeric, max_f numeric, n_readings bigint not null, last_reading_at timestamptz not null, station text,
      computed_at timestamptz not null default now(), first_reading_at timestamptz,
      primary key (city_key, obs_date, source));
    create table derived_weather_peak (city_key text not null, month integer not null, peak_hour_local numeric,
      window_width_h numeric, n_days integer, computed_at timestamptz not null default now(),
      primary key (city_key, month));
    insert into cities values ${Object.entries(CITIES).map(([k, z]) => `('${k}', ${z ? `'${z}'` : 'null'})`).join(', ')};`);
  await db.exec(OLD_CLIMB);
  await db.exec(OLD_CLIMATE);
  await db.exec(OLD_PEAK);
  await db.exec(OLD_HOURS);
  // As live: the peak function's search_path and its grants.
  await db.exec(`alter function refresh_weather_peak_city(text, int) set search_path = public, extensions;
                 grant execute on function refresh_weather_peak_city(text, int) to service_role;`);

  // 40 days of half-hourly readings, from an instant part-way through a day:
  // a diurnal curve peaking near 15:00 local with hashed noise, a second
  // source every hour in nyc, and every 37th reading without a temperature.
  await db.exec(`
    insert into weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select c.city_key, 'S', t,
           case when (extract(epoch from t)::bigint / 1800) % 37 = 0 then null
                else round((15 + 8 * sin(2 * pi() * (extract(hour from t at time zone coalesce(c.timezone, 'UTC')) - 9) / 24)
                            + (abs(hashtext(c.city_key || t::text)) % 300) / 100.0)::numeric, 1) end,
           null, 'IEM'
      from cities c
     cross join generate_series(date_trunc('hour', now()) - interval '40 days' + interval '7 hours 30 minutes',
                                now(), interval '30 minutes') t;
    insert into weather_observations (city_key, station, valid_at, temp_c, temp_f, source)
    select 'nyc', 'K', t, round((14 + 9 * sin(2 * pi() * (extract(hour from t at time zone 'America/New_York') - 9.5) / 24)
                                 + (abs(hashtext('k' || t::text)) % 250) / 100.0)::numeric, 1), null, 'NWS'
      from generate_series(date_trunc('hour', now()) - interval '40 days' + interval '8 hours 15 minutes',
                           now(), interval '1 hour') t;
    update weather_observations set temp_f = round(temp_c * 9 / 5 + 32, 1) where temp_c is not null;`);

  // ---- 1. Every reading held: the new definitions are the old ones. ------
  await db.exec('select refresh_city_day_hours()');
  const climb0 = await snap(CLIMB);
  const climate0 = await snap(CLIMATE);
  const peaks0 = await peaks();
  assert.ok(JSON.parse(climb0).length >= 3 * 20, 'the fixture serves too few climb cells to test anything');
  assert.ok(JSON.parse(climate0).every((r) => r.baseline === 'trailing_30d'), climate0);
  assert.ok(JSON.parse(peaks0).length >= 3, 'the fixture serves too few peak months');

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  assert.equal(await snap(CLIMB), climb0, 'the climb profile changed with every reading held');
  assert.equal(await snap(CLIMATE), climate0, 'the city climate changed with every reading held');
  assert.equal(await peaks(), peaks0, 'the peak hour changed with every reading held');

  // ---- 2. The peak of each whole day, kept; never the cut day. ------------
  const r = (await q('select refresh_city_day_hours() as r'))[0].r;
  assert.equal(r.ok, true);
  const firstWhole = await q(`
    with h as (select min(valid_at) as oldest from weather_observations)
    select c.city_key,
           case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                       at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date end as first_whole
      from cities c cross join h order by 1`);
  const cached = await q(`select k.city_key, min(k.obs_date) as lo, count(*)::int as n,
                                 count(*) filter (where k.n_readings < 12)::int as thin
                            from derived_city_day_peak k group by 1 order by 1`);
  for (const [i, f] of firstWhole.entries()) {
    assert.equal(cached[i].city_key, f.city_key);
    assert.equal(String(cached[i].lo), String(f.first_whole), `${f.city_key}: a day before the first whole one was cached`);
  }
  // Each cached peak is the reading the old function ranks first.
  const wrong = await q(`
    with d as (
      select o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as local_date,
             extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))
               + extract(minute from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) / 60.0 as local_hour,
             count(*) over (partition by o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date) as n,
             row_number() over (partition by o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                                order by o.temp_c desc, o.valid_at) as rk
        from weather_observations o join cities c using (city_key) where o.temp_c is not null)
    select count(*)::int as n from derived_city_day_peak k
      left join d on d.city_key = k.city_key and d.local_date = k.obs_date and d.rk = 1
     where d.local_hour is distinct from k.peak_local_hour or d.n is distinct from k.n_readings`);
  assert.equal(wrong[0].n, 0, 'a cached peak is not the day\'s first-ranked reading');

  // ---- 3. The prune keeps three days. -------------------------------------
  await db.exec(`create table kept as select * from weather_observations;
                 delete from weather_observations where valid_at < now() - interval '3 days';`);
  assert.equal(await snap(CLIMB), climb0, 'the climb profile changed when the readings kept three days');
  assert.equal(await snap(CLIMATE), climate0, 'the city climate changed when the readings kept three days');

  // The peak hour: the old function over the days the readings still hold
  // (the cut day as cut) and every whole day the cache kept before them.
  const peaks3 = await peaks();
  await db.exec(`
    create table held as select * from weather_observations;
    insert into weather_observations
    select k.* from kept k join cities c using (city_key)
     where (k.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= (
             select case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                                at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                         then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                         else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date end
               from (select min(valid_at) as oldest from kept) h)
       and (k.valid_at at time zone coalesce(c.timezone, 'UTC'))::date < (
             select min((w.valid_at at time zone coalesce(c.timezone, 'UTC'))::date)
               from held w where w.city_key = k.city_key)
       and not exists (select 1 from held w where w.obs_id = k.obs_id);`);
  await db.exec(OLD_PEAK);
  const expected = await peaks();
  assert.notEqual(expected, peaks0, 'the cut changed nothing the old function saw: the fixture tests nothing');
  assert.equal(peaks3, expected, 'the peak hour after the prune is not the readings held plus the whole days before');
  await db.exec(`truncate weather_observations; insert into weather_observations select * from held;`);
  await db.exec(MIG);
  assert.equal(await peaks(), expected, 're-applied, the peak hour moved');

  // ---- 4. Each city's UTC-day maximum, as recompute_correlation grouped it.
  await db.exec(`insert into weather_observations (city_key, valid_at, temp_c, source)
                 values ('reykjavik', date_trunc('day', now()) + interval '1 hour' - interval '1 day', null, 'X')`);
  const utc = await q(`
    select (select count(*)::int from (select * from v_city_utc_day_max
                                        except all
                                        select city_key, valid_at::date, max(temp_c) from weather_observations group by 1, 2) a) as extra,
           (select count(*)::int from (select city_key, valid_at::date, max(temp_c) from weather_observations group by 1, 2
                                        except all
                                        select * from v_city_utc_day_max) b) as missing,
           (select count(*)::int from v_city_utc_day_max) as n`);
  assert.deepEqual([utc[0].extra, utc[0].missing], [0, 0]);
  assert.ok(utc[0].n > 0);

  // ---- 5. Grants and settings. ---------------------------------------------
  const peakFn = (await q(`select proconfig::text as cfg,
                                  has_function_privilege('service_role', oid, 'execute') as svc
                             from pg_proc where proname = 'refresh_weather_peak_city'`))[0];
  assert.equal(peakFn.cfg, '{"search_path=public, extensions"}');
  assert.equal(peakFn.svc, true, 'the peak function lost its grant');
  for (const role of ['anon', 'authenticated']) {
    for (const rel of ['derived_city_day_peak', 'v_city_utc_day_max']) {
      assert.equal((await q(`select has_table_privilege('${role}', '${rel}', 'select') as ok`))[0].ok, false,
        `${role} can read ${rel}`);
    }
    assert.equal((await q(`select has_function_privilege('${role}', 'refresh_city_day_hours(text)', 'execute') as ok`))[0].ok,
      false, `${role} can refresh the caches`);
  }
  for (const rel of ['derived_city_day_peak', 'v_city_utc_day_max']) {
    assert.equal((await q(`select has_table_privilege('service_role', '${rel}', 'select') as ok`))[0].ok, true, rel);
  }

  console.log('PASS: weather-readers-caches: with every reading held the climb profile, city climate and peak hour are the old ones; each whole day\'s peak is cached, never the cut day; after a three-day prune the climb profile and climate are unchanged and the peak hour is the readings held plus the whole days before; v_city_utc_day_max groups as recompute_correlation did; service role only, search_path kept, re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
