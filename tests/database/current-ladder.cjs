// ===========================================================================
// THE CURRENT PREDICTION BETWEEN PRICING RUNS (plan v2.3 P4.9).
//
// publish_current_ladders keeps one ladder per city-day: a whole ladder or
// nothing, the newest wins, a resend writes nothing, one bad row never takes
// the rest of its batch down. v_current_prediction serves the newer of the
// newest pricing and that ladder, and anon reads it but not the table.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260929234500_the_current_prediction_between_pricing_runs.sql');

const M1 = '00000000-0000-0000-0000-0000000000a1';
const M2 = '00000000-0000-0000-0000-0000000000a2';
const OLD = '00000000-0000-0000-0000-0000000000a3';
const B = (i) => `00000000-0000-0000-0000-00000000000${i}`;   // bands b1..b3 of M1
const C = (i) => `00000000-0000-0000-0000-0000000000c${i}`;   // bands c1..c2 of M2
const V = '00000000-0000-0000-0000-0000000000f1';

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.markets (market_id uuid primary key, city_key text not null, resolution_date date);
    create table public.bands (band_id uuid primary key, market_id uuid not null, band_lo numeric, band_hi numeric,
                               open_low boolean default false, open_high boolean default false);
    create view public.v_canonical_bands as select * from public.bands;
    create table public.model_versions (version_id uuid primary key, label text);
    create table public.band_probabilities (prob_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null, raw_prob numeric, calibrated_prob numeric, centre_c numeric,
      sigma_c numeric, observed_floor_c numeric, forecast_version uuid);
    insert into public.markets values ('${M1}', 'nyc', current_date), ('${M2}', 'paris', current_date + 1),
                                      ('${OLD}', 'rome', current_date - 5);
    insert into public.bands (band_id, market_id, band_lo, band_hi, open_low, open_high) values
      ('${B(1)}', '${M1}', null, 20, true, false), ('${B(2)}', '${M1}', 20, 21, false, false),
      ('${B(3)}', '${M1}', 21, null, false, true),
      ('${C(1)}', '${M2}', null, 15, true, false), ('${C(2)}', '${M2}', 15, null, false, true),
      ('00000000-0000-0000-0000-0000000000d1', '${OLD}', null, null, true, true);
    insert into public.model_versions values ('${V}', 'station_correction:test');
    -- M1 priced twice; the newest instant (2 h ago) is the pricing the view serves
    insert into public.band_probabilities (band_id, computed_at, raw_prob, calibrated_prob, centre_c, sigma_c, observed_floor_c, forecast_version) values
      ('${B(1)}', now() - interval '6 hours', 0.1, 0.1, 20.5, 1, null, '${V}'),
      ('${B(2)}', now() - interval '6 hours', 0.8, 0.8, 20.5, 1, null, '${V}'),
      ('${B(3)}', now() - interval '6 hours', 0.1, 0.1, 20.5, 1, null, '${V}'),
      ('${B(1)}', now() - interval '2 hours', 0.2, 0.2, 20.4, 1, 19.0, '${V}'),
      ('${B(2)}', now() - interval '2 hours', 0.7, 0.7, 20.4, 1, 19.0, '${V}'),
      ('${B(3)}', now() - interval '2 hours', 0.1, 0.1, 20.4, 1, 19.0, '${V}'),
      ('00000000-0000-0000-0000-0000000000d1', now() - interval '5 days', 1, 1, 20, 1, null, '${V}');
  `);
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // re-runnable

  const pub = async (rows) => (await db.query('select public.publish_current_ladders($1::jsonb) r',
    [JSON.stringify(rows)])).rows[0].r;
  const row = (over = {}) => ({
    city_key: 'nyc', target_date: null, market_id: M1, priced_at: null, reason: 'station_max',
    engine_version: 'e1', priced_from: 'station_correction:test', centre_c: 21.2, sigma_c: 0.8,
    observed_floor_c: 21.0, ladder: { [B(1)]: 0.0, [B(2)]: 0.3, [B(3)]: 0.7 }, ...over,
  });
  const today = (await db.query("select current_date::text d, (now() - interval '1 hour') h1, (now() - interval '30 minutes') h05, (now() - interval '3 hours') h3")).rows[0];
  const base = { target_date: today.d };

  // --- served before anything is published: the newest pricing instant only
  const view = async (m) => (await db.query(
    `select band_id::text, prob::float8 p, source, is_top, checkpoint, priced_from, observed_floor_c::float8 f
       from public.v_current_prediction where market_id = $1 order by band_id`, [m])).rows;
  let v = await view(M1);
  assert.deepEqual(v.map((r) => [r.p, r.source]), [[0.2, 'pricing'], [0.7, 'pricing'], [0.1, 'pricing']]);
  assert.deepEqual(v.map((r) => r.is_top), [false, true, false]);
  assert.equal(v[0].priced_from, 'station_correction:test');
  assert.equal((await view(OLD)).length, 0, 'a market five days gone is not current, priced or not');
  assert.equal((await view(M2)).length, 0, 'nothing priced, nothing held: nothing served');

  // --- a whole ladder, newer than the pricing: written and served
  let r = await pub([row({ ...base, priced_at: today.h1 })]);
  assert.deepEqual([r.rows, r.written, r.not_newer, r.refused.length], [1, 1, 0, 0]);
  v = await view(M1);
  assert.deepEqual(v.map((x) => [x.p, x.source]), [[0, 'station_max'], [0.3, 'station_max'], [0.7, 'station_max']]);
  assert.deepEqual(v.map((x) => x.is_top), [false, false, true]);
  assert.equal(v[0].f, 21.0);

  // --- the engine's ladders are rounded to six places: 1 within 1e-4 is a ladder
  r = await pub([row({ ...base, priced_at: today.h3, ladder: { [B(1)]: 0.000003, [B(2)]: 0.300003, [B(3)]: 0.700003 } })]);
  assert.deepEqual([r.refused.length, r.not_newer], [0, 1], 'a rounded ladder is a ladder (and this one is older)');

  // --- the same ladder again writes nothing; an older one writes nothing
  r = await pub([row({ ...base, priced_at: today.h1 })]);
  assert.deepEqual([r.written, r.not_newer], [0, 1]);
  r = await pub([row({ ...base, priced_at: today.h3, ladder: { [B(1)]: 1, [B(2)]: 0, [B(3)]: 0 } })]);
  assert.deepEqual([r.written, r.not_newer], [0, 1]);
  assert.equal((await view(M1)).find((x) => x.is_top).band_id, B(3), 'the newest still stands');

  // --- a newer checkpoint ladder replaces it, and must name its checkpoint
  r = await pub([row({ ...base, priced_at: today.h05, reason: 'checkpoint' })]);
  assert.equal(r.refused[0].why, 'a checkpoint ladder must name its checkpoint');
  r = await pub([row({ ...base, priced_at: today.h05, reason: 'checkpoint', checkpoint: 'noon',
    ladder: { [B(1)]: 0.1, [B(2)]: 0.45, [B(3)]: 0.45 } })]);
  assert.equal(r.written, 1);
  v = await view(M1);
  assert.equal(v[0].source, 'checkpoint'); assert.equal(v[0].checkpoint, 'noon');
  assert.equal(v.find((x) => x.is_top).band_id, B(2), 'a tie goes to the lower band_id');
  const held = (await db.query(`select top_band_id::text t from public.current_ladders where city_key = 'nyc'`)).rows[0];
  assert.equal(held.t, B(2));

  // --- a pricing newer than the held ladder is served instead
  await db.exec(`insert into public.band_probabilities (band_id, computed_at, raw_prob, calibrated_prob, forecast_version)
    values ('${B(1)}', now() - interval '1 minute', 0.05, 0.05, '${V}'), ('${B(2)}', now() - interval '1 minute', 0.05, 0.05, '${V}'),
           ('${B(3)}', now() - interval '1 minute', 0.9, 0.9, '${V}')`);
  v = await view(M1);
  assert.deepEqual(v.map((x) => x.source), ['pricing', 'pricing', 'pricing']);
  assert.deepEqual(v.map((x) => x.p), [0.05, 0.05, 0.9]);

  // --- a whole ladder or nothing; one bad row never takes the batch down
  const bad = [
    [{ ladder: { [B(1)]: 0.5, [B(2)]: 0.5 } }, 'not the whole ladder: every canonical band of the market, once'],
    [{ ladder: { [B(1)]: 0.2, [B(2)]: 0.3, [B(3)]: 0.4, [C(1)]: 0.1 } }, 'not the whole ladder: every canonical band of the market, once'],
    [{ ladder: { [B(1)]: 0.2, [B(2)]: 0.3, [B(3)]: 0.4 } }, 'the ladder sums to 0.9'],
    [{ ladder: { [B(1)]: -0.2, [B(2)]: 0.2, [B(3)]: 1.0 } }, 'a probability is not a number in [0, 1]'],
    [{ ladder: { [B(1)]: '0.2', [B(2)]: 0.3, [B(3)]: 0.5 } }, 'a probability is not a number in [0, 1]'],
    [{ ladder: [0.2, 0.3, 0.5] }, 'ladder must be an object of band_id: probability'],
    [{ city_key: 'paris' }, 'the market is another city-day'],
    [{ market_id: '00000000-0000-0000-0000-0000000000ff' }, 'no such market'],
    [{ reason: 'hunch' }, 'reason must be checkpoint or station_max'],
    [{ engine_version: '' }, 'engine_version required'],
  ];
  const later = (await db.query("select now() + interval '2 minutes' t")).rows[0].t;
  const batch = bad.map(([over]) => row({ ...base, priced_at: later, ...over }));
  batch.push(row({ city_key: 'paris', market_id: M2, target_date: null, priced_at: later,
    ladder: { [C(1)]: 0.25, [C(2)]: 0.75 } }));
  batch[batch.length - 1].target_date = (await db.query('select (current_date + 1)::text d')).rows[0].d;
  batch.push(row({ ...base, market_id: 'not-a-uuid', priced_at: later }));
  batch.push(row({ ...base, priced_at: (await db.query("select now() + interval '1 hour' t")).rows[0].t }));
  r = await pub(batch);
  assert.equal(r.rows, bad.length + 3);
  assert.equal(r.written, 1, 'the one good row is written');
  const whys = r.refused.map((x) => x.why);
  bad.forEach(([, why], i) => assert.equal(whys[i], why, `row ${i}`));
  assert.match(whys[bad.length], /^refused by the database: /);
  assert.equal(whys[bad.length + 1], 'priced_at missing or in the future');
  v = await view(M2);
  assert.deepEqual(v.map((x) => [x.p, x.source, x.is_top]), [[0.25, 'station_max', false], [0.75, 'station_max', true]],
    'a held ladder with no pricing is served');

  // --- who may do what
  const g = (await db.query(`select
      has_table_privilege('anon', 'public.current_ladders', 'select') anon_table,
      has_table_privilege('authenticated', 'public.current_ladders', 'select') auth_table,
      has_table_privilege('service_role', 'public.current_ladders', 'insert') svc_table,
      has_function_privilege('anon', 'public.publish_current_ladders(jsonb)', 'execute') anon_fn,
      has_function_privilege('authenticated', 'public.publish_current_ladders(jsonb)', 'execute') auth_fn,
      has_function_privilege('service_role', 'public.publish_current_ladders(jsonb)', 'execute') svc_fn,
      has_table_privilege('anon', 'public.v_current_prediction', 'select') anon_view`)).rows[0];
  assert.deepEqual(Object.values(g), [false, false, true, false, false, true, true]);

  console.log('current-ladder: a whole ladder or nothing, the newest wins, a resend writes nothing, one bad row '
    + 'refused alone; the view serves the newer of the pricing and the held ladder; anon reads the view only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
