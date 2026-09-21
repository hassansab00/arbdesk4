-- ===========================================================================
-- A STRATEGY'S RECORD MUST NOT DEPEND ON WHICH DESK SUBSCRIBED TO IT.
--
-- v_strategy_board is the page that answers "is this strategy any good". Its
-- `paid` CTE reads fact_signal_outcome and then filters `where filled` - and
-- `filled` is true only when a DESK took the signal. So the board's verdict
-- for five of nine strategies was:
--
--     s3_concentration      1,008 fired    'firing, but nothing has filled yet'
--     s6_anchor_insurance   1,361 fired    'firing, but nothing has filled yet'
--     s5_running_max_lock      37 fired    'firing, but nothing has filled yet'
--     s8 / s9                   4 fired    'firing, but nothing has filled yet'
--
-- while the same table already held, for those same signals, whether the call
-- was RIGHT - scored against settlement, with no desk involved:
--
--     s4_tail_fade      376 scored   292 correct   77.7%
--     s3_concentration  702 scored   113 correct   16.1%
--     s1                562 scored    68 correct   12.1%
--     s6                898 scored    79 correct    8.8%
--
-- The evidence was there the whole time. The board asked the desk instead.
--
-- WHAT "RIGHT" IS STILL NOT, and why this is more than re-pointing the view:
-- a hit rate is not a P&L. Being right 16% of the time on a band bought at 10c
-- makes money; being right 78% of the time on a band bought at 95c loses it.
-- So every signal is marked to settlement at the price that justified IT - no
-- desk, no cash, no approval, no min_edge - and the board reports return on
-- stake beside the hit rate.
--
-- THE TWO MARKS, because the strategies do not all mean the same thing by
-- price_at_fire, and one formula across both would be a fabricated number:
--
--   single_leg            s1 s3 s4 s5 s7, AND each leg of s8/s9. price_at_fire
--                         is that band's own executable ask, fee EXCLUSIVE.
--                         s8 and s9 emit one signal PER LEG carrying a shared
--                         payload.basket_group, so a leg is a real purchase of
--                         that band at that price and marks like any other.
--   basket_fee_inclusive  s2 and s6. One signal carries the SUM across legs and
--                         it is already fee-inclusive (s2 fee_inclusive_cost,
--                         s6 fee_inclusive_sum_ask). It pays if ANY leg lands,
--                         so marking it against the anchor band alone - which
--                         is what signal_correct still does - charges a
--                         five-band basket the outcome of one of its bands.
--                         That is why s6's 8.8% is not its hit rate.
--
-- A VIEW, NOT COLUMNS. The first cut of this added mark_* columns to
-- fact_signal_outcome and backfilled them, and ad4_70's append-only trigger
-- refused the UPDATE - correctly. These tables are an immutable record and a
-- derived number has no business being frozen into one: computed on read it
-- cannot drift from its formula, it covers every row banked before today
-- without a backfill, and a correction to the formula corrects the history.
--
-- Idempotent: two CREATE OR REPLACE views, no writes.
-- ===========================================================================

