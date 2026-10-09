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
// And on 20261009110100, over the same relations plus paper_activity: the
// trades the daily export has pruned (trades_archived, by_strategy) are added
// back - only the ledger's own strategy's share, only on its own ledger - and
// a row written before by_strategy existed counts its totals and leaves wins
// unknown (null), never zero.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20261009100000_the_board_reads_the_engines_ledgers.sql'), 'utf-8');
const MIG_ARCHIVED = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20261009110100_the_board_keeps_archived_ledger_trades.sql'), 'utf-8');

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
    create table paper_activity (event_id bigserial primary key, account_id text, event_type text,
      payload jsonb, occurred_at timestamptz default now());

    insert into strategies values
      ('s_sig',   'a signal strategy', 'YES', 'hassan', true, null, null, null, null, null, '{}'),
      ('e_none',  'engine, nothing bought', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_open',  'engine, open trades', 'NO', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_few',   'engine, few settled', 'NO', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_win',   'engine, 30 settled up', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_lose',  'engine, 30 settled down', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_old',   'engine, bought long ago', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('s_old',   'a signal strategy with a shadow ledger', 'NO', 'CLAUDE_CANDIDATE', true, null, null, null, null, null, '{}'),
      ('e_arch',  'engine, everything archived', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_mix',   'engine, live and archived', 'NO', null, true, null, null, null, null, null, '{"origin":"engine"}'),
      ('e_legacy','engine, an archive without by_strategy', 'YES', null, true, null, null, null, null, null, '{"origin":"engine"}');
    insert into signals (strategy_id, status, fired_at) values ('s_sig', 'fired', now() - interval '1 day');
    insert into paper_accounts values
      ('L_e_none', 'shadow', 'e_none'), ('L_e_open', 'shadow', 'e_open'), ('L_e_few', 'shadow', 'e_few'),
      ('L_e_win', 'shadow', 'e_win'), ('L_e_lose', 'shadow', 'e_lose'), ('L_e_old', 'shadow', 'e_old'),
      ('L_s_old', 'shadow', 's_old'), ('desk', null, null),
      ('L_e_arch', 'shadow', 'e_arch'), ('L_e_mix', 'shadow', 'e_mix'), ('L_e_legacy', 'shadow', 'e_legacy'),
      -- an account that is not a shadow ledger but names an engine strategy
      ('desk_e_none', 'portfolio', 'e_none');
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
      ('L_e_old', 'e_old', now() - interval '40 days', now() - interval '39 days', 3),
      ('L_e_mix', 'e_mix', now() - interval '1 day', now() - interval '2 hours', 3),
      ('L_e_mix', 'e_mix', now() - interval '1 day', now() - interval '2 hours', -1),
      ('L_e_legacy', 'e_legacy', now() - interval '2 days', now() - interval '1 day', 1);
    -- What the daily export pruned (prune_exported_paper_trades' rows).
    insert into paper_activity (account_id, event_type, payload) values
      ('L_e_arch', 'trades_archived', '{"trades": 12, "realized_pnl": 905.5, "by_strategy":
         {"e_arch": {"trades": 3, "won": 2, "realized_pnl": 5.5},
          "intruder": {"trades": 9, "won": 9, "realized_pnl": 900}}}'),
      ('L_e_mix', 'trades_archived', '{"trades": 1, "realized_pnl": -3, "by_strategy":
         {"e_mix": {"trades": 1, "won": 0, "realized_pnl": -3}}}'),
      ('L_e_mix', 'trades_archived', '{"trades": 1, "realized_pnl": 0.75, "by_strategy":
         {"e_mix": {"trades": 1, "won": 1, "realized_pnl": 0.75}}}'),
      ('L_e_legacy', 'trades_archived', '{"trades": 2, "realized_pnl": 7}'),
      -- a desk's archive naming an engine strategy, and another event type: neither counts
      ('desk_e_none', 'trades_archived', '{"trades": 5, "realized_pnl": 50, "by_strategy":
         {"e_none": {"trades": 5, "won": 5, "realized_pnl": 50}}}'),
      ('L_e_none', 'position_settled', '{"realized_pnl": 99}');
    insert into paper_trades (account_id, strategy_id, opened_at, closed_at, net_pnl)
    select 'L_e_win', 'e_win', now() - interval '5 days', now() - interval '4 days', case when g % 3 = 0 then -1 else 2 end
      from generate_series(1, 30) g;
    insert into paper_trades (account_id, strategy_id, opened_at, closed_at, net_pnl)
    select 'L_e_lose', 'e_lose', now() - interval '5 days', now() - interval '4 days', case when g % 3 = 0 then 2 else -2 end
      from generate_series(1, 31) g;`);

  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  const n = (v) => (v === null ? null : Number(v));
  const first = Object.fromEntries((await q(`select * from v_strategy_board`)).map((r) => [r.strategy_id, r]));
  await db.exec(MIG_ARCHIVED);
  await db.exec(MIG_ARCHIVED);                                   // re-runnable
  const rows = Object.fromEntries((await q(`select * from v_strategy_board`)).map((r) => [r.strategy_id, r]));

  // Where nothing was archived, 20261009110100 returns what 20261009100000 did.
  for (const id of ['s_sig', 's_old', 'e_none', 'e_open', 'e_few', 'e_win', 'e_lose', 'e_old']) {
    assert.deepEqual(rows[id], first[id], `${id} changed, and nothing of it was archived`);
  }

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

  // ARCHIVED TRADES COUNT. Everything archived: its share only, not the intruder's.
  assert.equal(rows.e_arch.filled_all_time, 3);
  assert.equal(rows.e_arch.won_all_time, 2);
  assert.equal(n(rows.e_arch.net_pnl), 5.5);
  assert.equal(n(rows.e_arch.win_rate_pct), 66.7);
  assert.equal(rows.e_arch.fired_30d, 0);
  assert.equal(rows.e_arch.verdict, 'on, but nothing has met its conditions in 30 days');
  // Live and archived together: 2 live (+3, -1) and 2 archived (-3, +0.75).
  assert.equal(rows.e_mix.fired_30d, 2);
  assert.equal(rows.e_mix.filled_all_time, 4);
  assert.equal(rows.e_mix.won_all_time, 2);
  assert.equal(n(rows.e_mix.net_pnl), -0.25);
  assert.equal(n(rows.e_mix.win_rate_pct), 50);
  assert.equal(rows.e_mix.verdict, 'too few settled trades to judge - 4 of 30, net $-0.25 on its own paper ledger');
  // A row from before by_strategy: totals count, wins are unknown - not zero.
  assert.equal(rows.e_legacy.filled_all_time, 3);
  assert.equal(n(rows.e_legacy.net_pnl), 8);
  assert.equal(rows.e_legacy.won_all_time, null);
  assert.equal(rows.e_legacy.win_rate_pct, null);
  // A desk's archive and another event type on the ledger never count.
  assert.equal(rows.e_none.filled_all_time, 0);
  assert.equal(rows.e_none.net_pnl, null);

  console.log('PASS: board-engine-ledgers: signal strategies read their signals as before; an engine strategy reads its own shadow ledger (bought, settled, won, net, win rate) and waits for 30 settled trades; desks, other ledgers and non-engine shadow trades never count; trades the export pruned are added back from trades_archived (its own share, its own ledger; unknown wins stay null); re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
