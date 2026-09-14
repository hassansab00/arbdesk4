-- ===========================================================================
-- ad4_64_capacity_per_city.sql - recompute_capacity(), one city per call.
--
-- WHAT BROKE
--
-- The Daily Pipeline died at "Derived recompute" on six consecutive runs and
-- then again on 2026-09-14, this time with Postgres saying it plainly:
--
--   recompute_capacity -> HTTP 500: {"code":"57014",
--     "message":"canceling statement due to statement timeout"}
--
-- recompute_capacity() rebuilds the WHOLE capacity table in one statement. It
-- walks every band's newest book snapshot through v_band_book and calls
-- capacity_side() EIGHT times per row - ask and bid, at 2c, 5c and 10c, plus
-- the depth sums. Measured on the live database with only TWO of those eight
-- calls in place, over 9,012 rows:
--
--   producing the 9,012 rows        1,085 ms
--   the aggregate (capacity_side)   8,131 ms   <- and that is a quarter of it
--
-- So the real function is somewhere near 30 seconds against a statement
-- timeout of roughly 15. It is not close, and no amount of indexing helps:
-- the time is inside a per-row function, not in the scan.
--
-- WHY SLICING IS THE FIX, AND WHY IT IS EXACT
--
-- statement_timeout runs from the start of the TOP-LEVEL statement and is
-- never reset by anything a function does internally, so the only way to buy
-- more budget is to make each unit of work its own statement. This is the
-- third time this codebase has hit that wall: common.refresh_feature_cache
-- and ad4_56's refresh_weather_peak_city are the other two, and this file is
-- deliberately the same shape as ad4_56.
--
-- The split costs nothing in correctness. The whole-table version groups by
-- (city_key, hour_utc) and nothing in the aggregate crosses a city boundary,
-- so 54 single-city statements produce byte-identical rows to one 54-city
-- statement. The only difference is that each city carries its own
-- computed_at instead of sharing one, and both readers of this table already
-- take the newest row PER CITY:
--
--   ad4_17_city_stats.sql   select distinct on (city_key) ... order by computed_at desc
--   ad4_19_stats_cache.sql  select distinct on (city_key) ... order by computed_at desc
--
-- recompute_capacity() is left exactly as it was. It is what ad4_phase2's own
-- install block calls, it is correct, and on a small book archive it is fine.
-- ===========================================================================

create or replace function recompute_capacity_city(p_city text)
returns int language plpgsql security definer as $ad4$
declare v_rows int;
begin
  insert into derived_capacity (city_key, computed_at, hour_utc,
                                usd_at_2c, usd_at_5c, usd_at_10c, usd_full, live_bands)
  select
    m.city_key,
    now(),
    extract(hour from bb.observed_at)::int as hour_utc,
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.02, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.02, false)),
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.05, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.05, false)),
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.10, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.10, false)),
    sum(coalesce(bb.ask_depth_usd, 0) + coalesce(bb.bid_depth_usd, 0)),
    count(*) filter (where bb.market_state = 'LIVE' or bb.tradeable is true)
  from v_band_book bb
  join bands b   on b.band_id = bb.band_id
  join markets m on m.market_id = b.market_id
  where bb.observed_at is not null
    and m.city_key = p_city
  group by m.city_key, extract(hour from bb.observed_at)::int;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$ad4$;

comment on function recompute_capacity_city(text) is
  'One city''s book capacity by hour. Same measurement as recompute_capacity(), sliced so each call is its own top-level statement with its own timeout budget - the whole-table version spends most of its time inside capacity_side(), eight calls per row, and is cancelled by Supabase''s statement timeout.';

do $ad4$
declare r text;
begin
  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function recompute_capacity_city(text) to %I', r);
    end if;
  end loop;
end $ad4$;
