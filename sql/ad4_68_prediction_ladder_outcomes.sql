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


create or replace view public.v_prediction_ladder as
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
  e.side,
  e.market_price,
  e.edge_pp,
  e.edge_net_pp,
  e.fillable_usd_5c as depth_5c,
  e.tradeable,
  e.block_reason,
  e.computed_at as edge_at,
  case
    when vb.resolution_state = 'confirmed' and vb.settled_yes is not null then 'venue'
    when fb.settled_yes is not null then 'weather'
  end as outcome_source,
  coalesce(
    case when vb.resolution_state = 'confirmed' then vb.confirmed_at end,
    fb.captured_at
  ) as settled_at
from markets m
join bands b on b.market_id = m.market_id
left join v_latest_prob p on p.band_id = b.band_id
left join v_latest_edge e on e.band_id = b.band_id
left join mv_venue_band_resolution vb on vb.band_id = b.band_id
left join fact_band_outcome fb on fb.band_id = b.band_id
where m.resolution_date >= (current_date - 45)
  and m.resolution_date <= (current_date + 16);

comment on view public.v_prediction_ladder is
  'Every band the desk priced, with what it predicted and what actually happened. won comes from the venue when it has confirmed the band and from fact_band_outcome otherwise; settled_value is the observed maximum in Celsius; outcome_source names which answered, and is null for a day not yet settled.';

grant select on public.v_prediction_ladder to anon, authenticated, service_role;
