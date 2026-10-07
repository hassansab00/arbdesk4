// ===========================================================================
// EVERY CORRELATION BUT EACH PAIR'S NEWEST GOES TO THE REPOSITORY (WXPredict
// build 2.A, group B; 20261007130000_superseded_correlations_go_to_the_repo.sql).
//
// The export view offers exactly the rows a newer row of the same pair has
// superseded - never a pair's newest, however old - each with one unique text
// key the archive can keyset-page on. prune_city_correlation deletes only
// those rows at least 2 days old, only the exact count the archive read back
// from its committed file, and every reader's rows (the newest per pair, the
// newest computation) are the same after it. request_reclaim takes the table,
// the daily backstop is scheduled, the view and the function are the service
// role's alone, the table stays readable as it was, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const MIG = fs.readFileSync(path.join(MIGRATIONS, '20261007130000_superseded_correlations_go_to_the_repo.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  // The live shape (information_schema, pg_constraint, pg_policy, relacl;
  // 7 Oct). cron as a recorder.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
    create table public.cities (city_key text primary key);
    insert into public.cities values ('london'), ('nyc'), ('paris'), ('tokyo');
    create table public.derived_city_correlation (
      city_a text not null references public.cities(city_key),
      city_b text not null references public.cities(city_key),
      computed_at timestamptz not null default now(),
      n_days int, err_corr numeric,
      primary key (city_a, city_b, computed_at));
    alter table public.derived_city_correlation enable row level security;
    create policy anon_read on public.derived_city_correlation for select using (true);
    grant select on public.derived_city_correlation to anon, authenticated;
    grant all on public.derived_city_correlation to service_role;
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // Eleven rows, five pairs:
  //   london|nyc     4 days of recomputes; three superseded and old
  //   london|paris   one row, ten days old: its newest, so it stays
  //   nyc|paris      a superseded old row and a newest 1.5 days old
  //   nyc|tokyo      a superseded row inside the 2-day keep, and its newest
  //   london|tokyo   two rows a microsecond apart: two keys, the older superseded
  const ago = (d) => `now() - interval '${d} days'`;
  await db.exec(`insert into public.derived_city_correlation (city_a, city_b, computed_at, n_days, err_corr) values
    ('london', 'nyc',    ${ago(5)},   150, 0.10),
    ('london', 'nyc',    ${ago(4)},   151, 0.11),
    ('london', 'nyc',    ${ago(3)},   152, 0.12),
    ('london', 'nyc',    ${ago(1)},   154, 0.14),
    ('london', 'paris',  ${ago(10)},  90,  0.71),
    ('nyc',    'paris',  ${ago(3)},   120, 0.20),
    ('nyc',    'paris',  ${ago(1.5)}, 121, 0.21),
    ('nyc',    'tokyo',  ${ago(1.9)}, 60,  -0.05),
    ('nyc',    'tokyo',  ${ago(0.5)}, 61,  -0.04),
    ('london', 'tokyo',  timestamptz '2026-09-20 05:00:00.000001+00', 80, 0.30),
    ('london', 'tokyo',  timestamptz '2026-09-20 05:00:00.000002+00', 80, 0.31)`);
  const count = async () => (await db.query('select count(*)::int as n from public.derived_city_correlation')).rows[0].n;
  const prune = async (args) => (await db.query(`select public.prune_city_correlation(${args}) as r`)).rows[0].r;
  // What the readers read: signal_engine._correlations (the newest row per
  // pair) and _context (the rows of the newest computation).
  const newestPerPair = async () => (await db.query(`
    select distinct on (city_a, city_b) city_a, city_b, computed_at::text, n_days, err_corr::text
      from public.derived_city_correlation order by city_a, city_b, computed_at desc`)).rows;
  const newestComputation = async () => (await db.query(`
    select city_a, city_b, err_corr::text from public.derived_city_correlation
     where computed_at = (select max(computed_at) from public.derived_city_correlation)
     order by city_a, city_b`)).rows;

  // THE VIEW: the six superseded rows, never a pair's newest.
  const offered = (await db.query(`select city_a, city_b, err_corr::text from public.v_prunable_city_correlation order by correlation_key`)).rows;
  assert.equal(offered.length, 6, JSON.stringify(offered));
  const newest = await newestPerPair();
  for (const n of newest) {
    assert.ok(!offered.some((o) => o.city_a === n.city_a && o.city_b === n.city_b && o.err_corr === n.err_corr),
      `${n.city_a}|${n.city_b}'s newest row is offered`);
  }
  assert.ok(!offered.some((o) => o.city_b === 'paris' && o.city_a === 'london'), 'a lone old row is offered');

  // THE KEY: one per row, the computation to the microsecond in UTC.
  const keys = (await db.query(`select correlation_key from public.v_prunable_city_correlation order by correlation_key`)).rows.map((r) => r.correlation_key);
  assert.equal(new Set(keys).size, keys.length, 'two rows share an export key');
  for (const k of keys) {
    assert.match(k, /^[a-z_]+\|[a-z_]+\|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$/, k);
  }
  assert.ok(keys.includes('london|tokyo|2026-09-20T05:00:00.000001'));
  assert.ok(!keys.includes('london|tokyo|2026-09-20T05:00:00.000002'), 'the newer of two rows a microsecond apart is offered');

  // The archive's walk: a page at a time, strictly after the last key.
  const seen = [];
  let after = null;
  for (;;) {
    const page = (await db.query(
      `select correlation_key from public.v_prunable_city_correlation
        where ($1::text is null or correlation_key > $1::text)
        order by correlation_key limit 2`, [after])).rows;
    if (!page.length) break;
    seen.push(...page.map((r) => r.correlation_key));
    after = page[page.length - 1].correlation_key;
  }
  assert.deepEqual(seen, keys, 'the paged walk missed or repeated a row');

  // Every column of the table rides along, unchanged, plus the key.
  const viewCols = (await db.query(`select column_name from information_schema.columns where table_name = 'v_prunable_city_correlation' order by ordinal_position`)).rows.map((r) => r.column_name);
  assert.deepEqual(viewCols, ['correlation_key', 'city_a', 'city_b', 'computed_at', 'n_days', 'err_corr']);

  // The floor is two days; a committed prune needs the verified count.
  for (const k of ['1', '0', 'null']) {
    const short = await prune(k);
    assert.equal(short.ok, false, k);
    assert.match(short.error, /at least 2/);
  }
  const blind = await prune('2, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the five superseded rows older than two days, nothing deleted.
  const dry = await prune('2, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 5);
  assert.equal(Number(dry.pairs), 5);
  assert.equal(Number(dry.rows_now), 11);
  assert.equal(await count(), 11);

  // A cutoff newer than the floor is moved back to it, never forward; an
  // older one is honoured (london|nyc at 5 days, london|tokyo's older row).
  assert.equal(Number((await prune(`2, true, now()`)).would_delete), 5, 'a cutoff inside the window was honoured');
  assert.equal(Number((await prune(`2, true, ${ago(4.5)}`)).would_delete), 2);

  // A count that differs from the file deletes nothing.
  const wrong = await prune(`2, false, null, 4`);
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await count(), 11, 'a mismatched prune deleted rows');

  // A RECOMPUTE BETWEEN THE EXPORT AND THE PRUNE supersedes london|paris's
  // lone row: the file's five no longer match, and nothing goes.
  await db.exec(`insert into public.derived_city_correlation (city_a, city_b, computed_at, n_days, err_corr)
                 values ('london', 'paris', now(), 91, 0.72)`);
  const moved = await prune(`2, false, null, 5`);
  assert.equal(moved.ok, false, JSON.stringify(moved));
  assert.equal(Number(moved.would_delete), 6);
  assert.equal(await count(), 12, 'a prune deleted rows its file does not hold');

  // The exact count: six go, and every reader reads the same rows after.
  const perPairBefore = await newestPerPair();
  const computationBefore = await newestComputation();
  const done = await prune(`2, false, null, 6`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 6);
  assert.equal(Number(done.pairs_before), 5);
  assert.equal(Number(done.pairs_after), 5);
  assert.equal(await count(), 6);
  assert.deepEqual(await newestPerPair(), perPairBefore, 'a pair\'s newest row changed');
  assert.deepEqual(await newestComputation(), computationBefore, 'the newest computation changed');
  // The superseded row inside the keep stays.
  assert.equal((await db.query(`select count(*)::int as n from public.derived_city_correlation where city_a = 'nyc' and city_b = 'tokyo'`)).rows[0].n, 2);

  // Nothing old left to take: a no-op, not an error.
  const none = await prune(`2, false, null, 0`);
  assert.equal(none.ok, true, JSON.stringify(none));
  assert.equal(Number(none.deleted), 0);

  // request_reclaim takes the table; a table the archive does not prune is
  // still refused. The backstop is daily, after the 02:36 archive.
  const rr = (await db.query(`select public.request_reclaim('derived_city_correlation') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_derived_city_correlation');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  const calls = (await db.query(`select jobname, schedule, command from cron.calls`)).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_derived_city_correlation' && c.schedule === '10 3 * * *'
    && c.command === 'VACUUM (FULL, ANALYZE) public.derived_city_correlation'), JSON.stringify(calls));
  // Every table before it is still allowed: the new one is appended.
  for (const t of ['research_captures', 'decisions', 'weather_forecast_models', 'band_probabilities']) {
    assert.equal((await db.query(`select public.request_reclaim('${t}') as r`)).rows[0].r.ok, true, t);
  }

  // The view is the service role's alone, and so are both functions.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}', 'public.v_prunable_city_correlation', 'select') as ok`)).rows[0].ok,
      false, `${role} can read the export view`);
  }
  assert.equal((await db.query(`select has_table_privilege('service_role', 'public.v_prunable_city_correlation', 'select') as ok`)).rows[0].ok, true);
  await db.exec('set role anon');
  await assert.rejects(db.query('select count(*) from public.v_prunable_city_correlation'), /permission denied/);
  // The table itself reads as it did.
  assert.equal((await db.query('select count(*)::int as n from public.derived_city_correlation')).rows[0].n, 6);
  await db.exec('reset role');
  await db.exec('set role service_role');
  assert.equal((await db.query('select count(*)::int as n from public.v_prunable_city_correlation')).rows[0].n, 1);
  await db.exec('reset role');
  for (const fn of ['public.prune_city_correlation(integer,boolean,timestamptz,bigint)', 'public.request_reclaim(text)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log('PASS: city-correlation: the view offers exactly the superseded rows, never a pair\'s newest, one unique key each that pages every row once; a two-day floor, the exact verified count or nothing, a recompute in between refused; every pair\'s newest row and the newest computation unchanged after the prune; request_reclaim and the daily backstop take the table with every earlier table still allowed; service role only, the table readable as before, re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
