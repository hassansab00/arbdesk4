// ===========================================================================
// THE CLIMB PROFILE READS THE LAST 30 WHOLE LOCAL DAYS (plan v2 P1.6 phase 2,
// step 1; 20260929100000_the_climb_profile_reads_thirty_days.sql).
//
// Scored on a year of the repository's readings, 30 days of history beat 60,
// 90 and all of it (docs/HISTORY_WINDOWS_2026-09-29.md), each day predicted
// from the days strictly before it. So the view reads exactly those: local
// days today-30 to today-1 in each city's own zone, whatever the table keeps.
// Run on the real migration, four zones without daylight saving from UTC-11 to
// UTC+14:
//   - every city-hour counts 30 days, not the 45 held before them;
//   - today, cut at 13:00 but past the 12-hour test, is not one of them (it
//     used to be: the 05:00Z refresh read Asian days cut at 13:00);
//   - the oldest day is whole even at UTC+14, where it starts 14 hours before
//     its UTC date: the index bound (32 days) reaches it;
//   - the values are those of the 30 days alone.
// Everything runs in one transaction, so the readings and the view share one
// now() and a run across midnight cannot move "today" between them.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260929100000_the_climb_profile_reads_thirty_days.sql'), 'utf-8');
const CITIES = { utc: 'UTC', tokyo: 'Asia/Tokyo', kiritimati: 'Pacific/Kiritimati', pago: 'Pacific/Pago_Pago' };

(async () => {
  const db = new PGlite();
  await db.exec(`
    set timezone = 'UTC';
    create table cities (city_key text primary key, timezone text);
    create table weather_observations (obs_id bigserial primary key, city_key text not null,
      valid_at timestamptz not null, temp_c numeric);
    insert into cities values ${Object.entries(CITIES).map(([k, z]) => `('${k}', '${z}')`).join(', ')};`);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  const rows = await db.transaction(async (tx) => {
    // Days today-45 .. today-31: flat at 30 C, so no climb at any hour.
    // Days today-30 .. today-1: 10 + h up to 24 C at 14:00, then down 1 C an
    // hour, so the climb left at hour h is 14 - h before 14:00 and 0 after.
    // Today: 00:00-13:00 only, rising 10 + h - 14 hours, past the 12-hour test.
    await tx.exec(`
      insert into weather_observations (city_key, valid_at, temp_c)
      select c.city_key, (d::date + make_interval(hours => h)) at time zone c.timezone,
             case when d::date < (now() at time zone c.timezone)::date - 30 then 30
                  when d::date = (now() at time zone c.timezone)::date then 10 + h
                  when h <= 14 then 10 + h else 24 - (h - 14) end
        from cities c
        cross join lateral generate_series(((now() at time zone c.timezone)::date - 45)::timestamp,
                                           ((now() at time zone c.timezone)::date)::timestamp,
                                           interval '1 day') d
        cross join generate_series(0, 23) h
       where d::date < (now() at time zone c.timezone)::date or h <= 13;
      -- a reading without a temperature is not a reading
      insert into weather_observations (city_key, valid_at, temp_c)
      select city_key, now() - interval '3 days', null from cities;`);
    return (await tx.query(`select * from v_city_climb_profile_live order by city_key, local_hour`)).rows;
  });

  const by = {};
  for (const r of rows) (by[r.city_key] ||= []).push(r);
  for (const city of Object.keys(CITIES)) {
    const cells = by[city] || [];
    assert.equal(cells.length, 24, `${city}: ${cells.length} hours served`);
    for (const c of cells) {
      const at = `${city} ${c.local_hour}:00`;
      assert.equal(c.n_days, 30, `${at} counted ${c.n_days} days`);
      const want = Math.max(14 - c.local_hour, 0);
      assert.equal(Number(c.typical_climb_left_c), want, `${at} climb left ${c.typical_climb_left_c}`);
      assert.equal(Number(c.climb_left_sd_c), 0, `${at} sd ${c.climb_left_sd_c}: another day got in`);
      assert.equal(Number(c.pct_already_peaked), c.local_hour >= 14 ? 100 : 0, `${at} peaked`);
    }
  }

  console.log('PASS: climb-profile-window: from UTC-11 to UTC+14 every city-hour counts exactly the 30 whole local days before today - not the older days held, not today cut at 13:00, and the oldest day whole where it starts 14 hours before its UTC date - with the values of those days alone; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
