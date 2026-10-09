// ===========================================================================
// THE WEATHER TABLES KEEP THREE DAYS (Fresh Supabase, part 2b;
// 20261009090000_the_weather_tables_keep_three_days.sql).
//
// Six days of half-hourly readings in three zones (UTC+14, UTC-10, UTC), from
// an instant part-way through a day, and forecasts with two runs per lead
// from six days back to two ahead. On the real migration, with
// refresh_city_day_hours as live (20261008200000):
//   - both prunes refuse under three days and take three;
//   - the observations prune refuses while a whole day going has no peak in
//     derived_city_day_peak, names how many, and goes through once the
//     refresh has kept it; the first day the readings hold, which began
//     before the oldest of them, is never whole, never cached and never
//     asked for;
//   - a committed prune at the archive's instant deletes the counted rows,
//     and the next night's prune, past the day the first one cut into, goes
//     through too;
//   - the forecasts prune deletes every day before three back;
//   - service role only; re-runnable.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf-8');
const MIG = read('supabase/migrations/20261009090000_the_weather_tables_keep_three_days.sql');

// refresh_city_day_hours as live since 20261008200000.
function fn(file, name, quote) {
  const text = read(file);
  const start = text.search(new RegExp(`create or replace function (public\\.)?${name}\\(`));
  assert.ok(start >= 0, `${name} in ${file}`);
  const open = text.indexOf(quote, start);
  const close = text.indexOf(`${quote};`, open + quote.length);
  return text.slice(start, close + quote.length + 1);
}
const HOURS = fn('supabase/migrations/20261008200000_the_long_weather_readers_read_the_caches.sql',
  'refresh_city_day_hours', '$fn$');

const CITIES = { kiritimati: 'Pacific/Kiritimati', honolulu: 'Pacific/Honolulu', reykjavik: null };

