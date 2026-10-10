// ===========================================================================
// THE WIDTH AROUND THE CORRECTED CENTRE (plan v2.3 P3.9 part 3): the width
// table is the service role's alone and refuses an impossible width, a lead
// outside 1-3 or an anchor that is not earlier than its night; an open day's
// width always names its version; every price can record the stored width;
// the freshness page tracks it; and the pricing switch starts OFF and is
// never switched back off by a re-run. 20261010170000 (Hassan, 10 Oct) turns
// it on once, keeps everything else in the row, and never turns it back on
// after someone has turned it off.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const DIR = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const P39 = fs.readFileSync(path.join(DIR, '20260926120000_station_correction.sql'), 'utf-8');
const MIG = fs.readFileSync(path.join(DIR, '20260927190000_the_width_around_the_corrected_centre.sql'), 'utf-8');
const ON = fs.readFileSync(path.join(DIR, '20261010170000_the_station_width_prices.sql'), 'utf-8');

async function refused(db, sql, params) {
  try { await db.query(sql, params); } catch (e) { return true; }
  return false;
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings(key text primary key, value jsonb not null,
      updated_at timestamptz not null default now());
    create table public.band_probabilities(prob_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null default now(), sigma_c numeric);
    create table public.data_freshness_spec(table_name text primary key, ts_column text,
      fresh_hours numeric, layer text not null, plain_english text not null);
  `);
  await db.exec(P39);
  await db.exec(MIG);

  const flag = async () => (await db.query(
    `select value from public.settings where key = 'station_width_pricing'`)).rows[0].value;
  assert.equal((await flag()).enabled, false, 'nothing prices from the width until the forward score says so');
  assert.equal((await flag()).max_lead_days, 1, 'lead 1: what the study and the score cover');

  const W = `insert into public.derived_station_width (city_key, lead_days, sigma_c, fitted_sigma_c, pooled_sigma_c,
               mae_c, pooled_mae_c, n, n_pooled, version, as_of, prev_sigma_c, prev_as_of)
             values ($1, $2, $3, $3, 1.04, 0.8, 0.83, 30, 1435, 'station-width:2026-09-28:a', '2026-09-28', $4, $5)`;
  await db.query(W, ['london', 1, 1.05, 1.0, '2026-09-27']);
  assert.ok(await refused(db, W, ['paris', 1, 0, null, null]), 'a width of 0 accepted');
  assert.ok(await refused(db, W, ['paris', 0, 1.0, null, null]), 'lead 0 has no width of its own');
  assert.ok(await refused(db, W, ['paris', 4, 1.0, null, null]), 'lead 4 accepted');
  assert.ok(await refused(db, W, ['paris', 1, 1.0, 1.0, '2026-09-28']), 'an anchor from its own night accepted');

  const F = `insert into public.derived_corrected_forecast (city_key, for_date, lead_days, combined_c, n_sources,
               sources, version, width_c, width_version)
             values ($1, '2026-09-29', 1, 21.5, 7, '{}', 'station-correction:2026-09-28:x', $2, $3)`;
  await db.query(F, ['london', 1.05, 'station-width:2026-09-28:a']);
  await db.query(F, ['paris', null, null]);
  assert.ok(await refused(db, F, ['rome', 1.05, null]), 'a width without its version accepted');
  assert.ok(await refused(db, F, ['rome', null, 'station-width:2026-09-28:a']), 'a version without a width accepted');
  assert.ok(await refused(db, F, ['rome', 0, 'station-width:2026-09-28:a']), 'a width of 0 accepted');

  await db.query(`insert into public.band_probabilities (band_id, sigma_c, station_width_c)
                  values ('00000000-0000-0000-0000-000000000001', 1.53, 1.05)`);
  const spec = (await db.query(
    `select ts_column, fresh_hours::int h from public.data_freshness_spec where table_name = 'derived_station_width'`)).rows;
  assert.deepEqual(spec, [{ ts_column: 'computed_at', h: 30 }], 'tracked for freshness');

  await db.exec(`update public.settings set value = jsonb_set(value, '{enabled}', 'true') where key = 'station_width_pricing'`);
  await db.exec(MIG);
  assert.equal((await flag()).enabled, true, 're-runnable without resetting the switch');
  assert.equal((await db.query('select count(*)::int n from public.derived_station_width')).rows[0].n, 1);
  assert.equal((await db.query('select count(*)::int n from public.derived_corrected_forecast')).rows[0].n, 2);

  // TURNED ON (Hassan, 10 Oct): from the seed, once.
  await db.exec(`update public.settings set value = jsonb_set(value, '{enabled}', 'false') where key = 'station_width_pricing'`);
  const seed = await flag();
  await db.exec(ON);
  const on = await flag();
  assert.equal(on.enabled, true, 'the width prices');
  assert.equal(on.max_lead_days, 1, 'day-ahead only, as before');
  assert.equal(on.why, seed.why, 'the seed\'s reason is kept');
  assert.equal(on.enabled_at, '2026-10-10');
  assert.equal(on.enabled_by, 'Hassan');
  assert.match(on.enabled_why, /\+0\.1171/, 'the forward score it was turned on by is in the row');
  await db.exec(`update public.settings set value = jsonb_set(value, '{enabled}', 'false') where key = 'station_width_pricing'`);
  await db.exec(ON);
  await db.exec(MIG);
  assert.equal((await flag()).enabled, false, 'switched off later, a re-run does not turn it back on');
  const noSettings = new PGlite();
  await noSettings.exec(ON);   // the database contracts have no settings table: a no-op there

  const g = (await db.query(`select
      has_table_privilege('anon', 'public.derived_station_width', 'select') a,
      has_table_privilege('authenticated', 'public.derived_station_width', 'select') u,
      has_table_privilege('service_role', 'public.derived_station_width', 'update') s`)).rows[0];
  assert.deepEqual([g.a, g.u, g.s], [false, false, true], 'derived_station_width grants');
  console.log('station-width: service role only, a width is positive with its version and an earlier anchor, ' +
              'every price can record it, tracked for freshness, the switch starts off and a re-run keeps it; ' +
              'turned on once (10 Oct), never back on after it is turned off');
})().catch((e) => { console.error(e); process.exit(1); });
