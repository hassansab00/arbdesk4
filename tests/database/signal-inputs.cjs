// ===========================================================================
// A WEEK-OLD SIGNAL'S DECISION INPUTS GO TO THE REPOSITORY (plan v2 P1.6
// phase 1, step 4, 28 Sep, 20260928235000_a_week_old_signals_inputs_go_to_the_repo.sql).
// Hassan approved phase 1 "as long as we dont lose any collected data".
//
// prune_signal_inputs strips payload.decision_inputs from signals seven or
// more days old, only the exact count the archive read back from its committed
// file. THE ROWS STAY, and so does every other payload key: band_ids and
// basket_group (the strategy board), decision_snapshot (the databank),
// cycle_id. The payload is marked decision_inputs_in_repo and leaves the
// export view, so nothing is exported twice. The strip is not research: no
// capture of it, and capture is back on for the next statement.
// request_reclaim takes the table, the daily backstop is scheduled, the view
// and the function are the service role's alone, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260928235000_a_week_old_signals_inputs_go_to_the_repo.sql'), 'utf-8');
const CAPTURE = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260923110000_a_backfill_is_not_research.sql'), 'utf-8');
const CAPTURE_FN = CAPTURE.slice(CAPTURE.indexOf('create or replace function arbdesk_private.archive_research_row()'),
  CAPTURE.indexOf('end $$;', CAPTURE.indexOf('create or replace function arbdesk_private.archive_research_row()')) + 7);

