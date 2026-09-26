// ===========================================================================
// THE HONEST STATION MODEL (plan v2.2 P2.9): its two shadow tables are the
// service role's alone, a step fraction outside (0, 1] is refused, and the
// pricing switch starts OFF and is never switched back off by a re-run.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260926150000_the_honest_station_model.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings(key text primary key, value jsonb not null,
      updated_at timestamptz not null default now());
  `);
  await db.exec(MIG);

  const flag = async () => (await db.query(
    `select value from public.settings where key = 'station_mos_pricing'`)).rows[0].value;
  assert.equal((await flag()).enabled, false, 'the engine does not price from it until the first rows are checked');

  await db.query(`insert into public.derived_mos_coefficients (city_key, lead_days, coef, n, pooled, step_fraction, version, as_of)
                  values ('london', 1, '{"intercept": 0.3}', 400, false, 1, 'station-mos:2026-09-27:a', '2026-09-27')`);
  for (const bad of [0, 1.5]) {
    let refused = false;
    try {
      await db.query(`insert into public.derived_mos_coefficients (city_key, lead_days, coef, n, pooled, step_fraction, version, as_of)
                      values ('paris', 1, '{}', 1, true, $1, 'v', '2026-09-27')`, [bad]);
    } catch (e) { refused = true; }
    assert.ok(refused, `step_fraction ${bad} accepted`);
  }
  await db.query(`insert into public.derived_mos_forecast (city_key, for_date, lead_days, mos_c, base_c, p39_c, blend_c, inputs, p39_version, version)
                  values ('london', '2026-09-28', 1, 21.8, 21.1, 22.0, 21.9, '{}', 'station-correction:2026-09-27:x', 'station-mos:2026-09-27:a')`);

  await db.exec(`update public.settings set value = jsonb_set(value, '{enabled}', 'true') where key = 'station_mos_pricing'`);
  await db.exec(MIG);
  assert.equal((await flag()).enabled, true, 're-runnable without resetting the switch');
  assert.equal((await db.query('select count(*)::int n from public.derived_mos_forecast')).rows[0].n, 1);

  for (const t of ['derived_mos_coefficients', 'derived_mos_forecast']) {
    const g = (await db.query(`select
        has_table_privilege('anon', 'public.${t}', 'select') a,
        has_table_privilege('authenticated', 'public.${t}', 'select') u,
        has_table_privilege('service_role', 'public.${t}', 'update') s`)).rows[0];
    assert.deepEqual([g.a, g.u, g.s], [false, false, true], `${t} grants`);
  }
  console.log('station-model: service role only, step fraction in (0, 1], the switch starts off and a re-run keeps it');
})().catch((e) => { console.error(e); process.exit(1); });
