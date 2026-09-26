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

  const g = (await db.query(`select
      has_function_privilege('anon', 'public.clock_tick(timestamptz, boolean)', 'execute') a,
      has_function_privilege('authenticated', 'public.clock_check()', 'execute') b,
      has_table_privilege('anon', 'public.clock_schedule', 'select') c`)).rows[0];
  assert.deepEqual([g.a, g.b, g.c], [false, false, false]);
  console.log('clock: the right workflows at the right UTC hour, closed to the browser, nothing scheduled without cron/net/vault');
})().catch((e) => { console.error(e); process.exit(1); });
