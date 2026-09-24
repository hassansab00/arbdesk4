-- ===========================================================================
-- THE EARLY RECORD, QUARANTINED - NOT DELETED (plan v2 P4.4)
--
-- fact_band_outcome rows captured before 13 Sep were settled from the desk's
-- own reading, through a reader with an interval/unit bug. Measured 24 Sep:
-- 531 city-days (26 Aug - 5 Sep); on the 43 where both the desk and the venue
-- named a winner, 37 disagree. They fed calibration, the ad4_45 width
-- feedback, the reliability haircut, strategy marks and the hit tournament.
--
-- Nothing is deleted or edited - fact_band_outcome is immutable, and stays
-- the record of what the desk believed. v_fact_band_outcome_clean is what
-- every learner and scorer reads instead:
--
--   captured on/after 13 Sep  the row as banked (outcome_provenance 'recorded')
--   captured before 13 Sep    only where the venue has CONFIRMED the band and
--                             its market (stored once in
--                             fact_band_outcome_venue_rebuilt): settled_yes is
--                             the venue's answer,
--                             observed_max_c is withheld (it came from the
--                             faulty reader), outcome_provenance 'venue_rebuilt'
--   everything else early     absent
--   any band in fact_band_outcome_exclusions  absent, with its reason on record
--
-- Owner-rights, granted like fact_band_outcome itself: security_invoker
-- views anon reads (v_verified_fact_band_outcome, v_calibration, ...) sit on
-- top of it, and anon may already read the raw table and the venue views.
-- ===========================================================================

create table if not exists public.fact_band_outcome_exclusions (
  band_id      uuid        primary key,
  reason       text        not null,
  recorded_at  timestamptz not null default clock_timestamp()
);
comment on table public.fact_band_outcome_exclusions is
  'Bands whose banked outcome is known wrong, with the reason. Append-only: v_fact_band_outcome_clean leaves them out; fact_band_outcome keeps them.';
drop trigger if exists fact_band_outcome_exclusions_immutable on public.fact_band_outcome_exclusions;
create trigger fact_band_outcome_exclusions_immutable before update or delete on public.fact_band_outcome_exclusions
  for each row execute function arbdesk_private.immutable_record();
alter table public.fact_band_outcome_exclusions enable row level security;
revoke all on public.fact_band_outcome_exclusions from public, anon, authenticated, service_role;
grant select, insert on public.fact_band_outcome_exclusions to service_role;

-- THE VENUE'S ANSWER FOR THE EARLY DAYS, STORED ONCE. The early set is closed
-- - nothing banked from now on is captured before 13 Sep - so the venue's
-- confirmed answer for each early band is written here once, with its source,
-- and read by key. The venue can still prove more of them (24 Sep: 2,027 of
-- 5,605 early bands confirmed, 3,578 unverified or partial), so
-- pipeline_daily calls rebuild_early_band_outcomes() after the settlement
-- sweep; it only ever adds. (A union over the venue views was 0.66 s a scan, and the
-- readers that look up one band at a time - v_signal_mark, per leg - ran it
-- once per lookup: a rolled-back rehearsal hit the statement timeout.)
create table if not exists public.fact_band_outcome_venue_rebuilt (
  band_id      uuid        primary key,
  settled_yes  boolean     not null,
  source       text        not null default 'v_venue_band_resolution:confirmed',
  rebuilt_at   timestamptz not null default clock_timestamp()
);
comment on table public.fact_band_outcome_venue_rebuilt is
  'For bands banked before 13 Sep: the venue''s confirmed answer, written once by rebuild_early_band_outcomes() (plan v2 P4.4). Append-only.';
drop trigger if exists fact_band_outcome_venue_rebuilt_immutable on public.fact_band_outcome_venue_rebuilt;
create trigger fact_band_outcome_venue_rebuilt_immutable before update or delete on public.fact_band_outcome_venue_rebuilt
  for each row execute function arbdesk_private.immutable_record();
alter table public.fact_band_outcome_venue_rebuilt enable row level security;
revoke all on public.fact_band_outcome_venue_rebuilt from public, anon, authenticated, service_role;
grant select, insert on public.fact_band_outcome_venue_rebuilt to service_role;

-- Idempotent: adds the early bands the venue has confirmed since the last
-- call, touches nothing already written. Returns the number added.
create or replace function public.rebuild_early_band_outcomes()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare n integer;
begin
  insert into public.fact_band_outcome_venue_rebuilt (band_id, settled_yes)
  select f.band_id, br.settled_yes
    from public.fact_band_outcome f
    join public.bands b on b.band_id = f.band_id
    join public.v_venue_band_resolution br on br.band_id = f.band_id and br.resolution_state = 'confirmed'
    join public.v_venue_market_resolution mr on mr.market_id = b.market_id and mr.resolution_state = 'confirmed'
   where f.captured_at < '2026-09-13'
     and br.settled_yes is not null
  on conflict (band_id) do nothing;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function public.rebuild_early_band_outcomes() from public, anon, authenticated;
grant execute on function public.rebuild_early_band_outcomes() to service_role;
select public.rebuild_early_band_outcomes();

