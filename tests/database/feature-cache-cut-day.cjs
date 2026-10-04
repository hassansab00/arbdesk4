// ===========================================================================
// A DAY THE PRUNE HAS CUT INTO KEEPS ITS CACHED VALUES (plan v2 P1.6, 28 Sep,
// 20260929010000_a_cut_day_keeps_its_cached_values.sql).
//
// prune_observations cuts every city at one instant, part-way through most
// cities' local day. refresh_feature_cache used to recompute that day from
// what the cut left and overwrite the value cached while it was whole (live,
// 28 Sep: 152 of 470 cut city-days with a daily maximum too low, by up to
// 12 C). Now a day that began before the oldest reading held is never
// updated - only inserted if it was never cached - and the first whole day's
// day-over-day terms come from the cached day before it. Whole days are
// computed exactly as the view computes them. Run with the real view
// (sql/ad4_21) and the real climb-profile view (sql/ad4_28).
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260929010000_a_cut_day_keeps_its_cached_values.sql'), 'utf-8');
const viewText = (file, name) => {
  const s = fs.readFileSync(path.join(ROOT, 'sql', file), 'utf-8');
  const a = s.indexOf(`create or replace view ${name} as`);
  assert.ok(a >= 0, `${name} not found in ${file}`);
  // the view ends at the first line that is exactly a statement end after it
  const rest = s.slice(a);
  const m = rest.search(/;\s*\n(\s*\n|--)/);
  return rest.slice(0, m + 1);
};

