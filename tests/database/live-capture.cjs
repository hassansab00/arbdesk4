// ===========================================================================
// THE LIVE RECORD (WXPredict build WP3; Hassan, 11 Oct: "go ahead with live
// capture"). live_capture_put stores a report or hourly value once per change
// (a value that changes is a new row, and one that changes back is stored
// again, so the newest row is always what was being served); a books row every
// time; it refuses a kind it does not keep and a time in the future.
// prune_live_capture deletes only rows two days old, only the count read back
// from the committed file, never a row the mirror has not had; request_reclaim
// may rewrite the table; the Workflows switch is seeded and survives a re-run;
// the browser reads none of it. From 12 Oct (WP3b) it also keeps the evening's
// forecasts: best_match at lead 2 ('hourly2') and the seven models at leads 1
// and 2 ('models1', 'models2'), the same way, and still refuses any other kind,
// at the function and at the table.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261011210000_the_live_record.sql'), 'utf-8');
const SQL = fs.readFileSync(path.join(__dirname, '..', '..', 'sql', 'ad4_prune_live_capture.sql'), 'utf-8');
const MIG2 = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261012150000_the_live_record_keeps_the_evening_forecasts.sql'), 'utf-8');
const KINDS = ['report', 'hourly', 'hourly2', 'models1', 'models2', 'books'];

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values ('workflow_schedules', '{"P0.4_trade_history": {"mode": "auto"}}');
    create table public.job_sla (job text primary key, cadence_minutes int not null, cadence text not null,
                                 scheduled_by text not null);
    create table public.ingest_log (log_id bigserial primary key, job text, status text, rows int, detail jsonb,
                                    logged_at timestamptz not null default now());
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log (job, status, rows, detail) values (p_job, p_status, p_rows, p_detail) $$;
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
  // registered, not yet due: one row, written once, saying why (the watchdog
  // counts a job with an SLA and no ok row as overdue)
  const reg = await one(`select status, rows, detail from public.ingest_log where job = 'archive_live_capture'`);
  assert.equal(reg.length, 1, 'one registration row, not one a re-run');
  assert.equal(reg[0].status, 'ok');
  assert.equal(reg[0].detail.registered, true);
  // the archive's watchdog row, and the backstop reclaim at 03:05
  assert.deepEqual((await one(`select cadence_minutes c from public.job_sla where job = 'archive_live_capture'`))[0], { c: 1440 });
  assert.ok((await one(`select 1 from cron.calls where name = 'ad4_reclaim_live_capture' and expr = '5 3 * * *'
                          and cmd = 'VACUUM (FULL, ANALYZE) public.live_capture'`)).length >= 1);
  // THE EVENING'S FORECASTS (WP3b): the migration widens the table's check and
  // the function's list to exactly these kinds, twice over (re-runnable)
  await db.exec(MIG2);
  await db.exec(MIG2);
  const chk = (await one(`select pg_get_constraintdef(oid) d from pg_constraint
                           where conrelid = 'public.live_capture'::regclass and contype = 'c'`));
  assert.equal(chk.length, 1, 'one check on the kind, not one a run');
  for (const k of KINDS) assert.ok(chk[0].d.includes(`'${k}'`), `the check keeps ${k}`);
  assert.equal((chk[0].d.match(/'[a-z0-9]+'::text/g) || []).length, KINDS.length, 'and no other kind');
  // the canonical file installs the same functions on its own
  await db.exec(SQL);
  assert.equal((await one(`select count(*)::int n from pg_constraint
                            where conrelid = 'public.live_capture'::regclass and contype = 'c'`))[0].n, 1);

  const NEW_MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
    '20261015132932_archived_record_windows.sql'), 'utf-8');
  // Model payload objects are supplied by the separate payload contract suite.
  await db.exec(NEW_MIG);
  await db.exec(NEW_MIG);
  const CURRENT_MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
    '20261015174914_current_forecast_capture.sql'), 'utf-8');
  await db.exec(CURRENT_MIG);
  await db.exec(CURRENT_MIG);

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
  // the evening's forecasts, once per change like 'hourly'
  const m7 = (x) => [x, x + 0.1, x + 0.2, null, x + 0.4, x + 0.5, x + 0.6];
  assert.deepEqual(await put('hourly2', T(3), [{ key: 'jeddah|2026-10-13T09:00', payload: [32.0, 24, 10, 600, 12, 0, 1009] }]),
    { rows: 1, new: 1 });
  assert.deepEqual(await put('hourly2', T(2), [{ key: 'jeddah|2026-10-13T09:00', payload: [32.0, 24, 10, 600, 12, 0, 1009] }]),
    { rows: 1, new: 0 });
  assert.deepEqual(await put('models1', T(2), [{ key: 'jeddah|2026-10-12T09:00', payload: m7(31) }]), { rows: 1, new: 1 });
  assert.deepEqual(await put('models2', T(2), [{ key: 'jeddah|2026-10-12T09:00', payload: m7(31) }]), { rows: 1, new: 1 },
    'the same key at the other lead is its own value');
  assert.deepEqual(await put('models2', T(1), [{ key: 'jeddah|2026-10-12T09:00', payload: m7(30) }]), { rows: 1, new: 1 });
  // the table itself refuses a kind it does not keep, whoever writes
  await assert.rejects(db.query(`insert into public.live_capture (kind, key, captured_at, payload)
                                 values ('models3', 'x', now(), '[]')`), /live_capture_kind_check/);
  // books: every tick, whatever it holds
  const books = { 'b1': [0.41, 0.43, 120, 80, 300.5, 210.2, 0.42, 1760216000, 5, 0.01] };
  assert.equal((await put('books', T(1), [{ key: 'all', payload: books }])).new, 1);
  assert.equal((await put('books', T(0), [{ key: 'all', payload: books }])).new, 1);

  // REFUSED: a kind it does not keep, a time in the future, rows that are not a list
  for (const [kind, at, rows, why] of [['trades', T(1), [], /not a kind/], ['models', T(1), [], /not a kind/],
    ['report', new Date(Date.now() + 3600e3).toISOString(), [], /future/],
    ['report', T(1), {}, /JSON array/]]) {
    await assert.rejects(db.query('select public.live_capture_put($1, $2, $3::jsonb)', [kind, at, JSON.stringify(rows)]), why);
  }

  // THE PRUNE: two days or nothing, the file's count or nothing.
  await put('report', T(80), [rep('KLGA|2026-10-08 10:51', 60), rep('KLGA|2026-10-08 11:51', 61)]);
  await put('books', T(75), [{ key: 'all', payload: books }]);
  const p = async (args) => (await one(`select public.prune_live_capture(${args}) r`))[0].r;
  assert.match((await p('0, true')).error, /at least 1/);
  assert.match((await p('2, false')).error, /p_expected_rows is required/);
  assert.equal((await p('2, true')).would_delete, 3);
  assert.match((await p('2, false, null, 4')).error, /count mismatch/);
  const before = (await one('select count(*)::int n from public.live_capture'))[0].n;
  const done = await p('2, false, null, 3');
  assert.equal(done.deleted, 3);
  assert.equal((await one('select count(*)::int n from public.live_capture'))[0].n, before - 3);
  assert.equal((await one(`select count(*)::int n from public.live_capture where captured_at < now() - interval '2 days'`))[0].n, 0);
  assert.equal((await p('2, true')).deleted, 0, 'nothing left to take');

  // One-day mode stops exactly at yesterday's mirrored UTC midnight,
  // even when a caller asks for now(). A wrong verified count deletes nothing.
  const boundary = (await one("select ((date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC') as b"))[0].b;
  const b = new Date(boundary).getTime();
  await put('report', new Date(b - 60000).toISOString(), [rep('mirror-old', 55)]);
  await put('report', new Date(b + 60000).toISOString(), [rep('mirror-young', 56)]);
  const dry1 = await p('1, true, now()');
  assert.equal(dry1.would_delete, 1);
  assert.match((await p('1, false, now(), 2')).error, /count mismatch/);
  assert.equal((await p('1, false, now(), 1')).deleted, 1);
  assert.equal((await one("select count(*)::int n from public.live_capture where key='mirror-young'"))[0].n, 1);

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

  // RE-RUNNABLE with rows in place, every kind among them
  const n = (await one('select count(*)::int n from public.live_capture'))[0].n;
  await db.exec(MIG);
  await db.exec(MIG2);
  await db.exec(NEW_MIG);
  assert.equal((await one('select count(*)::int n from public.live_capture'))[0].n, n);
  assert.equal((await put('models1', T(0), [{ key: 'tokyo|2026-10-12T00:00', payload: m7(20) }])).new, 1,
    'the migrations in order leave every kind writable');
  await db.exec(CURRENT_MIG);
  for (const kind of ['hourly0', 'models0']) {
    const r = [{key:'city|future-hour',payload:[21,22]}];
    assert.equal((await put(kind,T(1),r)).new,1);
    assert.equal((await put(kind,T(0),r)).new,0);
  }
  await db.exec(CURRENT_MIG);
  await db.exec(SQL);
  assert.equal((await put('hourly0', T(0), [{key:'city|new-hour',payload:[23]}])).new,1);
  assert.equal((await put('wx_features', T(1), [{key:'policy|city|date|morning',payload:{row_sha256:'test'}}])).new, 1);
  const RECEIPTS = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
    '20261016163242_capture_receipt_batches.sql'), 'utf-8');
  await db.exec(RECEIPTS);
  await db.exec(RECEIPTS);
  const receipts = [{key:'receipt-a',payload:[1],captured_at:T(2)},
                    {key:'receipt-b',payload:[2],captured_at:T(1)}];
  assert.equal((await put('hourly',T(0),receipts)).new,2);
  for (const row of receipts) {
    assert.equal((await db.query(`select captured_at=$2::timestamptz as exact,
      payload=$3::jsonb as same from public.live_capture where key=$1`,
      [row.key,row.captured_at,JSON.stringify(row.payload)])).rows[0].exact,true);
  }
  assert.equal((await put('hourly',T(0),receipts)).new,0,'first receipts survive a repeated transport');
  await assert.rejects(put('hourly',T(0),[{key:'future-row',payload:[1],captured_at:new Date(Date.now()+3600e3).toISOString()}]),/future/);
  await assert.rejects(put('hourly',T(0),[receipts[0],{...receipts[0],captured_at:T(0),payload:[9]}]),/separate batches/);
  assert.equal((await put('hourly',T(0),[{key:'legacy-receipt',payload:[3]}])).new,1);
  assert.equal((await db.query(`select has_function_privilege('anon',
    'public.live_capture_put(text,timestamptz,jsonb)','execute') as allowed`)).rows[0].allowed,false);
  console.log('PASS: live-capture: a report or forecast value (best_match at leads 1 and 2, the seven models at both) stored once per change and again when it changes back, a books row every tick; a kind it does not keep refused by the function and the table, a future time and a non-list refused; the prune takes two-day-old rows only, only the verified count; request_reclaim may rewrite it; the switch seeded and kept; the archive watchdog row and the 03:05 backstop; closed to the browser; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
