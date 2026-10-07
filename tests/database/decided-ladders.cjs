// ===========================================================================
// A DECIDED BAND'S LAST LADDER GOES TO THE REPOSITORY (WXPredict build 2.A,
// 7 Oct). v_unarchived_ladders takes a decided band's last book once its
// market's date is two days past - not a decided book that is not its band's
// last, not a market one day past, one book of a tied pair; the hourly prune
// nulls that ladder only once it is stamped and only once book_ladder_cache
// no longer holds the snapshot; a trading band's newest book is still never
// touched, nor either row of a tied newest pair by age (the readers order by
// observed_at alone and may take either; Codex on #337); every number and
// every row stays; the browser reaches neither.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const DIR = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const P513 = fs.readFileSync(path.join(DIR, '20260927100000_a_ladder_is_archived_before_it_is_pruned.sql'), 'utf-8');
const MIG = fs.readFileSync(path.join(DIR, '20261007190000_a_decided_bands_last_ladder_goes_to_the_repo.sql'), 'utf-8');

const band = (c) => `00000000-0000-0000-0000-0000000000${c}`;
const market = (c) => `10000000-0000-0000-0000-0000000000${c}`;

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.markets(market_id uuid primary key, resolution_date date);
    create table public.bands(band_id uuid primary key, market_id uuid);
    create table public.book_snapshots(snapshot_id bigint primary key, band_id uuid not null,
      observed_at timestamptz not null, market_state text not null, best_bid numeric, best_ask numeric,
      no_best_bid numeric, no_best_ask numeric, raw_book jsonb, no_book jsonb);
    create table public.book_ladder_cache(band_id uuid primary key, snapshot_id bigint not null,
      observed_at timestamptz not null);
    insert into public.markets values
      ('${market('0a')}', current_date - 3),   -- two days past and more
      ('${market('0b')}', current_date - 1),   -- one day past: not yet
      ('${market('0f')}', current_date - 2);   -- exactly two days past
    insert into public.bands values
      ('${band('0a')}', '${market('0a')}'), ('${band('0b')}', '${market('0b')}'),
      ('${band('0c')}', '${market('0a')}'), ('${band('0d')}', '${market('0a')}'),
      ('${band('0e')}', '${market('0a')}'), ('${band('0f')}', '${market('0f')}');
    insert into public.book_snapshots values
      -- a: a decided band's earlier book, then its last
      (1, '${band('0a')}', now() - interval '80 hours', 'DEAD_LOSER', null, 0.001, 0.998, 0.999, '{"asks":[[0.001,900]]}', '{"bids":[[0.998,5]]}'),
      (2, '${band('0a')}', now() - interval '75 hours', 'DEAD_LOSER', null, 0.001, 0.998, 0.999, '{"asks":[[0.001,901]]}', '{"bids":[[0.998,6]]}'),
      -- b: its market is only one day past
      (3, '${band('0b')}', now() - interval '30 hours', 'DEAD_LOSER', null, 0.002, null, null, '{"asks":[[0.002,50]]}', null),
      -- c: a trading band's newest book, older than 48 h
      (4, '${band('0c')}', now() - interval '75 hours', 'LIVE', 0.3, 0.32, 0.68, 0.7, '{"asks":[[0.32,10]]}', null),
      -- d: a decided last book the ladder cache still holds
      (5, '${band('0d')}', now() - interval '75 hours', 'DEAD_WINNER', 0.999, null, null, 0.001, '{"bids":[[0.999,70]]}', null),
      -- e: a tied pair; the higher snapshot_id is the last book
      (6, '${band('0e')}', now() - interval '74 hours', 'DEAD_LOSER', null, 0.001, null, null, '{"asks":[[0.001,1]]}', null),
      (7, '${band('0e')}', now() - interval '74 hours', 'DEAD_LOSER', null, 0.001, null, null, '{"asks":[[0.001,2]]}', null),
      -- f: a decided last book of a market exactly two days past
      (8, '${band('0f')}', now() - interval '50 hours', 'DEAD_LOSER', null, 0.001, null, null, '{"asks":[[0.001,3]]}', null);
    insert into public.book_ladder_cache values ('${band('0d')}', 5, now() - interval '75 hours');
  `);
  await db.exec(P513);
  await db.exec(MIG);
  await db.exec(MIG);   // re-runnable

  const ids = async (sql, params = []) => (await db.query(sql, params)).rows.map((r) => Number(r.snapshot_id));
  const ladders = () => ids('select snapshot_id from public.book_snapshots where raw_book is not null or no_book is not null order by 1');
  const fingerprint = async () => (await db.query(
    `select string_agg(concat_ws('|', snapshot_id, band_id, observed_at, market_state, best_bid, best_ask, no_best_bid, no_best_ask), ';' order by snapshot_id) as f, count(*)::int as n
       from public.book_snapshots`)).rows[0];
  const numbers = await fingerprint();

  // 1 - what the export takes: a decided band's last book of a market two
  // days past (2, 5, 7, 8) and the trading book (4); not an earlier decided
  // book (1), not a market one day past (3), not the tie's other book (6)
  assert.deepEqual(await ids('select snapshot_id from public.v_unarchived_ladders order by 1'), [2, 4, 5, 7, 8]);

  // 2 - nothing stamped: a decided band's last book keeps its ladder, and so
  // does the other row of a tied newest pair (6); the earlier decided book
  // (1) goes at 6 h as it always did
  await db.query('select public.prune_dead_book_detail()');
  assert.deepEqual(await ladders(), [2, 3, 4, 5, 6, 7, 8]);

  // 3 - the mark counts the same rows the export read, and stamps them
  const before = (await db.query(`select (now() - interval '1 day') as t`)).rows[0].t;
  let r = (await db.query('select public.mark_ladders_archived(1, true, $1, null) as r', [before])).rows[0].r;
  assert.equal(r.ok, true); assert.equal(Number(r.would_delete), 5);
  r = (await db.query('select public.mark_ladders_archived(1, false, $1, 5) as r', [before])).rows[0].r;
  assert.equal(r.ok, true); assert.equal(Number(r.marked), 5);
  assert.deepEqual(await ids('select snapshot_id from public.v_unarchived_ladders order by 1'), []);

  // 4 - stamped: the decided last books lose their ladder, except the one
  // the cache still holds (5); the trading band's newest book (4) is never
  // touched; the market one day past (3) was never stamped; the tie's other
  // row (6) is never stamped and, at its band's newest instant, never aged out
  await db.query('select public.prune_dead_book_detail()');
  assert.deepEqual(await ladders(), [3, 4, 5, 6]);

  // 5 - the cache lets go of 5 (its three-day trim): now it goes too
  await db.exec(`delete from public.book_ladder_cache where snapshot_id = 5`);
  await db.query('select public.prune_dead_book_detail()');
  assert.deepEqual(await ladders(), [3, 4, 6]);

  // 6 - every row and every number stays
  assert.deepEqual(await fingerprint(), numbers);

  // 7 - the browser roles reach neither
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`);
    await assert.rejects(db.query('select count(*) from public.v_unarchived_ladders'));
    await assert.rejects(db.query('select public.prune_dead_book_detail()'));
    await db.exec('reset role');
  }

  console.log("PASS: decided-ladders: a decided band's last book is exported once its market is two days past, nulled only once stamped and out of the ladder cache, a trading band's newest never, every number kept, closed to the browser");
})().catch((e) => { console.error(e); process.exit(1); });
