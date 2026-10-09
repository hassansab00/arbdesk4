// ===========================================================================
// THE BOARD READS THE ENGINE'S LEDGERS
// (20261009100000_the_board_reads_the_engines_ledgers.sql).
//
// The engine strategies fire no signals; each trades a shadow ledger of its
// own. On the real migration, over the relations v_strategy_board reads:
//   - a signal strategy's row is what it was (its arms, its counts);
//   - an engine strategy with nothing bought says so, as before;
//   - one that has bought reads its own ledger: bought in 30 days, all-time,
//     settled, won, net P&L, win rate over the settled, and a verdict that
//     waits for 30 settled trades;
//   - a desk's trades, another strategy's ledger, and a non-engine
//     strategy's shadow trades never count;
//   - re-runnable.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20261009100000_the_board_reads_the_engines_ledgers.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;

  await db.exec(`
    create table strategies (strategy_id text primary key, name text, side text, origin text, enabled boolean,
      conflict_class text, universe text[], regime_filter text[], capital_cap_pct numeric, max_concurrent int,
      extra jsonb);
    create table signals (signal_id bigserial primary key, strategy_id text, status text, fired_at timestamptz);
    create table fact_signal_outcome (signal_id bigint, strategy_id text, filled boolean, net_pnl numeric,
      slippage_c numeric, signal_correct boolean);
    create table v_signal_mark (signal_id bigint, mark_won boolean, price_at_fire numeric,
      mark_net_per_share numeric, mark_basis text);
    create table paper_accounts (account_id text primary key, kind text, strategy_id text);
    create table paper_trades (trade_id bigserial primary key, account_id text, strategy_id text,
      opened_at timestamptz, closed_at timestamptz, net_pnl numeric);

    insert into strategies values
      ('s_sig',   'a signal strategy', 'YES', 'hassan', true, null, null, null, null, null, '{}'),
      ('e_none',  'engine, nothing bought', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_open',  'engine, open trades', 'NO', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_few',   'engine, few settled', 'NO', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_win',   'engine, 30 settled up', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_lose',  'engine, 30 settled down', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_old',   'engine, bought long ago', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('s_old',   'a signal strategy with a shadow ledger', 'NO', 'CLAUDE_CANDIDATE', true, null, null, null, null, null, '{}');
    insert into signals (strategy_id, status, fired_at) values ('s_sig', 'fired', now() - interval '1 day');
    insert into paper_accounts values
      ('L_e_none', 'shadow', 'e_none'), ('L_e_open', 'shadow', 'e_open'), ('L_e_few', 'shadow', 'e_few'),
      ('L_e_win', 'shadow', 'e_win'), ('L_e_lose', 'shadow', 'e_lose'), ('L_e_old', 'shadow', 'e_old'),
      ('L_s_old', 'shadow', 's_old'), ('desk', null, null);
    insert into paper_trades (account_id, strategy_id, opened_at, closed_at, net_pnl) values
      ('L_e_open', 'e_open', now() - interval '2 hours', null, null),
      ('L_e_open', 'e_open', now() - interval '1 hour', null, null),
      ('L_e_few', 'e_few', now() - interval '2 days', now() - interval '1 day', 10),
      ('L_e_few', 'e_few', now() - interval '2 days', now() - interval '1 day', -4),
      ('L_e_few', 'e_few', now() - interval '3 hours', null, null),
      -- a desk took e_few too, and its own ledger traded e_open: neither counts
      ('desk', 'e_few', now() - interval '1 day', now(), 500),
      ('L_e_open', 'e_few', now() - interval '1 day', now(), 500),
      -- a signal strategy's shadow trades are its desk record, not this one
      ('L_s_old', 's_old', now() - interval '1 day', now(), 7),
      ('L_e_old', 'e_old', now() - interval '40 days', now() - interval '39 days', 3);
    insert into paper_trades (account_id, strategy_id, opened_at, closed_at, net_pnl)
    select 'L_e_win', 'e_win', now() - interval '5 days', now() - interval '4 days', case when g % 3 = 0 then -1 else 2 end
      from generate_series(1, 30) g;
    insert into paper_trades (account_id, strategy_id, opened_at, closed_at, net_pnl)
    select 'L_e_lose', 'e_lose', now() - interval '5 days', now() - interval '4 days', case when g % 3 = 0 then 2 else -2 end
      from generate_series(1, 31) g;`);

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  const rows = Object.fromEntries((await q(`select * from v_strategy_board`)).map((r) => [r.strategy_id, r]));
  const n = (v) => (v === null ? null : Number(v));

  // A signal strategy reads its signals, as before.
  assert.equal(rows.s_sig.fired_30d, 1);
  assert.equal(rows.s_sig.verdict, 'firing, but nothing it fired has settled yet');
  assert.equal(rows.s_sig.filled_all_time, 0);
  // ...and one that also has a shadow ledger is not read from it.
  assert.equal(rows.s_old.filled_all_time, 0);
  assert.equal(rows.s_old.net_pnl, null);
  assert.equal(rows.s_old.verdict, 'on, but nothing has met its conditions in 30 days');

  // An engine strategy with nothing bought says so, as before.
  assert.equal(rows.e_none.fired_30d, 0);
  assert.equal(rows.e_none.verdict, 'on, but nothing has met its conditions in 30 days');

  // Bought, nothing settled.
  assert.equal(rows.e_open.fired_30d, 2);
  assert.equal(rows.e_open.filled_all_time, 2, 'another strategy\'s trade on this ledger was counted');
  assert.equal(rows.e_open.verdict, 'trading on its own paper ledger: 2 bought, none settled yet');
  assert.equal(rows.e_open.win_rate_pct, null);

  // Few settled: the desk's +500 and the trade on e_open's ledger never count.
  assert.equal(rows.e_few.fired_30d, 3);
  assert.equal(rows.e_few.filled_all_time, 3);
  assert.equal(rows.e_few.won_all_time, 1);
  assert.equal(n(rows.e_few.net_pnl), 6);
  assert.equal(n(rows.e_few.win_rate_pct), 50);
  assert.equal(rows.e_few.verdict, 'too few settled trades to judge - 2 of 30, net $6.00 on its own paper ledger');

  // Thirty settled: profitable, or losing.
  assert.equal(n(rows.e_win.net_pnl), 20 * 2 - 10);
  assert.equal(rows.e_win.verdict, 'profitable on its own paper ledger');
  assert.equal(n(rows.e_win.win_rate_pct), 66.7);
  assert.ok(n(rows.e_lose.net_pnl) < 0);
  assert.equal(rows.e_lose.verdict, 'losing on its own paper ledger');

  // Nothing in 30 days: the verdict says so; the all-time record stays.
  assert.equal(rows.e_old.fired_30d, 0);
  assert.equal(rows.e_old.filled_all_time, 1);
  assert.equal(rows.e_old.verdict, 'on, but nothing has met its conditions in 30 days');

  console.log('PASS: board-engine-ledgers: signal strategies read their signals as before; an engine strategy reads its own shadow ledger (bought, settled, won, net, win rate) and waits for 30 settled trades; desks, other ledgers and non-engine shadow trades never count; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
