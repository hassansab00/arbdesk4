// ===========================================================================
// A DISPATCHED RUN THAT NEVER LOGGED IS A FAILURE (audit repair 2, 29 Sep).
//
// v_run_arrivals pairs every workflow the clock dispatched with every job
// clock_expected_jobs says that workflow writes: arrived, waiting or missing.
// run_health_watchdog() counts the missing ones as failures and reports
// partial / attention runs per job. Measured 27-29 Sep: 19 production runs
// failed while the watchdog said failed_jobs_24h 0 every time.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260930001000_a_dispatched_run_that_never_logged.sql');

const FILES = ['archive_observations.yml', 'forecasts.yml', 'observations.yml', 'paper_trade_log.yml',
               'pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml', 'weather_model.yml'];
const INTRADAY = ['weather_model_forecast', 'probability_engine', 'edge_engine', 'research_capture',
                  'paper_exits', 'signal_engine', 'paper_plans', 'paper_worker', 'paper_settlement'];

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.clock_schedule (file text primary key, hours_utc int[], weekdays_utc int[], inputs jsonb);
    insert into public.clock_schedule (file) values ${FILES.map((f) => `('${f}')`).join(', ')};
    create table public.ingest_log (log_id bigserial primary key, job text, status text, rows int,
                                    detail jsonb, logged_at timestamptz not null default now());
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log (job, status, rows, detail) values (p_job, p_status, p_rows, p_detail) $$;
    create function public.should_run(p_job text, p_trigger text) returns jsonb
      language sql as $$ select '{"run": true}'::jsonb $$;
    create table public.book_snapshots (observed_at timestamptz);
    create table public.weather_forecasts (run_at timestamptz);
    create table public.anomalies (detected_at timestamptz);
    create table public.city_volume (volume_usd numeric, n_trades bigint);
    create view public.v_city_volume as select * from public.city_volume;
    insert into public.book_snapshots values (now() - interval '10 minutes');
    insert into public.weather_forecasts values (now() - interval '1 hour');
    insert into public.city_volume values (1000, 50);
  `);
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // re-runnable

  const one = async (sql, params = []) => (await db.query(sql, params)).rows;
  const seeded = await one('select count(*)::int n from public.clock_expected_jobs');
  assert.equal(seeded[0].n, 37, 'the seed is written once and a second run changes nothing');

  const clock = (ago, files, dryRun = false) => db.query(
    `insert into public.ingest_log (job, status, rows, detail, logged_at)
     values ('P6.1_clock', 'ok', $1, $2::jsonb, now() - $3::interval)`,
    [files.length, JSON.stringify({ dry_run: dryRun, dispatched: files.map((file, i) => ({ file, request_id: i })) }), ago]);
  const logged = (job, at, status = 'ok') => db.query(
    `insert into public.ingest_log (job, status, rows, detail, logged_at) values ($1, $2, 0, '{}', now() - $3::interval)`,
    [job, status, at]);

  // 3 h ago: the intraday pipeline and the tick. Every intraday job arrives
  // except paper_exits, whose row comes 50 minutes later - outside its 45.
  await clock('3 hours', ['pipeline_intraday.yml', 'tick.yml']);
  for (const job of INTRADAY.filter((j) => j !== 'paper_exits' && j !== 'paper_settlement')) {
    await logged(job, '2 hours 57 minutes');
  }
  await logged('paper_settlement', '2 hours 56 minutes', 'partial');
  await logged('paper_exits', '2 hours 10 minutes');
  await logged('tick', '2 hours 59 minutes');
  await logged('P0.4_trade_history', '2 hours 59 minutes', 'attention');
  // a row from BEFORE a dispatch is not that dispatch's arrival
  await logged('weather_model', '31 hours');
  // 30 h ago: the weekly refit, which never logged - missing, but outside the watchdog's 24 h
  await clock('30 hours', ['weather_model.yml']);
  // 2 h ago: a dry run is not a dispatch
  await clock('2 hours', ['pipeline_daily.yml'], true);
  // 5 minutes ago: a tick still inside its 15 minutes
  await clock('5 minutes', ['tick.yml']);

  const state = async (file, job) => (await one(
    `select state, status from public.v_run_arrivals where file = $1 and job = $2 order by dispatched_at`, [file, job]));
  assert.deepEqual(await state('pipeline_intraday.yml', 'paper_exits'), [{ state: 'missing', status: null }],
    'a row after the window is not an arrival');
  assert.deepEqual(await state('pipeline_intraday.yml', 'paper_settlement'), [{ state: 'arrived', status: 'partial' }]);
  assert.deepEqual(await state('tick.yml', 'P0.4_trade_history'),
    [{ state: 'arrived', status: 'attention' }, { state: 'waiting', status: null }]);
  assert.deepEqual(await state('weather_model.yml', 'weather_model'), [{ state: 'missing', status: null }],
    'the row from before the dispatch does not count');
  assert.equal((await one(`select count(*)::int n from public.v_run_arrivals where file = 'pipeline_daily.yml'`))[0].n, 0,
    'a dry run dispatched nothing');
  const counts = await one(`select state, count(*)::int n from public.v_run_arrivals group by state order by state`);
  assert.deepEqual(counts, [{ state: 'arrived', n: 10 }, { state: 'missing', n: 3 }, { state: 'waiting', n: 2 }]);

  // The watchdog: the one missed run inside 24 h is a failure; weather_model
  // missed 30 h ago is not today's; partial / attention are reported per job.
  const wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd.status, 'attention');
  assert.equal(wd.checks.missed_runs_24h, 1);
  assert.ok(wd.failures.includes('1 dispatched run(s) never logged their job: paper_exits x1'), wd.failures);
  assert.deepEqual(wd.checks.not_ok_24h, {
    'P0.4_trade_history': { partial: 0, attention: 1 }, paper_settlement: { partial: 1, attention: 0 } });
  assert.equal(wd.checks.failed_jobs_24h, 0);
  assert.equal(wd.failures.length, 1, 'the book, the forecast and the volume are healthy in this fixture');
  const row = await one(`select status, detail from public.ingest_log where job = 'P4.1_health_watchdog'`);
  assert.equal(row.length, 1);
  assert.equal(row[0].status, 'attention');

  // A crash row the scripts now write (common._log_crash) is an error like any other.
  await db.query(`insert into public.ingest_log (job, status, rows, detail) values ('crash:paper_exits.py', 'error', 0, '{}')`);
  const wd2 = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd2.checks.failed_jobs_24h, 1);
  assert.ok(wd2.failures.includes('1 failed ingest job(s) in 24h: crash:paper_exits.py'), wd2.failures);
  assert.deepEqual(wd2.checks.not_ok_24h, wd.checks.not_ok_24h, "the watchdog's own 'attention' row is not counted");

  // Nothing expected of a workflow the clock does not know.
  await assert.rejects(db.query(`insert into public.clock_expected_jobs values ('nope.yml', 'x', 10, null)`),
    /foreign key/);
  await assert.rejects(db.query(`insert into public.clock_expected_jobs values ('tick.yml', 'x', 500, null)`),
    /check constraint/);

  // Service role only.
  for (const rel of ['public.clock_expected_jobs', 'public.v_run_arrivals']) {
    await db.exec('set role anon');
    await assert.rejects(db.query(`select * from ${rel}`), /permission denied/, `${rel} is not anon's`);
    await db.exec('reset role');
  }
  await db.exec('set role anon');
  await assert.rejects(db.query('select public.run_health_watchdog()'), /permission denied/);
  await db.exec('reset role');

  console.log('PASS: run-arrivals: a dispatched run owes its jobs\' rows - arrived, waiting or missing (a late row, a row before the dispatch and a dry run count for nothing); the watchdog fails on a missed run inside 24 h, counts a crash row, reports partial/attention per job without failing on them; the expectations name only scheduled workflows; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
