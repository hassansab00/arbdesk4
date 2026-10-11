-- ===========================================================================
-- AD4 PAGE CACHE (plan v2 P6.5) - the Predictive page reads stored rows.
--
-- v_prediction_ladder (ad4_31 / ad4_68), v_forecast_convergence_all (ad4_62),
-- v_city_hit_history (ad4_85), v_opportunities (ad4_13; below, 2.E) and the
-- six views of 2.E part 2 - v_prediction_hindsight (supabase/migrations/
-- 20261006180000), v_edge_scaling (ad4_31), v_calibration and
-- v_edge_realisation (ad4_18), v_model_forecast_skill (ad4_25) and
-- v_condition_skill (ad4_24) - are defined in full in those files. This
-- file runs after them and turns each into a wrapper over a materialized copy
-- of its own definition (v_X_live -> mv_X -> v_X), refreshed CONCURRENTLY by
-- refresh_page_cache(). It is re-runnable: after any of those files is
-- reinstalled, run this one again and the changed definition is carried
-- through. Why, with the measurements: supabase/migrations/
-- 20260924040000_pages_read_stored_rows.sql, 20261009210000 and
-- 20261011100000.
--
-- A wrapper keeps its view's own options (security_invoker): CREATE OR
-- REPLACE VIEW without them would reset them. An invoker wrapper reads the
-- stored copy as the page's own role, so that copy is readable by it.
--
-- THE LADDER'S COPY KEEPS YESTERDAY ON (WXPredict build 2.A, 7 Oct;
-- 20261007150000). Every reader of v_prediction_ladder reads yesterday or
-- later; the funnel's bucket planes, which read every day of the window,
-- read mv_city_ladder_edges below. The live view still computes any day.
-- ===========================================================================

-- The Candidate gate's v_city_status_live reads v_prediction_hindsight_live
-- (below); it is derived again after the loop, which may rebuild that view.
drop view if exists public.v_city_status_live;

do $$
declare
  spec   record;
  v      regclass;
  mv     regclass;
  def    text;
  cols   text;
  opts   text;
  invoker boolean;
  wrapped boolean;
begin
  for spec in
    select * from (values
      ('v_prediction_ladder',        'mv_prediction_ladder',        'band_id, cache_side_key',
       ', coalesce(v.side, ''-'') as cache_side_key', ' where v.for_date >= (current_date - 1)', ''),
      ('v_forecast_convergence_all', 'mv_forecast_convergence_all', 'city_key, for_date, model, lead_days', '', '', ''),
      ('v_city_hit_history',         'mv_city_hit_history',         'city_key, for_date', '', '', ''),
      -- 2.E part 2 (20261011100000): the hit record after the hit history it reads
      ('v_prediction_hindsight',     'mv_prediction_hindsight',     'city_key, for_date, called_when, call_order', '', '', ''),
      ('v_edge_scaling',             'mv_edge_scaling',             'edge_bucket', '', '', ' order by edge_bucket'),
      ('v_calibration',              'mv_calibration',              'bucket',      '', '', ' order by bucket'),
      ('v_edge_realisation',         'mv_edge_realisation',         'edge_bucket', '', '', ' order by ord'),
      ('v_model_forecast_skill',     'mv_model_forecast_skill',     'city_key, lead_days', '', '', ''),
      ('v_condition_skill',          'mv_condition_skill',          'city_key',    '', '', '')
    ) s(view_name, mv_name, key_cols, extra, keep, order_by)
  loop
    v := to_regclass('public.' || spec.view_name);
    continue when v is null;                         -- a database without this page
    mv := to_regclass('public.' || spec.mv_name);
    wrapped := mv is not null and exists (
      select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
       where r.ev_class = v and d.refobjid = mv);
    continue when wrapped;                           -- already served from its stored copy

    select coalesce(' with (' || array_to_string(c.reloptions, ', ') || ')', ''),
           exists (select 1 from unnest(c.reloptions) o where o ~* '^security_invoker=(true|on|yes|1)$')
      into opts, invoker
      from pg_class c where c.oid = v;
    def := pg_get_viewdef(v, true);
    execute format('drop materialized view if exists public.%I', spec.mv_name);
    execute format('drop view if exists public.%I', spec.view_name || '_live');
    execute format('create view public.%I%s as %s', spec.view_name || '_live', opts, def);
    execute format('revoke all on public.%I from public, anon, authenticated', spec.view_name || '_live');
    execute format('grant select on public.%I to service_role', spec.view_name || '_live');

    execute format('create materialized view public.%I as select v.*%s from public.%I v%s',
                   spec.mv_name, spec.extra, spec.view_name || '_live', spec.keep);
    execute format('create unique index %I on public.%I (%s)', spec.mv_name || '_key', spec.mv_name, spec.key_cols);
    execute format('revoke all on public.%I from public, anon, authenticated', spec.mv_name);
    execute format('grant select on public.%I to service_role', spec.mv_name);
    if invoker then
      execute format('grant select on public.%I to anon, authenticated', spec.mv_name);
    end if;

    select string_agg(format('%I', attname), ', ' order by attnum) into cols
      from pg_attribute
     where attrelid = to_regclass('public.' || spec.view_name || '_live') and attnum > 0 and not attisdropped;
    execute format('create or replace view public.%I%s as select %s from public.%I%s',
                   spec.view_name, opts, cols, spec.mv_name, spec.order_by);
    execute format('comment on materialized view public.%I is %L', spec.mv_name,
      'Stored rows of ' || spec.view_name || '_live, refreshed by refresh_page_cache() (plan v2 P6.5). The page reads ' || spec.view_name || ', which selects from here.');
  end loop;
