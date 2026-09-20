-- ===========================================================================
-- ad4_76_strategy_desk_board.sql - STRATEGIES, PER DESK.
--
-- "all te trades and strateies are in one place, not specific to te paper
-- desk selected." The trades were already per desk; PaperTradeHistory filters
-- on account_id. The strategies were not, and could not be.
--
-- /strategies reads v_strategy_board, which aggregates signals and
-- fact_signal_outcome - neither of which carries an account. So it answers
-- "has this strategy ever worked anywhere", which is a real question and the
-- wrong one when four desks are running four different policies. Measured
-- 20 Sep, the four desks allow:
--
--     Wide edge, all US     all nine          automatic, running
--     All Cities            s7, s8, s9 only   automatic, min_edge 0.23
--     Austin only           s1 only           assisted, paused
--     Main paper account    none at all       manual, paused
--
-- On the global board every one of those desks looks identical. A strategy
-- that has never been allowed to run HERE reads the same as one that ran here
-- and lost.
--
-- WHAT THIS ANSWERS INSTEAD, for one desk at a time:
--
--   allowed_here        is it in THIS desk's policy - the switch that decides
--                       whether it may proposeenything on this desk at all
--   enabled_globally    the /strategies toggle, which outranks the policy
--   proposed / blocked / took
--                       what it has actually done on THIS desk
--   last_block_reason   and when it proposed and got stopped, the reason -
--                       which is the sentence that was missing. 284 of 360
--                       plans on this database are blocked, and the single
--                       most common reason is the venue's minimum order size,
--                       not anything about the strategy.
--
-- It reads paper_trade_plans and paper_orders, which carry account_id, rather
-- than signals, which does not. That also makes it honest about the plan
-- statuses fixed in 20260920150000: before that migration every plan here
-- would have read "queued" forever and `took` would have been zero on a desk
-- holding 61 fills.
--
-- P&L IS DELIBERATELY NOT HERE. paper_trades carries strategy_id but no
-- account_id, so a per-desk profit number would have to be reconstructed by
-- joining back through orders, and a reconstructed number presented beside
-- measured ones is how the two get confused. Fills per desk is measured;
-- money per desk is a separate piece of work that needs the column.
--
-- RUN ORDER: after ad4_33_control.sql (v_strategy_board) and the paper desk
-- migrations. Re-runnable.
-- ===========================================================================

create or replace view public.v_strategy_desk_board as
with plans as (
  select p.account_id,
         p.strategy_id,
         count(*)::integer                                              as proposed,
         count(*) filter (where p.status = 'blocked')::integer          as blocked,
         count(*) filter (where p.status in ('filled','partial'))::integer as took,
         count(*) filter (where p.status = 'queued')::integer           as working,
         max(p.created_at)                                              as last_proposed_at,
         -- The newest reason a plan was stopped here. Ordered by created_at
         -- rather than picked arbitrarily, because "why did it stop" is only
         -- useful if it is the most recent why.
         (array_agg(p.reason order by p.created_at desc)
            filter (where p.status = 'blocked' and p.reason is not null))[1] as last_block_reason
    from public.paper_trade_plans p
   group by p.account_id, p.strategy_id
),
fills as (
  select o.account_id,
         o.strategy_id,
         count(*) filter (where o.status in ('filled','partial'))::integer as orders_filled
    from public.paper_orders o
   where o.strategy_id is not null
   group by o.account_id, o.strategy_id
)
select a.account_id,
       a.name                                       as desk_name,
       a.mode,
       a.entries_paused,
       s.strategy_id,
       s.name                                       as strategy_name,
       s.enabled                                    as enabled_globally,
       coalesce(a.policy -> 'strategies' @> to_jsonb(s.strategy_id), false) as allowed_here,
       coalesce(pl.proposed, 0)                     as proposed,
       coalesce(pl.blocked, 0)                      as blocked,
       coalesce(pl.took, 0)                         as took,
       coalesce(pl.working, 0)                      as working,
       coalesce(f.orders_filled, 0)                 as orders_filled,
       pl.last_proposed_at,
       pl.last_block_reason,
       -- The sentence the page shows. Order matters: the reasons a strategy
       -- is doing nothing are checked from the outermost switch inward, so
       -- "not switched on here" never reads as "never met its conditions".
       case
         when not s.enabled                          then 'off globally - the Strategies toggle outranks this desk'
         when not coalesce(a.policy -> 'strategies' @> to_jsonb(s.strategy_id), false)
                                                     then 'not allowed on this desk - tick it under Settings'
         when a.entries_paused                       then 'allowed, but this desk is paused'
         when a.mode = 'manual'                      then 'allowed, but this desk is manual - strategies do not propose'
         when coalesce(pl.proposed, 0) = 0           then 'allowed and running, but nothing has met its conditions here'
         when coalesce(pl.took, 0) = 0               then 'proposing here, but nothing has got through'
         else                                             'trading on this desk'
       end                                          as verdict
  from public.paper_accounts a
 cross join public.strategies s
  left join plans pl on pl.account_id = a.account_id and pl.strategy_id = s.strategy_id
  left join fills f  on f.account_id  = a.account_id and f.strategy_id  = s.strategy_id
 where a.archived_at is null
   and s.strategy_id <> 'system';

comment on view public.v_strategy_desk_board is
  'One row per desk per strategy: whether it is allowed there, what it has proposed and taken there, and the newest reason a plan was blocked there. v_strategy_board answers the same questions across every desk at once, which is the wrong grain when each desk runs its own policy.';

-- SERVICE ROLE ONLY, like every other desk read. This view selects from
-- paper_accounts, whose RLS deliberately limits a desk to its owner or to the
-- shared single-desk row - and a view runs as its owner, so granting anon here
-- would hand every desk's name and policy to any browser that asked. The page
-- reads it through /api/paper-desk with the service key, exactly as it already
-- reads orders, positions, activity and plans.
revoke all on public.v_strategy_desk_board from anon, authenticated;
grant select on public.v_strategy_desk_board to service_role;
