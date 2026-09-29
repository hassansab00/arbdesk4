// ===========================================================================
// THE MARKET SCORES BANKED BEFORE THE FIX (audit repair 3, 29 Sep).
//
// fact_checkpoint_outcome is immutable, and its rows banked before
// book_mark() scored the market on a one-sided book's last trade. The fix
// table holds those rows' market columns rescored by book_mark(), beside the
// originals; v_checkpoint_outcome serves the corrected values and says which.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const MIGRATION = path.join(MIGRATIONS, '20260930003000_the_market_scores_banked_before_the_fix.sql');
// book_mark() as it is live, taken from the migration that made it
const FIX_SOURCE = fs.readFileSync(path.join(MIGRATIONS, '20260926090000_hit_and_miss_scores_frozen_calls.sql'), 'utf-8');
const BOOK_MARK = FIX_SOURCE.slice(FIX_SOURCE.indexOf('create or replace function public.book_mark'),
                                   FIX_SOURCE.indexOf('$$;', FIX_SOURCE.indexOf('create or replace function public.book_mark')) + 3);

const id = (n) => `00000000-0000-0000-0000-0000000000${String(n).padStart(2, '0')}`;
// three buckets; the winner is "b"
const PROBS = { a: 0.2, b: 0.5, c: 0.3 };
// ask-only "a" offered at 0.001 that last traded at 0.999: the old rule made it the favourite
const DEAD = { a: { ask: 0.001, last: 0.999 }, b: { bid: 0.5, ask: 0.6 }, c: { bid: 0.2, ask: 0.3 } };
const PLAIN = { a: { bid: 0.1, ask: 0.2 }, b: { bid: 0.5, ask: 0.6 }, c: { bid: 0.2, ask: 0.3 } };

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
      language plpgsql set search_path='' as $$
      begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    ${BOOK_MARK}
    create table public.prediction_checkpoints (checkpoint_id uuid primary key, probs jsonb not null, market jsonb);
    create table public.fact_checkpoint_outcome (
      checkpoint_id uuid primary key references public.prediction_checkpoints (checkpoint_id),
      city_key text not null, target_date date not null, checkpoint text not null, engine_version text not null,
      local_decision_time timestamp not null, decided_at timestamptz not null, winner_band_id text not null,
      n_bands integer not null, ladder_has_winner boolean not null, model_prob_on_winner numeric not null,
      hit boolean not null, brier_model numeric not null, log_loss_model numeric not null,
      market_top_band_id text, market_hit boolean, market_complete boolean not null,
      market_prob_on_winner numeric, brier_market numeric, log_loss_market numeric,
      brier_uniform numeric not null, log_loss_uniform numeric not null, settled_at timestamptz,
      banked_at timestamptz not null default clock_timestamp());
    create trigger fact_checkpoint_outcome_immutable before update or delete on public.fact_checkpoint_outcome
      for each row execute function arbdesk_private.immutable_record();
  `);
  const cp = async (n, market, stored, bankedAt) => {
    await db.query('insert into public.prediction_checkpoints values ($1, $2::jsonb, $3::jsonb)',
      [id(n), JSON.stringify(PROBS), JSON.stringify(market)]);
    await db.query(`insert into public.fact_checkpoint_outcome (checkpoint_id, city_key, target_date, checkpoint,
        engine_version, local_decision_time, decided_at, winner_band_id, n_bands, ladder_has_winner,
        model_prob_on_winner, hit, brier_model, log_loss_model, market_top_band_id, market_hit, market_complete,
        market_prob_on_winner, brier_market, log_loss_market, brier_uniform, log_loss_uniform, banked_at)
      values ($1, 'nyc', '2026-09-24', 'noon', 'v', '2026-09-24 12:00', '2026-09-24 16:00Z', 'b', 3, true,
              0.5, true, 0.38, 0.693147, $2, $3, $4, $5, $6, $7, 0.666667, 1.098612, $8)`,
      [id(n), stored.band, stored.hit, stored.complete, stored.prob, stored.brier, stored.ll, bankedAt]);
  };
  // What book_mark() gives on DEAD and PLAIN: marks a 0.001, b 0.55, c 0.25 / a 0.15, b 0.55, c 0.25.
  const right = { band: 'b', hit: true, complete: true };
  // 1: banked before the fix, scored by the old rule - "a" the favourite, wrong
  await cp(1, DEAD, { band: 'a', hit: false, complete: true, prob: 0.333, brier: 0.9, ll: 1.1 }, '2026-09-25 11:14Z');
  // 2: banked before the fix, and the old rule happened to agree with book_mark()
  const plainTotal = 0.15 + 0.55 + 0.25;
  const p2 = Math.round((0.55 / plainTotal) * 1e6) / 1e6;
  const b2 = Math.round((((0.15 / plainTotal) ** 2) + ((0.55 / plainTotal - 1) ** 2) + ((0.25 / plainTotal) ** 2)) * 1e6) / 1e6;
  const l2 = Math.round(-Math.log(0.55 / plainTotal) * 1e6) / 1e6;
  await cp(2, PLAIN, { ...right, prob: p2, brier: b2, ll: l2 }, '2026-09-26 05:15Z');
  // 3: banked AFTER the fix with a stored value that differs - never "corrected"
  await cp(3, DEAD, { band: 'a', hit: false, complete: true, prob: 0.333, brier: 0.9, ll: 1.1 }, '2026-09-27 05:12Z');

  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // re-runnable: each row once

  const rows = (await db.query('select * from public.fact_checkpoint_outcome_market_fix order by checkpoint_id')).rows;
  assert.equal(rows.length, 1, 'only the pre-fix row whose stored market columns differ');
  const fx = rows[0];
  assert.equal(fx.checkpoint_id, id(1));
  assert.equal(fx.market_top_band_id, 'b');
  assert.equal(fx.market_hit, true);
  assert.equal(fx.market_complete, true);
  const deadTotal = 0.001 + 0.55 + 0.25;
  assert.equal(Number(fx.market_prob_on_winner), Math.round((0.55 / deadTotal) * 1e6) / 1e6);
  assert.equal(Number(fx.log_loss_market), Math.round(-Math.log(0.55 / deadTotal) * 1e6) / 1e6);
  assert.match(fx.reason, /book_mark/);

  // The original row is untouched.
  const orig = (await db.query(`select market_top_band_id, market_hit from public.fact_checkpoint_outcome where checkpoint_id = $1`, [id(1)])).rows[0];
  assert.deepEqual(orig, { market_top_band_id: 'a', market_hit: false });

  // The view: corrected where a correction exists, as written elsewhere.
  const v = (await db.query('select checkpoint_id, market_top_band_id, market_hit, market_corrected from public.v_checkpoint_outcome order by checkpoint_id')).rows;
  assert.deepEqual(v, [
    { checkpoint_id: id(1), market_top_band_id: 'b', market_hit: true, market_corrected: true },
    { checkpoint_id: id(2), market_top_band_id: 'b', market_hit: true, market_corrected: false },
    { checkpoint_id: id(3), market_top_band_id: 'a', market_hit: false, market_corrected: false },
  ]);

  // Append-only, like the table it corrects.
  await assert.rejects(db.query(`update public.fact_checkpoint_outcome_market_fix set market_hit = false`), /Append-only/);
  await assert.rejects(db.query(`delete from public.fact_checkpoint_outcome_market_fix`), /Append-only/);
  await assert.rejects(db.query(`truncate public.fact_checkpoint_outcome_market_fix`), /Append-only/);
  await assert.rejects(db.query(`insert into public.fact_checkpoint_outcome_market_fix (checkpoint_id, market_complete, reason)
                                 values ('${id(2)}', true, 'x')`), /check constraint/, 'a complete market has its scores');

  // The table says where its right numbers are.
  const note = (await db.query(`select col_description('public.fact_checkpoint_outcome'::regclass, attnum) c
                                  from pg_attribute where attrelid = 'public.fact_checkpoint_outcome'::regclass and attname = 'market_hit'`)).rows[0].c;
  assert.match(note, /v_checkpoint_outcome/);

  // Service role only, as the table it corrects.
  for (const rel of ['public.fact_checkpoint_outcome_market_fix', 'public.v_checkpoint_outcome']) {
    await db.exec('set role anon');
    await assert.rejects(db.query(`select * from ${rel}`), /permission denied/, `${rel} is not anon's`);
    await db.exec('reset role');
  }
  await db.exec('set role service_role');
  await db.query('select count(*) from public.fact_checkpoint_outcome_market_fix');   // the mirror reads it
  await assert.rejects(db.query(`update public.fact_checkpoint_outcome_market_fix set reason = 'x'`), /permission denied|Append-only/);
  await assert.rejects(db.query(`insert into public.fact_checkpoint_outcome_market_fix (checkpoint_id, market_complete, reason)
                                 values ('${id(3)}', false, 'x')`), /permission denied/, 'only the migration writes it');
  await db.exec('reset role');

  console.log('PASS: market-fix: the rows banked before book_mark() get their market columns rescored beside the originals - only where they differ, never a row banked after the fix, each once; the view serves the corrected values and says which; append-only; the table\'s columns point to the view; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
