-- ===========================================================================
-- THE MARK SAYS WHEN IT FIRED (29 Sep audit, repair 5).
--
-- signal_engine._earned_weights reads v_signal_mark filtered on fired_at >=
-- 45 days ago. The view never had that column, so every run got HTTP 400,
-- printed a note, kept every strategy at full weight and logged 'ok'. The
-- weights are logged, not applied to a shadow ledger, so nothing was sized
-- wrongly; they simply were never computed.
--
-- fired_at is appended as the view's last column, from fact_signal_outcome,
-- which the view already reads (its fired_at equals signals.fired_at on every
-- row, 30 Sep). Nothing before it changes; its one dependent,
-- v_strategy_board, names the columns it reads. The statement is
-- sql/ad4_33_control.sql's text. Re-runnable: create or replace.
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
-- one lateral counts both kinds of leg in a single pass. The band outcome
-- (v_fact_band_outcome_clean since plan v2 P4.4) is keyed by band_id, so the left join adds no rows and the two counts are the
-- two EXISTS counts they replace. Verified identical before it was applied -
-- 3,624 marks and the board's 10 rows, none differing in either direction -
-- and the board fell to 54,184 buffers.
with raw as materialized (
  select f.signal_id, f.strategy_id, f.side, f.price_at_fire, f.band_id, f.fired_at,
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
              else to_jsonb(array[r.band_id::text]) end             as leg_ids,
         r.fired_at
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
        left join public.v_fact_band_outcome_clean b on b.band_id = t.id::uuid
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
                                                                    as mark_net_per_share,
       -- When it fired: signal_engine._earned_weights reads the last 45 days
       -- by it, and got HTTP 400 on every run while the view had no such
       -- column (29 Sep audit, repair 5). Last, so no column before it moves.
       c.fired_at
  from counted c
 where c.legs_settled = c.legs;

comment on view public.v_signal_mark is
  'What ONE share of each settled signal, taken at the price that justified it, returned at settlement net of the venue fee, and when it fired. Desk-independent: no account, no cash, no approval. A signal whose legs have not all settled is absent rather than zero.';

revoke all on public.v_signal_mark from anon, authenticated;
grant select on public.v_signal_mark to service_role;
