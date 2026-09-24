-- ===========================================================================
-- THE PAGES READ STORED ROWS (plan v2 P6.5)
--
-- Measured 24 Sep from the API gateway log: over 24 hours, 300 browser reads
-- failed with a statement timeout (the browser roles stop at 8 s) - the red
-- "query failed" panels. v_data_freshness 164, v_opportunities 29,
-- v_city_hit_history + v_city_hit_summary 30, v_prediction_hindsight 13,
-- v_prediction_ladder 11, v_forecast_convergence_all 8. Every failure took
-- 8.5-13.7 s; none was a fast error.
--
-- Alone, at a quiet moment, every one of those views answers in 0.1-2.6 s.
-- They fail together: a page fires its panels at once, and three of them cost
-- 1.9-2.6 s each (v_prediction_ladder 2,557 ms, v_forecast_convergence_all
-- 2,487 ms, v_city_hit_history 1,860 ms), so on this database a page load
-- pushes itself and every light panel beside it past 8 s. (The other cause,
-- VACUUM FULL of eight tables at midday, is fixed in request_reclaim below.)
--
-- So those three are computed on a schedule and the page reads the result:
--
--   v_X_live   the view's full definition, unchanged (service role only)
--   mv_X       its rows, refreshed CONCURRENTLY - readers never wait on it
--   v_X        select <the same columns> from mv_X, replaced IN PLACE, so
--              its grants and every view built on it (v_city_hit_summary,
--              v_city_prediction_confidence, ...) are untouched and read
--              stored rows too
--
-- refresh_page_cache() refreshes all three: pg_cron at :12 and :42, and the
-- pipelines call it after they write. Proven before applying: in one
-- snapshot, each wrapper returns exactly its live view's rows, EXCEPT ALL both
-- ways.
--
-- RE-RUNNABLE, and safe after a sql/ reinstall: when v_X is a live definition
-- again (a file under sql/ recreated it), the block below rebuilds _live, the
-- stored copy and the wrapper from that definition, so a changed view is
-- never served from a stale shape.
-- ===========================================================================

do $$
declare
  spec   record;
  v      regclass;
  mv     regclass;
  def    text;
  cols   text;
  wrapped boolean;
begin
  for spec in
    select * from (values
      ('v_prediction_ladder',        'mv_prediction_ladder',        'band_id, cache_side_key',
       ', coalesce(v.side, ''-'') as cache_side_key'),
      ('v_forecast_convergence_all', 'mv_forecast_convergence_all', 'city_key, for_date, model, lead_days', ''),
      ('v_city_hit_history',         'mv_city_hit_history',         'city_key, for_date', '')
    ) s(view_name, mv_name, key_cols, extra)
  loop
    v := to_regclass('public.' || spec.view_name);
    continue when v is null;                         -- a database without this page
    mv := to_regclass('public.' || spec.mv_name);
    wrapped := mv is not null and exists (
      select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
       where r.ev_class = v and d.refobjid = mv);
    continue when wrapped;                           -- already served from its stored copy

    def := pg_get_viewdef(v, true);
    execute format('drop materialized view if exists public.%I', spec.mv_name);
    execute format('drop view if exists public.%I', spec.view_name || '_live');
    execute format('create view public.%I as %s', spec.view_name || '_live', def);
    execute format('revoke all on public.%I from public, anon, authenticated', spec.view_name || '_live');
    execute format('grant select on public.%I to service_role', spec.view_name || '_live');

    execute format('create materialized view public.%I as select v.*%s from public.%I v',
                   spec.mv_name, spec.extra, spec.view_name || '_live');
    execute format('create unique index %I on public.%I (%s)', spec.mv_name || '_key', spec.mv_name, spec.key_cols);
    execute format('revoke all on public.%I from public, anon, authenticated', spec.mv_name);
    execute format('grant select on public.%I to service_role', spec.mv_name);

    select string_agg(format('%I', attname), ', ' order by attnum) into cols
      from pg_attribute
     where attrelid = to_regclass('public.' || spec.view_name || '_live') and attnum > 0 and not attisdropped;
    execute format('create or replace view public.%I as select %s from public.%I', spec.view_name, cols, spec.mv_name);
    execute format('comment on materialized view public.%I is %L', spec.mv_name,
      'Stored rows of ' || spec.view_name || '_live, refreshed by refresh_page_cache() (plan v2 P6.5). The page reads ' || spec.view_name || ', which selects from here.');
  end loop;
end $$;

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
  foreach m in array array['mv_prediction_ladder', 'mv_forecast_convergence_all', 'mv_city_hit_history'] loop
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

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_refresh_page_cache', '12,42 * * * *', 'select public.refresh_page_cache()');
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- NO VACUUM FULL IN THE MIDDLE OF THE DAY.
--
-- request_reclaim (ad4_66) schedules a VACUUM FULL two minutes after the
-- archive prunes a table. The archive is scheduled for 03:00, but GitHub ran
-- it at 08:07 on 24 Sep, so eight tables were rewritten at 08:08-08:09 under
-- ACCESS EXCLUSIVE locks of 9-24 s each - every page read of them blocked,
-- and 14 panels failed in that quarter hour. The reclaim now runs straight
-- away only inside 00:00-06:00 UTC; otherwise at the next 01:00 UTC, each
-- table two minutes after the one before, so the space still comes back the
-- same night and no two rewrites overlap.
-- ---------------------------------------------------------------------------
create or replace function public.request_reclaim(p_table text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowed constant text[] := array[
    'research_captures', 'paper_resolution_evidence', 'book_snapshots', 'edges',
    'weather_observations', 'weather_forecasts', 'trades_observed'];
  v_now   timestamptz := now();
  v_hour  int := extract(hour from (v_now at time zone 'UTC'))::int;
  v_at    timestamptz;
  v_utc   timestamp;
  v_job   text;
  v_expr  text;
begin
  if p_table is null or not (p_table = any (v_allowed)) then
    raise exception 'request_reclaim: % is not a table the archive prunes', p_table
      using errcode = '22023';
  end if;

  if v_hour < 6 then
    v_at := v_now + make_interval(mins => 2 + 2 * (array_position(v_allowed, p_table) - 1));
  else
    v_at := ((date_trunc('day', v_now at time zone 'UTC') + interval '1 day' + interval '1 hour')
             at time zone 'UTC')
            + make_interval(mins => 2 * (array_position(v_allowed, p_table) - 1));
  end if;
  v_utc := v_at at time zone 'UTC';

  v_job  := 'ad4_reclaim_after_archive_' || p_table;
  v_expr := format('%s %s %s %s *',
                   extract(minute from v_utc)::int, extract(hour  from v_utc)::int,
                   extract(day    from v_utc)::int, extract(month from v_utc)::int);

  perform cron.schedule(v_job, v_expr,
                        format('VACUUM (FULL, ANALYZE) public.%I', p_table));

  return jsonb_build_object('ok', true, 'job', v_job, 'cron', v_expr, 'fires_at', v_at);
end $$;

comment on function public.request_reclaim(text) is
  'Schedules a VACUUM FULL of one archive-pruned table in the next quiet window (straight away inside 00:00-06:00 UTC, otherwise the next 01:00 UTC), staggered two minutes a table, so the space a prune frees comes back the same night without locking pages mid-day (plan v2 P6.5).';

revoke all on function public.request_reclaim(text) from public, anon, authenticated;
grant execute on function public.request_reclaim(text) to service_role;
