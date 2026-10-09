// ===========================================================================
// THE WATCHDOG KNOWS WHEN EACH JOB LAST WORKED (plan v2 P6.3; WXPredict build
// 2.D, R14; 9 Oct).
//
// public.job_sla holds every scheduled job's cadence; its SLA is one cadence
// plus max(30 min, cadence / 12), computed by the table. v_job_last_ok marks a
// job overdue when its last 'ok' is older than that, or when it has none.
// run_health_watchdog() fails on an overdue job. An 'implausible_edge'
// anomaly (every anomaly row of the 7 days to 9 Oct) is a note now; any other
// kind still fails. The checks it had before are unchanged.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const DIR = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const ARRIVALS = path.join(DIR, '20260930001000_a_dispatched_run_that_never_logged.sql');
const SLA = path.join(DIR, '20261009200000_the_watchdog_knows_when_each_job_last_worked.sql');
const FILES = ['archive_observations.yml', 'forecasts.yml', 'observations.yml', 'paper_trade_log.yml',
               'pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml', 'weather_model.yml'];

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
    create table public.anomalies (detected_at timestamptz, kind text);
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values ('workflow_schedules', '{"P0.2_market_discovery": {"mode": "auto"}}');
    create table public.city_volume (volume_usd numeric, n_trades bigint);
    create view public.v_city_volume as select * from public.city_volume;
    insert into public.book_snapshots values (now() - interval '10 minutes');
    insert into public.weather_forecasts values (now() - interval '1 hour');
    insert into public.city_volume values (1000, 50);
  `);
  await db.exec(fs.readFileSync(ARRIVALS, 'utf-8'));
  await db.exec(fs.readFileSync(SLA, 'utf-8'));
  await db.exec(fs.readFileSync(SLA, 'utf-8'));          // re-runnable

  const one = async (sql, params = []) => (await db.query(sql, params)).rows;
  const jobs = (await one('select job from public.job_sla order by job')).map((r) => r.job);
  assert.equal(jobs.length, 64, 'the seed is written once and a second run changes nothing');

  // The rule is the table's, not each row's.
  const sla = Object.fromEntries((await one('select job, max_age_minutes from public.job_sla')).map((r) => [r.job, r.max_age_minutes]));
  assert.equal(sla.tick, 90);
  assert.equal(sla.refresh_page_cache, 60);
  assert.equal(sla.probability_engine, 390);
  assert.equal(sla.databank, 26 * 60);
  assert.equal(sla.weather_model, 7 * 24 * 60 + 14 * 60);
  await assert.rejects(db.query(`update public.job_sla set max_age_minutes = 5 where job = 'tick'`),
    /can only be updated to DEFAULT/, 'the SLA cannot be set by hand');
  await assert.rejects(db.query(`insert into public.job_sla (job, cadence_minutes, cadence, scheduled_by)
                                 values ('x', 5, 'too often', 'nobody')`), /check constraint/);

  // Every job fresh: an 'ok' 10 minutes ago.
  for (const job of jobs) {
    await db.query(`insert into public.ingest_log (job, status, rows, detail, logged_at)
                    values ($1, 'ok', 0, '{}', now() - interval '10 minutes')`, [job]);
  }
  let wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.deepEqual(wd.checks.overdue_jobs, {});
  assert.equal(wd.status, 'ok', wd.failures);
  assert.equal(wd.summary, 'AD4 P4.1: all clear.');

  // The tick's last ok 2 h ago (its SLA is 90 min): overdue, though it ran
  // since and logged 'attention'. The databank's 25 h ago: inside 26 h. The
  // honest record never ok, only 'partial': overdue with none on record.
  await db.exec(`
    delete from public.ingest_log where job in ('tick', 'databank', 'P2.9_honest_record');
    insert into public.ingest_log (job, status, rows, detail, logged_at) values
      ('tick', 'ok', 0, '{}', now() - interval '2 hours'),
      ('tick', 'attention', 0, '{}', now() - interval '1 hour'),
      ('databank', 'ok', 0, '{}', now() - interval '25 hours'),
      ('P2.9_honest_record', 'partial', 0, '{}', now() - interval '20 hours');
  `);
  const last = await one(`select job, overdue, last_status, age_minutes from public.v_job_last_ok
                           where job in ('tick', 'databank', 'P2.9_honest_record') order by job`);
  assert.deepEqual(last.map((r) => [r.job, r.overdue, r.last_status]), [
    ['P2.9_honest_record', true, 'partial'], ['databank', false, 'ok'], ['tick', true, 'attention']]);
  assert.equal(last.find((r) => r.job === 'P2.9_honest_record').age_minutes, null);

  wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd.status, 'attention');
  assert.deepEqual(Object.keys(wd.checks.overdue_jobs).sort(), ['P2.9_honest_record', 'tick']);
  assert.equal(Number(wd.checks.overdue_jobs.tick.sla_h), 1.5);
  assert.equal(Number(wd.checks.overdue_jobs.tick.age_h), 2);
  assert.equal(wd.checks.overdue_jobs.tick.last_status, 'attention');
  assert.equal(wd.checks.overdue_jobs['P2.9_honest_record'].last_ok_at, null);
  assert.ok(wd.failures.includes(
    '2 job(s) past their SLA: P2.9_honest_record (last ok none on record, allowed 26.0h), tick (last ok 2.0h ago, allowed 1.5h)'),
    wd.failures);
  assert.equal(wd.failures.length, 1, 'only the SLA fails: the book, forecast, volume and arrivals are healthy here');
  assert.ok(wd.summary.includes('past their SLA') && wd.summary.includes('NOT emailed - email is off'));

  // A JOB PAUSED ON PURPOSE IS NOT OVERDUE (Codex on #357). Switched 'off' or
  // 'manual' on the Workflows page, should_run() logs only 'skipped' rows.
  await db.exec(`
    delete from public.ingest_log where job = 'P0.2_market_discovery';
    insert into public.ingest_log (job, status, rows, detail, logged_at) values
      ('P0.2_market_discovery', 'ok', 0, '{}', now() - interval '3 days'),
      ('P0.2_market_discovery', 'skipped', 0, '{"gate": "off"}', now() - interval '1 hour');
  `);
  const paused = async () => (await one(`select overdue, mode, last_status from public.v_job_last_ok
                                          where job = 'P0.2_market_discovery'`))[0];
  assert.deepEqual(await paused(), { overdue: true, mode: 'auto', last_status: 'skipped' },
    'in auto, three days without an ok is overdue');
  for (const mode of ['off', 'manual']) {
    await db.query(`update public.settings set value = jsonb_set(value, '{P0.2_market_discovery,mode}', to_jsonb($1::text))
                     where key = 'workflow_schedules'`, [mode]);
    assert.deepEqual(await paused(), { overdue: false, mode, last_status: 'skipped' }, `${mode} is a choice, not a fault`);
  }
  await db.exec(`delete from public.ingest_log where job = 'P0.2_market_discovery';
                 insert into public.ingest_log (job, status, rows, detail, logged_at)
                 values ('P0.2_market_discovery', 'ok', 0, '{}', now() - interval '5 minutes');`);

  // IMPLAUSIBLE EDGES ARE INFORMATION. Five of them fail nothing and are a note;
  // one anomaly of any other kind still fails, as every anomaly did before.
  await db.exec(`
    delete from public.ingest_log where job in ('tick', 'P2.9_honest_record');
    insert into public.ingest_log (job, status, rows, detail, logged_at) values
      ('tick', 'ok', 0, '{}', now() - interval '5 minutes'),
      ('P2.9_honest_record', 'ok', 0, '{}', now() - interval '5 minutes');
    insert into public.anomalies select now() - interval '1 hour', 'implausible_edge' from generate_series(1, 5);
  `);
  wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd.status, 'ok', wd.failures);
  assert.equal(wd.checks.anomalies_24h, 5);
  assert.equal(wd.checks.implausible_edges_24h, 5);
  assert.ok(wd.context_notes.some((n) => n.startsWith('5 implausible-edge row(s) in 24h')), wd.context_notes);
  await db.exec(`insert into public.anomalies values (now() - interval '1 hour', 'stale_quote')`);
  wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd.status, 'attention');
  assert.deepEqual(wd.failures, ['1 anomaly row(s) in 24h']);

  // The checks it had before still fail as they did: a stale book, a crash row.
  await db.exec(`delete from public.anomalies where kind <> 'implausible_edge';
                 update public.book_snapshots set observed_at = now() - interval '4 hours';
                 insert into public.ingest_log (job, status, rows, detail) values ('crash:tick.py', 'error', 0, '{}');`);
  wd = (await one('select public.run_health_watchdog() r'))[0].r;
  assert.equal(wd.checks.failed_jobs_24h, 1);
  assert.ok(wd.failures.some((f) => f.startsWith('stale_book: 240min old')), wd.failures);
  assert.ok(wd.failures.includes('1 failed ingest job(s) in 24h: crash:tick.py'), wd.failures);

  // Service role only.
  for (const rel of ['public.job_sla', 'public.v_job_last_ok']) {
    await db.exec('set role anon');
    await assert.rejects(db.query(`select * from ${rel}`), /permission denied/, `${rel} is not anon's`);
    await db.exec('reset role');
  }
  await db.exec('set role anon');
  await assert.rejects(db.query('select public.run_health_watchdog()'), /permission denied/);
  await db.exec('reset role');

  console.log('PASS: job-sla: every scheduled job has an SLA the table computes (one cadence plus max(30 min, cadence/12)); a job whose last ok is older, or that has none, is overdue and fails the watchdog by name; implausible edges are a note, any other anomaly still fails; the old checks still fail as before; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
