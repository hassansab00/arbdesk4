-- ===========================================================================
-- THE PAGE CACHE KEEPS THE DAYS IT SHOWS (WXPredict build 2.A; Hassan, 7 Oct:
-- "proceed with the next storage cut").
--
-- mv_prediction_ladder is a stored copy of v_prediction_ladder_live, which
-- spans the markets of the last 45 days and the next 16 (v_prediction_ladder_
-- bands). Measured 7 Oct 13:06Z: 39,742 rows, 15.0 MiB, and 36,640 of the
-- rows are for days before yesterday. Its readers, every one (repo grep, n8n,
-- and the live dependents and function bodies):
--
--   web/app/predictive/page.tsx    the ladder for_date >= today (UTC)
--   web/components/CityCards.tsx   for_date >= yesterday (UTC), not closed
--   v_city_prediction_confidence   for_date >= current_date, three times
--   web/app/predictive/page.tsx    the funnel's bucket planes: band_lo and
--                                  band_hi of the city's rows, every day
--
-- The first three read nothing before yesterday. The fourth drew planes from
-- all 45 days: 445 of 1,050 edges are only in past markets (7 Oct), so it
-- moves to mv_city_ladder_edges below, which keeps exactly those edges.
--
-- NOTHING IS DELETED: a materialized view is a copy. v_prediction_ladder_live
-- still computes any day of the 61 from the tables, which keep every row.
--
-- 1. mv_prediction_ladder keeps for_date >= current_date - 1 (at refresh
--    time; the page's bound is its own UTC date, never earlier). The old
--    copy is renamed aside, the new one built under the real name, the
--    wrapper v_prediction_ladder repointed with CREATE OR REPLACE (the same
--    35 columns, so its grants and v_city_prediction_confidence stay bound),
--    and the old copy dropped with nothing depending on it.
-- 2. mv_city_ladder_edges: every bucket edge of each active city's markets in
--    the ladder's own window (current_date - 45 to + 16), from the same
--    canonical markets and bands; v_city_ladder_edges is what the page reads.
--    Proved 7 Oct 13:11Z: 1,050 edges, equal to the stored ladder's both ways.
-- 3. refresh_page_cache refreshes the edges too.
--
-- Measured 7 Oct: the shorter ladder computes in 442 ms; the whole one took
-- 8.9-10.0 s a refresh, twice an hour. The edges compute in 100 ms.
-- The bodies are the ones in sql/ad4_89_page_cache.sql. Re-runnable; a
-- database without the ladder cache (the contracts' fixture) is left alone.
-- ===========================================================================

do $migration$
declare
  cols text;
begin
  if to_regclass('public.mv_prediction_ladder') is null
     or to_regclass('public.v_prediction_ladder_live') is null
     or to_regclass('public.v_prediction_ladder') is null
     or to_regclass('public.v_canonical_markets') is null
     or to_regclass('public.v_canonical_bands') is null then
    raise notice '2.A: the prediction ladder cache is not installed here (sql/ad4_89); nothing to do';
    return;
  end if;

  -- 1. The stored ladder keeps yesterday on. Skipped once it does. The old
  --    copy steps aside under another name, the new one is built under the
  --    real name, the wrapper is repointed, and the old copy is dropped.
  if position('CURRENT_DATE - 1' in upper(pg_get_viewdef('public.mv_prediction_ladder'::regclass))) = 0 then
    alter materialized view public.mv_prediction_ladder rename to mv_prediction_ladder_whole;
    alter index public.mv_prediction_ladder_key rename to mv_prediction_ladder_whole_key;
    execute $v$
      create materialized view public.mv_prediction_ladder as
      select v.*, coalesce(v.side, '-') as cache_side_key
        from public.v_prediction_ladder_live v
       where v.for_date >= (current_date - 1)
    $v$;
    create unique index mv_prediction_ladder_key on public.mv_prediction_ladder (band_id, cache_side_key);
    revoke all on public.mv_prediction_ladder from public, anon, authenticated;
    grant select on public.mv_prediction_ladder to service_role;

    -- The wrapper's own columns, in its own order, from the new copy.
    select string_agg(format('%I', attname), ', ' order by attnum) into cols
      from pg_attribute
     where attrelid = 'public.v_prediction_ladder'::regclass and attnum > 0 and not attisdropped;
    execute format('create or replace view public.v_prediction_ladder as select %s from public.mv_prediction_ladder', cols);

    -- Nothing depends on the old copy now; without CASCADE this fails, and
    -- rolls everything back, if something still does.
    drop materialized view public.mv_prediction_ladder_whole;
  end if;

  comment on materialized view public.mv_prediction_ladder is
    'Stored rows of v_prediction_ladder_live from yesterday on (current_date - 1 at refresh), refreshed by refresh_page_cache() (plan v2 P6.5; WXPredict build 2.A). The page reads v_prediction_ladder, which selects from here; every reader reads yesterday or later, and the live view still computes any day of its window.';

  -- 2. The funnel's bucket planes: every edge of the ladder's window.
  if to_regclass('public.mv_city_ladder_edges') is null then
    execute $v$
      create materialized view public.mv_city_ladder_edges as
      select distinct m.city_key, e.edge
        from public.v_canonical_markets m
        join public.cities ct on ct.city_key = m.city_key
                             and coalesce(ct.status, 'active') = 'active'
        join public.v_canonical_bands b on b.market_id = m.market_id
        cross join lateral (values (b.band_lo), (b.band_hi)) e(edge)
       where e.edge is not null
         and m.resolution_date >= (current_date - 45)
         and m.resolution_date <= (current_date + 16)
    $v$;
    create unique index mv_city_ladder_edges_key on public.mv_city_ladder_edges (city_key, edge);
  end if;
  revoke all on public.mv_city_ladder_edges from public, anon, authenticated;
  grant select on public.mv_city_ladder_edges to service_role;

  create or replace view public.v_city_ladder_edges as
  select city_key, edge from public.mv_city_ladder_edges;
  revoke all on public.v_city_ladder_edges from public;
  grant select on public.v_city_ladder_edges to anon, authenticated, service_role;

  comment on materialized view public.mv_city_ladder_edges is
    'Every bucket edge (band_lo and band_hi) of each active city''s markets in the prediction ladder''s window, current_date - 45 to + 16 (v_prediction_ladder_bands), refreshed by refresh_page_cache(). The Predictive page draws the funnel''s planes from it (WXPredict build 2.A).';
  comment on view public.v_city_ladder_edges is
    'The bucket edges the Predictive funnel draws as planes, per city: the stored rows of mv_city_ladder_edges (WXPredict build 2.A).';
end
$migration$;

create or replace function public.refresh_page_cache()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  m text;
  t0 timestamptz;
  out jsonb := '{}'::jsonb;
begin
  foreach m in array array['mv_prediction_ladder', 'mv_city_ladder_edges', 'mv_forecast_convergence_all', 'mv_city_hit_history'] loop
    continue when to_regclass('public.' || m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I', m);
    out := out || jsonb_build_object(m, round(extract(epoch from clock_timestamp() - t0) * 1000));
  end loop;
  perform public.log_ingest('refresh_page_cache', 'ok', 0, jsonb_build_object('ms', out));
  return out;
end $$;

comment on function public.refresh_page_cache() is
  'Refreshes the stored rows the Predictive page reads (plan v2 P6.5), without blocking a reader. pg_cron at :12 and :42; the pipelines call it after they write.';

revoke all on function public.refresh_page_cache() from public, anon, authenticated;
grant execute on function public.refresh_page_cache() to service_role;
