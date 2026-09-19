// ===========================================================================
// A MAXIMUM IS NEVER BELOW A READING OF THE SAME DAY.
//
// That is arithmetic, and on 2026-09-19 it was false for 14 of 54 cities:
// munich held a running maximum of 9.0 C while its thermometer read 21.5 C,
// and 25 cities held no maximum at all. The cause was that the running
// maximum was assembled only from weather_observations, and IEM serves 37 of
// the 54 cities about a day late - so for those cities the archive has
// nothing about today, while n8n keeps live_weather.temp_c current for all of
// them.
//
// That field drives holds_running_max, out_of_reach, day_decided, strategy s5
// and the observed-max floor in the probability engine. An understated
// maximum leaves probability on buckets the day has already passed and makes
// bands it has already cleared look unreachable.
//
// WHY THIS RUNS REAL SQL rather than reading the files as text. The bug this
// test exists to catch is a DISAGREEMENT between two pieces of SQL: the view
// that reports what a maximum rests on, and the function that stores it. I
// wrote exactly that bug an hour before writing this file - the function
// gated the live thermometer to the city's own day and the view did not, so
// sixteen cities were 'absent' in the table and 'floor_only' in the view, and
// nothing that read either one could have told. Text assertions pass on two
// definitions that disagree. Only running them does not.
//
// The SQL is EXTRACTED FROM THE SHIPPED FILES, never retyped here, so this
// cannot pass against a definition the database does not have.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const SQL = path.join(__dirname, '..', '..', 'sql');

/** One statement, lifted verbatim out of the file that ships it. */
function statement(file, opener, closer) {
  const src = fs.readFileSync(path.join(SQL, file), 'utf-8');
  const i = src.indexOf(opener);
  assert.ok(i >= 0, `${file} no longer contains ${JSON.stringify(opener)}`);
  const j = src.indexOf(closer, i + opener.length);
  assert.ok(j >= 0, `${file}: ${JSON.stringify(opener)} has no ${JSON.stringify(closer)}`);
  return src.slice(i, j + closer.length);
}

/** A timezone in which it is currently about midday.
 *
 *  The scenarios below place readings at now-2h, now-1h and now-10min and
 *  need all of them to fall on the same LOCAL day as now, and one reading at
 *  now-14h that must fall on the previous one. Anchoring to a fixed zone
 *  would make the suite pass or fail depending on the hour it was run. */
function middayZone() {
  let off = 12 - new Date().getUTCHours();
  if (off > 12) off -= 24;
  if (off < -11) off += 24;
  if (off === 0) return 'UTC';
  // Etc/GMT signs are inverted: Etc/GMT-8 is UTC+8.
  return off > 0 ? `Etc/GMT-${off}` : `Etc/GMT+${-off}`;
}