(async () => {
  const db = new PGlite();
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    create table cities (city_key text primary key, timezone text);
    create table weather_observations (obs_id bigserial primary key, city_key text, station text,
      valid_at timestamptz, temp_c numeric, dewpoint_c numeric, humidity numeric, wind_speed numeric,
      wind_dir_deg numeric, precip numeric, cloud_cover numeric, pressure_hpa numeric,
      source text not null);
    create table derived_city_day_features (city_key text, obs_date date, max_c numeric, min_c numeric,
      diurnal_range_c numeric, n_obs int, prev_max_c numeric, delta_max_c numeric, morning_temp_c numeric,
      morning_dewpoint_c numeric, dewpoint_depression_c numeric, morning_humidity numeric,
      morning_pressure_hpa numeric, morning_to_max_c numeric, cloud_mean numeric, cloud_max numeric,
      wind_mean numeric, wind_max numeric, precip_total numeric, pressure_change_24h_hpa numeric,
      wind_u_mean numeric, wind_v_mean numeric, computed_at timestamptz,
      primary key (city_key, obs_date));
    create table derived_climb_profile (city_key text, local_hour int, n_days int, typical_climb_left_c numeric,
      climb_left_sd_c numeric, climb_left_p10_c numeric, climb_left_p90_c numeric, pct_already_peaked numeric);
    insert into cities values ('nyc', 'America/New_York'), ('tokyo', 'Asia/Tokyo');
  `);
  await db.exec(viewText('ad4_21_weather_features.sql', 'v_city_day_features'));
  await db.exec(viewText('ad4_28_feature_cache.sql', 'v_city_climb_profile_live'));
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // Hourly readings, 30 Jun 00:00Z to 6 Jul 00:00Z. Each local day peaks at
  // 15:00 local at 20 + days since 30 Jun, and cools 0.8 C an hour either side, so
  // a day's evening alone reads several degrees under its maximum.
  await db.exec(`
    insert into weather_observations (city_key, valid_at, source, temp_c, dewpoint_c, pressure_hpa)
    select c.city_key, t, 'IEM',
           20 + ((t at time zone c.timezone)::date - date '2026-06-30')
              - 0.8 * abs(extract(hour from (t at time zone c.timezone)) - 15),
           10, 1010 + ((t at time zone c.timezone)::date - date '2026-06-30')
      from cities c,
           generate_series(timestamptz '2026-06-30 00:00+00', timestamptz '2026-07-05 23:00+00', interval '1 hour') t`);
  const refresh = async () => {
    for (const c of ['nyc', 'tokyo']) {
      const r = (await db.query(`select refresh_feature_cache(null, '${c}') as r`)).rows[0].r;
      assert.equal(r.ok, true, JSON.stringify(r));
    }
  };
  const day = async (c, d) => (await db.query(
    `select n_obs, max_c::float as max_c, prev_max_c::float as prev, delta_max_c::float as delta,
            pressure_change_24h_hpa::float as dp
       from derived_city_day_features where city_key = '${c}' and obs_date = '${d}'`)).rows[0];

  await refresh();
  // Whole days carry every reading and the true maximum.
  assert.deepEqual(await day('nyc', '2026-07-02'), { n_obs: 24, max_c: 22, prev: 21, delta: 1, dp: 1 });
  assert.deepEqual(await day('tokyo', '2026-07-02'), { n_obs: 24, max_c: 22, prev: 21, delta: 1, dp: 1 });
  // The data's own first day is part of a day, and is cached once (the
  // prune's guard needs a row) - it began before the oldest reading held.
  const tokyoFirst = await day('tokyo', '2026-06-30');
  assert.ok(tokyoFirst && tokyoFirst.n_obs === 15, JSON.stringify(tokyoFirst));

  // THE CUT: every reading before 2 Jul 00:00Z goes, as the prune does. For
  // nyc (UTC-4) that leaves the last four hours of local 1 Jul; for tokyo
  // (UTC+9) it takes the first nine hours of local 2 Jul.
  await db.exec(`delete from weather_observations where valid_at < timestamptz '2026-07-02 00:00+00'`);
  const view = async (c, d) => (await db.query(
    `select n_obs, max_c::float as max_c from v_city_day_features where city_key = '${c}' and obs_date = '${d}'`)).rows[0];
  assert.deepEqual(await view('nyc', '2026-07-01'), { n_obs: 4, max_c: 17 }, 'the fixture does not cut the day');
  assert.deepEqual(await view('tokyo', '2026-07-02'), { n_obs: 15, max_c: 22 });

  const r = (await db.query(`select refresh_feature_cache(null, 'nyc') as r`)).rows[0].r;
  await db.query(`select refresh_feature_cache(null, 'tokyo')`);
  assert.equal(r.cut_days_left_as_cached, 1);

  // The cut days keep what they held while whole...
  assert.deepEqual(await day('nyc', '2026-07-01'), { n_obs: 24, max_c: 21, prev: 20, delta: 1, dp: 1 },
    'a cut day was overwritten from what the cut left');
  assert.deepEqual(await day('tokyo', '2026-07-02'), { n_obs: 24, max_c: 22, prev: 21, delta: 1, dp: 1 },
    'a cut day was overwritten from what the cut left');
  // ...and the first whole day measures itself against the cached day before
  // it, not against the evening the cut left (the view's lag says 17).
  assert.deepEqual(await day('nyc', '2026-07-02'), { n_obs: 24, max_c: 22, prev: 21, delta: 1, dp: 1 },
    'the first whole day took its prev_max_c from the part-day');
  assert.deepEqual(await day('tokyo', '2026-07-03'), { n_obs: 24, max_c: 23, prev: 22, delta: 1, dp: 1 });
  // A later whole day is exactly what the view says.
  const v = (await db.query(`select max_c::float as m, n_obs, prev_max_c::float as p from v_city_day_features where city_key = 'nyc' and obs_date = '2026-07-04'`)).rows[0];
  assert.deepEqual(await day('nyc', '2026-07-04'), { n_obs: v.n_obs, max_c: v.m, prev: v.p, delta: 1, dp: 1 });

  // Running it again changes nothing.
  const before = (await db.query(`select md5(string_agg(row(city_key, obs_date, max_c, n_obs, prev_max_c, delta_max_c)::text, '|' order by city_key, obs_date)) as h from derived_city_day_features`)).rows[0].h;
  await refresh();
  const after = (await db.query(`select md5(string_agg(row(city_key, obs_date, max_c, n_obs, prev_max_c, delta_max_c)::text, '|' order by city_key, obs_date)) as h from derived_city_day_features`)).rows[0].h;
  assert.equal(after, before, 'a second refresh changed the cache');

  // THE LABEL IS THE SETTLEMENT FEED'S MAXIMUM (4 Oct, 20261004130000).
  // A five-minute NWS reading warmer than the routine reports does not move
  // max_c; max_c_all_sources keeps it. A day the routine feed missed keeps
  // the maximum over what was measured.
  await db.exec(`
    insert into weather_observations (city_key, valid_at, source, temp_c) values
      ('nyc', timestamptz '2026-07-04 19:35+00', 'NWS', 26),
      ('nyc', timestamptz '2026-07-06 16:00+00', 'NWS', 30)`);
  const lab = async (d) => (await db.query(
    `select max_c::float as max_c, max_c_all_sources::float as all_src, max_c_source as src
       from v_city_day_features where city_key = 'nyc' and obs_date = '${d}'`)).rows[0];
  assert.deepEqual(await lab('2026-07-04'), { max_c: 24, all_src: 26, src: 'settlement_feed' },
    'a five-minute reading moved the label');
  assert.deepEqual(await lab('2026-07-06'), { max_c: 30, all_src: 30, src: 'all_sources' });
  await refresh();
  assert.deepEqual(await day('nyc', '2026-07-04'), { n_obs: 25, max_c: 24, prev: 23, delta: 1, dp: 1 },
    'the cache took the five-minute maximum');

  console.log("PASS: feature-cache-cut-day: a day the prune cut into keeps the values it was cached with (nyc's evening, tokyo's afternoon), the first whole day's prev_max_c/delta/pressure change come from the cached day before, whole days match the view, the label is the settlement feed's maximum, a never-cached first day is still cached for the prune's guard, a rerun changes nothing, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
