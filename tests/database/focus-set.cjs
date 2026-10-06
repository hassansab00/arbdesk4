// ===========================================================================
// THE FOCUS SET IS RECORDED ONCE AND NEVER REWRITTEN (WXPredict build F.1).
// The Seasonal Focus 10 is a pre-registration: written before its outcomes,
// read by the browser, and refused any update, delete or truncate, so the
// shortlist cannot be changed after the cities it names win or lose.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261006150000_the_focus_set_is_recorded.sql');

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
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // every migration runs every time

  const rows = (await db.query(`select set_id, label, city_keys, window_from::text wf, window_to::text wt,
                                       evaluate_from::text ef, method_path, method_sha256
                                  from public.focus_sets`)).rows;
  assert.equal(rows.length, 1, 'one set, written once however often the migration runs');
  const s = rows[0];
  assert.equal(s.set_id, 'seasonal-2026-10-06');
  assert.deepEqual(s.city_keys, ['lucknow', 'karachi', 'helsinki', 'wellington', 'tel_aviv',
                                 'milan', 'chicago', 'moscow', 'miami', 'amsterdam']);
  assert.deepEqual([s.wf, s.wt, s.ef], ['2026-10-06', '2026-11-30', '2026-10-08']);
  assert.equal(s.method_sha256, 'b02509634235cd25ef75310b8b89f2a046b178cf5c44946d7f95974f0d87655e');

  for (const sql of [`update public.focus_sets set city_keys = array['london']`,
                     `update public.focus_sets set evaluate_from = date '2026-10-06'`,
                     'delete from public.focus_sets',
                     'truncate public.focus_sets']) {
    let refused = false;
    try { await db.query(sql); } catch (e) { refused = true; }
    assert.ok(refused, `accepted: ${sql}`);
  }

  for (const bad of [
    // an evaluation that starts before the window opens
    `('x1','x',array['a'],date '2026-10-06',date '2026-11-30',date '2026-10-05','p','${'a'.repeat(64)}')`,
    // a method hash that is not a sha256
    `('x2','x',array['a'],date '2026-10-06',date '2026-11-30',date '2026-10-08','p','nothex')`,
    // an empty set
    `('x3','x','{}'::text[],date '2026-10-06',date '2026-11-30',date '2026-10-08','p','${'a'.repeat(64)}')`,
  ]) {
    let refused = false;
    try {
      await db.query(`insert into public.focus_sets (set_id, label, city_keys, window_from, window_to,
                        evaluate_from, method_path, method_sha256) values ${bad}`);
    } catch (e) { refused = true; }
    assert.ok(refused, `accepted a malformed set: ${bad}`);
  }

  const g = (await db.query(`select
      has_table_privilege('anon','public.focus_sets','select') a_select,
      has_table_privilege('anon','public.focus_sets','insert') a_insert,
      has_table_privilege('authenticated','public.focus_sets','update') u_update,
      has_table_privilege('service_role','public.focus_sets','insert') s_insert`)).rows[0];
  assert.deepEqual(g, { a_select: true, a_insert: false, u_update: false, s_insert: true });

  await db.exec('set role anon');
  const seen = (await db.query('select count(*)::int n from public.focus_sets')).rows[0].n;
  await db.exec('reset role');
  assert.equal(seen, 1, 'the browser reads the set');

  console.log('PASS: focus-set: the Seasonal Focus 10 recorded once with its window, its evaluation start (8 Oct) and the hash of the study that chose it; never updated, deleted or truncated; malformed sets refused; the browser reads it, only the service role writes; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
