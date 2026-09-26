-- ===========================================================================
-- ad4_68_prediction_ladder_outcomes.sql - THE LADDER NEVER LEARNED THE ANSWER.
--
-- Safe to run any time. Replaces one view. Changes no data.
--
-- v_prediction_ladder is what the Predictive page reads, and it took its
-- outcome from two columns on `markets`:
--
--     m.settled_value
--     m.winning_band_id = b.band_id  AS won
--
-- Measured on 16 Sep, both are empty and always have been:
--
--     closed markets                     1,217
--     markets.settled_value non-null         0
--     markets.winning_band_id non-null       0
--     ladder rows                       19,788
--     ladder rows carrying an outcome        0
--
-- The only writer of those columns is scripts/settlement.py, and it is in no
-- workflow - grep across .github/workflows returns a single mention, inside an
-- error message. It has never run. So the page showed a prediction and, for
-- every day ever settled, a blank where the result should be, and the
-- scorecard could not score a single city-day.
--
-- THE ANSWER WAS BEING RECORDED THE WHOLE TIME, somewhere else. The venue
-- settlement path writes paper_resolution_evidence -> v_venue_band_resolution,
-- and the banked outcome path writes fact_band_outcome:
--
--     v_venue_band_resolution   2,236 bands, all confirmed, 203 settled YES
--     fact_band_outcome         7,233 bands, 269 settled YES, to 2026-09-15
--
-- Repointing the view fills 7,841 of 14,762 rows across 15 days, 6,276 of them
-- with an observed temperature as well. No new collection, no new pipeline.
--
--
-- TWO SOURCES, AND THE ORDER BETWEEN THEM IS THE POINT.
--
-- `won` asks which contract PAID, so the venue answers first: it is the only
-- authority on its own settlement, and a desk is scored on money rather than
-- on weather. Only its CONFIRMED state is trusted - v_venue_band_resolution
-- can hold a band whose evidence is incomplete, and a disputed row must not
-- masquerade as a result. fact_band_outcome answers when the venue has not,
-- which is most older days.
--
-- `settled_value` asks what the TEMPERATURE was, which the venue never states,
-- so it always comes from fact_band_outcome.observed_max_c. In Celsius, like
-- the column it replaces: settlement.py converted to the local unit at
-- comparison time rather than storing it converted.
--
-- outcome_source says which of the two answered, or null for a day not yet
-- settled. Without it a band that lost and a band nobody has resolved look
-- identical - which is exactly the failure this file exists to end, and it
-- would be careless to reintroduce it one column over.
--
-- Both joins are one-to-one, verified rather than assumed: 7,233 rows across
-- 7,233 distinct band_ids, and 2,236 across 2,236. A duplicate on either side
-- would silently multiply every ladder row.
--
-- The new columns are APPENDED. create or replace view requires the existing
-- columns to keep their position, name and type, and v_city_prediction_
-- confidence is built on this view. It reads only forward-looking rows and
-- never touches settled_value or won, so it is unaffected either way.
-- ===========================================================================

-- ===========================================================================
-- AND WHY THE PAGE TIMED OUT. Measured 2026-09-19 on the query the Predictive
-- page actually sends - `where for_date >= current_date limit 1000`:
--
--   20,831 ms  before
--      346 ms  after
--
-- Three separate causes, and none of them was a missing index on a table:
--
-- 1. THE PLANNER ESTIMATED v_venue_band_resolution AT ONE ROW. It returns
--    11,122. It is a GroupAggregate over a subquery, and a view has no
--    statistics, so Postgres believed a nested loop was free:
--
--      Nested Loop Left Join
--        Join Filter: (vb.band_id = b.band_id)
--        Rows Removed by Join Filter: 257,502,091   <- to return 1,000 rows
--        ->  Materialize (actual rows=11122 loops=23154)
--
--    No index fixes a bad row estimate. Only real statistics do, so the
--    resolution is MATERIALISED below, with a unique index on band_id.
--
-- 2. THE WINDOW FUNCTION BLOCKED THE DATE FILTER. band_index was computed as
--    row_number() over (partition by market_id order by band_lo), and a
--    window cannot be pushed past a WHERE - so asking for the next sixteen
--    days still built all 23,154 rows of the last sixty-one and then threw
--    10,483 of them away.
--
--    bands.band_index already holds exactly that number. Checked on all
--    18,005 rows: 0 null, 0 disagreeing with the derived value. Reading the
--    stored column lets the filter reach markets, which then returns 102 rows
--    instead of 1,495.
--
-- 3. THE EDGE HISTORY WAS SORTED ON EVERY READ. v_latest_edge is
--    `distinct on (band_id, side)`, and the only index was (band_id,
--    computed_at), so every read incrementally sorted 122,764 rows to keep
--    13,418. ad4_ix_edges_band_side_time serves the distinct directly.
-- ===========================================================================

