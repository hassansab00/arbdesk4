// ===========================================================================
// HIT AND MISS SCORES ONLY FROZEN CALLS (migration 20260926090000).
//
// book_mark() holds a one-sided book's last trade inside the side that is
// quoted: live on 26 Sep, 970 ask-only books carried a last of 0.999 over an
// ask of 0.001, and that dead bucket became "the market's favourite". The
// bank function now uses it, and v_checkpoint_calls recomputes the market side
// of every banked row - including one banked under the old rule - from the
// stored books. v_checkpoint_scoreboard adds them up per checkpoint.
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
    create table public.v_venue_market_resolution (market_id uuid, city_key text, resolution_date date,
      resolution_state text, winning_band_id uuid, confirmed_at timestamptz);
    create table public.bands (band_id uuid primary key, band_label text);
  `);
  await db.exec(MIG('20260924000000_prediction_checkpoints.sql'));
  await db.exec(MIG('20260924020000_checkpoint_outcomes.sql'));

  const B = (n) => `00000000-0000-0000-0000-00000000000${n}`;
  await db.exec(`insert into public.bands values ('${B(1)}', '20'), ('${B(2)}', '21'), ('${B(3)}', '22')`);
  const ins = `insert into public.prediction_checkpoints
      (city_key, target_date, checkpoint, local_decision_time, engine_version, model_path,
       probs, top_band_id, top_prob, market, market_top_band_id)
      values ($1, $2, $3, '2026-09-24 12:00', 'v1', 'forecast', $4::jsonb, $5, $6, $7::jsonb, $8)`;
  const probs = JSON.stringify({ [B(1)]: 0.2, [B(2)]: 0.5, [B(3)]: 0.3 });
  // b1 is dead: offered at 0.001, last traded 0.999. b2 is two-sided at 0.9.
  // b3 is bid-only at 0.05 with a last of 0.01.
  const deadBook = JSON.stringify({
    [B(1)]: { bid: null, ask: 0.001, last: 0.999 },
    [B(2)]: { bid: 0.88, ask: 0.92, last: 0.11 },
    [B(3)]: { bid: 0.05, ask: null, last: 0.01 } });

  // Banked under the OLD rule first: the stored row calls b1 the favourite.
  await db.query(ins, ['nyc', '2026-09-24', 'noon', probs, B(2), 0.5, deadBook, B(1)]);
  await db.exec(`insert into public.v_venue_market_resolution values
    (gen_random_uuid(), 'nyc', '2026-09-24', 'confirmed', '${B(2)}', '2026-09-25 03:00+00'),
    (gen_random_uuid(), 'nyc', '2026-09-25', 'confirmed', '${B(2)}', '2026-09-26 03:00+00')`);
  assert.equal((await db.query('select public.bank_checkpoint_outcomes() n')).rows[0].n, 1);
  const old = (await db.query(`select market_top_band_id, market_hit from public.fact_checkpoint_outcome`)).rows[0];
  assert.equal(old.market_top_band_id, B(1), 'the old rule took the dead bucket');
  assert.equal(old.market_hit, false);

  await db.exec(MIG('20260926090000_hit_and_miss_scores_frozen_calls.sql'));

  // the rule itself
  const mark = async (b, a, l) => (await db.query('select public.book_mark($1, $2, $3) x', [b, a, l])).rows[0].x;
  near(await mark(null, 0.001, 0.999), 0.001, 'ask-only: last held at the ask');
  near(await mark(0.05, null, 0.01), 0.05, 'bid-only: last held at the bid');
  near(await mark(null, 0.2, 0.15), 0.15, 'a last inside the quote stands');
  near(await mark(0.88, 0.92, 0.11), 0.9, 'two-sided: the mid');
  assert.equal(await mark(null, null, null), null);
  assert.equal(await mark(null, 0.3, null), null, 'no trade, one side: no number');

  // banked under the NEW rule
  await db.query(ins, ['nyc', '2026-09-25', 'noon', probs, B(2), 0.5, deadBook, B(1)]);
  assert.equal((await db.query('select public.bank_checkpoint_outcomes() n')).rows[0].n, 1);
  const fresh = (await db.query(`select * from public.fact_checkpoint_outcome where target_date = '2026-09-25'`)).rows[0];
  assert.equal(fresh.market_top_band_id, B(2), 'the favourite is the 90c band');
  assert.equal(fresh.market_hit, true);
  assert.equal(fresh.market_complete, true);
  // marks 0.001, 0.9, 0.05: total 0.951
  const t = 0.951;
  near(fresh.market_prob_on_winner, 0.9 / t, 'market p on winner');
  near(fresh.brier_market, (0.001 / t) ** 2 + (0.9 / t - 1) ** 2 + (0.05 / t) ** 2, 'market Brier');

  // the view grades the old row by the new rule, and both rows alike
  const calls = (await db.query(`select * from public.v_checkpoint_calls order by for_date`)).rows;
  assert.equal(calls.length, 2);
  for (const c of calls) {
    assert.equal(c.market_band, '21');
    assert.equal(c.market_hit, true);
    near(c.market_price, 0.9, 'the favourite\'s mark');
    near(c.brier_market, fresh.brier_market, 'the same Brier on both');
    assert.equal(c.called_band, '21');
    assert.equal(c.winner_band, '21');
    assert.equal(c.hit, true);
    assert.equal(c.checkpoint_order, 3);
    assert.equal(c.after_peak, false);
  }

  const board = (await db.query(`select * from public.v_checkpoint_scoreboard order by city_key`)).rows;
  assert.deepEqual(board.map((r) => r.city_key), ['all', 'nyc']);
  const all = board[0];
  assert.equal(Number(all.city_days), 2);
  assert.equal(Number(all.model_hits), 2);
  assert.equal(Number(all.market_hits), 2);
  assert.equal(Number(all.market_scored), 2);
  near(all.model_claimed, 0.5, 'stated confidence');
  near(all.brier_model, 0.04 + 0.25 + 0.09, 'engine Brier');

  const g = (await db.query(`select
      has_table_privilege('anon','public.v_checkpoint_calls','select') c,
      has_table_privilege('anon','public.v_checkpoint_scoreboard','select') s,
      has_table_privilege('anon','public.fact_checkpoint_outcome','select') f`)).rows[0];
  assert.deepEqual([g.c, g.s, g.f], [true, true, false]);
  // and actually read as the browser: the view calls book_mark(), whose
  // EXECUTE is checked as the reader (live, anon was refused until granted)
  await db.exec('set role anon');
  assert.equal((await db.query('select count(*)::int n from public.v_checkpoint_calls')).rows[0].n, 2);
  assert.equal((await db.query('select count(*)::int n from public.v_checkpoint_scoreboard')).rows[0].n, 2);
  await db.exec('reset role');

  // re-runnable
  await db.exec(MIG('20260926090000_hit_and_miss_scores_frozen_calls.sql'));
  console.log('frozen-calls: one-sided books held inside their quote, old and new rows graded alike, scoreboard adds up');
})().catch((e) => { console.error(e); process.exit(1); });
