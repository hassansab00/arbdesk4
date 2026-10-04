// ===========================================================================
// ENGINE VARIANTS IN SHADOW (P1.1's candidate, docs/P11_DA_FLOOR_PREREG.md):
// append-only and the service role's alone, one row per call and variant, a
// ladder that sums to one with its top on it, same-day checkpoints only, a
// day-ahead call priced before the row was decided, q only with a floor.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGS = ['20260924000000_prediction_checkpoints.sql', '20261004180000_engine_variants_in_shadow.sql']
  .map((f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8'));

async function refused(db, sql, params, why) {
  try { await db.query(sql, params); } catch (e) { return e.message; }
  assert.fail(`accepted, and should not have been: ${why}`);
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
  `);
  for (const m of MIGS) await db.exec(m);
  await db.exec(MIGS[1]);   // re-runnable

  const ins = `insert into public.variant_shadow_checkpoints
      (city_key, target_date, checkpoint, variant, variant_version, engine_version,
       day_ahead_centre_c, day_ahead_sigma_c, day_ahead_priced_at, floor_c, q_down, q_up,
       unit, probs, top_band_id, top_prob, decided_at)
      values ($1, '2026-10-05', $2, $3, $4, 'git:abc', 21.2, $5, $6::timestamptz, $7, $8, $9,
              'C', $10::jsonb, $11, 0.5, '2026-10-05T11:36:00Z')`;
  const ok = ['london', 'noon', 'da_floor', 'da_floor:v1', 1.4, '2026-10-04T20:40:00Z', 20.4, 0.03, 0.06,
              '{"b1":0.2,"b2":0.5,"b3":0.3}', 'b2'];
  const with_ = (i, v) => ok.map((x, j) => (j === i ? v : x));
  await db.query(ins, ok);
  await refused(db, ins, ok, 'the same call and variant twice');
  await db.query(ins, with_(2, 'other').map((x, j) => (j === 3 ? 'other:v1' : x)));   // another variant may sit beside it
  await refused(db, ins, with_(1, 'd1_eve'), 'd1_eve (the day has not begun)');
  await refused(db, ins, with_(3, 'da_carried:v1'), 'a version that names another variant');
  await refused(db, ins, with_(4, 0), 'a width of zero');
  await refused(db, ins, with_(1, 'morning').map((x, j) => (j === 5 ? '2026-10-05T12:00:00Z' : x)),
                'a day-ahead call priced after the row was decided');
  await refused(db, ins, with_(1, 'morning').map((x, j) => (j === 6 ? null : x)), 'q without a floor');
  await refused(db, ins, with_(1, 'morning').map((x, j) => (j === 7 ? null : x)), 'a floor without q');
  await refused(db, ins, with_(1, 'morning').map((x, j) => (j === 9 ? '{"b1":0.2,"b2":0.5}' : x)),
                'a ladder that does not sum to one');
  await refused(db, ins, with_(1, 'morning').map((x, j) => (j === 10 ? 'b9' : x)), 'a top bucket off the ladder');
  await db.query(ins, with_(1, 'morning').map((x, j) => (j === 6 || j === 7 || j === 8 ? null : x)));   // no floor, no q
  await refused(db, `update public.variant_shadow_checkpoints set top_prob = 0.6`, [], 'an edit');
  await refused(db, `delete from public.variant_shadow_checkpoints`, [], 'a delete');
  await refused(db, `truncate public.variant_shadow_checkpoints`, [], 'a truncate');
  assert.equal((await db.query('select count(*)::int n from public.variant_shadow_checkpoints')).rows[0].n, 3);

  const g = (await db.query(`select
      has_table_privilege('anon', 'public.variant_shadow_checkpoints', 'select') a,
      has_table_privilege('authenticated', 'public.variant_shadow_checkpoints', 'select') u,
      has_table_privilege('service_role', 'public.variant_shadow_checkpoints', 'insert') i,
      has_table_privilege('service_role', 'public.variant_shadow_checkpoints', 'update') s`)).rows[0];
  assert.deepEqual([g.a, g.u, g.i, g.s], [false, false, true, false]);
  console.log('variant-shadow: append-only, service role only, one row per call and variant, a ladder sums to one with its top on it, same-day only, the day-ahead call before the decision, q only with a floor; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
