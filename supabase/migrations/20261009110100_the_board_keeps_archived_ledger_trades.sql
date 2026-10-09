-- ===========================================================================
-- THE BOARD KEEPS AN ENGINE LEDGER'S ARCHIVED TRADES (9 Oct; review of #351).
--
-- 20261009100000 made an engine strategy's all-time record read its own
-- shadow ledger's paper_trades. The daily export prunes a closed trade 30 days
-- after it closes, so from the first engine trade to go (s12_no's, closed
-- 30 Sep 08:36Z: the export run of 31 Oct) those counts, the net and the
-- verdict would have fallen back. The prune's trades_archived row now carries
-- per strategy the trades, wins and net it took (20261009110000), and the
-- board adds them back: ledger = ledger_live + ledger_archived. A row written
-- before that key existed counts its totals and leaves wins unknown (null),
-- never zero; won_all_time says so too.
--
-- No trades_archived row exists yet (9 Oct), so today this returns exactly
-- what 20261009100000 returns: proved live in one statement, EXCEPT ALL both
-- ways, before applying. The statement is sql/ad4_33's, verbatim
-- (tests/test_the_board_asks_the_signals_not_the_desk.py). CREATE OR REPLACE
-- keeps the columns, owner and grants. Nothing is deleted. Re-runnable.
-- ===========================================================================

