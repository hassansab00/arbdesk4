// ===========================================================================
// A TRADE IS KNOWN BY ITS TRANSACTION (R46 part 2, 10 Oct;
// 20261010190000_a_trade_is_known_by_its_transaction.sql).
//
// Measured 10 Oct: 119 of the 15,726 trades the API serves for 10 sampled
// events share the five-column key with another trade, each with its own
// transactionHash; the hash index merged them. With the transaction in the
// key, separate transactions go in apart, the same transaction is still
// turned away, a print without a transaction is turned away exactly as
// before, and during the changeover a print already held WITHOUT its hash is
// not stored again when it comes back WITH one. The guard reads plain
// columns, so a connection whose first plan of the table was anon's still
// inserts (the fault 20261008180000 met). Re-runnable, service role only.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const read = (f) => fs.readFileSync(path.join(ROOT, 'supabase', 'migrations', f), 'utf-8');
const HASH = read('20261008170000_a_trade_is_known_by_its_hash.sql');
const DROP_WIDE = read('20261008170100_the_wide_trade_index_goes.sql');
const NO_TARGET = read('20261008180000_a_trade_print_needs_no_conflict_target.sql');
const TX = read('20261010190000_a_trade_is_known_by_its_transaction.sql');

const COND = '0x' + 'cd'.repeat(32);

