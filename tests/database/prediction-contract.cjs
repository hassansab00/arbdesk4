// ===========================================================================
// ONE PREDICTION CONTRACT AND A VERSION REGISTRY (P2.2 part 1): every recorded
// call - the engine's served checkpoint, S10's shadow ladder, the engine
// variant - reads back from v_prediction_contract in one shape with the right
// family, version, role, centre and uncertainty; the registry is append-only,
// its view reads the latest state, and the seed is written once; the browser
// reads none of it yet.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const BASE = ['20260924000000_prediction_checkpoints.sql', '20260927090000_s10_shadow_observe.sql',
              '20261004180000_engine_variants_in_shadow.sql'];
const CONTRACT = MIG('20261004190000_one_prediction_contract.sql');

async function refused(db, sql, params, why) {
  try { await db.query(sql, params); } catch (e) { return e.message; }
  assert.fail(`accepted, and should not have been: ${why}`);
}

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.cities (city_key text primary key, icao text);
    insert into public.cities values ('london', 'EGLC'), ('nyc', 'KLGA');
  `);
  for (const f of BASE) await db.exec(MIG(f));
  await db.exec(CONTRACT);
  await db.exec(CONTRACT);   // re-runnable: the seed is written once

  const one = async (sql, params = []) => (await db.query(sql, params)).rows;

  // The seed: nine families, each once, the engine served and S10 in shadow.
  const seed = await one(`select family, version, horizon, state from public.v_model_registry order by family, version`);
  assert.equal(seed.length, 9);
  assert.equal((await one('select count(*)::int n from public.model_registry'))[0].n, 9);
  const state = Object.fromEntries(seed.map((r) => [`${r.family}|${r.version}`, r.state]));
  assert.equal(state['engine|station-corrected forecast path'], 'served');
  assert.equal(state['s10|rd1:2026-09-25:f5372ebb05'], 'shadow');
  assert.equal(state['s10|rd3:2026-09-25:555719d4a1'], 'shadow');
  assert.equal(state['engine_variant|da_floor:v1'], 'shadow');
  assert.equal(state['calibration|temperature:T=1.141'], 'fitted');

  // A promotion is a new event, and the view reads the latest; the past stays.
  await db.query(`insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to)
                  values ('engine_variant', 'da_floor:v1', 'same day', 'eligible', 'test', 'a rehearsal', 'engine')`);
  assert.equal((await one(`select state from public.v_model_registry where family = 'engine_variant'`))[0].state, 'eligible');
  assert.equal((await one(`select count(*)::int n from public.model_registry where family = 'engine_variant'`))[0].n, 2);
  await refused(db, `update public.model_registry set state = 'served'`, [], 'an edit');
  await refused(db, `delete from public.model_registry`, [], 'a delete');
  await refused(db, `truncate public.model_registry`, [], 'a truncate');
  await refused(db, `insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
                     values ('x', 'y', 'z', 'promoted', 'test', 'e')`, [], "a state outside the plan's words");
  await refused(db, `insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
                     values ('x', 'y', 'z', 'shadow', 'test', '  ')`, [], 'a move without evidence');

  // One call of each kind, read back in one shape.
  await db.query(`insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path, probs, top_band_id, top_prob,
       centre_c, sigma_c, running_max_c, forecast_model, priced_from, raw_forecast_c, station, decided_at)
      values ('london', '2026-10-05', 'noon', '2026-10-05 12:00', 'git:abc', 'forecast', '{"b1":0.2,"b2":0.5,"b3":0.3}',
              'b2', 0.5, 21.8, 0.9, 20.4, 'open_meteo_forecast',
              'station_correction:station-correction:2026-10-04:da319e41d6:open_meteo_forecast', 21.2, 'EGLL',
              '2026-10-05T11:36:30Z')`);
  await db.query(`insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path, probs, top_band_id, top_prob,
       inputs_ok, block_reason)
      values ('nyc', '2026-10-05', 'noon', '2026-10-05 12:00', 'git:abc', 'forecast', '{"b1":0.5,"b2":0.5}',
              'b1', 0.5, false, 'stale_forecast')`);
  await db.query(`insert into public.s10_shadow_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, model_hour, model_version, contract,
       probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, inputs, decided_at)
      values ('london', '2026-10-05', 'noon', '2026-10-05 12:00', 12, 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1',
              '{"b1":0.1,"b2":0.6,"b3":0.3}', 'b2', 0.6, 21.5, 20.5, 23.0, 20.4, '{"hour": 12}', '2026-10-05T11:36:40Z')`);
  await db.query(`insert into public.variant_shadow_checkpoints
      (city_key, target_date, checkpoint, variant, variant_version, engine_version, day_ahead_centre_c, day_ahead_sigma_c,
       day_ahead_priced_at, floor_c, q_down, q_up, unit, probs, top_band_id, top_prob, station, decided_at)
      values ('london', '2026-10-05', 'noon', 'da_floor', 'da_floor:v1', 'git:abc', 21.2, 1.4, '2026-10-04T20:40:00Z',
              20.4, 0.02, 0.05, 'C', '{"b1":0.05,"b2":0.55,"b3":0.4}', 'b2', 0.55, 'EGLL', '2026-10-05T11:36:50Z')`);

  // nyc: the S10 row sits beside a served call from before 4 Oct (no station);
  // a later call that does carry one must not lend it its station.
  await db.query(`insert into public.s10_shadow_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, model_hour, model_version, contract,
       probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('nyc', '2026-10-05', 'noon', '2026-10-05 12:00', 12, 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1',
              '{"b1":0.5,"b2":0.5}', 'b1', 0.5, 18.0, 17.0, 19.0, 17.2, now() + interval '1 minute')`);
  await db.query(`insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path, probs, top_band_id, top_prob,
       station, decided_at)
      values ('nyc', '2026-10-05', 'noon', '2026-10-05 12:00', 'git:later', 'forecast', '{"b1":0.5,"b2":0.5}',
              'b1', 0.5, 'KJFK', now() + interval '1 hour')`);

  const rows = await one(`select * from public.v_prediction_contract where code_version is distinct from 'git:later'
                           order by recorded_in, city_key`);
  assert.equal(rows.length, 5);
  const by = Object.fromEntries(rows.map((r) => [`${r.model_family}|${r.city_key}`, r]));
  assert.deepEqual([by['s10|nyc'].station, by['s10|nyc'].station_source], ['KLGA', 'cities_now'],
    'a served call recorded after the S10 row lends it nothing');
  const eng = by['engine|london'];
  assert.equal(eng.serving_role, 'served');
  assert.equal(eng.artifact_version, 'station_correction:station-correction:2026-10-04:da319e41d6:open_meteo_forecast');
  assert.equal(eng.code_version, 'git:abc');
  // The station recorded with the call, not today's cities row (EGLC): a
  // corrected station must not rewrite the past.
  assert.deepEqual([eng.station, eng.station_source], ['EGLL', 'recorded']);
  assert.deepEqual([by['engine|nyc'].station, by['engine|nyc'].station_source], ['KLGA', 'cities_now'],
    'a call from before 4 Oct names the station as it stands now, and says so');
  assert.equal(Number(eng.raw_forecast_c), 21.2);
  assert.equal(Number(eng.priced_centre_c), 21.8);
  assert.equal(Number(eng.uncertainty_c), 0.9);
  assert.equal(eng.input_provenance.floor_c, 20.4);
  assert.equal(eng.fallback_state, 'forecast');
  assert.equal(by['engine|nyc'].fallback_state, 'stale_forecast', 'a blocked call names why');
  assert.equal(by['engine|nyc'].artifact_version, 'forecast', 'no priced_from and no model: the path, as before 4 Oct');
  const s10 = by['s10|london'];
  assert.deepEqual([s10.serving_role, s10.artifact_version, s10.code_version], ['shadow', 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1']);
  assert.equal(Number(s10.priced_centre_c), 21.5);
  assert.equal(Number(s10.uncertainty_c), Number((2.5 / 2.5631).toFixed(4)));
  assert.equal(s10.uncertainty_kind, 'q10_q90_as_sigma');
  assert.equal(s10.input_provenance.hour, 12);
  assert.deepEqual([s10.station, s10.station_source], ['EGLL', 'served_call'], 'S10 takes the served call\'s station');
  const v = by['engine_variant|london'];
  assert.deepEqual([v.serving_role, v.artifact_version], ['shadow', 'da_floor:v1']);
  assert.equal(Number(v.priced_centre_c), 21.2);
  assert.equal(v.input_provenance.q_down, 0.02);
  assert.deepEqual([v.station, v.station_source], ['EGLL', 'recorded']);
  for (const r of rows) assert.ok(r.prediction_id && r.as_of && r.probs && r.top_band_id, r.recorded_in);

  const g = (await one(`select
      has_table_privilege('anon', 'public.v_prediction_contract', 'select') a1,
      has_table_privilege('authenticated', 'public.v_model_registry', 'select') a2,
      has_table_privilege('anon', 'public.model_registry', 'select') a3,
      has_table_privilege('service_role', 'public.v_prediction_contract', 'select') s1,
      has_table_privilege('service_role', 'public.model_registry', 'insert') s2,
      has_table_privilege('service_role', 'public.model_registry', 'update') s3`))[0];
  assert.deepEqual([g.a1, g.a2, g.a3, g.s1, g.s2, g.s3], [false, false, false, true, true, false]);
  console.log('prediction-contract: engine, S10 and the variant read back in one shape (family, version, role, station, centre, uncertainty, provenance, fallback); the registry is append-only, reads the latest state, seeds once; service role only');
})().catch((e) => { console.error(e); process.exit(1); });