end $$;

-- THE CANDIDATE GATE KEEPS THE LIVE HINDSIGHT (2.E part 2; 20261011100000).
-- v_city_status counts each city's settled days per checkpoint from
-- v_prediction_hindsight, the stored rows; the page reads it so. The tick's
-- Candidate gate (scripts/engine_shadow.py) reads v_city_status_live: the same
-- definition over v_prediction_hindsight_live, derived here from whatever
-- v_city_status is now.
do $$
declare
  v    regclass := to_regclass('public.v_city_status');
  def  text;
  opts text;
  n    int;
begin
  if v is null or to_regclass('public.v_prediction_hindsight_live') is null then
    raise notice 'no v_city_status or no stored hindsight here; no v_city_status_live';
    return;
  end if;
  def := pg_get_viewdef(v, true);
  select count(*) into n from regexp_matches(def, '\m(public\.)?v_prediction_hindsight\M', 'g');
  if n <> 1 then
    raise exception 'v_city_status names v_prediction_hindsight % times, not once: derive v_city_status_live by hand', n;
  end if;
  select coalesce(' with (' || array_to_string(c.reloptions, ', ') || ')', '') into opts
    from pg_class c where c.oid = v;
  execute format('create view public.v_city_status_live%s as %s', opts,
                 regexp_replace(def, '\m(public\.)?v_prediction_hindsight\M', 'public.v_prediction_hindsight_live'));
  revoke all on public.v_city_status_live from public, anon, authenticated;
  grant select on public.v_city_status_live to service_role;
  comment on view public.v_city_status_live is
    'v_city_status over v_prediction_hindsight_live: the Candidate gate (scripts/engine_shadow.py) reads this, so its settled days are counted from the tables. The page reads v_city_status, over the stored hindsight (WXPredict build 2.E part 2).';
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