-- The resolution, with statistics. The LIVE view is deliberately not
-- replaced: scripts/databank.py freezes band outcomes from
-- v_venue_band_resolution and must see a settlement the moment it is
-- confirmed. A review page can be an hour behind and nothing is decided on
-- it, so only v_prediction_ladder reads this copy.
create materialized view if not exists mv_venue_band_resolution as
  select * from v_venue_band_resolution;

-- Not optional: refresh ... concurrently requires a unique index, and without
-- concurrently a page reading mid-refresh blocks.
create unique index if not exists mv_vbr_band on mv_venue_band_resolution (band_id);
analyze mv_venue_band_resolution;

create or replace function public.refresh_venue_band_resolution()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare n integer;
begin
  refresh materialized view concurrently mv_venue_band_resolution;
  analyze mv_venue_band_resolution;
  select count(*) into n from mv_venue_band_resolution;
  return n;
end $$;

comment on materialized view mv_venue_band_resolution is
  'v_venue_band_resolution with statistics. The view is a GroupAggregate the planner estimates at one row when it returns eleven thousand, and that estimate turned the Predictive page into a 257-million-row nested loop. Refreshed hourly; the live view remains the source for anything that must see a settlement immediately.';

revoke all on function public.refresh_venue_band_resolution() from public, anon, authenticated;
grant execute on function public.refresh_venue_band_resolution() to service_role;
grant select on mv_venue_band_resolution to anon, authenticated, service_role;

-- pg_cron, not an Action: a matview refresh of eleven thousand rows costs the
-- database about a second and costs the minute budget nothing.
create extension if not exists pg_cron;
select cron.schedule('ad4_refresh_venue_band_resolution', '7 * * * *',
                     $$select public.refresh_venue_band_resolution()$$);

-- The distinct-on index the edge history never had.
create index if not exists ad4_ix_edges_band_side_time
  on edges (band_id, side, computed_at desc);
analyze edges;


-- ===========================================================================
-- AND WHY IT TIMED OUT AGAIN. Measured 2026-09-22, as the browser's role, on
-- the same query - `where for_date >= current_date limit 1000`:
--
--    3,726 ms  before
--       18 ms  after
--
-- The latest probability and the latest edge came from v_latest_prob and
-- v_latest_edge: `distinct on` over the WHOLE of band_probabilities and
-- edges. The planner estimated this view at 20 rows, so it merge-joined them
-- in band order - walking 69,339 edges (2,166 ms) and 33,448 probabilities
-- (1,457 ms) to use the latest row of 500 bands. Those two tables grow every
-- intraday run, so the page got slower every day until it hit 57014.
--
-- Driven from the band instead: one index probe per band on
-- band_probabilities_band_id_computed_at_forecast_version_key and one on
-- ad4_ix_edges_band_side_time. The ordering is copied exactly -
-- (computed_at desc, prob_id desc) for the probability, (side, computed_at
-- desc) per band for the edge, so a band still yields one row per side.
-- Verified identical before it was applied: 24,991 rows, 0 differing in
-- either direction (EXCEPT ALL both ways, same transaction).
-- ===========================================================================
-- ---------------------------------------------------------------------------
-- ONE ROW PER BAND: what was predicted and what happened, with no edge in it.
--
-- The edge is the only thing in the ladder that is per SIDE, and it is what
-- makes a band two rows. v_prediction_hindsight only ever wanted the band -
-- it read the two-row ladder and threw the second row away with a
-- `distinct on (..., band_id) order by (side = 'YES') desc` - but it still
-- paid for the edge probe on every settled band it discarded: 160,603 of its
-- 311,570 buffers. Everything per BAND lives here, once, and the ladder below
-- adds the edge to it; hindsight reads this and never touches edges at all.
-- ---------------------------------------------------------------------------
create or replace view public.v_prediction_ladder_bands as
select
  m.city_key,
  m.resolution_date as for_date,
  m.market_id,
  b.band_id,
  -- THE STORED COLUMN, not a window function over it. See cause 2 above.
  b.band_index,
  b.band_label,
  b.band_lo,
  b.band_hi,
  b.open_low,
  b.open_high,
  m.closed,
  -- The temperature the day actually reached. The venue does not publish one.
  fb.observed_max_c as settled_value,
  -- Which contract paid. Venue first, and only when it says confirmed.
  coalesce(
    case when vb.resolution_state = 'confirmed' then vb.settled_yes end,
    fb.settled_yes
  ) as won,
  p.raw_prob,
  p.calibrated_prob,
  coalesce(p.calibrated_prob, p.raw_prob) as model_prob,
  p.forecast_max_c,
  p.sigma_c,
  p.confidence,
  p.regime_label,
  case
    when vb.resolution_state = 'confirmed' and vb.settled_yes is not null then 'venue'
    when fb.settled_yes is not null then 'weather'
  end as outcome_source,
  coalesce(
    case when vb.resolution_state = 'confirmed' then vb.confirmed_at end,
    fb.captured_at
  ) as settled_at
