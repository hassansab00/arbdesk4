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
  -- Active only: this feeds the opportunity cards, and a card that cannot be
  -- traded does not need to know how its price moved.
  join cities c on c.city_key = m.city_key
               and coalesce(c.status, 'active') = 'active'
  where m.resolution_date >= current_date
),
-- (The four prices - now, and nearest to 1h, 6h and 24h ago - are found per
-- band in the select below, one backwards probe each.)
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
-- FOUR PROBES PER BAND, NOT A 48-HOUR SCAN.
--
-- These were four `distinct on` passes over every snapshot of the last 48
-- hours - 44,280 rows fetched and sorted to answer four questions about each
-- of ~1,070 bands. Measured 2026-09-22 as the browser's role: 3,785 ms, of
-- which 3,081 ms was that scan, against an 8-second limit and alongside the
-- rest of the Opportunities page. Each question is really "the newest reading
-- at or before time T", which ad4_ix_book_band_time (band_id, observed_at)
-- answers with one backwards index probe. Same window, same `mid is not null`,
-- same "newest at or before" rule - verified by fingerprinting the whole
-- output before and after.
--
-- The price nearest each lag, so "an hour ago" means the closest reading to an
-- hour ago rather than the nearest reading of any age. A band snapshotted once
-- a day would otherwise report yesterday's price as its 1-hour figure.
left join lateral (
  select s.mid as mid_now, s.observed_at as book_at from book_snapshots s
   where s.band_id = lb.band_id and s.mid is not null
     and s.observed_at > now() - interval '48 hours'
   order by s.observed_at desc limit 1) np on true
left join lateral (
  select s.mid as mid_1h from book_snapshots s
   where s.band_id = lb.band_id and s.mid is not null
     and s.observed_at > now() - interval '48 hours'
     and s.observed_at <= now() - interval '45 minutes'
   order by s.observed_at desc limit 1) l1 on true
left join lateral (
  select s.mid as mid_6h from book_snapshots s
   where s.band_id = lb.band_id and s.mid is not null
     and s.observed_at > now() - interval '48 hours'
     and s.observed_at <= now() - interval '5 hours'
   order by s.observed_at desc limit 1) l6 on true
left join lateral (
  select s.mid as mid_24h from book_snapshots s
   where s.band_id = lb.band_id and s.mid is not null
     and s.observed_at > now() - interval '48 hours'
     and s.observed_at <= now() - interval '22 hours'
   order by s.observed_at desc limit 1) l24 on true
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