async function world() {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.trades_observed (trade_id bigserial primary key, band_id uuid, condition_id text,
      traded_at timestamptz, price numeric, size numeric, side text, proxy_wallet text not null default '',
      ingested_at timestamptz not null default now(), city_key text, token_id text, observed_at timestamptz);
    create unique index ad4_uq_trade_dedupe on public.trades_observed (condition_id, traded_at, price, size, proxy_wallet);
  `);
  await db.exec(HASH);
  await db.exec(NO_TARGET);
  await db.exec(DROP_WIDE);
  return db;
}

const print = (o = {}) => Object.assign({ band_id: '00000000-0000-0000-0000-000000000001', condition_id: COND,
  city_key: 'tokyo', token_id: '123', side: 'BUY', price: 0.5, size: 10, traded_at: '2026-10-10T12:00:00+00:00',
  proxy_wallet: '0xw1', ingested_at: '2026-10-10T12:05:00+00:00' }, o);

async function insert(db, rows) {
  const r = await db.query('select trade_id from public.insert_trade_prints($1::jsonb)', [JSON.stringify(rows)]);
  return r.rows.map((x) => Number(x.trade_id));
}
const count = async (db, where = 'true') =>
  (await db.query(`select count(*)::int as n from public.trades_observed where ${where}`)).rows[0].n;

(async () => {
  const db = await world();
  // Rows held before the change: no transaction (the old ingest's insert).
  assert.equal((await insert(db, [print(), print({ size: 20 })])).length, 2);

  await db.exec(TX);
  await db.exec(TX);                                         // re-runnable
  const idx = (await db.query(`select indexname from pg_indexes where tablename = 'trades_observed' order by 1`))
    .rows.map((r) => r.indexname);
  for (const want of ['ad4_uq_trade_tx_key', 'ad4_uq_trade_dedupe_untx', 'ad4_ix_trades_untx_print']) {
    assert.ok(idx.includes(want), `${want} missing: ${idx.join(',')}`);
  }
  assert.ok(!idx.includes('ad4_uq_trade_dedupe_hash'), 'the full hash index would still merge separate transactions');

  // 1. THE CHANGEOVER: a print held without its hash comes back with one and is not stored again.
  assert.deepEqual(await insert(db, [print({ transaction_hash: '0xt1' })]), [], 'the overlap, now with a hash');
  assert.equal(await count(db), 2);

  // 2. Separate transactions sharing the five columns go in apart; the same transaction once.
  const two = await insert(db, [print({ size: 30, transaction_hash: '0xa' }), print({ size: 30, transaction_hash: '0xb' })]);
  assert.equal(two.length, 2, 'two transactions, two trades');
  assert.deepEqual(await insert(db, [print({ size: 30, transaction_hash: '0xa' }),
                                     print({ size: 30, transaction_hash: '0xb' })]), [], 'the overlap is turned away');
  assert.equal((await insert(db, [print({ size: 40, transaction_hash: '0xc' }),
                                  print({ size: 40, transaction_hash: '0xc' })])).length, 1, 'a batch duplicate once');
  // numeric scale and zone are still one trade
  assert.deepEqual(await insert(db, [print({ size: '30.00', price: '0.50', transaction_hash: '0xa',
                                             traded_at: '2026-10-10T14:00:00+02:00' })]), []);

  // 3. A print without a transaction is turned away exactly as before ('' counts as none).
  assert.deepEqual(await insert(db, [print()]), []);
  assert.deepEqual(await insert(db, [print({ transaction_hash: '' })]), []);
  assert.equal((await insert(db, [print({ size: 50 }), print({ size: 50 })])).length, 1);
  assert.equal(await count(db, `transaction_hash = ''`), 0, "'' is stored as no transaction");

  // 4. Once no row is held without a hash (pruned to the archive), nothing is guarded.
  await db.exec(`delete from public.trades_observed where transaction_hash is null`);
  assert.equal((await insert(db, [print({ transaction_hash: '0xt1' })])).length, 1);

  // 5. Immutable, and the insert is the service role's alone.
  const vol = (await db.query(`select provolatile from pg_proc where proname = 'trade_tx_key'`)).rows[0].provolatile;
  assert.equal(vol, 'i');
  for (const role of ['anon', 'authenticated', 'public']) {
    const ok = (await db.query(`select has_function_privilege($1, 'public.insert_trade_prints(jsonb)', 'execute') as ok`,
      [role])).rows[0].ok;
    assert.equal(ok, false, `${role} may not insert trades`);
  }

  // 6. Whoever planned the table first in a connection, the insert works and the guard holds.
  for (const first of ['service_role', 'anon']) {
    const fresh = await world();
    await insert(fresh, [print()]);                          // a row held without a hash
    await fresh.exec(TX);
    await fresh.exec(`grant select on public.trades_observed to anon;
      grant select, insert on public.trades_observed to service_role;
      grant usage on sequence public.trades_observed_trade_id_seq to service_role;
      revoke all on function public.trade_dedupe_key(text, timestamptz, numeric, numeric, text) from public;
      revoke all on function public.trade_tx_key(text, timestamptz, numeric, numeric, text, text) from public;
      grant execute on function public.trade_dedupe_key(text, timestamptz, numeric, numeric, text) to service_role;
      grant execute on function public.trade_tx_key(text, timestamptz, numeric, numeric, text, text) to service_role;`);
    await fresh.exec(`set role ${first}; select count(*) from public.trades_observed where city_key = 'tokyo'; reset role;`);
    await fresh.exec('set role service_role');
    assert.deepEqual(await insert(fresh, [print({ transaction_hash: '0xt1' })]), [], `first plan as ${first}: the guard holds`);
    assert.equal((await insert(fresh, [print({ size: 9, transaction_hash: '0xa' }),
                                       print({ size: 9, transaction_hash: '0xb' })])).length, 2,
                 `first plan as ${first}: two transactions go in`);
    await fresh.close();
  }

  // 7. The install files (sql/) re-run over separate transactions sharing the
  //    five columns: neither may try to rebuild a full five-column index (Codex on #366).
  {
    const sqlDir = path.join(ROOT, 'sql');
    const PLAIN = fs.readFileSync(path.join(sqlDir, 'ad4_53_trade_dedupe_plain.sql'), 'utf-8');
    const INSTALL = fs.readFileSync(path.join(sqlDir, 'ad4_trade_dedupe_hash.sql'), 'utf-8');
    const inst = new PGlite();
    await inst.exec(`create role anon; create role authenticated; create role service_role;
      create table public.trades_observed (trade_id bigserial primary key, band_id uuid, condition_id text,
        traded_at timestamptz, price numeric, size numeric, side text, proxy_wallet text,
        ingested_at timestamptz not null default now(), city_key text, token_id text, observed_at timestamptz);`);
    await inst.exec(PLAIN);
    await inst.exec(INSTALL);
    const two = await insert(inst, [print({ transaction_hash: '0xa' }), print({ transaction_hash: '0xb' })]);
    assert.equal(two.length, 2, 'installed fresh: two transactions go in apart');
    await inst.exec(PLAIN);                                  // re-run: must not rebuild ad4_uq_trade_dedupe
    await inst.exec(INSTALL);                                // re-run: must not rebuild ad4_uq_trade_dedupe_hash
    const left = (await inst.query(`select indexname from pg_indexes where tablename = 'trades_observed' order by 1`))
      .rows.map((r) => r.indexname);
    assert.ok(!left.includes('ad4_uq_trade_dedupe') && !left.includes('ad4_uq_trade_dedupe_hash'), left.join(','));
    assert.equal(await count(inst), 2, 'the re-run keeps both trades');
    await inst.close();
  }

  console.log('PASS: trade-tx-key: the install files re-run over separate transactions sharing the five columns; separate transactions sharing condition, second, price, size and wallet go in apart; the same transaction is turned away (scale, zone and batch duplicates too); a print without one is turned away as before; a print held without its hash is not stored again when it returns with one; the full hash index is gone; immutable; service role only; re-runnable; a connection whose first plan was anon\'s still inserts');
})().catch((e) => { console.error(e); process.exit(1); });
