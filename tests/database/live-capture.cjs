// ===========================================================================
// THE LIVE RECORD (WXPredict build WP3; Hassan, 11 Oct: "go ahead with live
// capture"). live_capture_put stores a report or hourly value once per change
// (a value that changes is a new row, and one that changes back is stored
// again, so the newest row is always what was being served); a books row every
// time; it refuses a kind it does not keep and a time in the future.
// prune_live_capture deletes only rows two days old, only the count read back
// from the committed file, never a row the mirror has not had; request_reclaim
// may rewrite the table; the Workflows switch is seeded and survives a re-run;
// the browser reads none of it.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261011210000_the_live_record.sql'), 'utf-8');
const SQL = fs.readFileSync(path.join(__dirname, '..', '..', 'sql', 'ad4_prune_live_capture.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values ('workflow_schedules', '{"P0.4_trade_history": {"mode": "auto"}}');
    create table public.job_sla (job text primary key, cadence_minutes int not null, cadence text not null,
                                 scheduled_by text not null);
    create schema cron;
    create table cron.calls (name text, expr text, cmd text);
    create function cron.schedule(n text, e text, c text) returns bigint language sql
      as $$ insert into cron.calls values (n, e, c); select 1::bigint $$;
  `);
  await db.exec(MIG);
  const one = async (sql, p = []) => (await db.query(sql, p)).rows;

  // THE SWITCH, seeded beside the others; a mode set on the page survives a re-run.
  assert.deepEqual((await one(`select value -> 'P2.12_live_capture' v from public.settings`))[0].v,
    { mode: 'auto', every_minutes: 0 });
  await db.exec(`update public.settings set value = jsonb_set(value, '{P2.12_live_capture,mode}', '"off"')`);
  await db.exec(MIG);
  assert.equal((await one(`select value -> 'P2.12_live_capture' ->> 'mode' m from public.settings`))[0].m, 'off');
  assert.equal((await one(`select value -> 'P0.4_trade_history' ->> 'mode' m from public.settings`))[0].m, 'auto');
  // the archive's watchdog row, and the backstop reclaim at 03:05
  assert.deepEqual((await one(`select cadence_minutes c from public.job_sla where job = 'archive_live_capture'`))[0], { c: 1440 });
  assert.ok((await one(`select 1 from cron.calls where name = 'ad4_reclaim_live_capture' and expr = '5 3 * * *'
                          and cmd = 'VACUUM (FULL, ANALYZE) public.live_capture'`)).length >= 1);
  // the canonical file installs the same functions on its own
  await db.exec(SQL);

  const put = async (kind, at, rows) => (await one('select public.live_capture_put($1, $2, $3::jsonb) r',
    [kind, at, JSON.stringify(rows)]))[0].r;
  const T = (h) => new Date(Date.now() - h * 3600e3).toISOString();

  // ONCE PER CHANGE. Six hours of reports come back every tick; each is stored
  // once, with the first time it was seen.
  const rep = (k, temp) => ({ key: k, payload: [String(temp), '60.0', '', 'METAR X'] });
  assert.deepEqual(await put('report', T(3), [rep('OEJN|2026-10-11 18:00', 88), rep('OEJN|2026-10-11 19:00', 86)]),
    { rows: 2, new: 2 });
  assert.deepEqual(await put('report', T(2), [rep('OEJN|2026-10-11 18:00', 88), rep('OEJN|2026-10-11 19:00', 86),
    rep('OEJN|2026-10-11 20:00', 86)]), { rows: 3, new: 1 });
  // a changed value is a new row beside the old; changing BACK is stored again,
  // so the newest row is always what was served (A, B, A keeps three)
  await put('hourly', T(5), [{ key: 'jeddah|2026-10-12T09:00', payload: [31.2, 24, 10, 600, 12, 0, 1009] }]);
  await put('hourly', T(4), [{ key: 'jeddah|2026-10-12T09:00', payload: [31.6, 24, 10, 600, 12, 0, 1009] }]);
  const back = await put('hourly', T(3), [{ key: 'jeddah|2026-10-12T09:00', payload: [31.2, 24, 10, 600, 12, 0, 1009] }]);
  assert.equal(back.new, 1, 'a value that changes back is what is being served again');
  assert.equal((await one(`select count(*)::int n from public.live_capture where kind = 'hourly'`))[0].n, 3);
  // a key twice in one batch with the same payload is one row
  assert.deepEqual(await put('hourly', T(1), [{ key: 'tokyo|2026-10-12T00:00', payload: [20] },
    { key: 'tokyo|2026-10-12T00:00', payload: [20] }]), { rows: 2, new: 1 });
  // books: every tick, whatever it holds
  const books = { 'b1': [0.41, 0.43, 120, 80, 300.5, 210.2, 0.42, 1760216000, 5, 0.01] };
  assert.equal((await put('books', T(1), [{ key: 'all', payload: books }])).new, 1);
  assert.equal((await put('books', T(0), [{ key: 'all', payload: books }])).new, 1);

  // REFUSED: a kind it does not keep, a time in the future, rows that are not a list
  for (const [kind, at, rows, why] of [['trades', T(1), [], /not a kind/], ['report', new Date(Date.now() + 3600e3).toISOString(), [], /future/],
    ['report', T(1), {}, /JSON array/]]) {
    await assert.rejects(db.query('select public.live_capture_put($1, $2, $3::jsonb)', [kind, at, JSON.stringify(rows)]), why);
  }

  // THE PRUNE: two days or nothing, the file's count or nothing.
  await put('report', T(80), [rep('KLGA|2026-10-08 10:51', 60), rep('KLGA|2026-10-08 11:51', 61)]);
  await put('books', T(75), [{ key: 'all', payload: books }]);
  const p = async (args) => (await one(`select public.prune_live_capture(${args}) r`))[0].r;
  assert.match((await p('1, true')).error, /at least 2/);
  assert.match((await p('2, false')).error, /p_expected_rows is required/);
  assert.equal((await p('2, true')).would_delete, 3);
  assert.match((await p('2, false, null, 4')).error, /count mismatch/);
  const before = (await one('select count(*)::int n from public.live_capture'))[0].n;
  const done = await p('2, false, null, 3');
  assert.equal(done.deleted, 3);
  assert.equal((await one('select count(*)::int n from public.live_capture'))[0].n, before - 3);
  assert.equal((await one(`select count(*)::int n from public.live_capture where captured_at < now() - interval '2 days'`))[0].n, 0);
  assert.equal((await p('2, true')).deleted, 0, 'nothing left to take');

  // THE RECLAIM may rewrite it, and only what the archive prunes.
  const rr = (await one(`select public.request_reclaim('live_capture') r`))[0].r;
  assert.equal(rr.job, 'ad4_reclaim_after_archive_live_capture');
  await assert.rejects(db.query(`select public.request_reclaim('cities')`), /not a table the archive prunes/);

  // CLOSED TO THE BROWSER.
  const g = (await one(`select
      has_table_privilege('anon', 'public.live_capture', 'select') a,
      has_table_privilege('authenticated', 'public.live_capture', 'select') b,
      has_function_privilege('anon', 'public.live_capture_put(text, timestamptz, jsonb)', 'execute') c,
      has_function_privilege('authenticated', 'public.prune_live_capture(integer, boolean, timestamptz, bigint)', 'execute') d,
      has_function_privilege('service_role', 'public.live_capture_put(text, timestamptz, jsonb)', 'execute') e,
      has_function_privilege('service_role', 'public.prune_live_capture(integer, boolean, timestamptz, bigint)', 'execute') f,
      has_table_privilege('service_role', 'public.live_capture', 'select') h`))[0];
  assert.deepEqual([g.a, g.b, g.c, g.d, g.e, g.f, g.h], [false, false, false, false, true, true, true]);

  // RE-RUNNABLE with rows in place
  const n = (await one('select count(*)::int n from public.live_capture'))[0].n;
  await db.exec(MIG);
  assert.equal((await one('select count(*)::int n from public.live_capture'))[0].n, n);
  console.log('PASS: live-capture: a report or hourly value stored once per change and again when it changes back, a books row every tick; a kind it does not keep, a future time and a non-list refused; the prune takes two-day-old rows only, only the verified count; request_reclaim may rewrite it; the switch seeded and kept; the archive watchdog row and the 03:05 backstop; closed to the browser; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
