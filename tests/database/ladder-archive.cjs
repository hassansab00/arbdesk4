// ===========================================================================
// A LADDER IS ARCHIVED BEFORE IT IS PRUNED (plan v2 P5.13). The hourly prune
// may null a tradeable book's raw_book / no_book only on a snapshot the
// repository archive already holds (a decided book keeps its 6-hour prune); marking it archived deletes nothing; the mark agrees with the
// export's count or refuses; the browser roles reach neither the view nor the
// function.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260927100000_a_ladder_is_archived_before_it_is_pruned.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.book_snapshots(snapshot_id bigint primary key, band_id uuid not null,
      observed_at timestamptz not null, market_state text not null, best_bid numeric, best_ask numeric,
      no_best_bid numeric, no_best_ask numeric, raw_book jsonb, no_book jsonb);
    insert into public.book_snapshots values
      (1, '00000000-0000-0000-0000-00000000000a', now() - interval '72 hours', 'LIVE', 0.3, 0.32, null, null, '{"asks":[[0.32,10]]}', null),
      (2, '00000000-0000-0000-0000-00000000000a', now() - interval '60 hours', 'LIVE', 0.3, 0.32, null, null, '{"asks":[[0.32,11]]}', '{"asks":[[0.7,5]]}'),
      (3, '00000000-0000-0000-0000-00000000000a', now() - interval '1 hour',   'LIVE', 0.3, 0.32, null, null, '{"asks":[[0.32,12]]}', null),
      (4, '00000000-0000-0000-0000-00000000000b', now() - interval '10 hours', 'DEAD_LOSER', null, 0.001, null, null, '{"asks":[[0.001,900]]}', null),
      (5, '00000000-0000-0000-0000-00000000000b', now() - interval '2 hours',  'DEAD_LOSER', null, 0.001, null, null, null, null);
  `);
  await db.exec(MIG);
  await db.exec(MIG);   // re-runnable

  const ladders = async () => (await db.query(
    'select snapshot_id from public.book_snapshots where raw_book is not null or no_book is not null order by 1')).rows.map((r) => Number(r.snapshot_id));

  // 1 - nothing archived: the prune nulls no tradeable ladder, however old;
  // the decided book (4, DEAD_LOSER, 10 h) goes at 6 h as it always did
  assert.equal((await db.query('select public.prune_dead_book_detail() as n')).rows[0].n, 1);
  assert.deepEqual(await ladders(), [1, 2, 3]);
  assert.equal((await db.query('select count(*)::int as n from public.v_unarchived_ladders')).rows[0].n, 3);

  // 2 - the mark refuses without the verified count, and on a count that moved
  const before = (await db.query(`select (now() - interval '5 hours') as t`)).rows[0].t;
  let r = (await db.query('select public.mark_ladders_archived(1, false, $1, null) as r', [before])).rows[0].r;
  assert.equal(r.ok, false);
  r = (await db.query('select public.mark_ladders_archived(0, true, $1, null) as r', [before])).rows[0].r;
  assert.equal(r.ok, false, 'a window under the one-day floor must refuse');
  r = (await db.query('select public.mark_ladders_archived(1, true, $1, null) as r', [before])).rows[0].r;
  assert.equal(r.ok, true); assert.equal(Number(r.would_delete), 2);          // snapshots 1 and 2
  r = (await db.query('select public.mark_ladders_archived(1, false, $1, 3) as r', [before])).rows[0].r;
  assert.equal(r.ok, false, 'a count that does not match the export must refuse');
  assert.equal((await db.query('select count(*)::int as n from public.book_snapshots where ladder_archived_at is not null')).rows[0].n, 0);

  // 3 - marked: nothing is deleted, every ladder is still there
  r = (await db.query('select public.mark_ladders_archived(1, false, $1, 2) as r', [before])).rows[0].r;
  assert.equal(r.ok, true); assert.equal(Number(r.marked), 2);
  assert.equal((await db.query('select count(*)::int as n from public.book_snapshots')).rows[0].n, 5);
  assert.deepEqual(await ladders(), [1, 2, 3]);
  assert.deepEqual((await db.query('select snapshot_id from public.v_unarchived_ladders order by 1')).rows.map((x) => Number(x.snapshot_id)), [3]);

  // 4 - now the prune may null them: archived and past their age. Snapshot 3
  // (unarchived, and the newest for its band) keeps its ladder.
  assert.equal((await db.query('select public.prune_dead_book_detail() as n')).rows[0].n, 2);
  assert.deepEqual(await ladders(), [3]);
  const kept = (await db.query('select best_bid, best_ask from public.book_snapshots where snapshot_id = 1')).rows[0];
  assert.equal(Number(kept.best_ask), 0.32, 'the numeric columns stay');

  // 5 - the browser roles reach neither
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`);
    await assert.rejects(db.query('select count(*) from public.v_unarchived_ladders'));
    await assert.rejects(db.query('select public.mark_ladders_archived(1, true, now(), null)'));
    await assert.rejects(db.query('select public.prune_dead_book_detail()'));
    await db.exec('reset role');
  }

  console.log('PASS: ladder-archive: a tradeable ladder is nulled only once archived (a decided one at 6 h as before), the mark deletes nothing and agrees with the export count, closed to the browser');
})().catch((e) => { console.error(e); process.exit(1); });
