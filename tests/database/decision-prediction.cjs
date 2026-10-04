// ===========================================================================
// DECISIONS NAME THEIR CALL, AND NO REFIT SERVES UNRECORDED (P2.2 part 3).
//   v_decision_prediction resolves every decision to the call it acted on and
//   says how (recorded, checkpoint, same_tick, not_recorded, no_call,
//   signal_path); the two new columns are named together; decisions stay
//   append-only; anon reads none of it.
//   record_model_versions() records each nightly version in the state its
//   switches, its prices and its forward rows' age give, retires what it supersedes (in the order
//   the versions first priced, so the page's newest retired is the newer
//   fit) and an older fit that never priced, names the rollback, registers a
//   new shadow version, and is idempotent. v_learning_status marks each
//   family's newest retired version and counts the rest for the page.
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
    -- what the engine priced: each price's forecast label (sql/, not migrations)
    create table public.model_versions (version_id uuid primary key default gen_random_uuid(), kind text, label text,
      created_at timestamptz default now());
    create table public.band_probabilities (prob_id bigserial primary key, band_id uuid, computed_at timestamptz not null,
      forecast_version uuid);
    insert into public.settings values
      ('station_correction_pricing', '{"enabled": true, "min_lead_days": 1}'),
      ('station_mos_pricing', '{"enabled": true, "min_lead_days": 1}'),
      ('station_width_pricing', '{"enabled": false, "max_lead_days": 1}'),
      ('calibration_map', '{"T": 1.141003, "applies": false, "fitted_at": "2026-09-29T04:59:20Z", "settlement_dates": 16}');
  `);
  for (const f of ORDER) await db.exec(MIG(f));
  // A night's fit, in the forward rows the engine reads.
  const night = async (day, { corr, width, mos, mosFrom }) => {
    if (corr) {
      await db.query(`insert into public.derived_corrected_forecast (city_key, for_date, lead_days, combined_c, n_sources,
          sources, version, width_c, width_version, computed_at) values ('london', current_date + $1::int, 1, 20, 3, '{}', $2,
          case when $3::text is null then null else 1.2 end, $3, now())`, [day, corr, width || null]);
    }
    if (mos) {
      await db.query(`insert into public.derived_mos_forecast (city_key, for_date, lead_days, mos_c, base_c, blend_c, inputs,
          p39_version, version, computed_at) values ('london', current_date + $1::int, 1, 20, 20, 20, '{}', $2, $3, now())`,
        [day, mosFrom || corr, mos]);
    }
  };
  // A price the engine made, labelled as forecast_provenance labels it.
  const price = async (hoursAgo, corr, mos, width) => {
    const label = `station_correction:${corr}${mos ? '+' + mos : ''}${width ? '+' + width : ''}:open_meteo_forecast:2026-10-04T00:00`;
    let v = (await db.query('select version_id from public.model_versions where label = $1', [label])).rows[0];
    if (!v) v = (await db.query(`insert into public.model_versions (kind, label) values ('forecast', $1) returning version_id`, [label])).rows[0];
    await db.query(`insert into public.band_probabilities (band_id, computed_at, forecast_version)
                    values (gen_random_uuid(), now() - $1::numeric * interval '1 hour', $2)`, [hoursAgo, v.version_id]);
  };
  // Versions as the fits name them: family, date, hash.
  const C1 = 'station-correction:2026-10-01:aaa0000001', M1 = 'station-mos:2026-10-01:bbb0000001';
  const W1 = 'station-width:2026-10-01:ccc0000001';
  const C2 = 'station-correction:2026-10-02:aaa0000002', M2 = 'station-mos:2026-10-02:bbb0000002';
  const W2 = 'station-width:2026-10-02:ccc0000002';
  await night(1, { corr: C1, width: W1, mos: M1 });
  await price(5, C1, M1);
  await price(4, C1);                       // a city-day the blend did not reach

  await db.exec(PART3);
  await db.exec(PART3);   // re-runnable: constraints, view, function, events once

  // ---------------------------------------------------------------- the registry
  const latest = async () => Object.fromEntries((await db.query(
    `select family || '|' || version || '|' || horizon k, state, rollback_to, evidence, decided_by from public.v_model_registry`)).rows
    .map((r) => [r.k, r]));
  const st = (reg, k) => (reg[k] || {}).state;
  let reg = await latest();
  assert.equal(st(reg, `station_correction|${C1}|as priced`), 'served', 'priced, so served (Hassan\'s decision)');
  assert.equal(st(reg, `station_mos|${M1}|as priced`), 'served');
  assert.equal(st(reg, `station_width|${W1}|as priced`), 'shadow', 'never priced: the width switch is off');
  // Part 1's hand-seeded width row gives way to the recorded versions, once,
  // in the migration: one candidate per family (review of #306). Its seeded
  // event stays, and a second apply appends nothing.
  assert.deepEqual([st(reg, 'station_width|W2 per-city width|day ahead'), reg['station_width|W2 per-city width|day ahead'].evidence],
    ['retired', `the nightly station_width versions are recorded one by one from P2.2 part 3; the newest is ${W1}`]);
  assert.equal((await db.query(`select count(*)::int n from public.model_registry where version = 'W2 per-city width'`)).rows[0].n, 2,
               'its seeded event stays, the retirement appended once');
  assert.equal(reg[`station_width|${W1}|as priced`].decided_by, 'rule:nightly refit; the switch is off');
  assert.equal(st(reg, 'calibration|temperature:T=1.141|all checkpoints'), 'fitted', 'the name part 1 seeded: no new event');
  assert.equal((await db.query(`select count(*)::int n from public.model_registry where family = 'calibration'`)).rows[0].n, 1);
  assert.ok(/^priced 2 times, .* \(band_probabilities, prediction_checkpoints\); settings.station_correction_pricing.enabled = true, min_lead_days = 1$/.test(
    reg[`station_correction|${C1}|as priced`].evidence));
  const run = async () => (await db.query('select public.record_model_versions() r')).rows[0].r;
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'idempotent');
  // A candidate registered by hand in a nightly family is the hand's, not the
  // hourly function's: it stands (review of #306).
  await db.query(`insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
                  values ('station_correction', 'hand candidate', 'day ahead', 'shadow', 'hassan', 'registered by hand')`);
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'a hand-registered candidate is no event');
  assert.equal(st(await latest(), 'station_correction|hand candidate|day ahead'), 'shadow');

  // A fit that wrote its coefficients and nothing the engine reads is no event.
  const need = async (t) => (await db.query(`select column_name, data_type from information_schema.columns
      where table_name = $1 and is_nullable = 'NO' and column_default is null`, [t])).rows;
  for (const [t, v] of [['derived_station_correction', 'station-correction:d9:phantom'],
                        ['derived_mos_coefficients', 'station-mos:d9:phantom'],
                        ['derived_station_width', 'station-width:d9:phantom']]) {
    const cols = await need(t);
    const vals = cols.map((c) => c.column_name === 'version' ? `'${v}'` : c.column_name === 'city_key' ? `'london'`
      : /timestamp/.test(c.data_type) ? 'now()' : c.data_type === 'date' ? 'current_date'
      : /int|numeric|real|double/.test(c.data_type) ? '1' : c.data_type === 'boolean' ? 'true'
      : c.data_type === 'jsonb' ? `'{}'` : `'x'`);
    await db.exec(`insert into public.${t} (${cols.map((c) => c.column_name).join(',')}) values (${vals.join(',')})`);
    if (!cols.some((c) => c.column_name === 'version')) await db.exec(`update public.${t} set version = '${v}'`);
  }
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'a coefficient version alone is no event');
  assert.equal((await db.query(`select count(*)::int n from public.model_registry where version like '%phantom%'`)).rows[0].n, 0);

  // The next night's fit, before it prices: fitted, and the incumbent still served.
  await night(2, { corr: C2, width: W2, mos: M2 });
  let r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_mos|${M2}|as priced`),
                    st(reg, `station_width|${W2}|as priced`)], ['fitted', 'fitted', 'shadow']);
  assert.equal(reg[`station_correction|${C2}|as priced`].decided_by, 'rule:nightly refit; it has not priced yet');
  assert.equal(st(reg, `station_correction|${C1}|as priced`), 'served', 'the incumbent until the fit prices');
  // Yesterday's width never priced (its switch is off): superseded by the
  // newer fit, so one candidate stands, not one a night (review of #306).
  assert.deepEqual([st(reg, `station_width|${W1}|as priced`), reg[`station_width|${W1}|as priced`].evidence],
                   ['retired', `superseded by the newer fit ${W2} before it priced`]);
  assert.equal(r.retired, 1, 'only the superseded width: the served correction and MOS stand until the fit prices');

  // It prices: served, the rollback named, and yesterday's superseded.
  await price(1, C2, M2);
  r = await run();
  reg = await latest();
  assert.equal(st(reg, `station_correction|${C2}|as priced`), 'served');
  assert.equal(reg[`station_correction|${C2}|as priced`].rollback_to, C1);
  assert.equal(st(reg, `station_correction|${C1}|as priced`), 'retired');
  assert.ok(reg[`station_correction|${C1}|as priced`].evidence.startsWith(`superseded by ${C2}, first priced`));
  assert.deepEqual([st(reg, `station_mos|${M2}|as priced`), st(reg, `station_mos|${M1}|as priced`)], ['served', 'retired']);
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'no churn: a superseded version priced earlier stays retired');

  // Side by side: yesterday's fit still prices a city-day the new one did not
  // rewrite, after the new one began - both are served (review of #306).
  await price(0.5, C1, M1);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C1}|as priced`), st(reg, `station_correction|${C2}|as priced`)],
                   ['served', 'served']);
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0]);

  // A lead setting changes: no event. A price label names the version, not
  // the lead, so the nightly versions' horizon is "as priced" and the setting
  // goes in the evidence (review of #306).
  await db.exec(`update public.settings set value = value || '{"min_lead_days": 2}' where key = 'station_correction_pricing'`);
  r = await run();
  assert.deepEqual([r.appended, r.retired], [0, 0]);
  await price(0.3, C2, M2);
  r = await run();
  assert.deepEqual([r.appended, r.retired], [0, 0], 'a new price of a served version is no event either');

  // The width serves when it prices: its switch on, the label names it.
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_width_pricing'`);
  await price(0.2, C2, M2, W2);
  r = await run();
  reg = await latest();
  assert.equal(st(reg, `station_width|${W2}|as priced`), 'served');

  // A switch goes off: retired at once, though its prices are still in the
  // window - the engine can no longer use it (review of #306). On again: its
  // prices serve it again.
  await db.exec(`update public.settings set value = value || '{"enabled": false}' where key = 'station_correction_pricing'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), reg[`station_correction|${C2}|as priced`].evidence],
                   ['retired', 'settings.station_correction_pricing.enabled is off']);
  assert.deepEqual([st(reg, `station_mos|${M2}|as priced`), reg[`station_mos|${M2}|as priced`].evidence],
                   ['retired', 'station correction is off'], 'the blend serves only inside the correction');
  assert.equal(st(reg, `station_width|${W2}|as priced`), 'retired');
  // C1 and C2 served side by side and retire in one run, at one decided_at:
  // the newer fit (first priced later) is retired last, so it is the newest
  // retired version the page shows (review of #306).
  const ret = (await db.query(`select version, event_id from public.model_registry
                                 where family = 'station_correction' and state = 'retired'
                                   and decided_at = (select max(decided_at) from public.model_registry)
                                 order by event_id`)).rows.map((x) => x.version);
  assert.deepEqual(ret, [C1, C2], 'retired oldest fit first');
  const newest = async () => (await db.query(`select family, version, retired_in_family::int n from public.v_learning_status
                                               where newest_retired order by family`)).rows;
  assert.deepEqual((await newest()).find((x) => x.family === 'station_correction'),
                   { family: 'station_correction', version: C2, n: 2 });
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'switched off, nothing more to record');
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_correction_pricing'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_mos|${M2}|as priced`),
                    st(reg, `station_width|${W2}|as priced`)], ['served', 'served', 'served']);

  // The forward rows expire under the switch's max_age_hours: nothing can
  // price again, though the prices are still in the 36 h window (review of
  // #306). The width rides on the correction's rows, and a MOS row blends only
  // into a fresh correction row, so its own fresh rows do not keep it serving.
  await db.exec(`update public.settings set value = value || '{"max_age_hours": 24}' where key = 'station_correction_pricing'`);
  await db.exec(`update public.derived_corrected_forecast set computed_at = computed_at - interval '30 hours'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), reg[`station_correction|${C2}|as priced`].evidence],
    ['retired', 'its forward rows are older than 24 h (settings.station_correction_pricing.max_age_hours), so it cannot price']);
  assert.equal(st(reg, `station_width|${W2}|as priced`), 'retired', 'the width rides on the correction rows');
  assert.equal(st(reg, `station_mos|${M2}|as priced`), 'retired', 'no fresh correction row to blend into');
  assert.match(reg[`station_mos|${M2}|as priced`].evidence, /^no forward row it can price from/);
  assert.deepEqual([(await run()).appended, (await run()).retired], [0, 0], 'expired, nothing more to record');
  await db.exec(`update public.derived_corrected_forecast set computed_at = computed_at + interval '30 hours'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_mos|${M2}|as priced`),
                    st(reg, `station_width|${W2}|as priced`)], ['served', 'served', 'served'], 'fresh again: served again');
  // The MOS rows' own age, under their own switch.
  await db.exec(`update public.settings set value = value || '{"max_age_hours": 24}' where key = 'station_mos_pricing'`);
  await db.exec(`update public.derived_mos_forecast set computed_at = computed_at - interval '30 hours'`);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_mos|${M2}|as priced`)], ['served', 'retired']);
  await db.exec(`update public.derived_mos_forecast set computed_at = computed_at + interval '30 hours'`);
  r = await run();
  assert.equal(st(await latest(), `station_mos|${M2}|as priced`), 'served');

  // Not priced for 36 hours: nothing of it serves.
  await db.exec(`update public.band_probabilities set computed_at = computed_at - interval '40 hours'`);
  r = await run();
  reg = await latest();
  for (const k of [`station_correction|${C1}|as priced`, `station_correction|${C2}|as priced`, `station_mos|${M2}|as priced`,
                   `station_width|${W2}|as priced`]) {
    if (reg[k]) assert.equal(reg[k].state, 'retired', k);
  }
  assert.equal(reg[`station_correction|${C2}|as priced`].evidence, 'not priced in the last 36 h');
  assert.equal((await db.query(`select count(*)::int n from public.v_model_registry
                                  where state = 'served' and family like 'station\\_%'`)).rows[0].n, 0, 'no nightly version serves');
  // And priced again by the tick: the checkpoint's priced_from counts too.
  await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time, engine_version,
      model_path, probs, top_band_id, top_prob, centre_c, sigma_c, priced_from, decided_at)
      values ('london', current_date + 1, 'd1_eve', now(), 'git:a', 'forecast', '{"x": 1}', 'x', 1, 20, 1, $1, now())`,
    [`station_correction:${C2}+${M2}:open_meteo_forecast:2026-10-04T00:00`]);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_mos|${M2}|as priced`)], ['served', 'served']);

  // A same-day refit whose name sorts after C2's but which first priced
  // before C2's latest call: the two serve side by side, then retire in one
  // run. The order is by first price, not by name or scan order, so C2 (first
  // priced later) is the newest retired version (review of #306).
  const C3 = 'station-correction:2026-10-02:fff0000003';
  await night(3, { corr: C3 });             // a forward row to price from (review of #306)
  await price(0.1, C3);
  await price(0, C3);
  r = await run();
  reg = await latest();
  assert.deepEqual([st(reg, `station_correction|${C2}|as priced`), st(reg, `station_correction|${C3}|as priced`)], ['served', 'served']);
  await db.exec(`update public.settings set value = value || '{"enabled": false}' where key = 'station_correction_pricing'`);
  r = await run();
  assert.deepEqual((await db.query(`select version from public.model_registry
                                     where family = 'station_correction' and state = 'retired'
                                       and decided_at = (select max(decided_at) from public.model_registry)
                                     order by event_id`)).rows.map((x) => x.version), [C3, C2]);
  assert.equal((await newest()).find((x) => x.family === 'station_correction').version, C2);
  await db.exec(`update public.settings set value = value || '{"enabled": true}' where key = 'station_correction_pricing'`);
  r = await run();

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
  assert.equal(st(await latest(), 'station_correction|hand candidate|day ahead'), 'shadow',
               'every refit, switch, expiry and supersession above left the hand-registered candidate alone');

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

  // The page breaks ties between one run's events on event_id (review of #306).
  // The page asks for the standing rows and each family's newest retired one
  // (state.neq.retired or newest_retired); the view counts the rest.
  await db.exec('set role anon');
  assert.ok((await db.query('select event_id from public.v_learning_status order by event_id desc limit 1')).rows[0].event_id);
  const page = (await db.query(`select family, state, newest_retired, retired_in_family::int n from public.v_learning_status
                                 where state <> 'retired' or newest_retired`)).rows;
  const all = (await db.query(`select family, state from public.v_learning_status`)).rows;
  assert.equal(page.filter((x) => x.state !== 'retired').length, all.filter((x) => x.state !== 'retired').length,
               'every standing version is fetched');
  const fams = new Set(all.filter((x) => x.state === 'retired').map((x) => x.family));
  assert.equal(page.filter((x) => x.state === 'retired').length, fams.size, 'one retired row per family');
  assert.equal(page.filter((x) => x.newest_retired).reduce((n, x) => n + x.n, 0),
               all.filter((x) => x.state === 'retired').length, 'the counts cover every retired version');
  await db.exec('reset role');

  // anon reads none of it.
  await db.exec('set role anon');
  for (const t of ['v_decision_prediction', 'decisions', 'model_registry']) {
    await assert.rejects(db.query(`select 1 from public.${t} limit 1`), /permission denied/, t);
  }
  await assert.rejects(db.query('select public.record_model_versions()'), /permission denied/);
  await db.exec('reset role');

  console.log('decision-prediction: every decision resolves to the call it acted on (recorded, checkpoint, same_tick, not_recorded, no_call, signal_path); a nightly version is served while it prices and is not superseded (side by side too), retired when switched off, superseded or unpriced, oldest first; the unpriced fit recorded once with its reason, an older one retired; the newest retired per family and the counts for the page; idempotent; anon reads none of it');
})().catch((e) => { console.error(e); process.exit(1); });
