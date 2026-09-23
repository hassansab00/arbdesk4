// ===========================================================================
// OBSERVATION TRUST, SHRUNK TOWARD THE POOL (plan v2 P2.3).
//
// Runs the migration as shipped against a stand-in for v_settlement_agreement
// (a table with exactly the columns the refresh reads) and pins every rule-11
// property: the pooled prior, k = 20, the bounds, the 0.10 nightly step and
// the re-baseline on a new version.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260923170000_trust_shrunk_toward_the_pool.sql');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.cities(city_key text primary key, status text default 'active',
      observation_trust numeric);
    create table public.v_settlement_agreement(city_key text, with_a_reading int, agreed int,
      one_bucket_up int, one_bucket_down int);
    -- 40 ladders in all, 36 agree: pooled trust 0.9, q_up 0.05, q_down 0.025.
    insert into public.v_settlement_agreement values
      ('good', 20, 20, 0, 0),
      ('bad',  10,  6, 2, 1),
      ('new',  10, 10, 0, 0);
    insert into public.cities(city_key, observation_trust) values
      ('good', 0.5), ('bad', 0.99), ('new', null), ('unseen', null);
  `);

  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // its guarded refresh runs here

  const row = async (c) => (await db.query(
    `select observation_trust::float8 t, observation_q_up::float8 up, observation_q_down::float8 down,
            observation_trust_n n, observation_trust_version v from cities where city_key=$1`, [c])).rows[0];
  const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-4, `${msg}: ${a} vs ${b}`);

  // (hits + 20 * pooled) / (n + 20)
  let r = await row('bad');
  near(r.t, (6 + 20 * 0.9) / 30, 'bad trust');
  near(r.up, (2 + 20 * 0.05) / 30, 'bad q_up');
  near(r.down, (1 + 20 * 0.025) / 30, 'bad q_down');
  assert.equal(r.n, 10);
  assert.equal(r.v, 'beta_k20_pooled_v1');
  near((await row('good')).t, (20 + 18) / 40, 'good trust re-baselined from 0.5 in one step: a new version is not drift');
  assert.equal((await row('unseen')).t, null, 'a city with no ladder was invented a value');

  // Within one version, a night moves at most 0.10.
  await db.exec(`update public.v_settlement_agreement set agreed = 0 where city_key = 'good'`);
  const before = (await row('good')).t;
  await db.query('select public.refresh_observation_trust()');
  near(before - (await row('good')).t, 0.10, 'the nightly step was not capped at 0.10');

  // Bounds hold whatever the counts say.
  await db.exec(`update public.v_settlement_agreement set one_bucket_up = 10 where city_key = 'bad'`);
  await db.exec(`update public.cities set observation_trust_version = 'old' where city_key = 'bad'`);
  await db.query('select public.refresh_observation_trust()');
  assert.ok((await row('bad')).up <= 0.5, 'q_up escaped its bound');

  const g = (await db.query(`select has_function_privilege('anon','public.refresh_observation_trust()','execute') a`)).rows[0];
  assert.equal(g.a, false, 'anon can run the refresh');

  console.log('observation-trust: shrunk toward the pool (k=20), bounded, stepped at most 0.10 a night, re-based on a new version');
})().catch((e) => { console.error(e); process.exit(1); });
