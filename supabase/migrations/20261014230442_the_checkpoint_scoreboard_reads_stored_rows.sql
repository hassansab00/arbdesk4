-- The checkpoint scoreboard reads stored rows (R11, WXPredict build 2.E).
-- Its full definition is copied, without changing the first-call filter or
-- any score. The wrapper is replaced in place, keeping columns, grants and
-- dependent views. The existing :12/:42 and post-pipeline refresh carries it;
-- new results may lag that refresh, as the other historical page panels do.
-- No engine reader or clock expression in its view tree (checked live).
-- Re-runnable; a database without the scoreboard is left alone. No data is
-- removed. If a later install replaces the definition, ad4_89 rebuilds it.

set local lock_timeout = '5s';

do $migration$
declare
  spec    record;
  v       regclass;
  mv      regclass;
  def     text;
  cols    text;
  opts    text;
  invoker boolean;
  wrapped boolean;
begin
  for spec in
    select * from (values
      ('v_checkpoint_scoreboard', 'mv_checkpoint_scoreboard', 'checkpoint, city_key', '')
    ) s(view_name, mv_name, key_cols, order_by)
  loop
    v := to_regclass('public.' || spec.view_name);
    continue when v is null;                         -- a database without this page
    mv := to_regclass('public.' || spec.mv_name);
    wrapped := mv is not null and exists (
      select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
       where r.ev_class = v and d.refobjid = mv);
    -- The view's own options, carried to the live copy and the wrapper: a
    -- CREATE OR REPLACE VIEW without them would reset security_invoker.
    select coalesce(' with (' || array_to_string(c.reloptions, ', ') || ')', ''),
           exists (select 1 from unnest(c.reloptions) o where o ~* '^security_invoker=(true|on|yes|1)$')
      into opts, invoker
      from pg_class c where c.oid = v;
    if not wrapped then
      -- NOTHING IS REMOVED HERE. A stored copy or live view left from before
      -- means the view was reinstalled live since; sql/ad4_89_page_cache.sql
      -- rebuilds both from its new definition.
      if mv is not null or to_regclass('public.' || spec.view_name || '_live') is not null then
        raise exception '% is live but a stored copy exists: run sql/ad4_89_page_cache.sql, which rebuilds it',
          spec.view_name;
      end if;
      def := pg_get_viewdef(v, true);
      execute format('create view public.%I%s as %s', spec.view_name || '_live', opts, def);
      execute format('create materialized view public.%I as select v.* from public.%I v',
                     spec.mv_name, spec.view_name || '_live');
      execute format('create unique index %I on public.%I (%s)', spec.mv_name || '_key', spec.mv_name, spec.key_cols);

      select string_agg(format('%I', attname), ', ' order by attnum) into cols
        from pg_attribute
       where attrelid = to_regclass('public.' || spec.view_name || '_live') and attnum > 0 and not attisdropped;
      execute format('create or replace view public.%I%s as select %s from public.%I%s',
                     spec.view_name, opts, cols, spec.mv_name, spec.order_by);
    end if;

    execute format('revoke all on public.%I from public, anon, authenticated', spec.view_name || '_live');
    execute format('grant select on public.%I to service_role', spec.view_name || '_live');
    execute format('revoke all on public.%I from public, anon, authenticated', spec.mv_name);
    execute format('grant select on public.%I to service_role', spec.mv_name);
    -- An invoker wrapper reads the stored copy as the page's own role.
    if invoker then
      execute format('grant select on public.%I to anon, authenticated', spec.mv_name);
    end if;
    execute format('comment on view public.%I is %L', spec.view_name || '_live',
      'The full definition of ' || spec.view_name || ', computed from the tables on every read. The page reads '
      || spec.view_name || ', the stored rows (plan v2 P6.5; WXPredict build 2.E part 3).');
    execute format('comment on materialized view public.%I is %L', spec.mv_name,
      'Stored rows of ' || spec.view_name || '_live, refreshed by refresh_page_cache() (plan v2 P6.5; WXPredict build 2.E part 3). The page reads '
      || spec.view_name || ', which selects from here.');
  end loop;
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
  foreach m in array array['mv_opportunities', 'mv_prediction_ladder', 'mv_city_ladder_edges', 'mv_forecast_convergence_all', 'mv_city_hit_history',
                           'mv_prediction_hindsight', 'mv_edge_scaling', 'mv_calibration', 'mv_edge_realisation', 'mv_model_forecast_skill', 'mv_condition_skill', 'mv_checkpoint_scoreboard'] loop
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

-- TELL POSTGREST: the Candidate gate reads v_city_status_live through it,
-- which answers PGRST205 for a relation its schema cache has not seen.
notify pgrst, 'reload schema';