-- THE OPPORTUNITIES (WXPredict build 2.E, 9 Oct; 20261009210000). Every page
-- reads v_opportunities or a view built on it, and the masthead counts its
-- tradeable rows once a minute on every open page: about 2 s each, 38 of the
-- 156 browser reads that died at 8 s in the 24 h to 9 Oct 20:34Z. Stored the
-- same way, with two differences: the wrapper re-applies the live view's row
-- condition on the clock (a market shows until its date has passed in the
-- city's time zone) and its order. Between refreshes the 24-hour volume
-- figures lag the clock (9 Oct: 2.15 % at most, two minutes after a build). The strategies read v_opportunities_live
-- (scripts/signal_engine.py): they run between the edge engine and the
-- pipeline's refresh, and must see the edges just written.
do $$
declare
  v       regclass := to_regclass('public.v_opportunities');
  mv      regclass := to_regclass('public.mv_opportunities');
  def     text;
  cols    text;
  wrapped boolean;
begin
  if v is null then
    raise notice 'no v_opportunities (sql/ad4_13); nothing to store';
    return;
  end if;
  wrapped := mv is not null and exists (
    select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
     where r.ev_class = v and d.refobjid = mv);
  if not wrapped then
    def := pg_get_viewdef(v, true);
    drop materialized view if exists public.mv_opportunities;
    drop view if exists public.v_opportunities_live;
    execute format('create view public.v_opportunities_live as %s', def);
    create materialized view public.mv_opportunities as select v.* from public.v_opportunities_live v;
    create unique index mv_opportunities_key on public.mv_opportunities (band_id, side);

    select string_agg(format('%I', attname), ', ' order by attnum) into cols
      from pg_attribute
     where attrelid = 'public.v_opportunities_live'::regclass and attnum > 0 and not attisdropped;
    execute format('create or replace view public.v_opportunities as select %s from public.mv_opportunities '
                   'where resolution_date >= (now() at time zone coalesce(timezone, ''UTC''))::date '
                   'order by score desc nulls last', cols);
  end if;

  revoke all on public.v_opportunities_live from public, anon, authenticated;
  grant select on public.v_opportunities_live to service_role;
  revoke all on public.mv_opportunities from public, anon, authenticated;
  grant select on public.mv_opportunities to service_role;

  comment on view public.v_opportunities_live is
    'The full definition of v_opportunities (sql/ad4_13), computed from the tables on every read. The strategies read this (scripts/signal_engine.py); the pages read v_opportunities, the stored rows (plan v2 P6.5; WXPredict build 2.E).';
  comment on materialized view public.mv_opportunities is
    'Stored rows of v_opportunities_live, refreshed by refresh_page_cache() (plan v2 P6.5; WXPredict build 2.E). The pages read v_opportunities, which selects from here.';
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
  foreach m in array array['mv_opportunities', 'mv_prediction_ladder', 'mv_city_ladder_edges', 'mv_forecast_convergence_all', 'mv_city_hit_history',
                           'mv_prediction_hindsight', 'mv_edge_scaling', 'mv_calibration', 'mv_edge_realisation', 'mv_model_forecast_skill', 'mv_condition_skill'] loop
    continue when to_regclass('public.' || m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I', m);
    out := out || jsonb_build_object(m, round(extract(epoch from clock_timestamp() - t0) * 1000));
  end loop;
  perform public.log_ingest('refresh_page_cache', 'ok', 0, jsonb_build_object('ms', out));
  return out;
end $$;

comment on function public.refresh_page_cache() is
  'Refreshes the stored rows the pages read (plan v2 P6.5), without blocking a reader: the opportunities, the Predictive page''s views and the hit record, calibration and skill views. pg_cron at :12 and :42; the pipelines call it after they write.';

revoke all on function public.refresh_page_cache() from public, anon, authenticated;
grant execute on function public.refresh_page_cache() to service_role;

-- TELL POSTGREST (Codex on #358). signal_engine reads v_opportunities_live
-- and the Candidate gate v_city_status_live through PostgREST, which answers
-- PGRST205 for a relation its schema cache has not seen (sql/ad4_13).
-- Supabase's pgrst_ddl_watch event trigger also asks for the reload on DDL;
-- this does not depend on it.
notify pgrst, 'reload schema';

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_refresh_page_cache', '12,42 * * * *', 'select public.refresh_page_cache()');
  end if;
end $$;