create or replace view public.v_signal_mark as
with shaped as (
  select f.signal_id,
         f.strategy_id,
         f.side,
         f.price_at_fire                                            as price_at_fire,
         -- s2/s6 carry the whole basket in one signal; s8/s9 carry a leg each
         -- and tie them together with basket_group.
         (s.payload ? 'band_ids') and not (s.payload ? 'basket_group')
                                                                    as summed,
         case when (s.payload ? 'band_ids') and not (s.payload ? 'basket_group')
              then coalesce(jsonb_array_length(s.payload -> 'band_ids'), 1)
              else 1 end                                            as legs,
         case when (s.payload ? 'band_ids') and not (s.payload ? 'basket_group')
              then s.payload -> 'band_ids'
              else to_jsonb(array[f.band_id::text]) end             as leg_ids
    from public.fact_signal_outcome f
    join public.signals s on s.signal_id = f.signal_id
   where f.action = 'ENTER'
     and f.side in ('YES', 'NO')
     and f.price_at_fire is not null
     and f.price_at_fire > 0
     and f.price_at_fire < 1
),
counted as (
  select h.*,
         -- Every leg must have settled, or the basket has no outcome yet.
         (select count(*) from jsonb_array_elements_text(h.leg_ids) t(id)
           where exists (select 1 from public.fact_band_outcome b
                          where b.band_id = t.id::uuid
                            and b.settled_yes is not null))          as legs_settled,
         (select count(*) from jsonb_array_elements_text(h.leg_ids) t(id)
           where exists (select 1 from public.fact_band_outcome b
                          where b.band_id = t.id::uuid
                            and b.settled_yes))                      as legs_yes
    from shaped h
)
select c.signal_id,
       c.strategy_id,
       c.price_at_fire,
       case when c.summed then 'basket_fee_inclusive' else 'single_leg' end as mark_basis,
       c.legs::integer                                              as mark_legs,
       -- A YES position pays when the day lands on any leg it bought; a NO
       -- position pays when it lands on none of them.
       (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                                                                    as mark_won,
       round((case when (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                   then 1 else 0 end) - c.price_at_fire, 6)         as mark_gross_per_share,
       -- s2/s6 already priced the fee into price_at_fire. Charging it again
       -- here would be the same dollar counted twice.
       case when c.summed then 0
            else round(0.05 * c.price_at_fire * (1 - c.price_at_fire), 6) end
                                                                    as mark_fee_per_share,
       round((case when (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                   then 1 else 0 end)
             - c.price_at_fire
             - case when c.summed then 0
                    else 0.05 * c.price_at_fire * (1 - c.price_at_fire) end, 6)
                                                                    as mark_net_per_share
  from counted c
 where c.legs_settled = c.legs;

comment on view public.v_signal_mark is
  'What ONE share of each settled signal, taken at the price that justified it, returned at settlement net of the venue fee. Desk-independent: no account, no cash, no approval. A signal whose legs have not all settled is absent rather than zero.';

revoke all on public.v_signal_mark from anon, authenticated;
grant select on public.v_signal_mark to service_role;


-- ===========================================================================
-- THE BOARD, RE-POINTED AT THE EVIDENCE IT ALREADY HAD.
--
-- filled_all_time / won_all_time / net_pnl / win_rate_pct KEEP their meaning -
-- they are the desk's realised record and deleting them would throw away the
-- only numbers that include real fills and real slippage. What changes is that
-- they are no longer the whole story, and no longer what the verdict is built
-- from.
--
-- New columns are appended rather than slotted in beside their relatives:
-- `create or replace view` may only add at the end, and mid-list Postgres
-- refuses with 42P16 rather than renaming every column after it.
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
  coalesce(f.n_fired, 0)                                     as fired_30d,
  coalesce(f.n_waiting, 0)                                   as waiting,
  f.last_fired_at,
  coalesce(p.n_filled, 0)                                    as filled_all_time,
  coalesce(p.n_won, 0)                                       as won_all_time,
  p.net_pnl,
  p.avg_slippage_c,
  case when coalesce(p.n_filled, 0) > 0
       then round(100.0 * p.n_won / p.n_filled, 1) end       as win_rate_pct,
  -- THE VERDICT NOW ASKS THE SIGNALS, NOT THE DESK. "Nothing has filled yet"
  -- described the desk's subscription list and read as a fact about the
  -- strategy; 30 days of it is how s3 and s6 looked unproven while 1,600
  -- settled signals of theirs sat in this same table.
  case
    when s.strategy_id = 'system'          then 'not a trading strategy'
    when not s.enabled                     then 'off - it cannot propose anything'
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
order by s.enabled desc,
         case when coalesce(sc.stake, 0) > 0 then sc.mark_net / sc.stake end desc nulls last,
         s.strategy_id;

comment on view v_strategy_board is
  'Every strategy with its own record beside its switch. The record is its SIGNALS marked to settlement - no desk, no cash, no approval - because whether a desk subscribed is a deployment choice and not evidence about the strategy. The filled_* and net_pnl columns remain: those are the realised desk record, which is the only place real slippage appears.';
