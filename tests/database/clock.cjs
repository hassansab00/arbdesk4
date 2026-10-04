// ===========================================================================
// THE CLOCK IN SUPABASE (plan v2 P6.2): clock_due() starts the right
// workflows at the right UTC hour, the browser cannot dispatch, and a
// database without pg_cron / pg_net / Vault schedules nothing.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260926130000_the_clock_moves_into_supabase.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role;`);
  await db.exec(MIG);
  await db.exec(MIG);   // re-runnable

  const due = async (at) => (await db.query(
    'select array_agg(file order by file) f from public.clock_due($1::timestamptz)', [at])).rows[0].f || [];
  assert.deepEqual(await due('2026-09-27T04:36:00Z'), ['pipeline_daily.yml', 'pipeline_intraday.yml', 'tick.yml']);
  assert.deepEqual(await due('2026-09-27T13:36:00Z'), ['tick.yml']);
  assert.deepEqual(await due('2026-09-27T02:36:00Z'), ['archive_observations.yml', 'tick.yml']);
  // weather_model.yml: Mondays at 08 only (28 Sep 2026 is a Monday)
  assert.ok((await due('2026-09-28T08:36:00Z')).includes('weather_model.yml'));
  assert.ok(!(await due('2026-09-27T08:36:00Z')).includes('weather_model.yml'));
  const n = (await db.query('select count(*)::int n from public.clock_schedule')).rows[0].n;
  assert.equal(n, 8);
  const inputs = (await db.query(`select inputs from public.clock_schedule where file = 'archive_observations.yml'`)).rows[0].inputs;
  assert.deepEqual(inputs, { commit: 'true', table: 'all' });

  // 4 Oct (20261004170000): intraday every 6 hours, forecasts.yml off the
  // clock (its row kept: clock_expected_jobs references it), the daily run
  // expected to log the forecast ingest. Re-runnable.
  await db.exec(`create table public.clock_expected_jobs (
    file text not null references public.clock_schedule (file), job text not null,
    within_minutes int not null check (within_minutes between 5 and 180), note text,
    primary key (file, job));`);
  const PRO = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
    '20261004170000_the_minutes_fit_the_pro_plan.sql'), 'utf-8');
  const stamp = async () => (await db.query(
    `select file, updated_at::text u from public.clock_schedule order by file`)).rows;
  await db.exec(PRO);
  const once = await stamp();
  await db.exec(PRO);
  assert.deepEqual(await stamp(), once, 'a second run touches no row');
  assert.deepEqual(await due('2026-10-05T04:36:00Z'), ['pipeline_daily.yml', 'tick.yml'], 'intraday no longer beside the daily run');
  assert.deepEqual(await due('2026-10-05T03:36:00Z'), ['tick.yml'], 'forecasts.yml is started by hand now');
  assert.deepEqual(await due('2026-10-05T02:36:00Z'), ['archive_observations.yml', 'pipeline_intraday.yml', 'tick.yml']);
  for (const h of ['08', '14', '20']) {
    assert.ok((await due(`2026-10-05T${h}:36:00Z`)).includes('pipeline_intraday.yml'), h);
  }
  for (const h of ['00', '04', '06', '12', '16', '18']) {
    assert.ok(!(await due(`2026-10-05T${h}:36:00Z`)).includes('pipeline_intraday.yml'), h);
  }
  let forecastsDue = 0;
  for (let h = 0; h < 24; h += 1) {
    if ((await due(`2026-10-05T${String(h).padStart(2, '0')}:36:00Z`)).includes('forecasts.yml')) forecastsDue += 1;
  }
  assert.equal(forecastsDue, 0);
  assert.equal((await db.query('select count(*)::int n from public.clock_schedule')).rows[0].n, 8, 'no row removed');
  assert.deepEqual((await db.query(`select file, job, within_minutes from public.clock_expected_jobs`)).rows,
    [{ file: 'pipeline_daily.yml', job: 'ingest_forecasts', within_minutes: 105 }]);

  const g = (await db.query(`select
      has_function_privilege('anon', 'public.clock_tick(timestamptz, boolean)', 'execute') a,
      has_function_privilege('authenticated', 'public.clock_check()', 'execute') b,
      has_table_privilege('anon', 'public.clock_schedule', 'select') c`)).rows[0];
  assert.deepEqual([g.a, g.b, g.c], [false, false, false]);
  console.log('clock: the right workflows at the right UTC hour (intraday every 6 h and forecasts.yml by hand from 4 Oct), closed to the browser, nothing scheduled without cron/net/vault');
})().catch((e) => { console.error(e); process.exit(1); });
