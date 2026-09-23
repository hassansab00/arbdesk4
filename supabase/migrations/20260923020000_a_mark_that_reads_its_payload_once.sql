-- ===========================================================================
-- v_strategy_board, 163,507 buffers -> 54,184, and not one number different.
--
-- The board is on the Strategies and Analytics pages. It was 3.5 s warm and
-- more cold on this instance, and pg_stat_statements had it at a 4.5 s mean
-- over 294 calls - close enough to the browser's 8 s limit that a busy minute
-- turned it into 57014. Nearly all of that was v_signal_mark, which the board
-- reads for every settled ENTER signal:
--
--   * signals.payload is TOASTed for 71% of signals (avg 3 KB, 20 MB of
--     TOAST) and Postgres re-reads it for every reference - eight per row.
--   * legs_settled / legs_yes were correlated subqueries in an inlined CTE,
--     so each output column naming legs_yes re-ran its own copy.
--
-- The payload is now read three times per row in a materialised step, and one
-- lateral counts both kinds of leg. fact_band_outcome is keyed by band_id, so
-- the join adds no rows. Verified identical in one REPEATABLE READ snapshot
-- before it was applied: 3,624 marks and the board's 10 rows, EXCEPT ALL both
-- ways, zero.
--
-- The same statement is in sql/ad4_33_control.sql; tests/test_the_board_asks_
-- the_signals_not_the_desk.py holds the two copies equal.
-- ===========================================================================

create or replace view public.v_signal_mark as
-- THE PAYLOAD, READ THREE TIMES INSTEAD OF EIGHT - AND THE LEGS, ONCE.
--
-- signals.payload is over 2 KB for 71% of signals (avg 3 KB, 20 MB of TOAST),
-- so it lives out of line, and Postgres re-reads it for EVERY reference: the
-- summed / legs / leg_ids expressions below named it eight times per row.
-- legs_settled and legs_yes were correlated subqueries in a CTE the planner
-- inlines, so each of the output columns that mentions legs_yes ran its own
-- copy. Together: 163,507 buffers for v_strategy_board's ten rows.
--
-- `raw` reads the three things the mark needs from the payload once each and
-- is materialised, so everything after it works on small values in memory;
-- one lateral counts both kinds of leg in a single pass. fact_band_outcome is
-- keyed by band_id, so the left join adds no rows and the two counts are the
-- two EXISTS counts they replace. Verified identical before it was applied -
-- 3,624 marks and the board's 10 rows, none differing in either direction -
-- and the board fell to 54,184 buffers.
with raw as materialized (
  select f.signal_id, f.strategy_id, f.side, f.price_at_fire, f.band_id,
         s.payload ? 'band_ids'     as has_band_ids,
         s.payload ? 'basket_group' as has_basket_group,
         s.payload -> 'band_ids'    as band_ids
    from public.fact_signal_outcome f
    join public.signals s on s.signal_id = f.signal_id
   where f.action = 'ENTER'
     and f.side in ('YES', 'NO')
     and f.price_at_fire is not null
     and f.price_at_fire > 0
     and f.price_at_fire < 1
),
shaped as (
  select r.signal_id,
         r.strategy_id,
         r.side,
         r.price_at_fire                                            as price_at_fire,
         -- s2/s6 carry the whole basket in one signal; s8/s9 carry a leg each
         -- and tie them together with basket_group.
         r.has_band_ids and not r.has_basket_group                  as summed,
         case when r.has_band_ids and not r.has_basket_group
              then coalesce(jsonb_array_length(r.band_ids), 1)
              else 1 end                                            as legs,
         case when r.has_band_ids and not r.has_basket_group
              then r.band_ids
              else to_jsonb(array[r.band_id::text]) end             as leg_ids
    from raw r
),
counted as (
  select h.*, n.legs_settled, n.legs_yes
    from shaped h
    -- Every leg must have settled, or the basket has no outcome yet.
    cross join lateral (
      select count(*) filter (where b.settled_yes is not null) as legs_settled,
             count(*) filter (where b.settled_yes)             as legs_yes
        from jsonb_array_elements_text(h.leg_ids) t(id)
        left join public.fact_band_outcome b on b.band_id = t.id::uuid
    ) n
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


-- ---------------------------------------------------------------------------
-- v_strategy_board itself, as sql/ad4_33_control.sql defines it and as
-- production already runs it.
--
-- The retired-strategy verdict arm ("retired <date> - <record>") was added to
-- sql/ad4_33 and to the live database when the model-vs-price strategies were
-- retired, but no migration carried it: a database built from these
-- migrations would still say "off - it cannot propose anything" for a
-- strategy that was deliberately retired. The test meant to catch that
-- compared the wrong text - it stopped at a semicolon inside a comment - so
-- the drift was invisible. Carried here, unchanged from ad4_33.
-- ---------------------------------------------------------------------------
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