(async () => {
  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];

  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    create table cities (city_key text primary key, timezone text);
    create table weather_observations (obs_id bigserial primary key, city_key text not null, station text,
      valid_at timestamptz not null, observed_at timestamptz not null default now(), temp_c numeric,
      temp_f numeric, source text not null);
    create table derived_city_day_features (city_key text, obs_date date, primary key (city_key, obs_date));
    create table derived_city_day_hours (city_key text not null, obs_date date not null, temp_c numeric[] not null,
      n_hours integer not null, computed_at timestamptz not null default now(), primary key (city_key, obs_date));
    create table derived_station_day_sources (city_key text not null, obs_date date not null, source text not null,
      max_c numeric, max_f numeric, n_readings bigint not null, last_reading_at timestamptz not null, station text,
      computed_at timestamptz not null default now(), first_reading_at timestamptz,
      primary key (city_key, obs_date, source));
    create table derived_city_day_peak (city_key text not null, obs_date date not null, peak_local_hour numeric not null,
      n_readings integer not null, computed_at timestamptz not null default now(), primary key (city_key, obs_date));
    create table weather_forecasts (forecast_id bigserial primary key, city_key text not null, model text not null,
      run_at timestamptz not null, for_date date not null, lead_days integer, forecast_max_c numeric, source text);
    create table derived_forecast_skill (city_key text, lead_days integer, computed_at timestamptz not null default now());
    create table v_hit_forecasts_live (city_key text, for_date date, lane text, model text, forecast_max_c numeric,
      known_at timestamptz);
    create table derived_hit_forecasts (city_key text, for_date date, lane text, model text, forecast_max_c numeric,
      known_at timestamptz);
    create table derived_forecast_latest (city_key text, for_date date, model text, lead_days integer,
      forecast_max_c numeric, run_at timestamptz);
    insert into cities values ${Object.entries(CITIES).map(([k, z]) => `('${k}', ${z ? `'${z}'` : 'null'})`).join(', ')};`);
  await db.exec(HOURS);

  // Six days of readings from 13:10Z six days back: Kiritimati's 03:10 the
  // next local day, Honolulu's 03:10, Reykjavik's 13:10. Every 23rd reading
  // without a temperature.
  await db.exec(`
    insert into weather_observations (city_key, station, valid_at, temp_c, source)
    select c.city_key, 'S', t,
           case when (extract(epoch from t)::bigint / 1800) % 23 = 0 then null
                else round((15 + 8 * sin(2 * pi() * (extract(hour from t at time zone coalesce(c.timezone, 'UTC')) - 9) / 24)
                            + (abs(hashtext(c.city_key || t::text)) % 300) / 100.0)::numeric, 1) end,
           'IEM'
      from cities c
     cross join generate_series((current_date - 6)::timestamp at time zone 'UTC' + interval '13 hours 10 minutes',
                                now(), interval '30 minutes') t;
    insert into weather_forecasts (city_key, model, run_at, for_date, lead_days, forecast_max_c, source)
    select c.city_key, 'm', (d - l)::timestamp at time zone 'UTC' + r * interval '6 hours', d, l,
           20 + l + r, 'open-meteo'
      from cities c
     cross join generate_series(current_date - 6, current_date + 2, interval '1 day') g(dd)
     cross join lateral (select g.dd::date as d) x
     cross join generate_series(0, 2) l
     cross join generate_series(0, 1) r;
    insert into derived_forecast_skill (city_key, lead_days) values ('reykjavik', 1);
    insert into derived_forecast_latest
    select distinct on (city_key, for_date, model, lead_days) city_key, for_date, model, lead_days, forecast_max_c, run_at
      from weather_forecasts where for_date < current_date order by city_key, for_date, model, lead_days, run_at desc;`);

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // The nightly refresh, as common.refresh_feature_cache runs it.
  const fill = async () => {
    const r = (await one('select refresh_city_day_hours() as r')).r;
    assert.equal(r.ok, true, JSON.stringify(r));
    await db.exec(`insert into derived_city_day_features
                   select distinct o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                     from weather_observations o join cities c using (city_key)
                   on conflict do nothing`);
  };
  await fill();

  // ---- 1. The floors. -------------------------------------------------------
  for (const [f, cut] of [['prune_observations', ''], ['prune_forecasts', '']]) {
    const two = (await one(`select public.${f}(2, true${cut}) as r`)).r;
    assert.equal(two.ok, false, JSON.stringify(two));
    assert.match(two.error, /at least 3/);
  }

  // ---- 2. The first day held was never whole and is never asked for. -------
  const firstDays = await q(`
    select c.city_key,
           ((select min(valid_at) from weather_observations) at time zone coalesce(c.timezone, 'UTC'))::date as d
      from cities c order by 1`);
  for (const { city_key: city, d } of firstDays) {
    const n = (await one(`select count(*)::int as n from derived_city_day_peak
                           where city_key = '${city}' and obs_date = '${d.toISOString().slice(0, 10)}'`)).n;
    assert.equal(n, 0, `${city}: the day the readings began part-way through was cached`);
  }

  // The archive's instant: three days back at 02:36Z.
  const cutAt = `((current_date - 3)::timestamp at time zone 'UTC' + interval '2 hours 36 minutes')`;
  const dry = (await one(`select public.prune_observations(3, true, ${cutAt}) as r`)).r;
  assert.equal(dry.ok, true, JSON.stringify(dry));

  // ---- 3. A whole day going without its peak stops the prune. --------------
  const victim = await one(`
    select k.city_key, k.obs_date::text as d from derived_city_day_peak k join cities c using (city_key)
     where k.city_key = 'kiritimati'
       and ((k.obs_date + 1)::timestamp at time zone c.timezone) <= ${cutAt}
     order by k.obs_date limit 1`);
  assert.ok(victim, 'no whole Kiritimati day lies wholly before the cut: the fixture tests nothing');
  await db.exec(`delete from derived_city_day_peak where city_key = '${victim.city_key}' and obs_date = '${victim.d}'`);
  const refused = (await one(`select public.prune_observations(3, true, ${cutAt}) as r`)).r;
  assert.equal(refused.ok, false, JSON.stringify(refused));
  assert.match(refused.error, /not in derived_city_day_peak/);
  assert.equal(Number(refused.unkept_peak_days), 1, JSON.stringify(refused));
  // A committed call is refused the same way, and deletes nothing.
  const before = (await one('select count(*)::int as n from weather_observations')).n;
  const doomed = (await one(`select count(*)::int as n from weather_observations where valid_at < ${cutAt}`)).n;
  assert.equal((await one(`select public.prune_observations(3, false, ${cutAt}, ${doomed}) as r`)).r.ok, false);
  assert.equal((await one('select count(*)::int as n from weather_observations')).n, before);

  // ---- 4. The refresh keeps it; the committed prune deletes what was counted.
  await fill();
  const done = (await one(`select public.prune_observations(3, false, ${cutAt}, ${doomed}) as r`)).r;
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), doomed);
  assert.equal((await one(`select count(*)::int as n from weather_observations where valid_at < ${cutAt}`)).n, 0);
  assert.ok(doomed > 0 && (await one('select count(*)::int as n from weather_observations')).n === before - doomed);

  // ---- 5. The next night: past the day the first prune cut into. -----------
  await fill();
  const cut2 = `(${cutAt} + interval '1 day')`;
  const doomed2 = (await one(`select count(*)::int as n from weather_observations where valid_at < ${cut2}`)).n;
  const next = (await one(`select public.prune_observations(3, false, ${cut2}, ${doomed2}) as r`)).r;
  assert.equal(next.ok, true, JSON.stringify(next));
  assert.equal(Number(next.deleted), doomed2);

  // ---- 6. The forecasts keep three days. ------------------------------------
  const fcDoomed = (await one(`select count(*)::int as n from weather_forecasts where for_date < current_date - 3`)).n;
  assert.equal(fcDoomed, 3 * 3 * 3 * 2, 'three cities, three days, three leads, two runs');
  const fcDry = (await one(`select public.prune_forecasts(3, true) as r`)).r;
  assert.equal(fcDry.ok, true, JSON.stringify(fcDry));
  assert.equal(Number(fcDry.would_delete), fcDoomed);
  const fcDone = (await one(`select public.prune_forecasts(3, false, current_date - 3, ${fcDoomed}) as r`)).r;
  assert.equal(fcDone.ok, true, JSON.stringify(fcDone));
  assert.equal((await one(`select min(for_date)::text as d from weather_forecasts`)).d,
    (await one(`select (current_date - 3)::text as d`)).d);

  // ---- 7. Service role only. ------------------------------------------------
  for (const sig of ['prune_observations(integer, boolean, timestamptz, bigint)',
                     'prune_forecasts(integer, boolean, date, bigint)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await one(`select has_function_privilege('${role}', 'public.${sig}', 'execute') as ok`)).ok, false,
        `${role} can run ${sig}`);
    }
    assert.equal((await one(`select has_function_privilege('service_role', 'public.${sig}', 'execute') as ok`)).ok, true, sig);
  }

  console.log('PASS: weather-tables-keep-three-days: both prunes refuse under three days and take three; the observations prune refuses while a whole day going has no cached peak (the part-day the readings begin with is never asked for), goes through once the refresh keeps it, deletes the counted rows and goes through again the next night; the forecasts prune deletes every day before three back; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