-- THE CANONICAL LADDER (plan v2 P2.5). Raw `bands` stores the inclusive
-- convention and zero-width labels its collectors wrote before 6 Sep; the
-- canonical views apply the append-only corrections. Measured 23 Sep: of
-- 17,611 rows, 7,340 change, every one a corrected band whose bounds differ,
-- all dated 21 Aug - 5 Sep. Nothing after 5 Sep changes. Needs
-- 20260923150000: the canonical views run with owner rights.
from v_canonical_markets m
-- ACTIVE ONLY. The Predictive page is where a city is judged; a retired one
-- has stopped being judged. Its settled history stays readable in
-- fact_band_outcome and the archive views, which is where history belongs.
join cities ct on ct.city_key = m.city_key
              and coalesce(ct.status, 'active') = 'active'
join v_canonical_bands b on b.market_id = m.market_id
-- v_latest_prob, for this band only. Same order, including the tie-break.
left join lateral (
  select bp.raw_prob, bp.calibrated_prob, bp.forecast_max_c, bp.sigma_c,
         bp.confidence, bp.regime_label
    from band_probabilities bp
   where bp.band_id = b.band_id
   order by bp.computed_at desc, bp.prob_id desc
   limit 1
) p on true
left join mv_venue_band_resolution vb on vb.band_id = b.band_id
left join v_fact_band_outcome_clean fb on fb.band_id = b.band_id
where m.resolution_date >= (current_date - 45)
  and m.resolution_date <= (current_date + 16);

comment on view public.v_prediction_ladder_bands is
  'One row per band the desk priced in the last 45 days and the next 16: the latest probability and how the band settled. v_prediction_ladder is this plus the latest edge per side; read this instead when the side does not matter, because the edge lookup is most of the cost.';

grant select on public.v_prediction_ladder_bands to anon, authenticated, service_role;

create or replace view public.v_prediction_ladder as
select
  lb.city_key,
  lb.for_date,
  lb.market_id,
  lb.band_id,
  lb.band_index,
  lb.band_label,
  lb.band_lo,
  lb.band_hi,
  lb.open_low,
  lb.open_high,
  lb.closed,
  lb.settled_value,
  lb.won,
  lb.raw_prob,
  lb.calibrated_prob,
  lb.model_prob,
  lb.forecast_max_c,
  lb.sigma_c,
  lb.confidence,
  lb.regime_label,
  e.side,
  e.market_price,
  e.edge_pp,
  e.edge_net_pp,
  e.fillable_usd_5c as depth_5c,
  e.tradeable,
  e.block_reason,
  e.computed_at as edge_at,
  lb.outcome_source,
  lb.settled_at
from v_prediction_ladder_bands lb
-- v_latest_edge, for this band only: the latest edge on EACH side.
left join lateral (
  select distinct on (x.side)
         x.side, x.market_price, x.edge_pp, x.edge_net_pp, x.fillable_usd_5c,
         x.tradeable, x.block_reason, x.computed_at
    from edges x
   where x.band_id = lb.band_id
   order by x.side, x.computed_at desc
) e on true;

comment on view public.v_prediction_ladder is
  'Every band the desk priced, with what it predicted and what actually happened. won comes from the venue when it has confirmed the band and from fact_band_outcome otherwise; settled_value is the observed maximum in Celsius; outcome_source names which answered, and is null for a day not yet settled.';

grant select on public.v_prediction_ladder to anon, authenticated, service_role;


-- ===========================================================================
-- THE GRADE (v_prediction_hindsight) LIVES IN
-- supabase/migrations/20260926090000_hit_and_miss_scores_frozen_calls.sql.
--
-- It was defined here from 22 Sep, grading each band's LATEST probability
-- with no time limit - the engine re-prices through the day and after it, so
-- the "prediction" it graded was mostly the thermometer read back (26 Sep, its
-- last 30 days: 627 of 647 scored city-days last priced after 15:00 local,
-- 306 after the day had ended). It now grades only calls frozen before the
-- answer, and it needs the checkpoint tables that only migrations create, so
-- it is defined there and not here.
-- ===========================================================================
