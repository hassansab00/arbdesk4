// ===========================================================================
// BOOK PROOF GOES TO THE REPOSITORY (plan v2 P1.6 phase 1, step 2, 28 Sep,
// 20260928220000_book_proof_goes_to_the_repo.sql). Hassan approved phase 1
// "as long as we dont lose any collected data": prune_book_evidence deletes
// only rows older than a day, only the exact count the archive read back
// from its committed file, and only by claiming the append-only guard's
// single-table exemption - which nothing else can use. request_reclaim takes
// the table, and the daily backstop is scheduled. The migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260928220000_book_proof_goes_to_the_repo.sql'), 'utf-8');
const EXEMPTION = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_70_archive_exemption.sql'), 'utf-8');
const GUARD = EXEMPTION.slice(EXEMPTION.indexOf('create or replace function arbdesk_private.immutable_record()'),
  EXEMPTION.indexOf('$fn$;', EXEMPTION.indexOf('create or replace function arbdesk_private.immutable_record()')) + 5);

(async () => {
  const db = new PGlite();
  // The table and its triggers exactly as 20260912083705 creates them; the
  // guard exactly as sql/ad4_70 defines it; cron as a recorder.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
  `);
  await db.exec(GUARD);
  await db.exec(`
    create table public.paper_book_evidence (
      snapshot_id text primary key,
      token_id text not null,
      observed_at timestamptz not null,
      captured_at timestamptz not null default clock_timestamp(),
      payload jsonb not null
    );
    create trigger book_evidence_immutable before update or delete on public.paper_book_evidence
      for each row execute function arbdesk_private.immutable_record();
    create trigger book_evidence_no_truncate before truncate on public.paper_book_evidence
      for each statement execute function arbdesk_private.immutable_record();
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  const hoursAgo = (h) => `now() - interval '${h} hours'`;
  await db.exec(`insert into public.paper_book_evidence values
    ('a', 't1', ${hoursAgo(50)}, ${hoursAgo(50)}, '{"bids": [1]}'),
    ('b', 't1', ${hoursAgo(30)}, ${hoursAgo(30)}, '{"bids": [2]}'),
    ('c', 't2', ${hoursAgo(26)}, ${hoursAgo(26)}, '{"bids": [3]}'),
    ('d', 't2', ${hoursAgo(2)},  ${hoursAgo(2)},  '{"bids": [4]}'),
    ('e', 't3', ${hoursAgo(0)},  ${hoursAgo(0)},  '{"bids": [5]}')`);
  const count = async () => (await db.query('select count(*)::int as n from public.paper_book_evidence')).rows[0].n;
  const prune = async (args) => (await db.query(`select public.prune_book_evidence(${args}) as r`)).rows[0].r;

  // The table stays append-only for everyone else.
  await assert.rejects(db.exec(`delete from public.paper_book_evidence where snapshot_id = 'a'`), /Append-only/);
  await assert.rejects(db.exec(`update public.paper_book_evidence set token_id = 'x' where snapshot_id = 'a'`), /Append-only/);

  // The floor is a day; a committed prune needs the verified count.
  const zero = await prune('0');
  assert.equal(zero.ok, false);
  assert.match(zero.error, /at least 1/);
  const blind = await prune('1, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the three rows older than a day, nothing deleted.
  const dry = await prune('1, true');
  assert.equal(dry.ok, true);
  assert.equal(Number(dry.would_delete), 3);
  assert.equal(Number(dry.would_keep), 2);
  assert.equal(await count(), 5);

  // A count that differs from the file deletes nothing.
  const wrong = await prune(`1, false, null, 2`);
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await count(), 5, 'a mismatched prune deleted rows');

  // A cutoff newer than the floor is moved back to it, never forward.
  const tooNew = await prune(`1, true, ${hoursAgo(1)}`);
  assert.equal(Number(tooNew.would_delete), 3, 'a cutoff inside the last day was honoured');

  // The exact count: the three old rows go, the two recent stay.
  const done = await prune(`1, false, null, 3`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 3);
  const left = (await db.query('select string_agg(snapshot_id, \',\' order by snapshot_id) as ids from public.paper_book_evidence')).rows[0].ids;
  assert.equal(left, 'd,e');

  // The exemption ended with the delete: a direct delete is refused again.
  await assert.rejects(db.exec(`delete from public.paper_book_evidence where snapshot_id = 'd'`), /Append-only/);
  assert.equal(await count(), 2);

  // Nothing older than a day: a no-op, not an error.
  const none = await prune(`1, false, null, 0`);
  assert.equal(none.ok, true);
  assert.equal(Number(none.deleted), 0);

  // request_reclaim takes the table and schedules its VACUUM FULL; a table
  // the archive does not prune is still refused. The backstop is scheduled.
  const rr = (await db.query(`select public.request_reclaim('paper_book_evidence') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_paper_book_evidence');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = (await db.query(`select jobname, schedule, command from cron.calls order by jobname, schedule`)).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_paper_book_evidence' && c.schedule === '25 3 * * *'
    && c.command === 'VACUUM (FULL, ANALYZE) public.paper_book_evidence'), JSON.stringify(calls));
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_after_archive_paper_book_evidence'
    && c.command === 'VACUUM (FULL, ANALYZE) public.paper_book_evidence'));

  // Both functions are the service role's alone.
  for (const fn of ['public.prune_book_evidence(integer,boolean,timestamptz,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log("PASS: book-evidence: a day's floor, the exact verified count or nothing, the append-only guard holds for everyone else and again after the prune, request_reclaim and the daily backstop take the table, service role only, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
