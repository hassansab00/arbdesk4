// ===========================================================================
// DOES OUR THERMOMETER NAME THE BAND THE VENUE SETTLED ON? (plan v2 P2.2)
//
// The three cases the plan names, as fixtures, run through the SQL that ships:
//
//   DALLAS 13 SEP   The routine METAR peaked at 99.0F; an NWS five-minute row
//                   at :55 said 100.4F (a whole 38C). The old view kept any
//                   row within four minutes of report_minute whatever its
//                   source, read 100.4 and missed the 98-99F winner.
//   NYC 20 SEP      Routine reports peaked at 71F; a report the station filed
//                   off the hour said 71.96F (22.2C) and the winner was
//                   72-73F. Counted, and rounded the way the venue reads it,
//                   that report names the winner. The minute window threw it
//                   away.
//   LONDON 17 SEP   Half-hourly METARs at :20 and :50. The max, 22C, came at
//                   14:20Z; report_minute is 50, so the window dropped it and
//                   we read 21.
//
// NYC and London only come right once the primary source carries every report
// the station files (P2.1) - these fixtures give it those reports, which is
// the state P2.1 produces. Dallas is right today.
//
// The SQL is LIFTED FROM THE SHIPPED FILES, never retyped here.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const SQL = path.join(__dirname, '..', '..', 'sql');

function statement(file, opener, closer) {
  const src = fs.readFileSync(path.join(SQL, file), 'utf-8');
  const i = src.indexOf(opener);
  assert.ok(i >= 0, `${file} no longer contains ${JSON.stringify(opener)}`);
  const j = src.indexOf(closer, i + opener.length);
  assert.ok(j >= 0, `${file}: ${JSON.stringify(opener)} has no ${JSON.stringify(closer)}`);
  return src.slice(i, j + closer.length);
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create table cities(city_key text primary key, timezone text, report_minute int);
    create table weather_observations(obs_id bigserial primary key, city_key text, valid_at timestamptz,
      temp_c numeric, temp_f numeric, source text, station text);
    create table bands(band_id text primary key, market_id text);
    -- v_canonical_markets and v_coherent_band_outcome are views in production;
    -- here they are tables holding exactly the columns this file reads.
    create table v_canonical_markets(market_id text primary key, unit text);
    create table v_coherent_band_outcome(band_id text, city_key text, for_date date, band_lo numeric,
      band_hi numeric, open_low boolean, open_high boolean, settled_yes boolean);
  `);
  await db.exec(statement('ad4_34_trade_plan.sql', 'create or replace function band_contains(', '$ad4$;'));
  await db.exec(statement('ad4_82_settlement_agreement.sql', 'create or replace function obs_primary_source()', '$$;'));
  await db.exec(statement('ad4_82_settlement_agreement.sql', 'create or replace function venue_round(', '$$;'));
  await db.exec(statement('ad4_82_settlement_agreement.sql', 'create or replace view v_station_day_max as', 'group by 1, 2;'));
  await db.exec(statement('ad4_82_settlement_agreement.sql', 'create or replace view v_settlement_agreement as', 'group by city_key;'));

  // venue_round: whole degrees in the market's unit.
  const vr = async (c, u) => Number((await db.query('select venue_round($1::numeric,$2) v', [c, u])).rows[0].v);
  assert.equal(await vr(37.22, 'F'), 99, '37.22C is 98.996F, which the venue shows as 99');
  assert.equal(await vr(22.2, 'F'), 72, '22.2C is 71.96F -> 72');
  assert.equal(await vr(38, 'F'), 100, '38C is 100.4F -> 100');
  assert.equal(await vr(21.5, 'C'), 22, 'half rounds up');
  assert.equal(await vr(21.49, 'C'), 21);
  assert.equal((await db.query("select venue_round(null,'C') v")).rows[0].v, null);

  const f2c = (f) => Math.round((f - 32) * 5 / 9 * 100) / 100;
  const add = async (city, iso, f, source, station, c = f2c(f)) =>
    db.query('insert into weather_observations(city_key,valid_at,temp_c,temp_f,source,station) values($1,$2,$3,$4,$5,$6)',
      [city, iso, c, f, source, station]);

  await db.exec(`insert into cities values ('dallas','America/Chicago',53),('nyc','America/New_York',51),
                                           ('london','Europe/London',50);`);
  // DALLAS 13 Sep (local): routine METARs at :53 up to 99.0F; NWS 5-min at :55 of 100.4F.
  await add('dallas', '2026-09-13T20:53:00Z', 97.0, 'IEM', 'DAL');
  await add('dallas', '2026-09-13T21:53:00Z', 99.0, 'IEM', 'DAL');
  await add('dallas', '2026-09-13T21:55:00Z', 100.4, 'NWS', 'KDAL', 38);
  // NYC 20 Sep (local): routine at :51 up to 71F; a filed report at 01:04Z (21:04 local) of 71.96F.
  await add('nyc', '2026-09-20T22:51:00Z', 71.0, 'IEM', 'NYC');
  await add('nyc', '2026-09-21T01:04:00Z', 71.96, 'IEM', 'NYC', 22.2);
  // LONDON 17 Sep (local): half-hourly, max 22C at 14:20Z; routine :50 at 21C.
  await add('london', '2026-09-17T13:50:00Z', 69.8, 'IEM', 'EGLC', 21);
  await add('london', '2026-09-17T14:20:00Z', 71.6, 'IEM', 'EGLC', 22);
  await add('london', '2026-09-17T14:50:00Z', 69.8, 'IEM', 'EGLC', 21);

  await db.exec(`
    insert into v_canonical_markets values ('m_dal','F'),('m_nyc','F'),('m_lon','C');
    insert into bands values ('b_dal','m_dal'),('b_nyc','m_nyc'),('b_lon','m_lon');
    insert into v_coherent_band_outcome values
      ('b_dal','dallas','2026-09-13',98,100,false,false,true),
      ('b_nyc','nyc','2026-09-20',72,74,false,false,true),
      ('b_lon','london','2026-09-17',22,23,false,false,true);`);

  const day = async (city, d) => (await db.query(
    'select max_f_hourly, max_c_hourly, n_hourly, station from v_station_day_max where city_key=$1 and for_date=$2',
    [city, d])).rows[0];
  const dal = await day('dallas', '2026-09-13');
  assert.equal(Number(dal.max_f_hourly), 99, 'the NWS five-minute feed leaked into the station maximum');
  assert.equal(dal.station, 'DAL', 'the station column names the primary source, not the first alphabetically');
  assert.equal(Number(dal.n_hourly), 2);
  assert.equal(Number((await day('nyc', '2026-09-20')).max_c_hourly), 22.2,
    'a report the station filed off the routine minute was dropped');
  assert.equal(Number((await day('london', '2026-09-17')).max_c_hourly), 22,
    "London's :20 report was dropped by a window around report_minute");

  const agree = (await db.query('select city_key, agreed, with_a_reading from v_settlement_agreement order by 1')).rows;
  for (const r of agree) {
    assert.equal(Number(r.with_a_reading), 1, `${r.city_key}: no reading was compared`);
    assert.equal(Number(r.agreed), 1, `${r.city_key}: the station maximum did not name the venue's winner`);
  }
  assert.equal(agree.length, 3);

  await db.close();
  console.log('settlement-agreement: Dallas 13 Sep, NYC 20 Sep and London 17 Sep each name the venue winner');
})().catch((e) => { console.error(e); process.exit(1); });