(async () => {
  const db = new PGlite();
  const TZ = middayZone();

  await db.exec(`create role anon; create role authenticated; create role service_role;`);

  // The fixture: only what the extracted SQL actually reads. live_weather
  // deliberately omits readings_today and running_max_basis - ad4_71 adds
  // them, and an install that fails to is an install where the provenance
  // silently never appears.
  await db.exec(`
    create table public.cities(city_key text primary key, display_name text, unit text,
      status text, timezone text, latitude numeric, longitude numeric);
    create table public.weather_observations(obs_id bigserial primary key, city_key text,
      valid_at timestamptz, temp_c numeric, source text);
    create table public.live_weather(city_key text primary key, updated_at timestamptz,
      observed_at timestamptz, temp_c numeric, running_max_c numeric,
      running_max_at timestamptz, running_min_c numeric, temp_change_1h numeric,
      temp_change_3h numeric, trend text, minutes_to_peak int, peak_window_state text,
      day_decided boolean default false, local_date date, source_kind text);
    -- v_trade_timing is the clock half and is not what is under test here; an
    -- empty stub is honest, and it is what a city with no observation series
    -- has in production anyway.
    create table public.trade_timing_rows(city_key text, local_hour numeric,
      window_opens_hour numeric, window_closes_hour numeric, minutes_to_peak int,
      rolling_over boolean, direction text);
    create view public.v_trade_timing as select * from public.trade_timing_rows;
  `);

  // The real v_city_today_readings, so "today's readings" cannot mean one
  // thing here and another in the timing chain.
  await db.exec(statement('ad4_26_temp_trend.sql',
    'create or replace view v_city_today_readings as', '::date;'));

  // The whole of ad4_71, including the two ALTERs and the grants.
  await db.exec(fs.readFileSync(path.join(SQL, 'ad4_71_observation_health.sql'), 'utf-8'));

  // The refresh function only - the rest of that file schedules pg_cron,
  // which PGlite has no extension for.
  await db.exec(statement('ad4_live_weather_timing.sql',
    'create or replace function public.refresh_live_weather_timing()', '\nend;\n$$;'));

  // ---- the four situations the 54 cities are actually in -------------------
  await db.exec(`
    insert into public.cities(city_key, display_name, status, timezone) values
      ('series_city',     'Series',      'active', '${TZ}'),
      ('floor_city',      'Floor only',  'active', '${TZ}'),
      ('stale_live_city', 'Stale live',  'active', '${TZ}'),
      ('one_reading_city','One reading', 'active', '${TZ}');

    -- A city the archive covers: three readings today, peaking at 22.0.
    insert into public.weather_observations(city_key, valid_at, temp_c, source) values
      ('series_city', now() - interval '2 hours',  18.0, 'iem'),
      ('series_city', now() - interval '1 hour',   22.0, 'iem'),
      ('series_city', now() - interval '10 minutes', 20.0, 'iem'),
      -- and one reading today for the city that has exactly one
      ('one_reading_city', now() - interval '30 minutes', 19.0, 'iem'),
      -- yesterday's archive for the two cities the feed runs a day behind on
      ('floor_city',      now() - interval '30 hours', 12.0, 'iem'),
      ('stale_live_city', now() - interval '30 hours', 27.0, 'iem');

    insert into public.live_weather(city_key, updated_at, observed_at, temp_c,
                                    running_max_c, running_max_at, running_min_c, source_kind)
    values
      ('series_city', now(), now() - interval '10 minutes', 20.0,
        null, null, null, 'station'),
      -- THE MUNICH CASE: a stored maximum of 9.0 from early this morning while
      -- the thermometer reads 21.5. Impossible, and it was the live state.
      ('floor_city', now(), now(), 21.5,
        9.0, now() - interval '6 hours', 9.0, 'model'),
      -- THE SHANGHAI CASE: the newest live reading belongs to YESTERDAY, so
      -- nothing at all is known about the day now in progress.
      ('stale_live_city', now(), now() - interval '14 hours', 28.7,
        null, null, null, 'model'),
      ('one_reading_city', now(), now() - interval '30 minutes', 19.0,
        null, null, null, 'station');
  `);

  const refreshed = (await db.query('select public.refresh_live_weather_timing() as n')).rows[0].n;
  assert.equal(refreshed, 4, 'the refresh did not touch every city');

  const rows = Object.fromEntries((await db.query(`
    select lw.city_key, lw.running_max_c::float8 as running_max_c,
           lw.running_min_c::float8 as running_min_c, lw.readings_today,
           lw.running_max_basis, lw.running_max_at, lw.local_date::text as local_date,
           r.running_max_c::float8 as view_max_c, r.running_max_basis as view_basis,
           h.timing_trustworthy, h.stored_max_below_latest, h.note
      from public.live_weather lw
      join public.v_city_running_max r using (city_key)
      join public.v_city_observation_health h using (city_key)
  `)).rows.map(r => [r.city_key, r]));

  // ---- 1. the invariant, city by city -------------------------------------
  assert.equal(rows.series_city.running_max_c, 22.0,
    'a series of readings must yield the highest of them');
  assert.equal(rows.series_city.readings_today, 3);
  assert.equal(rows.series_city.running_max_basis, 'series');
  assert.equal(rows.series_city.running_min_c, 18.0);

  assert.equal(rows.floor_city.running_max_c, 21.5,
    'a stored maximum of 9.0 beneath a thermometer reading 21.5 is the bug this '
    + 'file exists for: the maximum must rise to the reading, not stay below it');
  assert.equal(rows.floor_city.readings_today, 0);
  assert.equal(rows.floor_city.running_max_basis, 'floor_only',
    'one live reading is a FLOOR under the maximum, never the maximum');

  assert.equal(rows.stale_live_city.running_max_c, null,
    "yesterday's 28.7 must not become today's maximum - that is the same error "
    + "as handing today's running maximum to tomorrow's market");
  assert.equal(rows.stale_live_city.running_max_basis, 'absent');

  assert.equal(rows.one_reading_city.running_max_basis, 'floor_only',
    'ONE reading is not a series, however fresh it is - s5 must not lock on it');
  assert.equal(rows.one_reading_city.readings_today, 1);

  // ---- 2. nothing anywhere breaks the arithmetic --------------------------
  const impossible = (await db.query(`
    select lw.city_key, lw.running_max_c, lw.running_min_c, lw.temp_c
      from public.live_weather lw
      join public.cities c using (city_key)
     where (lw.observed_at at time zone c.timezone)::date
         = (now() at time zone c.timezone)::date
       and ( (lw.running_max_c is not null and lw.temp_c > lw.running_max_c)
          or (lw.running_min_c is not null and lw.temp_c < lw.running_min_c) )
  `)).rows;
  assert.deepEqual(impossible, [],
    'a maximum below - or a minimum above - a reading of the same day');

  // ---- 3. the view and the stored row cannot drift ------------------------
  //
  // Both sides call ad4_running_max_basis(), and both must feed it a live
  // reading gated to the city's own day. When only one of them did, sixteen
  // cities read 'absent' from the table and 'floor_only' from the view.
  for (const [city, r] of Object.entries(rows)) {
    assert.equal(r.view_basis, r.running_max_basis,
      `${city}: v_city_observation_health says ${r.view_basis} and live_weather says `
      + `${r.running_max_basis} - two definitions of the same fact have drifted apart`);
    assert.equal(r.view_max_c, r.running_max_c,
      `${city}: the view and the stored row disagree on the maximum itself`);
  }

  // ---- 4. timing is only trustworthy where a series is actually fresh -----
  assert.equal(rows.series_city.timing_trustworthy, true);
  for (const city of ['floor_city', 'stale_live_city', 'one_reading_city']) {
    assert.equal(rows[city].timing_trustworthy, false,
      `${city} has no usable series; s7 computes a slope from one and must not fire`);
  }

  // ---- 5. the day it is about is the CITY's day ---------------------------
  const localToday = (await db.query(
    `select (now() at time zone $1)::date::text as d`, [TZ])).rows[0].d;
  for (const [city, r] of Object.entries(rows)) {
    assert.equal(r.local_date, localToday,
      `${city} is stamped with a day that is not its own`);
  }

  console.log(`observation-health: 5 contracts hold (zone ${TZ}, local ${localToday})`);
  await db.close();
})().catch(err => { console.error(err); process.exit(1); });
