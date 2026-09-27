// ===========================================================================
// S10 IN SHADOW (plan v2 P7.4 part 1): both tables are append-only and the
// service role's alone, and a shadow ladder must sum to one with its top
// bucket on it - the same rules as prediction_checkpoints.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGS = ['20260924000000_prediction_checkpoints.sql', '20260927090000_s10_shadow_observe.sql']
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

  await db.query(`insert into public.s10_day1_inputs (city_key, local_date, hourly, models, models_spread_c)
                  values ('london', '2026-09-27', '{"9": [15.1, 40, 200]}', '{"gfs_seamless": 19.2}', 0.8)`);
  await refused(db, `insert into public.s10_day1_inputs (city_key, local_date, hourly) values ('london', '2026-09-27', '{}')`,
                [], 'a second inputs row for the same city-day');
  await refused(db, `insert into public.s10_day1_inputs (city_key, local_date, hourly) values ('paris', '2026-09-27', '[]')`,
                [], 'hourly that is not an object');
  await refused(db, `update public.s10_day1_inputs set models_spread_c = 2`, [], 'an edit');

  const ins = `insert into public.s10_shadow_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, model_hour, model_version, contract,
       probs, top_band_id, top_prob, running_max_c)
      values ($1, '2026-09-27', $2, '2026-09-27 12:00', 12, 'rd1:x', 's10-contract-v1', $3::jsonb, $4, $5, 17.5)`;
  await db.query(ins, ['london', 'noon', '{"b1":0.2,"b2":0.5,"b3":0.3}', 'b2', 0.5]);
  await refused(db, ins, ['london', 'noon', '{"b1":0.2,"b2":0.5,"b3":0.3}', 'b2', 0.5], 'the same call twice');
  await refused(db, ins, ['paris', 'noon', '{"b1":0.2,"b2":0.5}', 'b2', 0.5], 'a ladder that does not sum to one');
  await refused(db, ins, ['paris', 'noon', '{"b1":0.5,"b2":0.5}', 'b9', 0.5], 'a top bucket off the ladder');
  await refused(db, ins, ['paris', 'd1_eve', '{"b1":0.5,"b2":0.5}', 'b1', 0.5], 'd1_eve (the model needs the day)');
  await refused(db, `delete from public.s10_shadow_checkpoints`, [], 'a delete');
  await refused(db, `truncate public.s10_shadow_checkpoints`, [], 'a truncate');

  for (const t of ['s10_day1_inputs', 's10_shadow_checkpoints']) {
    const g = (await db.query(`select
        has_table_privilege('anon', 'public.${t}', 'select') a,
        has_table_privilege('authenticated', 'public.${t}', 'select') u,
        has_table_privilege('service_role', 'public.${t}', 'insert') i,
        has_table_privilege('service_role', 'public.${t}', 'update') s`)).rows[0];
    assert.deepEqual([g.a, g.u, g.i, g.s], [false, false, true, false], `${t} grants`);
  }
  console.log('s10-shadow: append-only, service role only, a ladder sums to one with its top on it, no d1_eve');
})().catch((e) => { console.error(e); process.exit(1); });
