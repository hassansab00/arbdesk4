// ===========================================================================
// THE PAGE READS THE CONTRACT (P2.2 part 2): anon reads the learning status
// and the calls side by side through owner-rights views, never the tables
// under them. A blinded version (under a pre-registered test) shows today's
// calls and no past one, and never an outcome; an unblinding event shows
// them. The engine counts once per checkpoint, its first capture.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const ORDER = ['20260924000000_prediction_checkpoints.sql', '20260927090000_s10_shadow_observe.sql',
               '20261004180000_engine_variants_in_shadow.sql', '20261004190000_one_prediction_contract.sql'];
const PAGE = MIG('20261004200000_the_page_reads_the_contract.sql');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.cities (city_key text primary key, icao text, timezone text);
    -- two cities a day apart on the clock: whatever the hour UTC, one of them
    -- is on a different date from UTC (+14 from 10:00Z, -12 until 12:00Z)
    insert into public.cities values ('london', 'EGLL', 'Europe/London'),
      ('kiritimati', 'PLCH', 'Pacific/Kiritimati'), ('baker', 'XXXX', 'Etc/GMT+12');
    -- winner_band_id is text, as live (information_schema, 4 Oct)
    create table public.fact_checkpoint_outcome (checkpoint_id uuid, city_key text, target_date date,
      winner_band_id text, settled_at timestamptz);
    create table public.v_canonical_bands (band_id uuid primary key, band_label text);
    revoke all on public.fact_checkpoint_outcome, public.v_canonical_bands from public, anon;
    insert into public.v_canonical_bands values
      ('00000000-0000-0000-0000-000000000001', '20°C'), ('00000000-0000-0000-0000-000000000002', '21°C'),
      ('00000000-0000-0000-0000-000000000003', '22°C');
  `);
  for (const f of ORDER) await db.exec(MIG(f));
  await db.exec(PAGE);
  await db.exec(PAGE);   // re-runnable: the blinding events are written once

  const B = (n) => `00000000-0000-0000-0000-00000000000${n}`;
  const ladder = (top) => JSON.stringify({ [B(1)]: top === 1 ? 0.6 : 0.2, [B(2)]: top === 2 ? 0.6 : 0.2, [B(3)]: top === 3 ? 0.6 : 0.2 });
  await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time,
      engine_version, model_path, probs, top_band_id, top_prob, centre_c, sigma_c, station, decided_at)
      values ('london', current_date - 1, 'noon', now(), 'git:abc', 'forecast', $1::jsonb, $2, 0.6, 21.0, 0.9, 'EGLL', now() - interval '1 day')`,
    [ladder(2), B(2)]);
  // a second capture of the same checkpoint by a later engine version
  await db.query(`insert into public.prediction_checkpoints (city_key, target_date, checkpoint, local_decision_time,
      engine_version, model_path, probs, top_band_id, top_prob, centre_c, sigma_c, station, decided_at)
      values ('london', current_date - 1, 'noon', now(), 'git:def', 'forecast', $1::jsonb, $2, 0.6, 22.0, 0.9, 'EGLL', now() - interval '20 hours')`,
    [ladder(3), B(3)]);
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date - 1, 'noon', now(), 12, $1, 's10-contract-v1', $2::jsonb, $3, 0.6, 21.5, 20.5, 22.5, 20.0, now() - interval '1 day')`,
    ['rd1:2026-09-25:f5372ebb05', ladder(2), B(2)]);
  // rd3 today on the city's own clock (UTC-12): no winner yet, so the page shows it
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('baker', (now() at time zone 'Etc/GMT+12')::date, 'noon', now(), 12, $1, 's10-contract-v1', $2::jsonb, $3, 0.6,
              22.0, 21.0, 23.0, 20.0, now())`,
    ['rd3:2026-09-25:555719d4a1', ladder(1), B(1)]);
  // rd3 yesterday on the city's own clock (UTC+14): its day is over though no winner is banked
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('kiritimati', (now() at time zone 'Pacific/Kiritimati')::date - 1, 'noon', now(), 12, $1, 's10-contract-v1', $2::jsonb, $3, 0.6,
              22.0, 21.0, 23.0, 20.0, now() - interval '1 day')`,
    ['rd3:2026-09-25:555719d4a1', ladder(1), B(1)]);
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date - 1, 'noon', now(), 12, $1, 's10-contract-v1', $2::jsonb, $3, 0.6, 22.0, 21.0, 23.0, 20.0, now() - interval '1 day')`,
    ['rd3:2026-09-25:555719d4a1', ladder(3), B(3)]);
  await db.query(`insert into public.variant_shadow_checkpoints (city_key, target_date, checkpoint, variant, variant_version,
      engine_version, day_ahead_centre_c, day_ahead_sigma_c, day_ahead_priced_at, unit, probs, top_band_id, top_prob, decided_at)
      values ('london', current_date - 1, 'noon', 'da_floor', 'da_floor:v1', 'git:abc', 21.2, 1.4, now() - interval '2 days',
              'C', $1::jsonb, $2, 0.6, now() - interval '1 day')`, [ladder(2), B(2)]);
  await db.query(`insert into public.fact_checkpoint_outcome values (gen_random_uuid(), 'london', current_date - 1, $1, now())`, [B(2)]);
  // a past day whose winner is not banked yet: a blinded call is withheld all the same
  await db.query(`insert into public.s10_shadow_checkpoints (city_key, target_date, checkpoint, local_decision_time, model_hour,
      model_version, contract, probs, top_band_id, top_prob, median_c, q10_c, q90_c, running_max_c, decided_at)
      values ('london', current_date - 2, 'noon', now(), 12, $1, 's10-contract-v1', $2::jsonb, $3, 0.6, 22.0, 21.0, 23.0, 20.0, now() - interval '2 days')`,
    ['rd3:2026-09-25:555719d4a1', ladder(3), B(3)]);

  // Everything below is read as the browser reads it.
  await db.exec('set role anon');
  const lineup = (await db.query(`select city_key, current_date - target_date as days_ago, model_family, version, serving_role, top_label,
                                         blind, withheld, winner_band_id, winner_label, hit, prob_on_winner
                                    from public.v_prediction_lineup order by model_family, version, target_date`)).rows;
  assert.equal(lineup.length, 7, 'one engine row (the first capture), rd1, rd3 in london on two days and in two more cities, da_floor');
  const eng = lineup.filter((r) => r.model_family === 'engine');
  assert.equal(eng.length, 1, 'the second capture of a checkpoint is not a second call');
  assert.deepEqual([eng[0].serving_role, eng[0].top_label, eng[0].blind, eng[0].winner_label, eng[0].hit, Number(eng[0].prob_on_winner)],
                   ['served', '21°C', false, '21°C', true, 0.6], 'the first capture, graded');
  const rd1 = lineup.find((r) => r.version === 'rd1:2026-09-25:f5372ebb05');
  assert.deepEqual([rd1.blind, rd1.withheld, rd1.top_label, rd1.hit], [false, false, '21°C', true]);
  const unbanked = (await db.query(`select withheld, top_label from public.v_prediction_lineup
                                       where city_key = 'london' and target_date = current_date - 2`)).rows;
  assert.deepEqual(unbanked.map((r) => [r.withheld, r.top_label]), [[true, null]], 'a past day is withheld before its winner is banked');
  for (const v of ['rd3:2026-09-25:555719d4a1', 'da_floor:v1']) {
    const r = lineup.find((x) => x.version === v && x.city_key === 'london' && x.days_ago === 1);
    assert.equal(r.serving_role, 'shadow', v);
    assert.deepEqual([r.blind, r.withheld], [true, true], v);
    assert.deepEqual([r.top_label, r.winner_band_id, r.winner_label, r.hit, r.prob_on_winner], [null, null, null, null, null],
                     `${v}: a settled call of a blinded version is its score read early`);
  }
  // "today" and "past" are the city's own: the UTC date would get one of these two wrong at any hour
  const rd3Today = lineup.find((x) => x.version === 'rd3:2026-09-25:555719d4a1' && x.city_key === 'baker');
  assert.deepEqual([rd3Today.blind, rd3Today.withheld, rd3Today.top_label, rd3Today.winner_label, rd3Today.hit],
                   [true, false, '20°C', null, null], 'a blinded call shows while its city\'s day runs, with no outcome');
  const rd3Over = lineup.find((x) => x.version === 'rd3:2026-09-25:555719d4a1' && x.city_key === 'kiritimati');
  assert.deepEqual([rd3Over.withheld, rd3Over.top_label], [true, null], 'and is withheld once its city\'s day is over');

  const status = (await db.query(`select family, version, state, stage, blind from public.v_learning_status order by family, version`)).rows;
  assert.equal(status.length, 10, 'the nine of part 1 and the retired first S10 file');
  const st = Object.fromEntries(status.map((r) => [`${r.family}|${r.version}`, r]));
  assert.equal(st['engine|station-corrected forecast path'].stage, 'serving');
  assert.equal(st['calibration|temperature:T=1.141'].stage, 'candidate fitting');
  assert.deepEqual([st['engine_variant|da_floor:v1'].stage, st['engine_variant|da_floor:v1'].blind], ['evaluation', true]);
  assert.deepEqual([st['s10|rd1:2026-09-25:f5372ebb05'].stage, st['s10|rd1:2026-09-25:f5372ebb05'].blind], ['evaluation', false]);
  assert.deepEqual([st['s10|rd1:2026-09-25:389620c0d9'].state, st['s10|rd1:2026-09-25:389620c0d9'].stage], ['retired', 'retired']);

  // anon reads the page views and nothing under them
  for (const t of ['prediction_checkpoints', 's10_shadow_checkpoints', 'variant_shadow_checkpoints', 'model_registry',
                   'v_model_registry', 'v_prediction_contract']) {
    await assert.rejects(db.query(`select 1 from public.${t} limit 1`), /permission denied/, t);
  }
  await db.exec('reset role');

  // The first look unblinds with a new event; the page then shows the outcome.
  await db.query(`insert into public.model_registry (family, version, horizon, state, decided_by, evidence, blind)
                  values ('engine_variant', 'da_floor:v1', 'same day', 'shadow', 'test', 'first look done', false)`);
  await db.exec('set role anon');
  const after = (await db.query(`select blind, withheld, top_label, hit from public.v_prediction_lineup where version = 'da_floor:v1'`)).rows[0];
  assert.deepEqual([after.blind, after.withheld, after.top_label, after.hit], [false, false, '21°C', true]);
  await db.exec('reset role');
  console.log('prediction-lineup: anon reads the learning status and the calls side by side, never the tables under them; the engine once per checkpoint; a blinded version shows its calls while the city local day runs, no past one and no outcome, until a new event unblinds it; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
