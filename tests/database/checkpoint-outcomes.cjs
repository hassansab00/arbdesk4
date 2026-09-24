// ===========================================================================
// EVERY CHECKPOINT, SCORED ONCE THE VENUE HAS SPOKEN (plan v2 P4.3).
// bank_checkpoint_outcomes() writes one immutable row per checkpoint whose
// market the venue confirmed, with hit, market_hit and multiclass Brier and
// log loss for the engine, the market and a uniform guess - checked here
// against numbers worked by hand.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const near = (a, b, why) => assert.ok(Math.abs(Number(a) - b) < 1e-6, `${why}: ${a} != ${b}`);

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    -- the live view's shape, as a table
    create table public.v_venue_market_resolution (market_id uuid, city_key text, resolution_date date,
      resolution_state text, winning_band_id uuid, confirmed_at timestamptz);
  `);
  await db.exec(MIG('20260924000000_prediction_checkpoints.sql'));
  await db.exec(MIG('20260924020000_checkpoint_outcomes.sql'));

  const B = (n) => `00000000-0000-0000-0000-00000000000${n}`;
  const ins = `insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path,
       probs, top_band_id, top_prob, market, market_top_band_id)
      values ($1, $2, $3, '2026-09-24 12:00', 'v1', 'forecast', $4::jsonb, $5, $6, $7::jsonb, $8)`;
  const probs = JSON.stringify({ [B(1)]: 0.2, [B(2)]: 0.5, [B(3)]: 0.3 });
  const fullBook = JSON.stringify({
    [B(1)]: { bid: 0.18, ask: 0.22, last: 0.2 },
    [B(2)]: { bid: 0.58, ask: 0.62, last: 0.6 },
    [B(3)]: { bid: null, ask: null, last: 0.2 } });
  const halfBook = JSON.stringify({ [B(1)]: { bid: 0.5, ask: 0.6, last: 0.5 } });
  await db.query(ins, ['nyc', '2026-09-24', 'noon', probs, B(2), 0.5, fullBook, B(2)]);
  await db.query(ins, ['nyc', '2026-09-24', 'morning', probs, B(2), 0.5, halfBook, B(1)]);
  await db.query(ins, ['la', '2026-09-24', 'noon', probs, B(2), 0.5, null, null]);

  await db.exec(`insert into public.v_venue_market_resolution values
    (gen_random_uuid(), 'nyc', '2026-09-24', 'confirmed', '${B(2)}', '2026-09-25 03:00+00'),
    (gen_random_uuid(), 'la',  '2026-09-24', 'partial',   null,       null)`);

  assert.equal((await db.query('select public.bank_checkpoint_outcomes() n')).rows[0].n, 2,
    'the two nyc checkpoints; la is not confirmed');
  const row = async (city, cp) => (await db.query(
    `select * from public.fact_checkpoint_outcome where city_key = $1 and checkpoint = $2`, [city, cp])).rows[0];

  const noon = await row('nyc', 'noon');
  assert.equal(noon.hit, true);
  assert.equal(noon.ladder_has_winner, true);
  near(noon.model_prob_on_winner, 0.5, 'p on winner');
  near(noon.brier_model, 0.04 + 0.25 + 0.09, 'engine Brier');
  near(noon.log_loss_model, -Math.log(0.5), 'engine log loss');
  assert.equal(noon.market_complete, true);
  assert.equal(noon.market_hit, true);
  // mids 0.2, 0.6 and the last trade 0.2 where the book is empty: sum 1.0
  near(noon.market_prob_on_winner, 0.6, 'market p on winner');
  near(noon.brier_market, 0.04 + 0.16 + 0.04, 'market Brier');
  near(noon.log_loss_market, -Math.log(0.6), 'market log loss');
  near(noon.brier_uniform, 1 - 1 / 3, 'uniform Brier');
  near(noon.log_loss_uniform, Math.log(3), 'uniform log loss');
  assert.equal(noon.n_bands, 3);

  const morning = await row('nyc', 'morning');
  assert.equal(morning.market_complete, false, 'two bands had no price');
  assert.equal(morning.brier_market, null);
  assert.equal(morning.market_hit, false, 'the favourite is still known: b1, which lost');

  // la confirms later: the next call adds it and nothing else
  await db.exec(`update public.v_venue_market_resolution set resolution_state = 'confirmed',
                 winning_band_id = '${B(9)}', confirmed_at = now() where city_key = 'la'`);
  assert.equal((await db.query('select public.bank_checkpoint_outcomes() n')).rows[0].n, 1);
  assert.equal((await db.query('select public.bank_checkpoint_outcomes() n')).rows[0].n, 0, 'idempotent');
  const la = await row('la', 'noon');
  assert.equal(la.ladder_has_winner, false, 'the winner is not on the ladder');
  assert.equal(la.hit, false);
  near(la.brier_model, 0.04 + 0.25 + 0.09 + 1, "the missing winner's (0 - 1)^2 counts");
  near(la.log_loss_model, -Math.log(1e-6), 'floored, not infinite');
  assert.equal(la.market_hit, null, 'no book, no favourite');

  for (const sql of ['update public.fact_checkpoint_outcome set hit = true',
                     'delete from public.fact_checkpoint_outcome',
                     'truncate public.fact_checkpoint_outcome']) {
    let refused = false;
    try { await db.query(sql); } catch (e) { refused = true; }
    assert.ok(refused, `accepted: ${sql}`);
  }
  const g = (await db.query(`select
      has_table_privilege('anon','public.fact_checkpoint_outcome','select') a,
      has_table_privilege('service_role','public.fact_checkpoint_outcome','insert') s,
      has_function_privilege('anon','public.bank_checkpoint_outcomes()','execute') fa,
      has_function_privilege('service_role','public.bank_checkpoint_outcomes()','execute') fs`)).rows[0];
  assert.deepEqual([g.a, g.s, g.fa, g.fs], [false, true, false, true]);
  console.log('checkpoint-outcomes: scored against the venue winner by hand-worked numbers, idempotent, append-only, service role only');
})().catch((e) => { console.error(e); process.exit(1); });
