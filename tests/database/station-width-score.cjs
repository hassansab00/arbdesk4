// ===========================================================================
// THE WIDTH SCORED FORWARD (plan v2.3 P3.9 part 3, part 2): one row per
// confirmed market, written once. It cannot be edited, deleted or truncated;
// the station-maximum scores are all there or all absent; the browser cannot
// read it; the freshness page tracks it; and a re-run keeps every row.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260927200000_the_width_scored_forward.sql'), 'utf-8');

async function refused(db, sql, params) {
  try { await db.query(sql, params); } catch (e) { return true; }
  return false;
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.data_freshness_spec(table_name text primary key, ts_column text,
      fresh_hours numeric, layer text not null, plain_english text not null);
  `);
  await db.exec(MIG);

  const I = `insert into public.fact_station_width_score (market_id, city_key, for_date, unit, priced_at, lead_days,
      centre_c, served_sigma_c, station_width_c, n_bands, winner_band_id, reproduce_max_abs,
      p_winner_served, p_winner_width, log_loss_served, log_loss_width, brier_served, brier_width,
      hit_served, hit_width, station_max_c, crps_served, crps_width, cover80_served, cover80_width)
    values ($1, 'london', '2026-09-29', $2, '2026-09-28 20:36+00', $3, 19.4, 1.5, 1.0, 11,
      '00000000-0000-0000-0000-000000000003', 0.000004, 0.25, 0.37, 1.386, 0.994, 0.78, 0.71, true, true,
      $4, $5, $6, $7, $8)`;
  const M = (n) => `11111111-0000-0000-0000-00000000000${n}`;
  const WHOLE = [19.6, 0.62, 0.55, true, true];
  await db.query(I, [M(1), 'C', 1, ...WHOLE]);
  await db.query(I, [M(2), 'F', 1, null, null, null, null, null]);   // no whole-day maximum yet: all absent
  for (let k = 0; k < WHOLE.length; k++) {
    const partial = WHOLE.map((v, i) => (i === k ? null : v));
    assert.ok(await refused(db, I, [M(3), 'C', 1, ...partial]), `a station score missing column ${k} accepted`);
  }
  assert.ok(await refused(db, I, [M(4), 'K', 1, ...WHOLE]), 'a unit other than C or F accepted');
  assert.ok(await refused(db, I, [M(5), 'C', 0, ...WHOLE]), 'a same-day pricing accepted');
  assert.ok(await refused(db, I, [M(1), 'C', 1, ...WHOLE]), 'a market scored twice');

  assert.ok(await refused(db, `update public.fact_station_width_score set log_loss_width = 0 where market_id = $1`, [M(1)]),
    'an edit accepted');
  assert.ok(await refused(db, `delete from public.fact_station_width_score where market_id = $1`, [M(1)]),
    'a delete accepted');
  assert.ok(await refused(db, `truncate public.fact_station_width_score`), 'a truncate accepted');

  const spec = (await db.query(
    `select ts_column, fresh_hours::int h from public.data_freshness_spec where table_name = 'fact_station_width_score'`)).rows;
  assert.deepEqual(spec, [{ ts_column: 'scored_at', h: 30 }], 'tracked for freshness');

  await db.exec(MIG);
  const n = (await db.query('select count(*)::int n from public.fact_station_width_score')).rows[0].n;
  assert.equal(n, 2, 're-runnable without losing a row');

  const g = (await db.query(`select
      has_table_privilege('anon', 'public.fact_station_width_score', 'select') a,
      has_table_privilege('authenticated', 'public.fact_station_width_score', 'select') u,
      has_table_privilege('service_role', 'public.fact_station_width_score', 'insert') i,
      has_table_privilege('service_role', 'public.fact_station_width_score', 'update') s`)).rows[0];
  assert.deepEqual([g.a, g.u, g.i, g.s], [false, false, true, false], 'grants: service role reads and adds only');
  console.log('station-width-score: one row per market, append-only, station scores whole or absent, ' +
              'service role only, tracked for freshness, re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
