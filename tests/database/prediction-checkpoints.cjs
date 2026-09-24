// ===========================================================================
// WHAT WE SAID, WHEN WE SAID IT (plan v2 P4.1): prediction_checkpoints is an
// append-only record. A call can be written once, never edited, never
// deleted, and never written as a ladder that does not sum to one.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260924000000_prediction_checkpoints.sql');

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
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));

  const ins = `insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path,
       probs, top_band_id, top_prob, inputs_ok, block_reason)
      values ($1, '2026-09-25', $2, '2026-09-24 18:00', $3, 'forecast', $4::jsonb, $5, $6, $7, $8)`;
  const good = ['nyc', 'd1_eve', 'abc123', '{"b1":0.2,"b2":0.5,"b3":0.3}', 'b2', 0.5, true, null];
  await db.query(ins, good);

  await refused(db, ins, good, 'the same call twice');
  await refused(db, `update public.prediction_checkpoints set top_prob = 0.9`, [], 'an edit');
  await refused(db, `delete from public.prediction_checkpoints`, [], 'a delete');
  await refused(db, `truncate public.prediction_checkpoints`, [], 'a truncate');
  await refused(db, ins, ['nyc', 'noon', 'abc123', '{"b1":0.2,"b2":0.5}', 'b2', 0.5, true, null],
    'a ladder that sums to 0.7');
  await refused(db, ins, ['nyc', 'noon', 'abc123', '{"b1":0.5,"b2":0.5}', 'b9', 0.5, true, null],
    'a top pick that is not on the ladder');
  await refused(db, ins, ['nyc', 'noon', 'abc123', '{"b1":0.5,"b2":0.5}', 'b1', 0.5, false, null],
    'a blocked call that does not say why');
  await refused(db, ins, ['nyc', 'lunch', 'abc123', '{"b1":0.5,"b2":0.5}', 'b1', 0.5, true, null],
    'a checkpoint label outside P4.2');
  // a new engine version may call the same moment again - a correction is a new row
  await db.query(ins, ['nyc', 'd1_eve', 'def456', '{"b1":0.1,"b2":0.8,"b3":0.1}', 'b2', 0.8, true, null]);

  const g = (await db.query(`select
      has_table_privilege('anon','public.prediction_checkpoints','select') anon_read,
      has_table_privilege('authenticated','public.prediction_checkpoints','select') auth_read,
      has_table_privilege('service_role','public.prediction_checkpoints','insert') svc_insert,
      has_table_privilege('service_role','public.prediction_checkpoints','update') svc_update`)).rows[0];
  assert.deepEqual([g.anon_read, g.auth_read, g.svc_insert, g.svc_update], [false, false, true, false]);
  const n = (await db.query('select count(*)::int n from public.prediction_checkpoints')).rows[0].n;
  assert.equal(n, 2);
  console.log('prediction-checkpoints: written once, never edited or deleted, a ladder sums to one, service role only');
})().catch((e) => { console.error(e); process.exit(1); });
