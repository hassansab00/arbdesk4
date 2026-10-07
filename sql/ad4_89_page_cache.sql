-- ===========================================================================
-- AD4 PAGE CACHE (plan v2 P6.5) - the Predictive page reads stored rows.
--
-- v_prediction_ladder (ad4_31 / ad4_68), v_forecast_convergence_all (ad4_62)
-- and v_city_hit_history (ad4_85) are defined in full in those files. This
-- file runs after them and turns each into a wrapper over a materialized copy
-- of its own definition (v_X_live -> mv_X -> v_X), refreshed CONCURRENTLY by
-- refresh_page_cache(). It is re-runnable: after any of those files is
-- reinstalled, run this one again and the changed definition is carried
-- through. Why, with the measurements: supabase/migrations/
-- 20260924040000_pages_read_stored_rows.sql.
--
-- THE LADDER'S COPY KEEPS YESTERDAY ON (WXPredict build 2.A, 7 Oct;
-- 20261007150000). Every reader of v_prediction_ladder reads yesterday or
-- later; the funnel's bucket planes, which read every day of the window,
-- read mv_city_ladder_edges below. The live view still computes any day.
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
       ', coalesce(v.side, ''-'') as cache_side_key', ' where v.for_date >= (current_date - 1)'),
      ('v_forecast_convergence_all', 'mv_forecast_convergence_all', 'city_key, for_date, model, lead_days', '', ''),
      ('v_city_hit_history',         'mv_city_hit_history',         'city_key, for_date', '', '')
    ) s(view_name, mv_name, key_cols, extra, keep)
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

    execute format('create materialized view public.%I as select v.*%s from public.%I v%s',
                   spec.mv_name, spec.extra, spec.view_name || '_live', spec.keep);
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

-- THE FUNNEL'S BUCKET PLANES (WXPredict build 2.A, 7 Oct): every edge of each
-- active city's markets in the ladder's own window (v_prediction_ladder_bands,
-- ad4_68: current_date - 45 to + 16), from the same canonical markets and
-- bands - equal to the edges of the whole ladder, both ways (1,050 on 7 Oct),
-- in 100 ms rather than the ladder's seconds.
do $$
begin
  if to_regclass('public.v_canonical_markets') is null or to_regclass('public.v_canonical_bands') is null then
    raise notice 'the canonical markets and bands are not installed; no bucket edges';
    return;
  end if;
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
  comment on view public.v_city_ladder_edges is
    'The bucket edges the Predictive funnel draws as planes, per city: the stored rows of mv_city_ladder_edges (WXPredict build 2.A).';
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

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_refresh_page_cache', '12,42 * * * *', 'select public.refresh_page_cache()');
  end if;
end $$;
