// ===========================================================================
// A MARKET WHOSE LOCAL DAY HAS ENDED IS CLOSED (plan v2 P2.4).
//
// Hassan, 23 Sep: "as long as their local date/day ended, they need to be
// closed, but if the temp wasn't settled, then there's a real issue."
//
// Runs the migration as shipped, and the gaps view lifted from the shipped
// file, against real time zones:
//
//   - an ended day is closed by the sweep, and says why;
//   - a discovery upsert that says closed=false cannot reopen it;
//   - today's market, where the day has not ended, is left alone;
//   - the venue's confirmed winner fills empty resolution fields and never
//     overwrites a filled one;
//   - an ended day with no temperature anywhere is listed as a real issue,
//     one before the city's collection start is not, and neither is a day
//     still inside the grace period.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIGRATION = path.join(ROOT, 'supabase', 'migrations', '20260923140000_a_market_whose_day_ended_is_closed.sql');

function statement(file, opener, closer) {
  const src = fs.readFileSync(path.join(ROOT, 'sql', file), 'utf-8');
  const i = src.indexOf(opener);
  assert.ok(i >= 0, `${file} no longer contains ${JSON.stringify(opener)}`);
  const j = src.indexOf(closer, i + opener.length);
  assert.ok(j >= 0, `${file}: ${JSON.stringify(opener)} has no ${JSON.stringify(closer)}`);
  return src.slice(i, j + closer.length);
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table cities(city_key text primary key, timezone text, status text);
    create table bands(band_id uuid primary key, market_id uuid);
    create table markets(market_id uuid primary key, city_key text not null, resolution_date date not null,
      event_slug text, closed boolean not null default false, winning_band_id uuid, resolved_band_id uuid,
      resolution_verified_at timestamptz, resolution_source_used text);
    -- Views in production; tables here holding exactly the columns read.
    create table v_venue_market_resolution(market_id uuid, resolution_state text, winning_band_id uuid,
      confirmed_at timestamptz);
    create table v_station_day_max(city_key text, for_date date, max_c_hourly numeric);
    create table weather_observations(city_key text, valid_at timestamptz);
    create table fact_band_outcome(city_key text, for_date date, observed_max_c numeric);
    create table fact_forecast_outcome(city_key text, for_date date, observed_max_c numeric);

    -- Auckland is always ahead of UTC, Honolulu always behind: whatever the
    -- hour the suite runs, their local "today" differ, which is the point.
    insert into cities values ('auckland','Pacific/Auckland','active'), ('honolulu','Pacific/Honolulu','active'),
                              ('newcity', null, 'pending_review'), ('gone','Pacific/Honolulu','retired');
  `);
  const today = async (tz) => (await db.query(`select (now() at time zone $1)::date::text d`, [tz])).rows[0].d;
  const akl = await today('Pacific/Auckland');
  const hnl = await today('Pacific/Honolulu');

  const M = (n) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
  const B = (n) => `10000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
  // Inserted BEFORE the migration: the state production is in.
  await db.query(`insert into markets(market_id, city_key, resolution_date, event_slug, closed) values
      ($1,'auckland',$5::date - 1,'akl-yesterday',false),
      ($2,'auckland',$5::date,    'akl-today',    false),
      ($3,'honolulu',$6::date - 2,'hnl-ended',    false),
      ($4,'newcity', '2020-01-01','no-timezone',  false),
      ($7,'gone',    $6::date - 2,'retired-ended',false)`,
    [M(1), M(2), M(3), M(4), akl, hnl, M(7)]);
  await db.query(`insert into bands values ($1,$2),($3,$2),($4,$5)`, [B(1), M(1), B(2), B(3), M(3)]);
  await db.query(`insert into v_venue_market_resolution values
      ($1,'confirmed',$2,'2026-09-20T00:00:00Z'), ($3,'unverified',null,null)`, [M(1), B(1), M(3)]);

  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));

  const row = async (id) => (await db.query('select * from markets where market_id=$1', [id])).rows[0];

  // The migration's own sweep ran.
  let r = await row(M(1));
  assert.equal(r.closed, true, "Auckland's yesterday is over and was still open");
  assert.equal(r.closed_reason, 'local_day_ended');
  assert.ok(r.closed_time, 'closed_time was not stamped');
  assert.equal(r.winning_band_id, B(1), "the venue's confirmed winner was not copied");
  assert.equal(r.resolved_band_id, B(1));
  assert.equal(r.resolution_source_used, 'venue_evidence');
  assert.equal((await row(M(2))).closed, false, "Auckland's today has not ended and was closed");
  r = await row(M(3));
  assert.equal(r.closed, true);
  assert.equal(r.winning_band_id, null, 'a winner was written without a confirmed venue settlement');
  assert.equal((await row(M(4))).closed, false, 'a city with no timezone cannot be judged, and was');
  assert.equal((await row(M(7))).closed, true, "a retired city's ended market is ended too");

  // A discovery upsert (P0.2 posts Gamma's closed flag) cannot reopen it.
  const stamped = (await row(M(1))).closed_time;
  await db.query(`update markets set closed = false where market_id = $1`, [M(1)]);
  r = await row(M(1));
  assert.equal(r.closed, true, 'a re-poll reopened a market whose day has ended');
  assert.equal(String(r.closed_time), String(stamped), 'closed_time moved on a re-poll');
  await db.query(`insert into markets(market_id, city_key, resolution_date, event_slug, closed)
                  values ($1,'honolulu',$2::date - 3,'hnl-new-but-ended',false)`, [M(5), hnl]);
  assert.equal((await row(M(5))).closed, true, 'a market inserted after its day ended arrived open');
  // Venue closes today's market early: kept, and said to be the venue.
  await db.query(`update markets set closed = true where market_id = $1`, [M(2)]);
  assert.equal((await row(M(2))).closed_reason, 'venue');

  // A filled winner is never overwritten.
  await db.query(`update markets set winning_band_id = $1, resolved_band_id = $1 where market_id = $2`, [B(3), M(3)]);
  await db.query(`update v_venue_market_resolution set resolution_state='confirmed', winning_band_id=$1 where market_id=$2`,
    [B(3), M(3)]);
  await db.query(`update v_venue_market_resolution set winning_band_id=$1 where market_id=$2`, [B(2), M(1)]);
  const out = (await db.query('select refresh_market_state() o')).rows[0].o;
  assert.equal(out.resolved, 0, 'a market that already had a winner was rewritten');
  assert.equal((await row(M(1))).winning_band_id, B(1));
  await db.query(`update v_venue_market_resolution set winning_band_id=$1 where market_id=$2`, [B(1), M(1)]);

  // Nobody but the service role runs the sweep.
  const grants = (await db.query(`select has_function_privilege('anon','refresh_market_state()','execute') a,
                                         has_function_privilege('service_role','refresh_market_state()','execute') s`)).rows[0];
  assert.deepEqual([grants.a, grants.s], [false, true]);

  // ---- the gaps view ------------------------------------------------------
  await db.exec(statement('ad4_87_market_settlement_gaps.sql',
    'create or replace view v_market_settlement_gaps as', 'where issue is not null;'));
  // Honolulu has been collected since 10 days before today.
  await db.query(`insert into weather_observations values ('honolulu', now() - interval '10 days'),
                                                           ('gone', now() - interval '10 days')`);
  const gaps = async () => Object.fromEntries((await db.query(
    'select event_slug, issue from v_market_settlement_gaps order by event_slug')).rows.map((g) => [g.event_slug, g.issue]));

  let g = await gaps();
  assert.equal(g['hnl-ended'], 'no_temperature', 'an ended day with no temperature is a real issue and was not listed');
  assert.equal(g['hnl-new-but-ended'], 'no_temperature');
  assert.equal(g['akl-yesterday'], undefined, 'Auckland has no collection start, so its days were never collected');
  assert.equal(g['akl-today'], undefined, 'a day that has not ended was listed');
  assert.equal(g['retired-ended'], undefined, 'a retired city is not collected on purpose, and was listed');

  // Any one of the three sources settles it.
  await db.query(`insert into v_station_day_max values ('honolulu', $1::date - 2, 29.4)`, [hnl]);
  await db.query(`insert into fact_forecast_outcome values ('honolulu', $1::date - 3, 28.9)`, [hnl]);
  g = await gaps();
  assert.equal(g['hnl-ended'], undefined, 'a station maximum did not settle the day');
  assert.equal(g['hnl-new-but-ended'], undefined, 'a banked forecast outcome did not settle the day');

  // A stored winner the venue disagrees with.
  await db.query(`update v_venue_market_resolution set winning_band_id=$1 where market_id=$2`, [B(2), M(3)]);
  assert.equal((await gaps())['hnl-ended'], 'winner_conflict');
  await db.query(`update v_venue_market_resolution set resolution_state='disputed' where market_id=$1`, [M(3)]);
  assert.equal((await gaps())['hnl-ended'], 'venue_disputed');

  // Inside the grace period: yesterday in Honolulu ended less than 3 h ago
  // only between 10:00Z and 13:00Z, so test the boundary on day_ended_at
  // directly rather than on the clock.
  const grace = (await db.query(`select count(*)::int n from v_market_settlement_gaps
                                 where issue = 'no_temperature' and since_day_end <= interval '3 hours'`)).rows[0].n;
  assert.equal(grace, 0, 'a day inside its 3 h grace was listed as missing its temperature');

  console.log('PASS: an ended local day closes and stays closed, the venue winner fills only empty fields, and a day with no temperature is listed');
})().catch((e) => { console.error(e); process.exit(1); });