create or replace view public.v_fact_band_outcome_clean as
select f.band_id, f.city_key, f.for_date, f.band_lo, f.band_hi, f.open_low, f.open_high,
       f.model_prob, f.sigma_c, f.confidence, f.regime_label, f.forecast_max_c,
       f.market_price, f.edge_net_pp, f.volume_usd, f.depth_5c, f.priced_at,
       case when f.captured_at >= '2026-09-13' then f.observed_max_c end as observed_max_c,
       case when f.captured_at >= '2026-09-13' then f.settled_yes else r.settled_yes end as settled_yes,
       f.captured_at,
       case when f.captured_at >= '2026-09-13' then f.obs_source end as obs_source,
       case when f.captured_at >= '2026-09-13' then 'recorded' else 'venue_rebuilt' end as outcome_provenance
  from public.fact_band_outcome f
  left join public.fact_band_outcome_venue_rebuilt r on r.band_id = f.band_id
 where (f.captured_at >= '2026-09-13' or r.band_id is not null)
   and not exists (select 1 from public.fact_band_outcome_exclusions x where x.band_id = f.band_id);

comment on view public.v_fact_band_outcome_clean is
  'fact_band_outcome as the learners should see it (plan v2 P4.4): rows banked before 13 Sep appear only where the venue confirmed them, with the venue''s answer and no observed maximum; excluded bands are absent.';

revoke all on public.v_fact_band_outcome_clean from public;
grant select on public.v_fact_band_outcome_clean to anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- The readers that learn or score, pointed at the clean record. Each is its
-- previous definition with fact_band_outcome replaced by the clean view and
-- nothing else changed; v_signal_mark is sql/ad4_33_control.sql's text.
-- v_verified_fact_band_outcome names its columns: it was written `select f.*`,
-- which Postgres froze at the 20 columns fact_band_outcome had then, and
-- f.* over the clean view would add obs_source and outcome_provenance.
-- ---------------------------------------------------------------------------
create or replace view public.v_verified_fact_band_outcome
with (security_invoker = true) as
 SELECT f.band_id, f.city_key, f.for_date, f.band_lo, f.band_hi, f.open_low, f.open_high,
    f.model_prob, f.sigma_c, f.confidence, f.regime_label, f.forecast_max_c, f.market_price,
    f.edge_net_pp, f.volume_usd, f.depth_5c, f.priced_at, f.observed_max_c, f.settled_yes,
    f.captured_at
   FROM public.v_fact_band_outcome_clean f
     JOIN public.bands b ON b.band_id = f.band_id
     JOIN public.v_venue_band_resolution br ON br.band_id = f.band_id
     JOIN public.v_venue_market_resolution mr ON mr.market_id = b.market_id
     JOIN public.v_band_outcome_coherence c ON c.city_key = f.city_key AND c.for_date = f.for_date
  WHERE br.resolution_state = 'confirmed'::text AND mr.resolution_state = 'confirmed'::text
    AND c.coherent AND NOT f.settled_yes IS DISTINCT FROM br.settled_yes;

create or replace view public.v_signal_outcome as
select
  f.signal_id,
  f.strategy_id,
  f.band_id,
  f.city_key,
  f.for_date,
  f.side,
  f.action,
  f.reason,
  f.severity,
  f.status,
  f.filled,
  f.price_at_fire,
  f.prob_at_fire,
  f.edge_at_fire,
  f.fill_price,
  f.shares,
  f.gross_pnl,
  f.net_pnl,
  f.slippage_c,
  f.forecast_version,
  f.calibration_version,
  f.cost_version,

  -- ---- the three moments ----------------------------------------------
  f.fired_at                                       as decided_at,
  coalesce(f.settled_at, b.captured_at)            as settled_at,
  f.captured_at                                    as banked_at,
  case when f.fired_at is not null and coalesce(f.settled_at, b.captured_at) is not null
       then round(extract(epoch from (coalesce(f.settled_at, b.captured_at) - f.fired_at))
                  / 3600.0, 1) end                 as hours_to_settlement,

  -- ---- the band's result, and the desk's call --------------------------
  coalesce(f.settled_yes, b.settled_yes)           as settled_yes,
  coalesce(
    f.signal_correct,
    case
      when coalesce(f.settled_yes, b.settled_yes) is null then null
      when f.action is distinct from 'ENTER' then null   -- an exit is a different question
      when f.side = 'YES' then      coalesce(f.settled_yes, b.settled_yes)
      when f.side = 'NO'  then not  coalesce(f.settled_yes, b.settled_yes)
    end)                                           as signal_correct,

  coalesce(
    f.outcome_source,
    case when f.settled_yes is not null then 'frozen with the signal'
         when b.settled_yes is not null then 'joined from the band outcome'
         else 'not settled yet' end)               as outcome_source,
  b.observed_max_c,
  b.band_lo,
  b.band_hi
from fact_signal_outcome f
left join v_fact_band_outcome_clean b on b.band_id = f.band_id;

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
                                                                    as mark_net_per_share
  from counted c
 where c.legs_settled = c.legs;
