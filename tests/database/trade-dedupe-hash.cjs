// ===========================================================================
// A TRADE IS KNOWN BY ITS HASH (WXPredict build 2.A group C, 8 Oct;
// 20261008170000_a_trade_is_known_by_its_hash.sql and
// 20261008170100_the_wide_trade_index_goes.sql).
//
// The trade ingest re-sends the overlap of every run; the five-column unique
// index (sql/ad4_53) turned it away at 19 MB on 88,746 prints. The same key
// as 16 bytes must turn away exactly the same prints: what the old index
// called equal (numeric 0.5 and 0.50, one instant whatever its zone), equal;
// what it kept apart (another wallet, a NULL), apart. The ingest's insert
// returns the ids of what it inserted. Closed to the browser. Re-runnable.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const read = (f) => fs.readFileSync(path.join(ROOT, 'supabase', 'migrations', f), 'utf-8');
const HASH = read('20261008170000_a_trade_is_known_by_its_hash.sql');
const DROP = read('20261008170100_the_wide_trade_index_goes.sql');

const COND = '0x' + 'ab'.repeat(32);

async function world() {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    -- as live (information_schema, 8 Oct), with ad4_53's index
    create table public.trades_observed (trade_id bigserial primary key, band_id uuid, condition_id text,
      traded_at timestamptz, price numeric, size numeric, side text, proxy_wallet text not null default '',
      ingested_at timestamptz not null default now(), city_key text, token_id text, observed_at timestamptz);
    create unique index ad4_uq_trade_dedupe on public.trades_observed (condition_id, traded_at, price, size, proxy_wallet);
  `);
  return db;
}

const print = (o = {}) => Object.assign({ band_id: '00000000-0000-0000-0000-000000000001', condition_id: COND,
  city_key: 'london', token_id: '123', side: 'BUY', price: 0.5, size: 10, traded_at: '2026-10-08T12:00:00+00:00',
  proxy_wallet: '0xw1', ingested_at: '2026-10-08T12:05:00+00:00' }, o);

async function insert(db, rows) {
  const r = await db.query('select trade_id from public.insert_trade_prints($1::jsonb)', [JSON.stringify(rows)]);
  return r.rows.map((x) => Number(x.trade_id));
}
const count = async (db) => (await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n;

(async () => {
  const db = await world();
  await db.exec(HASH);
  await db.exec(HASH);                                   // re-runnable

  // 1. New prints go in and their ids come back; a re-sent batch adds nothing.
  const first = await insert(db, [print(), print({ proxy_wallet: '0xw2' }), print({ price: 0.51 })]);
  assert.equal(first.length, 3);
  assert.deepEqual(await insert(db, [print(), print({ proxy_wallet: '0xw2' })]), [], 'the overlap is turned away');
  assert.equal(await count(db), 3);

  // 2. What the five-column index called equal is equal here: numeric scale,
  //    and one instant written in another zone.
  assert.deepEqual(await insert(db, [print({ price: '0.50', size: '10.000' })]), []);
  assert.deepEqual(await insert(db, [print({ traded_at: '2026-10-08T14:00:00+02:00' })]), []);
  await db.exec(`set timezone = 'Asia/Kolkata'`);
  assert.deepEqual(await insert(db, [print()]), [], 'the session zone changes nothing');
  await db.exec(`set timezone = 'UTC'`);

  // 3. What it kept apart stays apart: a NULL never conflicts, and texts that
  //    would join into one string unprefixed are two prints.
  assert.equal((await insert(db, [print({ condition_id: null }), print({ condition_id: null })])).length, 2);
  const h1 = '2026-10-08T10:00:00+00:00', h2 = '2026-10-08T11:00:00+00:00';
  const hex = async (ts) => (await db.query(`select encode(timestamptz_send($1::timestamptz),'hex') as h`, [ts])).rows[0].h;
  const a = print({ condition_id: 'c', traded_at: h1, price: 0.5, size: 2, proxy_wallet: `w|${await hex(h2)}|0.4|3|v` });
  const b = print({ condition_id: `c|${await hex(h1)}|0.5|2|w`, traded_at: h2, price: 0.4, size: 3, proxy_wallet: 'v' });
  assert.equal((await insert(db, [a, b])).length, 2, 'length-prefixed texts cannot run into each other');

  // 4. A duplicate inside one batch goes in once.
  assert.equal((await insert(db, [print({ size: 77 }), print({ size: 77 })])).length, 1);

  // 5. Before the wide index goes, both indexes agree on every row held:
  //    as many distinct hashes as distinct five-column keys among non-NULL keys.
  const agree = (await db.query(`select count(distinct public.trade_dedupe_key(condition_id, traded_at, price, size, proxy_wallet))::int as k,
      (select count(*)::int from (select distinct condition_id, traded_at, price, size, proxy_wallet from public.trades_observed
        where condition_id is not null) d) as c from public.trades_observed where condition_id is not null`)).rows[0];
  assert.equal(agree.k, agree.c);

  // 6. The wide index goes; the hash keeps turning the overlap away.
  await db.exec(DROP);
  await db.exec(DROP);                                   // re-runnable
  const idx = (await db.query(`select indexname from pg_indexes where tablename = 'trades_observed' order by 1`)).rows.map((r) => r.indexname);
  assert.ok(!idx.includes('ad4_uq_trade_dedupe') && idx.includes('ad4_uq_trade_dedupe_hash'), idx.join(','));
  const before = await count(db);
  assert.deepEqual(await insert(db, [print(), print({ price: '0.500' })]), []);
  assert.equal(await count(db), before);

  // 7. The key is IMMUTABLE (an index needs it) and the insert is the service role's alone.
  const vol = (await db.query(`select provolatile from pg_proc where proname = 'trade_dedupe_key'`)).rows[0].provolatile;
  assert.equal(vol, 'i');
  for (const role of ['anon', 'authenticated', 'public']) {
    const ok = (await db.query(`select has_function_privilege($1, 'public.insert_trade_prints(jsonb)', 'execute') as ok`,
      [role])).rows[0].ok;
    assert.equal(ok, false, `${role} may not insert trades`);
  }
  assert.equal((await db.query(`select has_function_privilege('service_role', 'public.insert_trade_prints(jsonb)', 'execute') as ok`)).rows[0].ok, true);

  console.log('PASS: trade-dedupe-hash: the 16-byte key turns away exactly what the five-column index did (numeric scale and zone equal, NULLs and wallets apart, texts that would run together apart, a batch duplicate once), the ingest\'s insert returns what it inserted, the wide index goes and the overlap is still turned away; immutable; service role only; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
