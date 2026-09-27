// ===========================================================================
// A RERUN CANNOT STEP TWICE (plan v2.3 P5.14): the two learners that price
// live keep the value each row stepped from, dated before the row's own night,
// so a second run the same night steps from the same place. The anchor can
// never be dated on or after its row, and the migration re-runs cleanly.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const mig = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings(key text primary key, value jsonb not null,
      updated_at timestamptz not null default now());
  `);
  await db.exec(mig('20260926120000_station_correction.sql'));
  await db.exec(mig('20260926150000_the_honest_station_model.sql'));
  // a row written before this migration, as the 27 Sep runs wrote them
  await db.query(`insert into public.derived_station_correction
                    (city_key, source, lead_days, bias_c, pooled_bias_c, n, n_pooled, version, as_of)
                  values ('wuhan', 'ecmwf', 1, -2.5, 0.3, 40, 1900, 'station-correction:2026-09-27:a', '2026-09-27')`);
  const RERUN = mig('20260927180000_a_rerun_cannot_step_twice.sql');
  await db.exec(RERUN);
  await db.exec(RERUN);                                           // re-runnable
  assert.equal((await db.query('select count(*)::int n from public.derived_station_correction')).rows[0].n, 1,
    'the migration keeps every row');
  const old = (await db.query('select prev_bias_c, prev_as_of from public.derived_station_correction')).rows[0];
  assert.deepEqual([old.prev_bias_c, old.prev_as_of], [null, null], 'a row written before it has no anchor');

  // tonight's row, stepped from last night's value: accepted
  await db.query(`update public.derived_station_correction
                     set bias_c = -2.75, prev_bias_c = -2.5, prev_as_of = '2026-09-27', as_of = '2026-09-28'`);
  await db.query(`insert into public.derived_mos_coefficients
                    (city_key, lead_days, coef, n, pooled, step_fraction, version, as_of, prev_coef, prev_as_of)
                  values ('wuhan', 1, '{"intercept": -1.0}', 400, false, 1, 'station-mos:2026-09-28:b', '2026-09-28',
                          '{"intercept": -0.8}', '2026-09-27')`);
  // an anchor dated on or after its own night: refused, in both tables
  for (const [sql, what] of [
    [`update public.derived_station_correction set prev_as_of = '2026-09-28'`, 'station correction, same night'],
    [`update public.derived_station_correction set prev_as_of = '2026-09-29'`, 'station correction, a later night'],
    [`update public.derived_mos_coefficients set prev_as_of = '2026-09-28'`, 'station model, same night'],
  ]) {
    await assert.rejects(db.query(sql), /anchor_is_earlier/, what);
  }
  for (const t of ['derived_station_correction', 'derived_mos_coefficients']) {
    const g = (await db.query(`select has_table_privilege('anon', 'public.${t}', 'select') a,
                                      has_table_privilege('service_role', 'public.${t}', 'update') s`)).rows[0];
    assert.deepEqual([g.a, g.s], [false, true], `${t}: still the service role's alone`);
  }
  console.log('rerun-anchor: each row keeps the value it stepped from, dated before its own night; re-runnable, grants unchanged');
})().catch((e) => { console.error(e); process.exit(1); });
