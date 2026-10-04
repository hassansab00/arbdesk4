// ===========================================================================
// DECISIONS NAME THEIR CALL, AND NO REFIT SERVES UNRECORDED (P2.2 part 3).
//   v_decision_prediction resolves every decision to the call it acted on and
//   says how (recorded, checkpoint, same_tick, not_recorded, no_call,
//   signal_path); the two new columns are named together; decisions stay
//   append-only; anon reads none of it.
//   record_model_versions() records each nightly version in the state its
//   switch gives, retires what it supersedes (yesterday's fit, or the same fit
//   at a horizon the switch no longer gives), names the rollback, registers a
//   new shadow version, and is idempotent.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const ORDER = ['20260924000000_prediction_checkpoints.sql', '20260925090000_decision_log.sql',
               '20260926120000_station_correction.sql', '20260926150000_the_honest_station_model.sql',
               '20260927090000_s10_shadow_observe.sql', '20260927190000_the_width_around_the_corrected_centre.sql',
               '20261004180000_engine_variants_in_shadow.sql', '20261004190000_one_prediction_contract.sql',
               '20261004200000_the_page_reads_the_contract.sql'];
const PART3 = MIG('20261004210000_decisions_name_their_call.sql');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.cities (city_key text primary key, icao text, timezone text);
    insert into public.cities values ('london', 'EGLL', 'Europe/London');
    create table public.fact_checkpoint_outcome (checkpoint_id uuid, city_key text, target_date date,
      winner_band_id text, settled_at timestamptz);
    create table public.v_canonical_bands (band_id uuid primary key, band_label text);
    create table public.strategies (strategy_id text primary key);
    insert into public.strategies values ('s10_winner'), ('s11_ladder'), ('s1_buy_low_sell_signal');
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values
      ('station_correction_pricing', '{"enabled": true, "min_lead_days": 1}'),
      ('station_mos_pricing', '{"enabled": true, "min_lead_days": 1}'),
      ('station_width_pricing', '{"enabled": false, "max_lead_days": 1}'),
      ('calibration_map', '{"T": 1.141003, "applies": false, "fitted_at": "2026-09-29T04:59:20Z", "settlement_dates": 16}');
  `);
  for (const f of ORDER) await db.exec(MIG(f));
  // The nightly fits' current versions, as the derived tables hold them.
  // Fill only the NOT NULL columns each table demands (read from the table, not restated).
  const put = async (table, version) => {
    const need = (await db.query(`select column_name, data_type from information_schema.columns
                                   where table_name = $1 and is_nullable = 'NO' and column_default is null`, [table])).rows;
    const vals = need.map((c) => c.column_name === 'version' ? `'${version}'`
      : c.column_name === 'city_key' ? `'london'`
      : /timestamp/.test(c.data_type) ? 'now()' : c.data_type === 'date' ? 'current_date'
      : /int|numeric|real|double/.test(c.data_type) ? '1' : c.data_type === 'boolean' ? 'true'
      : c.data_type === 'jsonb' ? `'{}'` : `'x'`);
    await db.exec(`delete from public.${table}`);
    await db.exec(`insert into public.${table} (${need.map((c) => c.column_name).join(',')}) values (${vals.join(',')})`);
    const hasVersion = need.some((c) => c.column_name === 'version');
    if (!hasVersion) await db.exec(`update public.${table} set version = '${version}'`);
    await db.exec(`update public.${table} set computed_at = now(), as_of = current_date`);
  };
  await put('derived_station_correction', 'station-correction:d1:aaa');
  await put('derived_mos_coefficients', 'station-mos:d1:bbb');
  await put('derived_station_width', 'station-width:d1:ccc');

  await db.exec(PART3);
  await db.exec(PART3);   // re-runnable: constraints, view, function, events once

  // ---------------------------------------------------------------- the registry
  const latest = async () => Object.fromEntries((await db.query(
    `select family || '|' || version || '|' || horizon k, state, rollback_to, evidence from public.v_model_registry`)).rows
    .map((r) => [r.k, r]));
  let reg = await latest();
  assert.equal(reg['station_correction|station-correction:d1:aaa|lead >= 1'].state, 'served', 'served by Hassan\'s decision');
  assert.equal(reg['station_mos|station-mos:d1:bbb|lead >= 1'].state, 'served');
  assert.equal(reg['station_width|station-width:d1:ccc|lead <= 1'].state, 'shadow', 'the width switch is off');
  assert.equal(reg['calibration|temperature:T=1.141|all checkpoints'].state, 'fitted', 'the name part 1 seeded: no new event');
  assert.equal((await db.query(`select count(*)::int n from public.model_registry where family = 'calibration'`)).rows[0].n, 1);
  assert.ok(/cells, as of .* settings.station_correction_pricing.enabled = true/.test(
    reg['station_correction|station-correction:d1:aaa|lead >= 1'].evidence));
  const run = async () => (await db.query('select public.record_model_versions() r')).rows[0].r;
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'idempotent');

  // The next night: a new correction fit serves, yesterday's retires, and the rollback is named.
  await put('derived_station_correction', 'station-correction:d2:ddd');
  let r = await run();
  assert.deepEqual([r.appended, r.retired], [1, 1]);
  reg = await latest();
  assert.equal(reg['station_correction|station-correction:d2:ddd|lead >= 1'].state, 'served');
  assert.equal(reg['station_correction|station-correction:d2:ddd|lead >= 1'].rollback_to, 'station-correction:d1:aaa');
  assert.equal(reg['station_correction|station-correction:d1:aaa|lead >= 1'].state, 'retired');
  assert.ok(reg['station_correction|station-correction:d1:aaa|lead >= 1'].evidence.startsWith('superseded by station-correction:d2:ddd'));

  // The horizon moves: the same fit at the new horizon, and retired at the old one.
  await db.exec(`update public.settings set value = value || '{"min_lead_days": 2}' where key = 'station_correction_pricing'`);
  r = await run();
  reg = await latest();
  assert.equal(reg['station_correction|station-correction:d2:ddd|lead >= 2'].state, 'served');
  assert.equal(reg['station_correction|station-correction:d2:ddd|lead >= 1'].state, 'retired', 'noon does not authorise the morning');

  // A switch turned on is a new state for the same version; turned off, fitted.
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_width_pricing'`);
  await db.exec(`update public.settings set value = value || '{"enabled": false}' where key = 'station_mos_pricing'`);
  r = await run();
  reg = await latest();
  assert.equal(reg['station_width|station-width:d1:ccc|lead <= 1'].state, 'served');
  assert.equal(reg['station_mos|station-mos:d1:bbb|lead >= 1'].state, 'fitted');

  // The MOS blend and the width are applied inside the correction's branch
  // (probability_engine): with the correction off, neither serves, whatever
  // its own switch says (review of #306).
  await db.exec(`update public.settings set value = value || '{"enabled": false}' where key = 'station_correction_pricing'`);
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_mos_pricing'`);
  r = await run();
  reg = await latest();
  assert.equal(reg['station_correction|station-correction:d2:ddd|lead >= 2'].state, 'fitted');
  assert.equal(reg['station_mos|station-mos:d1:bbb|lead >= 1'].state, 'fitted', 'its switch is on, the correction off');
  assert.equal(reg['station_width|station-width:d1:ccc|lead <= 1'].state, 'shadow', 'not served without the correction');
  assert.ok(/station correction is off, so it does not serve/.test(
    (await db.query(`select decided_by from public.v_model_registry where family = 'station_width'
                      and version = 'station-width:d1:ccc'`)).rows[0].decided_by));
  // An event is a change of state: the width moved (served -> shadow) and its
  // event says why; MOS was already fitted, so nothing new is written for it.
  assert.ok(/settings.station_correction_pricing.enabled = false/.test(
    (await db.query(`select evidence from public.v_model_registry where family = 'station_width'
                      and version = 'station-width:d1:ccc'`)).rows[0].evidence));
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_correction_pricing'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([reg['station_correction|station-correction:d2:ddd|lead >= 2'].state,
                    reg['station_mos|station-mos:d1:bbb|lead >= 1'].state,
                    reg['station_width|station-width:d1:ccc|lead <= 1'].state], ['served', 'served', 'served']);

  // A new calibration temperature: a new version, the old one retired.
  await db.exec(`update public.settings set value = value || '{"T": 1.2}' where key = 'calibration_map'`);
  r = await run();
  reg = await latest();
  assert.equal(reg['calibration|temperature:T=1.200|all checkpoints'].state, 'fitted');
  assert.equal(reg['calibration|temperature:T=1.141|all checkpoints'].state, 'retired');

  // A new S10 file in shadow is registered once; one already registered is not.
  const B = (n) => `00000000-0000-0000-0000-00000000000${n}`;
  const ladder = JSON.stringify({ [B(1)]: 0.2, [B(2)]: 0.6, [B(3)]: 0.2 });
  for (const v of ['rd1:2026-09-25:f5372ebb05', 'rd9:test:new']) {
    await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
        model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
        values ('london', current_date, 'morning', now(), 9, $1, 's10-contract-v1', $2::jsonb, $3, 0.6, 21, 20, 22, 19, now())`,
      [v, ladder, B(2)]);
  }
  r = await run();
  assert.equal(r.appended, 1);
  reg = await latest();
  assert.equal(reg['s10|rd9:test:new|same day'].state, 'shadow');
  assert.equal((await db.query(`select count(*)::int n from public.model_registry where version = 'rd1:2026-09-25:f5372ebb05'`)).rows[0].n, 1);
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'still idempotent');

  // ---------------------------------------------------------------- the decisions
  // One tick at T: the engine's noon call; S10's rd1 row 20 s later (same tick).
  // An older capture: the engine's prepeak call at T, but S10's row an hour before.
  const cp = (await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time,
      engine_version, model_path, probs, top_band_id, top_prob, centre_c, sigma_c, decided_at)
      values ('london', current_date, 'noon', now(), 'git:a', 'forecast', $1::jsonb, $2, 0.6, 21, 1, now() - interval '30 minutes')
      returning checkpoint_id`, [ladder, B(2)])).rows[0].checkpoint_id;
  const cp2 = (await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time,
      engine_version, model_path, probs, top_band_id, top_prob, centre_c, sigma_c, decided_at)
      values ('london', current_date, 'prepeak_1h', now(), 'git:a', 'forecast', $1::jsonb, $2, 0.6, 21, 1, now() - interval '30 minutes')
      returning checkpoint_id`, [ladder, B(2)])).rows[0].checkpoint_id;
  const s10 = (await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date, 'noon', now(), 12, 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1', $1::jsonb, $2, 0.6, 21, 20, 22, 19,
              now() - interval '30 minutes' + interval '20 seconds') returning checkpoint_id`, [ladder, B(2)])).rows[0].checkpoint_id;
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date, 'prepeak_1h', now(), 13, 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1', $1::jsonb, $2, 0.6, 21, 20, 22, 19,
              now() - interval '90 minutes')`, [ladder, B(2)]);
  const dec = async (run, sid, checkpoint, reason, pred) => (await db.query(
    `insert into public.decisions (run_id, decided_at, checkpoint_id, strategy_id, city_key, resolution_date, action,
       reason_code, prediction_id, prediction_source)
     values ($1, now() - interval '30 minutes', $2, $3, 'london', current_date, 'NONE', $4, $5, $6) returning decision_id`,
    [run, checkpoint, sid, reason, pred ? pred[0] : null, pred ? pred[1] : null])).rows[0].decision_id;
  const R = (n) => `10000000-0000-0000-0000-00000000000${n}`;
  const ids = {
    s11: await dec(R(1), 's11_ladder', cp, 'no_signal'),
    s10_same: await dec(R(2), 's10_winner', cp, 'own_rule_none'),
    s10_old: await dec(R(3), 's10_winner', cp2, 'own_rule_none'),
    s10_none: await dec(R(4), 's10_winner', cp, 'no_ladder'),
    old_path: await dec(R(5), 's1_buy_low_sell_signal', null, 'no_signal'),
    recorded: await dec(R(6), 's10_winner', cp, 'own_rule_wait', [s10, 's10_shadow_checkpoints']),
    no_cp: await dec(R(7), 's11_ladder', null, 'no_signal'),
  };
  const v = Object.fromEntries((await db.query(
    `select decision_id, link, recorded_in, prediction_id, engine_checkpoint_id from public.v_decision_prediction`)).rows
    .map((x) => [x.decision_id, x]));
  const is = (k, link, rec, pid) => assert.deepEqual([v[ids[k]].link, v[ids[k]].recorded_in, v[ids[k]].prediction_id],
                                                     [link, rec, pid], k);
  is('s11', 'checkpoint', 'prediction_checkpoints', cp);
  is('s10_same', 'same_tick', 's10_shadow_checkpoints', s10);
  is('s10_old', 'not_recorded', null, null);
  is('s10_none', 'no_call', null, null);
  is('old_path', 'signal_path', null, null);
  is('recorded', 'recorded', 's10_shadow_checkpoints', s10);
  is('no_cp', 'no_call', null, null);
  assert.equal(v[ids.s10_same].engine_checkpoint_id, cp, 'the tick\'s checkpoint stays named beside it');

  // The two columns are named together; decisions stay append-only.
  await assert.rejects(db.query(`insert into public.decisions (run_id, decided_at, strategy_id, city_key, resolution_date,
      action, reason_code, prediction_id) values ($1, now(), 's11_ladder', 'london', current_date, 'NONE', 'x', $2)`,
    [R(8), cp]), /decisions_prediction_named/);
  await assert.rejects(db.query(`insert into public.decisions (run_id, decided_at, strategy_id, city_key, resolution_date,
      action, reason_code, prediction_id, prediction_source) values ($1, now(), 's11_ladder', 'london', current_date, 'NONE', 'x', $2, 'elsewhere')`,
    [R(9), cp]), /decisions_prediction_source/);
  await assert.rejects(db.query(`update public.decisions set prediction_id = $1 where decision_id = $2`, [cp, ids.s10_old]));

  // anon reads none of it.
  await db.exec('set role anon');
  for (const t of ['v_decision_prediction', 'decisions', 'model_registry']) {
    await assert.rejects(db.query(`select 1 from public.${t} limit 1`), /permission denied/, t);
  }
  await assert.rejects(db.query('select public.record_model_versions()'), /permission denied/);
  await db.exec('reset role');

  console.log('decision-prediction: every decision resolves to the call it acted on (recorded, checkpoint, same_tick, not_recorded, no_call, signal_path); each nightly version recorded in its switch state, the superseded one retired (a moved horizon too), the rollback named, a new shadow version registered; idempotent; anon reads none of it');
})().catch((e) => { console.error(e); process.exit(1); });
