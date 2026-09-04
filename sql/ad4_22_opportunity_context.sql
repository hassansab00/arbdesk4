-- ===========================================================================
-- ad4_22_opportunity_context.sql - is this edge still there, and does the
-- market know yet?
--
-- v_opportunities ranks a band on a SNAPSHOT: edge, confidence, depth, volume,
-- all as of the last time each was computed. Every one of those is a still
-- photograph, and it cannot answer the two questions that decide whether an
-- edge is worth taking:
--
--   IS THE MARKET MOVING TOWARD ME OR AWAY? A band whose price is drifting
--     toward the model's number is a market coming round; one drifting away is
--     a market that increasingly disagrees, and the second is far more often a
--     sign the model is wrong than a sign the edge is growing.
--
--   HAS THE MARKET SEEN WHAT I HAVE SEEN? This is the sharper one. If the
--     forecast for a day changed AFTER the last book snapshot, the price on
--     screen was set against older information than the desk is holding. That
--     is the only kind of edge that is reliably real, because it has a cause:
--     the market has not repriced yet. If the book is newer than the forecast,
--     the market has seen everything AD4 has and still disagrees - which is a
--     much weaker place to be.
--
-- Bounded to markets resolving today or later and 48 hours of snapshots. The
-- whole point is what changed recently, and an unbounded scan of
-- book_snapshots is what made v_city_stats time out (sql/ad4_19).
--
-- Run order: after sql/ad4_21_weather_features.sql. Re-runnable.
-- ===========================================================================

create or replace view v_opportunity_context as
with live_bands as (
  select b.band_id, m.city_key, m.resolution_date
  from bands b
  join markets m using (market_id)
  where m.resolution_date >= current_date
),
snaps as (
  select s.band_id, s.observed_at, s.mid
  from book_snapshots s
  join live_bands lb on lb.band_id = s.band_id
  where s.observed_at > now() - interval '48 hours' and s.mid is not null
),
now_price as (
  select distinct on (band_id) band_id, mid as mid_now, observed_at as book_at
  from snaps order by band_id, observed_at desc
),
-- The price nearest each lag, so "an hour ago" means the closest reading to an
-- hour ago rather than the nearest reading of any age. A band snapshotted once
-- a day would otherwise report yesterday's price as its 1-hour figure.
lag_1h as (
  select distinct on (band_id) band_id, mid as mid_1h
  from snaps where observed_at <= now() - interval '45 minutes'
  order by band_id, observed_at desc
),
lag_6h as (
  select distinct on (band_id) band_id, mid as mid_6h
  from snaps where observed_at <= now() - interval '5 hours'
  order by band_id, observed_at desc
),
lag_24h as (
  select distinct on (band_id) band_id, mid as mid_24h
  from snaps where observed_at <= now() - interval '22 hours'
  order by band_id, observed_at desc
),
-- The two newest forecasts for each city-day, so a MOVE can be measured rather
-- than just a latest value.
fc2 as (
  select
    f.city_key, f.for_date, f.forecast_max_c, f.run_at,
    row_number() over (partition by f.city_key, f.for_date order by f.run_at desc) as rn
  from weather_forecasts f
  where f.for_date >= current_date and f.forecast_max_c is not null
),
fc_move as (
  select
    a.city_key, a.for_date,
    a.forecast_max_c                    as forecast_now_c,
    a.run_at                            as forecast_at,
    b.forecast_max_c                    as forecast_prev_c,
    (a.forecast_max_c - b.forecast_max_c) as forecast_move_c
  from (select * from fc2 where rn = 1) a
  left join (select * from fc2 where rn = 2) b
    on b.city_key = a.city_key and b.for_date = a.for_date
)
select
  lb.band_id,
  lb.city_key,
  lb.resolution_date,
  np.mid_now,
  np.book_at,
  l1.mid_1h,
  l6.mid_6h,
  l24.mid_24h,
  round(np.mid_now - l1.mid_1h, 4)   as drift_1h,
  round(np.mid_now - l6.mid_6h, 4)   as drift_6h,
  round(np.mid_now - l24.mid_24h, 4) as drift_24h,

  fm.forecast_now_c,
  fm.forecast_prev_c,
  round(fm.forecast_move_c, 2)       as forecast_move_c,
  fm.forecast_at,

  -- THE QUESTION. A forecast newer than the book means the price on screen was
  -- set against older information than the desk is holding.
  (fm.forecast_at is not null and np.book_at is not null and fm.forecast_at > np.book_at)
                                     as forecast_ahead_of_book,
  case
    when fm.forecast_at is null or np.book_at is null then null
    else round(extract(epoch from (fm.forecast_at - np.book_at)) / 3600.0, 2)
  end                                as forecast_lead_hours,

  round(extract(epoch from ((lb.resolution_date + 1)::timestamptz - now())) / 3600.0, 1)
                                     as hours_to_resolution
from live_bands lb
left join now_price np on np.band_id = lb.band_id
left join lag_1h  l1  on l1.band_id  = lb.band_id
left join lag_6h  l6  on l6.band_id  = lb.band_id
left join lag_24h l24 on l24.band_id = lb.band_id
left join fc_move fm  on fm.city_key = lb.city_key and fm.for_date = lb.resolution_date;


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_opportunity_context to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare v_n int; v_ahead int;
begin
  select count(*), count(*) filter (where forecast_ahead_of_book) into v_n, v_ahead
    from v_opportunity_context;
  raise notice 'ad4_22: % live band(s); % priced before the current forecast', v_n, v_ahead;
end
$ad4$;
