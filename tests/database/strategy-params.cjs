// ===========================================================================
// WHAT THE NIGHTLY LOOP LEARNED (plan v2 P5.8): one immutable row per
// parameter, scope and version; service role only; and the flag that keeps
// the priors in charge starts OFF and is never switched back on by a re-run.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260926110000_strategy_params.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.settings(key text primary key, value jsonb not null,
      updated_at timestamptz not null default now());
  `);
  await db.exec(MIG);

  const flag = async () => (await db.query(
    `select value from public.settings where key = 'strategy_learning'`)).rows[0].value;
  assert.equal((await flag()).enabled, false, 'priors frozen until the replay');

  const ins = `insert into public.strategy_params (param, scope, version, value, n, prior, bounds, as_of)
               values ('belief', 'all', $1, '{"bins":{}}', $2, '{"k0":30}', '{"p":[0.001,0.999]}', '2026-09-26')`;
  await db.query(ins, ['belief:2026-09-26:a', 98]);
  let dup = false;
  try { await db.query(ins, ['belief:2026-09-26:a', 98]); } catch (e) { dup = true; }
  assert.ok(dup, 'a version is written once');
  await db.query(`insert into public.strategy_params (param, scope, version, value, n, prior, as_of)
                  values ('belief', 'all', 'belief:2026-09-26:a', '{}', 1, '{}', '2026-09-26')
                  on conflict (param, scope, version) do nothing`);
  assert.equal((await db.query('select count(*)::int n from public.strategy_params')).rows[0].n, 1,
    'the loop\'s ignore-duplicates re-run adds nothing');

  for (const sql of ['update public.strategy_params set n = 0',
                     'delete from public.strategy_params',
                     'truncate public.strategy_params']) {
    let refused = false;
    try { await db.query(sql); } catch (e) { refused = true; }
    assert.ok(refused, `accepted: ${sql}`);
  }
  let negative = false;
  try { await db.query(ins, ['belief:x', -1]); } catch (e) { negative = true; }
  assert.ok(negative, 'n cannot be negative');

  // Hassan turns it on one day; a re-run of the migration must not turn it off.
  await db.exec(`update public.settings set value = '{"enabled": true}' where key = 'strategy_learning'`);
  await db.exec(MIG);
  assert.equal((await flag()).enabled, true, 're-runnable without resetting the flag');

  const g = (await db.query(`select
      has_table_privilege('anon','public.strategy_params','select') a,
      has_table_privilege('service_role','public.strategy_params','insert') s,
      has_table_privilege('service_role','public.strategy_params','update') u`)).rows[0];
  assert.deepEqual([g.a, g.s, g.u], [false, true, false]);
  console.log('strategy-params: one immutable row per version, service role only, the flag starts off and a re-run keeps it');
})().catch((e) => { console.error(e); process.exit(1); });
