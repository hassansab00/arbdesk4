-- ===========================================================================
-- THE HEAVY PAGE VIEWS READ STORED ROWS (plan v2 P6.5; WXPredict build 2.E
-- part 2, R11; 11 Oct).
--
-- NAMED FROM THE LOG. 2.E (20261009210000) took the masthead's two reads off
-- the clock; its acceptance (R11: no browser read dies at the 8 s statement
-- timeout for 24 h) then failed: 133 in the 24 h to 10 Oct 21:45Z, all from
-- page loads (PostgREST, user authenticator), in 31 bursts where a page's
-- reads die together. By view: v_city_status 28, v_prediction_hindsight 20,
-- v_data_freshness 18, v_prediction_hindsight_summary 13, v_city_volume 11,
-- 15 more at 1-6 each. The Predictive page asks about a dozen views at once,
-- each computed live, about 16 s of work on this instance; a read that fits
-- alone dies beside the others. v_prediction_hindsight is read a page of
-- 1,000 rows at a time (web/lib/readAll.ts): 5,533 rows on 11 Oct, six
-- requests, each computing the whole view again.
--
-- 1. SIX VIEWS ONLY PAGES READ, from stored rows, as v_opportunities and the
--    Predictive views became (v_X_live -> mv_X -> v_X, the wrapper replaced
--    IN PLACE so its grants and the views built on it stay bound):
--
--      v_prediction_hindsight   the hit record; v_prediction_hindsight_summary
--                               and v_city_status are built on it
--      v_edge_scaling           5,149 ms alone as anon (10 Oct night)
--      v_calibration            invoker views, the three: their copies are
--      v_edge_realisation       readable by anon and authenticated, as the
--                               wrapper reads them as the page's own role
--      v_model_forecast_skill   1,633 ms alone as anon (10 Oct night)
--      v_condition_skill
--
--    No script, workflow, tool or function body reads any of the six (11 Oct:
--    grep of scripts/, n8n/, tools/, .github/; pg_proc.prosrc live), and no
--    view in their trees reads the clock (now(), current_date and the like:
--    none in any view of the six trees, and the one function they call,
--    book_mark, is immutable), so at each refresh a copy equals its live view
--    and between refreshes it lags only what was written since. Each wrapper
--    keeps its view's options (CREATE OR REPLACE VIEW without WITH would
--    reset security_invoker) and its order (v_edge_scaling by edge_bucket,
--    v_calibration by bucket, which is its width_bucket(model_prob, 0, 1, 10);
--    v_edge_realisation by ord, which is its min(ord)).
--
--    Left live, and why: v_city_volume feeds v_opportunities_live, which the
--    strategies read (scripts/signal_engine.py); it, v_data_freshness,
--    v_current_prediction and v_strategy_board read the clock (11 Oct, their
--    definitions), so a stored copy would drift from them between refreshes.
--
-- 2. THE CANDIDATE GATE KEEPS THE LIVE HINDSIGHT. v_city_status counts each
--    city's settled days per checkpoint from v_prediction_hindsight, so it now
--    counts the stored rows, and its own cost goes with them (2,159 ms,
--    EXPLAIN ANALYZE 11 Oct 04:03Z, nearly all of it the hindsight). The page
--    reads it so. The tick's Candidate gate (scripts/engine_shadow.py) starts
--    its read when the tick starts, and the tick banks settled checkpoints
--    (scripts/confirm_recent.py) between refreshes; it reads
--    v_city_status_live, the same definition with v_prediction_hindsight_live
--    in its place, so it counts what it counted before. Derived from
--    v_city_status's own text (exactly one mention of the hindsight, or this
--    refuses), the service role's only. Apply this before the engine's
--    change that reads it is merged.
--
-- REHEARSED 11 Oct 04:09:06-04:09:43Z in one REPEATABLE READ snapshot, the
-- three parts below run and rolled back:
--   - every relation read the same rows before and after, EXCEPT ALL 0 and 0
--     both ways: v_prediction_hindsight 5,533, v_prediction_hindsight_summary
--     7, v_city_status 658 (and v_city_status_live 658 against it),
--     v_edge_scaling 1, v_calibration 10, v_edge_realisation 6,
--     v_model_forecast_skill 387, v_condition_skill 44; the three ordered
--     views in the same order (md5 of the rows as read);
--   - the six built in 2.28 s; 1,597,440 bytes stored (hindsight 1,351,680,
--     model skill 81,920, the other four 40,960 each);
--   - read as anon afterwards: 0.25 to 4.5 ms each of the eight (v_city_status
--     4.5 ms), where the same transaction had read seven of them live in 0.43
--     to 2.80 s (hindsight 2.80 s, edge scaling 2.59 s, city status 1.18 s,
--     model skill 1.05 s; condition skill not timed alone);
--   - refresh_page_cache() 21.9 s whole, the six new copies 7,890 ms of it
--     (hindsight 2,553, edge scaling 3,192, calibration 445, realisation 411,
--     model skill 991, condition skill 298).
--
-- The bodies are the ones in sql/ad4_89_page_cache.sql, less the statements
-- there that remove an old copy: this removes nothing, and refuses where a
-- stored copy is left from before (ad4_89 rebuilds it). Re-runnable; a
-- database without these views (the contracts' fixture) is left alone.
-- ===========================================================================

-- Replacing a view takes its exclusive lock, and every page read of it
-- queues behind a waiting lock. Wait 5 s at most; past that this fails whole
-- and is run again at a quieter moment (20261009210000).
set local lock_timeout = '5s';

-- 1. Six page views from stored rows.
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
      ('v_prediction_hindsight', 'mv_prediction_hindsight', 'city_key, for_date, called_when, call_order', ''),
      ('v_edge_scaling',         'mv_edge_scaling',         'edge_bucket',                                 ' order by edge_bucket'),
      ('v_calibration',          'mv_calibration',          'bucket',                                      ' order by bucket'),
      ('v_edge_realisation',     'mv_edge_realisation',     'edge_bucket',                                 ' order by ord'),
      ('v_model_forecast_skill', 'mv_model_forecast_skill', 'city_key, lead_days',                         ''),
      ('v_condition_skill',      'mv_condition_skill',      'city_key',                                    '')
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
      || spec.view_name || ', the stored rows (plan v2 P6.5; WXPredict build 2.E part 2).');
    execute format('comment on materialized view public.%I is %L', spec.mv_name,
      'Stored rows of ' || spec.view_name || '_live, refreshed by refresh_page_cache() (plan v2 P6.5; WXPredict build 2.E part 2). The page reads '
      || spec.view_name || ', which selects from here.');
  end loop;
