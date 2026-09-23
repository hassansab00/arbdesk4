// ===========================================================================
// A PRICEABLE MARKET IS ONE WHOSE LOCAL DAY HAS NOT ENDED (plan v2 P3.2).
//
// Honolulu is always behind UTC and Auckland always ahead, so whatever the
// hour, their local "today" differ from the UTC date the engines used to cut
// on. The migration runs as shipped against a stand-in v_canonical_markets.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260923180000_a_priceable_market_is_one_whose_local_day_has_not_ended.sql');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.cities(city_key text primary key, status text, timezone text);
    create table public.v_canonical_markets(market_id text primary key, city_key text, resolution_date date,
      unit text, correction_id bigint, closed boolean);
    insert into public.cities values ('honolulu','active','Pacific/Honolulu'),
      ('auckland','active','Pacific/Auckland'), ('gone','retired','Pacific/Honolulu'),
      ('nozone','active',null);
  `);
  const today = async (tz) => (await db.query(`select (now() at time zone $1)::date::text d`, [tz])).rows[0].d;
  const hnl = await today('Pacific/Honolulu');
  const akl = await today('Pacific/Auckland');
  await db.query(`insert into public.v_canonical_markets values
      ('hnl-today',     'honolulu', $1::date,     'F', null, false),
      ('hnl-yesterday', 'honolulu', $1::date - 1, 'F', null, false),
      ('akl-today',     'auckland', $2::date,     'C', null, false),
      ('akl-yesterday', 'auckland', $2::date - 1, 'C', null, false),
      ('akl-closed',    'auckland', $2::date + 1, 'C', null, true),
      ('retired',       'gone',     $1::date,     'F', null, false),
      ('no-timezone',   'nozone',   $1::date + 5, 'C', null, false)`, [hnl, akl]);
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));

  const ids = (await db.query('select market_id from v_priceable_markets order by 1')).rows.map(r => r.market_id);
  assert.deepEqual(ids, ['akl-today', 'hnl-today'],
    `priceable: ${ids.join(', ')} - a live local day was dropped, or an ended / closed / retired one kept`);
  const g = (await db.query(`select has_table_privilege('anon','public.v_priceable_markets','select') a,
                                    has_table_privilege('service_role','public.v_priceable_markets','select') s`)).rows[0];
  assert.deepEqual([g.a, g.s], [false, true]);
  console.log(`priceable-markets: each city's own today (Honolulu ${hnl}, Auckland ${akl}), not the UTC date`);
})().catch((e) => { console.error(e); process.exit(1); });
