// ===========================================================================
// THE ENGINE'S STATION-CORRECTED PATH ON THE DAY ITSELF, IN SHADOW
// (sd_corr:v1, docs/SD_CORR_PREREG.md): the variant table names each variant's
// own inputs and none of the other's; the contract reads the variant's own
// centre, width and provenance, and every row it returned before is unchanged;
// the registry holds sd_corr:v1 blind from its first row, written once; the
// browser sees a blinded call only while its city's day runs, and nothing
// under the page's views.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const ORDER = ['20260924000000_prediction_checkpoints.sql', '20260927090000_s10_shadow_observe.sql',
               '20261004180000_engine_variants_in_shadow.sql', '20261004190000_one_prediction_contract.sql',
               '20261004200000_the_page_reads_the_contract.sql'];
const SD = MIG('20261005090000_the_same_day_corrected_candidate.sql');

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
    create table public.cities (city_key text primary key, icao text, timezone text);
    insert into public.cities values ('london', 'EGLL', 'Europe/London'), ('baker', 'XXXX', 'Etc/GMT+12');
    create table public.fact_checkpoint_outcome (checkpoint_id uuid, city_key text, target_date date,
      winner_band_id text, settled_at timestamptz);
    create table public.v_canonical_bands (band_id uuid primary key, band_label text);
    revoke all on public.fact_checkpoint_outcome, public.v_canonical_bands from public, anon;
    insert into public.v_canonical_bands values
      ('00000000-0000-0000-0000-000000000001', '20°C'), ('00000000-0000-0000-0000-000000000002', '21°C'),
      ('00000000-0000-0000-0000-000000000003', '22°C');
  `);
  for (const f of ORDER) await db.exec(MIG(f));
  const one = async (sql, params = []) => (await db.query(sql, params)).rows;
  const B = (n) => `00000000-0000-0000-0000-00000000000${n}`;
  const ladder = (top) => JSON.stringify({ [B(1)]: top === 1 ? 0.6 : 0.2, [B(2)]: top === 2 ? 0.6 : 0.2, [B(3)]: top === 3 ? 0.6 : 0.2 });

  // What the contract holds before: one call of each kind.
  await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time,
      engine_version, model_path, probs, top_band_id, top_prob, centre_c, sigma_c, station, decided_at)
      values ('london', current_date - 1, 'noon', now(), 'git:abc', 'forecast', $1::jsonb, $2, 0.6, 21.0, 0.9, 'EGLL', now() - interval '1 day')`,
    [ladder(2), B(2)]);
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date - 1, 'noon', now(), 12, 'rd1:2026-09-25:f5372ebb05', 's10-contract-v1', $1::jsonb, $2, 0.6,
              21.5, 20.5, 22.5, 20.0, now() - interval '1 day')`, [ladder(2), B(2)]);
  await db.query(`insert into public.variant_shadow_checkpoints (city_key, target_date, checkpoint, variant, variant_version,
      engine_version, day_ahead_centre_c, day_ahead_sigma_c, day_ahead_priced_at, day_ahead_lead_days, floor_c, q_down, q_up,
      unit, probs, top_band_id, top_prob, decided_at, station)
      values ('london', current_date - 1, 'noon', 'da_floor', 'da_floor:v1', 'git:abc', 21.2, 1.4, now() - interval '2 days', 1,
              20.4, 0.03, 0.06, 'C', $1::jsonb, $2, 0.6, now() - interval '1 day', 'EGLL')`, [ladder(2), B(2)]);
  await db.exec('create table before_contract as select * from public.v_prediction_contract');

  await db.exec(SD);
  await db.exec(SD);   // re-runnable: columns, check, view and the blind event once

  // Every row the contract returned before comes back unchanged, and nothing else.
  const [d] = await one(`select
      (select count(*)::int from (select * from before_contract except all select * from public.v_prediction_contract) a) gone,
      (select count(*)::int from (select * from public.v_prediction_contract except all select * from before_contract) a) added,
      (select count(*)::int from before_contract) n`);
  assert.deepEqual(d, { gone: 0, added: 0, n: 3 }, 'the replaced contract returns the same rows');
  const [opts] = await one(`select reloptions from pg_class where oid = 'public.v_prediction_contract'::regclass`);
  assert.ok((opts.reloptions || []).includes('security_invoker=false'), 'still the owner\'s view, as part 2 made it');

  // An sd_corr row: the corrected inputs, none of the day-ahead call's.
  const ins = `insert into public.variant_shadow_checkpoints (city_key, target_date, checkpoint, variant, variant_version,
      engine_version, day_ahead_centre_c, day_ahead_sigma_c, day_ahead_priced_at,
      corrected_centre_c, corrected_width_c, corrected_version, corrected_width_version, corrected_computed_at,
      corrected_lead_days, corrected_n_sources, floor_c, q_down, q_up, unit, probs, top_band_id, top_prob, decided_at, station)
      values ($1, $2::date, 'noon', $3, $4, 'git:abc', $5, $6, $7::timestamptz,
              $8, $9, $10, $11, $12::timestamptz, 0, 7, 20.4, 0.03, 0.06, 'C', $13::jsonb, $14, 0.6, $15::timestamptz, 'EGLL')`;
  // a fixed past date for the constraint cases: never today or yesterday on any clock
  const ok = ['london', '2026-01-15', 'sd_corr', 'sd_corr:v1', null, null, null,
              21.6, 0.9, 'station-correction:2026-10-05:aaa0000001', 'station-width:2026-10-05:bbb0000001',
              '2026-10-05T05:02:00Z', ladder(2), B(2), '2026-10-05T11:36:00Z'];
  const with_ = (changes) => ok.map((x, j) => (j in changes ? changes[j] : x));
  await refused(db, ins, with_({ 4: 21.2, 5: 1.4, 6: '2026-10-04T20:40:00Z' }), 'an sd_corr row carrying a day-ahead call');
  await refused(db, ins, with_({ 7: null }), 'an sd_corr row without its centre');
  await refused(db, ins, with_({ 8: null }), 'an sd_corr row without a width');
  await refused(db, ins, with_({ 8: 0 }), 'a width of zero');
  await refused(db, ins, with_({ 9: null }), 'no correction version');
  await refused(db, ins, with_({ 9: 'station-mos:2026-10-05:ccc' }), 'a version that is not the station correction\'s');
  await refused(db, ins, with_({ 10: 'W2 per-city width' }), 'a width version that is not a nightly fit\'s');
  await refused(db, ins, with_({ 11: '2026-10-05T11:37:00Z' }), 'a corrected row computed after the decision');
  await refused(db, ins, with_({ 11: null }), 'no computation time');
  await db.query(ins, ok);
  // da_floor keeps its own inputs, and only those
  await refused(db, ins, with_({ 0: 'paris', 2: 'da_floor', 3: 'da_floor:v1', 4: 21.2, 5: 1.4, 6: '2026-10-04T20:40:00Z' }),
                'a da_floor row with its day-ahead call and corrected inputs beside it');
  await refused(db, ins, with_({ 0: 'paris', 2: 'da_floor', 3: 'da_floor:v1', 7: null, 8: null, 9: null, 10: null, 11: null }),
                'a da_floor row without its day-ahead call');
  await db.query(ins, with_({ 0: 'paris', 2: 'da_floor', 3: 'da_floor:v1', 4: 21.2, 5: 1.4, 6: '2026-10-04T20:40:00Z',
                              7: null, 8: null, 9: null, 10: null, 11: null }));
  await refused(db, `update public.variant_shadow_checkpoints set corrected_width_c = 1.0`, [], 'an edit');

  // The contract reads the variant's own centre, width and provenance.
  const [c] = await one(`select model_family, artifact_version, serving_role, priced_centre_c, uncertainty_c, uncertainty_kind,
                                input_provenance from public.v_prediction_contract
                          where recorded_in = 'variant_shadow_checkpoints' and artifact_version = 'sd_corr:v1'`);
  assert.deepEqual([c.model_family, c.artifact_version, c.serving_role, Number(c.priced_centre_c), Number(c.uncertainty_c),
                    c.uncertainty_kind], ['engine_variant', 'sd_corr:v1', 'shadow', 21.6, 0.9, 'sigma']);
  assert.deepEqual(Object.keys(c.input_provenance).sort(),
                   ['corrected_computed_at', 'corrected_lead_days', 'corrected_n_sources', 'corrected_version',
                    'corrected_width_version', 'floor_c', 'q_down', 'q_up', 'served_calibrated']);
  assert.equal(c.input_provenance.corrected_version, 'station-correction:2026-10-05:aaa0000001');

  // Blind from its first row, written once.
  const reg = await one(`select state, blind, decided_by from public.v_model_registry
                          where family = 'engine_variant' and version = 'sd_corr:v1'`);
  assert.deepEqual(reg, [{ state: 'shadow', blind: true, decided_by: 'docs/SD_CORR_PREREG.md' }]);
  assert.equal((await one(`select count(*)::int n from public.model_registry where version = 'sd_corr:v1'`))[0].n, 1);

  // The browser: a blinded call while its city's day runs, none of a past day, nothing under the views.
  await db.query(ins, with_({ 0: 'baker', 1: (await one(`select (now() at time zone 'Etc/GMT+12')::date::text d`))[0].d,
                              11: new Date(Date.now() - 3600e3).toISOString(), 14: new Date().toISOString() }));
  await db.query(ins, with_({ 1: (await one(`select (current_date - 1)::text d`))[0].d,
                              11: new Date(Date.now() - 30 * 3600e3).toISOString(),
                              14: new Date(Date.now() - 24 * 3600e3).toISOString() }));
  await db.exec('set role anon');
  const seen = (await db.query(`select city_key, current_date - target_date days_ago, blind, withheld, top_label, hit
                                  from public.v_prediction_lineup where version = 'sd_corr:v1' order by city_key`)).rows;
  const by = { baker: seen.find((r) => r.city_key === 'baker'),
               london: seen.find((r) => r.city_key === 'london' && r.days_ago === 1) };
  assert.deepEqual([by.baker.blind, by.baker.withheld, by.baker.top_label, by.baker.hit], [true, false, '21°C', null],
                   'shown while the city\'s day runs, with no outcome');
  assert.deepEqual([by.london.blind, by.london.withheld, by.london.top_label, by.london.hit], [true, true, null, null],
                   'a past call of a blinded version is withheld');
  for (const t of ['variant_shadow_checkpoints', 'v_prediction_contract', 'model_registry']) {
    await assert.rejects(db.query(`select 1 from public.${t} limit 1`), /permission denied/, t);
  }
  await db.exec('reset role');
  console.log('sd-corr-shadow: each variant names its own inputs and none of the other\'s; the replaced contract returns every earlier row unchanged and reads sd_corr\'s centre, width and provenance; sd_corr:v1 blind from its first row, once; anon sees a blinded call only while its day runs; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