end
$migration$;

-- 2. The Candidate gate keeps the live hindsight. v_city_status counts each
-- city's settled days per checkpoint from v_prediction_hindsight, which now
-- serves stored rows; the page reads it so. The tick's Candidate gate
-- (scripts/engine_shadow.py) reads v_city_status_live: the same definition
-- over v_prediction_hindsight_live, so the engine sees what it saw before.
do $migration$
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
  if to_regclass('public.v_city_status_live') is null then
    def := pg_get_viewdef(v, true);
    select count(*) into n from regexp_matches(def, '\m(public\.)?v_prediction_hindsight\M', 'g');
    if n <> 1 then
      raise exception 'v_city_status names v_prediction_hindsight % times, not once: derive v_city_status_live by hand', n;
    end if;
    select coalesce(' with (' || array_to_string(c.reloptions, ', ') || ')', '') into opts
      from pg_class c where c.oid = v;
    execute format('create view public.v_city_status_live%s as %s', opts,
                   regexp_replace(def, '\m(public\.)?v_prediction_hindsight\M', 'public.v_prediction_hindsight_live'));
  end if;
  if not exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
                  where r.ev_class = 'public.v_city_status_live'::regclass
                    and d.refobjid = 'public.v_prediction_hindsight_live'::regclass)
     or exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
                 where r.ev_class = 'public.v_city_status_live'::regclass
                   and d.refobjid = 'public.v_prediction_hindsight'::regclass) then
    raise exception 'v_city_status_live does not read the live hindsight alone';
  end if;
  revoke all on public.v_city_status_live from public, anon, authenticated;
  grant select on public.v_city_status_live to service_role;
  comment on view public.v_city_status_live is
    'v_city_status over v_prediction_hindsight_live: the Candidate gate (scripts/engine_shadow.py) reads this, so its settled days are counted from the tables. The page reads v_city_status, over the stored hindsight (WXPredict build 2.E part 2).';
end
$migration$;

-- 3. The refresh carries them. The hindsight's after the hit history it reads.
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

-- TELL POSTGREST: the Candidate gate reads v_city_status_live through it,
-- which answers PGRST205 for a relation its schema cache has not seen.
notify pgrst, 'reload schema';