(async () => {
  const db = new PGlite();
  // signals and research_captures as the migrations create them, the capture
  // function exactly as 20260923110000 defines it, its trigger as live; cron
  // as a recorder.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
    create table public.signals (signal_id bigint primary key, fired_at timestamptz default now(),
      strategy_id text, action text, band_id uuid, status text, payload jsonb);
    create table public.research_captures (
      capture_id uuid primary key default gen_random_uuid(),
      command_key text not null unique,
      captured_at timestamptz not null default clock_timestamp(),
      engine_version text not null,
      provenance text not null check(provenance in ('forward_capture','historical_import','source_revision')),
      source_relation text not null, source_key text not null,
      payload jsonb not null, payload_hash text not null,
      unique(source_relation, source_key, payload_hash));
  `);
  await db.exec(CAPTURE_FN);
  await db.exec(`create trigger preserve_research_output after insert or update on public.signals
                   for each row execute function arbdesk_private.archive_research_row();`);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  const daysAgo = (d) => `now() - interval '${d} days'`;
  const inputs = (n) => JSON.stringify({ [`band-${n}`]: { decision_evidence: { forecast: { forecast_version: `fv${n}` }, rows: [1, 2, 3] } } });
  await db.exec(`insert into public.signals (signal_id, fired_at, strategy_id, action, payload) values
    (1, ${daysAgo(10)}, 's2', 'ENTER', jsonb_build_object('decision_inputs', '${inputs(1)}'::jsonb,
        'decision_snapshot', '{"band-1": {"forecast_version": "fv1"}}'::jsonb,
        'band_ids', '["b1","b2"]'::jsonb, 'cycle_id', 'c1')),
    (2, ${daysAgo(8)},  's8', 'ENTER', jsonb_build_object('decision_inputs', '${inputs(2)}'::jsonb,
        'basket_group', 'g2', 'band_ids', '["b3"]'::jsonb, 'cycle_id', 'c2')),
    (3, ${daysAgo(9)},  's1', 'ALERT', '{"detail": "no inputs were ever written"}'::jsonb),
    (4, ${daysAgo(3)},  's1', 'ENTER', jsonb_build_object('decision_inputs', '${inputs(4)}'::jsonb, 'cycle_id', 'c4')),
    (5, ${daysAgo(0)},  's1', 'ENTER', jsonb_build_object('decision_inputs', '${inputs(5)}'::jsonb, 'cycle_id', 'c5'))`);
  const before = Object.fromEntries((await db.query('select signal_id, payload from public.signals')).rows
    .map((r) => [r.signal_id, r.payload]));
  const captures = async () => (await db.query(`select count(*)::int as n from public.research_captures where source_relation = 'signals'`)).rows[0].n;
  assert.equal(await captures(), 5, 'every insert is captured');
  const prune = async (args) => (await db.query(`select public.prune_signal_inputs(${args}) as r`)).rows[0].r;
  const inView = async () => (await db.query('select string_agg(signal_id::text, \',\' order by signal_id) as ids from public.v_signal_inputs_export')).rows[0].ids;

  // The export view lists the signals still carrying inputs, with the key.
  assert.equal(await inView(), '1,2,4,5');
  const one = (await db.query('select * from public.v_signal_inputs_export where signal_id = 1')).rows[0];
  assert.deepEqual(one.decision_inputs, JSON.parse(inputs(1)));
  assert.equal(one.strategy_id, 's2');

  // The floor is a week; a committed strip needs the verified count.
  const six = await prune('6');
  assert.equal(six.ok, false);
  assert.match(six.error, /at least 7/);
  const blind = await prune('7, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the two week-old signals with inputs; nothing changes.
  const dry = await prune('7, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 2);
  assert.equal(Number(dry.would_keep), 2);
  assert.equal(await inView(), '1,2,4,5');

  // A cutoff newer than the floor is moved back to it, never forward.
  const tooNew = await prune(`7, true, now()`);
  assert.equal(Number(tooNew.would_delete), 2, 'a cutoff inside the last week was honoured');

  // A count that differs from the file changes nothing.
  const wrong = await prune(`7, false, null, 3`);
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await inView(), '1,2,4,5', 'a mismatched strip changed payloads');

  // The exact count: two payloads lose decision_inputs, nothing else.
  const done = await prune(`7, false, null, 2`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 2);
  assert.equal(Number(done.stripped), 2);
  const after = Object.fromEntries((await db.query('select signal_id, payload from public.signals')).rows
    .map((r) => [r.signal_id, r.payload]));
  assert.equal(Object.keys(after).length, 5, 'a row left the table');
  for (const id of [1, 2]) {
    assert.equal(after[id].decision_inputs, undefined, `signal ${id} kept its inputs`);
    assert.equal(after[id].decision_inputs_in_repo, true);
    const { decision_inputs, ...rest } = before[id];
    const { decision_inputs_in_repo, ...kept } = after[id];
    assert.deepEqual(kept, rest, `signal ${id} lost a key besides decision_inputs`);
  }
  // What the board and the databank read is exactly as it was.
  assert.deepEqual(after[1].band_ids, ['b1', 'b2']);
  assert.equal(after[2].basket_group, 'g2');
  assert.deepEqual(after[1].decision_snapshot, { 'band-1': { forecast_version: 'fv1' } });
  // Untouched: no inputs to strip, or not a week old.
  for (const id of [3, 4, 5]) assert.deepEqual(after[id], before[id], `signal ${id} changed`);
  assert.equal(await inView(), '4,5', 'a stripped signal is still offered for export');

  // Not research: the strip captured nothing, and capture is on again for
  // the next statement.
  assert.equal(await captures(), 5, 'the strip was copied into research_captures');
  await db.exec(`update public.signals set status = 'seen' where signal_id = 5`);
  assert.equal(await captures(), 6, 'capture stayed off after the strip');

  // Nothing week-old left: a no-op, not an error.
  const none = await prune(`7, false, null, 0`);
  assert.equal(none.ok, true);
  assert.equal(Number(none.deleted), 0);

  // request_reclaim takes the table; a table the archive does not prune is
  // still refused; the backstop is scheduled.
  const rr = (await db.query(`select public.request_reclaim('signals') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_signals');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = (await db.query(`select jobname, schedule, command from cron.calls`)).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_signals' && c.schedule === '40 3 * * *'
    && c.command === 'VACUUM (FULL, ANALYZE) public.signals'), JSON.stringify(calls));

  // The view and both functions are the service role's alone.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}', 'public.v_signal_inputs_export', 'select') as ok`)).rows[0].ok,
      false, `${role} can read the export view`);
  }
  assert.equal((await db.query(`select has_table_privilege('service_role', 'public.v_signal_inputs_export', 'select') as ok`)).rows[0].ok, true);
  await db.exec('set role anon');
  await assert.rejects(db.query('select count(*) from public.v_signal_inputs_export'), /permission denied/);
  await db.exec('reset role');
  for (const fn of ['public.prune_signal_inputs(integer,boolean,timestamptz,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log("PASS: signal-inputs: a week's floor, the exact verified count or nothing, the rows and every other payload key stay (board, databank), a stripped signal leaves the export, no research capture of the strip and capture back on after, request_reclaim and the daily backstop take the table, service role only, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