create or replace view v_strategy_board as
with fired as (
  select strategy_id,
         count(*)::int                                       as n_fired,
         count(*) filter (where status = 'pending_approval')::int as n_waiting,
         max(fired_at)                                       as last_fired_at
    from signals
   where fired_at > now() - interval '30 days'
   group by strategy_id
),
paid as (
  select strategy_id,
         count(*) filter (where filled)::int                 as n_filled,
         count(*) filter (where filled and coalesce(net_pnl, 0) > 0)::int as n_won,
         round(sum(coalesce(net_pnl, 0)) filter (where filled), 2) as net_pnl,
         round(avg(slippage_c) filter (where filled), 2)     as avg_slippage_c
    from fact_signal_outcome
   group by strategy_id
),
-- THE DESK-INDEPENDENT RECORD. No join to an account, an order or a fill.
scored as (
  select f.strategy_id,
         count(f.signal_correct)::int                         as n_scored,
         count(*) filter (where f.signal_correct)::int         as n_correct,
         count(m.signal_id)::int                              as n_marked,
         count(*) filter (where m.mark_won)::int              as n_mark_won,
         round(sum(m.price_at_fire), 4)                       as stake,
         round(sum(m.mark_net_per_share), 4)                  as mark_net,
         -- One strategy uses one convention, so max() names it without
         -- collapsing two different meanings into one row.
         max(m.mark_basis)                                    as mark_basis
    from fact_signal_outcome f
    left join public.v_signal_mark m on m.signal_id = f.signal_id
   group by f.strategy_id
),
-- THE ENGINE'S OWN RECORD (9 Oct). S10, S11, S12 and their model-only twins
-- fire no signals: they decide through the engine (decisions, kept three
-- days) and trade a shadow ledger of their own, one strategy to a ledger
-- (P5.1), so that ledger is no desk's choice but the strategy itself. Its
-- paper trades are the record - each a fill against the venue's book and a
-- settlement on its resolution. Until 9 Oct the board read signals alone, so
-- the twins that had bought (23, 5 and 2 trades) read "nothing has met its
-- conditions in 30 days".
ledger_live as (
  select t.strategy_id,
         count(*) filter (where t.opened_at > now() - interval '30 days')::int as n_bought_30d,
         max(t.opened_at)                                    as last_bought_at,
         count(*)::int                                       as n_bought,
         count(t.closed_at)::int                             as n_settled,
         count(*) filter (where t.closed_at is not null and t.net_pnl > 0)::int as n_won,
         round(sum(t.net_pnl) filter (where t.closed_at is not null), 2) as net_pnl
    from paper_trades t
    join paper_accounts a on a.account_id = t.account_id
                         and a.kind = 'shadow' and a.strategy_id = t.strategy_id
    join strategies e on e.strategy_id = t.strategy_id
   where coalesce(e.origin, e.extra ->> 'origin') = 'engine'
   group by t.strategy_id
),
-- ...AND WHAT LEFT FOR THE REPOSITORY. The daily export prunes a closed trade
-- 30 days after it closes (prune_exported_paper_trades), and the
-- trades_archived row it writes on the ledger carries, per strategy, the
-- trades, wins and net it took (20261009110000). They are added back, so the
-- all-time record does not fall when the rows leave. A row without by_strategy
-- (written before that key existed) counts its totals and leaves wins
-- unknown, never zero.
ledger_archived as (
  select a.strategy_id,
         sum(case when x.payload ? 'by_strategy'
                  then coalesce((x.payload -> 'by_strategy' -> a.strategy_id ->> 'trades')::int, 0)
                  else (x.payload ->> 'trades')::int end)::int         as n_trades,
         sum(case when x.payload ? 'by_strategy'
                  then coalesce((x.payload -> 'by_strategy' -> a.strategy_id ->> 'won')::int, 0) end)::int as n_won,
         bool_and(x.payload ? 'by_strategy')                         as won_known,
         sum(case when x.payload ? 'by_strategy'
                  then coalesce((x.payload -> 'by_strategy' -> a.strategy_id ->> 'realized_pnl')::numeric, 0)
                  else (x.payload ->> 'realized_pnl')::numeric end)  as net_pnl
    from paper_activity x
    join paper_accounts a on a.account_id = x.account_id and a.kind = 'shadow'
    join strategies e on e.strategy_id = a.strategy_id
   where x.event_type = 'trades_archived'
     and coalesce(e.origin, e.extra ->> 'origin') = 'engine'
   group by a.strategy_id
),
ledger as (
  select strategy_id,
         coalesce(v.n_bought_30d, 0)                         as n_bought_30d,
         v.last_bought_at,
         coalesce(v.n_bought, 0) + coalesce(r.n_trades, 0)   as n_bought,
         coalesce(v.n_settled, 0) + coalesce(r.n_trades, 0)  as n_settled,
         case when r.strategy_id is null then v.n_won
              when r.won_known then coalesce(v.n_won, 0) + r.n_won end as n_won,
         case when v.net_pnl is null and r.net_pnl is null then null
              else round(coalesce(v.net_pnl, 0) + coalesce(r.net_pnl, 0), 2) end as net_pnl
    from ledger_live v
    full join ledger_archived r using (strategy_id)
)
select
  s.strategy_id,
  s.name,
  s.side,
  coalesce(s.origin, s.extra ->> 'origin')                   as origin,
  s.enabled,
  s.conflict_class,
  s.universe,
  s.regime_filter,
  s.capital_cap_pct,
  s.max_concurrent,
  -- An engine strategy's columns read its ledger (above); every other row
  -- reads exactly what it read before.
  coalesce(f.n_fired, l.n_bought_30d, 0)                     as fired_30d,
  coalesce(f.n_waiting, 0)                                   as waiting,
  coalesce(f.last_fired_at, l.last_bought_at)                as last_fired_at,
  coalesce(p.n_filled, l.n_bought, 0)                        as filled_all_time,
  -- An engine ledger whose archived wins are unknown says so (null), not 0.
  case when p.n_won is null and l.strategy_id is not null then l.n_won
       else coalesce(p.n_won, 0) end                       as won_all_time,
  coalesce(p.net_pnl, l.net_pnl)                             as net_pnl,
  p.avg_slippage_c,
  case when coalesce(p.n_filled, 0) > 0
       then round(100.0 * p.n_won / p.n_filled, 1)
       when coalesce(l.n_settled, 0) > 0
       then round(100.0 * l.n_won / l.n_settled, 1) end     as win_rate_pct,
  -- THE VERDICT NOW ASKS THE SIGNALS, NOT THE DESK. "Nothing has filled yet"
  -- described the desk's subscription list and read as a fact about the
  -- strategy; 30 days of it is how s3 and s6 looked unproven while 1,600
  -- settled signals of theirs sat in this same table.
  case
    when s.strategy_id = 'system'          then 'not a trading strategy'
    -- A RETIREMENT THAT ERASES ITS OWN REASON. Every arm below reads the
    -- record; `not s.enabled` used to short-circuit past all of them, so the
    -- moment a measured verdict was ACTED on - s1 at -7.6c on the dollar over
    -- 615 marked signals - the board stopped saying it and said "off"
    -- instead. The number stayed in return_on_stake_pct beside it; the
    -- sentence a reader actually reads did not. So a strategy carrying a
    -- retirement stamp states the stamp, which is the reason recorded at the
    -- time rather than one re-derived now from whatever has settled since.
    when not s.enabled
     and coalesce(s.extra, '{}'::jsonb) ? 'retired_on'
                                           then format('retired %s - %s',
                                                  s.extra ->> 'retired_on',
                                                  coalesce(s.extra ->> 'retired_record',
                                                           s.extra ->> 'retired_because'))
    when not s.enabled                     then 'off - it cannot propose anything'
    -- An engine strategy that has bought, judged on its own ledger: settled
    -- trades, the same 30 the signals need.
    when coalesce(f.n_fired, 0) = 0 and coalesce(l.n_bought_30d, 0) > 0
     and l.n_settled = 0                   then format('trading on its own paper ledger: %s bought, none settled yet',
                                                  l.n_bought)
    when coalesce(f.n_fired, 0) = 0 and coalesce(l.n_bought_30d, 0) > 0
     and l.n_settled < 30                  then format('too few settled trades to judge - %s of 30, net $%s on its own paper ledger',
                                                  l.n_settled, l.net_pnl)
    when coalesce(f.n_fired, 0) = 0 and coalesce(l.n_bought_30d, 0) > 0
     and l.net_pnl > 0                     then 'profitable on its own paper ledger'
    when coalesce(f.n_fired, 0) = 0 and coalesce(l.n_bought_30d, 0) > 0
                                           then 'losing on its own paper ledger'
    when coalesce(f.n_fired, 0) = 0        then 'on, but nothing has met its conditions in 30 days'
    when coalesce(sc.n_scored, 0) = 0      then 'firing, but nothing it fired has settled yet'
    -- 30 is where a binomial is usable under the normal approximation, which
    -- is the smallest sample a hit rate can honestly be quoted from.
    when coalesce(sc.n_scored, 0) < 30     then 'too few settled signals to judge - under 30'
    when coalesce(sc.stake, 0) = 0         then 'settled, but nothing it fired could be marked to a price'
    when coalesce(sc.mark_net, 0) > 0      then 'profitable on its own signals, before any desk'
    else                                        'losing on its own signals, before any desk'
  end                                                        as verdict,
  -- --- appended: the record that owes nothing to a desk -------------------
  coalesce(sc.n_scored, 0)                                   as settled_signals,
  coalesce(sc.n_correct, 0)                                  as correct_signals,
  case when coalesce(sc.n_scored, 0) > 0
       then round(100.0 * sc.n_correct / sc.n_scored, 1) end as hit_rate_pct,
  coalesce(sc.n_marked, 0)                                   as marked_signals,
  sc.stake                                                   as mark_stake_per_share,
  sc.mark_net                                                as mark_net_per_share,
  -- What a dollar committed to this strategy's own calls came back as. The
  -- number a hit rate cannot give you: 16% right at 10c beats 78% right at 95c.
  case when coalesce(sc.stake, 0) > 0
       then round(100.0 * sc.mark_net / sc.stake, 1) end     as return_on_stake_pct,
  sc.mark_basis,
  -- THE HIT RATE THAT IS TRUE OF A BASKET. hit_rate_pct above comes from
  -- signal_correct, which scores a signal against ITS OWN band - right for a
  -- single leg, and for s6 a five-band cover judged by one of its five. Live:
  -- s6 reads 8.8% there and wins 54% of the time here, and neither number is a
  -- typo. Both are kept because signal_correct is what the fitter was trained
  -- against and rewriting it would silently change that history.
  case when coalesce(sc.n_marked, 0) > 0
       then round(100.0 * sc.n_mark_won / sc.n_marked, 1) end as mark_win_rate_pct
from strategies s
left join fired  f  on f.strategy_id  = s.strategy_id
left join paid   p  on p.strategy_id  = s.strategy_id
left join scored sc on sc.strategy_id = s.strategy_id
left join ledger l  on l.strategy_id  = s.strategy_id
order by s.enabled desc,
         case when coalesce(sc.stake, 0) > 0 then sc.mark_net / sc.stake end desc nulls last,
         s.strategy_id;

comment on view v_strategy_board is
  'Every strategy with its own record beside its switch. The record is its SIGNALS marked to settlement - no desk, no cash, no approval - because whether a desk subscribed is a deployment choice and not evidence about the strategy. The filled_* and net_pnl columns remain: those are the realised desk record, which is the only place real slippage appears. An engine strategy (S10, S11, S12 and the model-only twins) fires no signals; its columns and verdict read the paper trades of its own shadow ledger, one strategy to a ledger, plus those the daily export has moved to the repository (that ledger''s trades_archived rows, by_strategy).';
