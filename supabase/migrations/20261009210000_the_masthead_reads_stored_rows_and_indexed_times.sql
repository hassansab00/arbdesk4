-- ===========================================================================
-- THE MASTHEAD READS STORED ROWS AND INDEXED TIMES (plan v2 P6.5; WXPredict
-- build 2.E, R11/R15; 9 Oct).
--
-- NAMED FROM THE LOG. The postgres log held 156 "canceling statement due to
-- statement timeout" in the 24 h to 9 Oct 20:34Z, every one a browser read
-- (user authenticator; the browser roles stop at 8 s). By the relation in
-- the query: v_data_freshness 57, v_opportunities 38, v_city_status 13,
-- v_prediction_hindsight 10, v_edge_scaling 7, v_prediction_hindsight_summary
-- 6, v_city_volume 6, 19 more across 12 relations. All 38 on v_opportunities
-- are the masthead's count of tradeable edges (web/components/Header.tsx),
-- which every open page sends once a minute; the 57 on v_data_freshness are
-- the freshness chips' read (web/lib/useFreshness.ts), every two minutes.
--
-- WHY THEY FAIL. The same work runs at very different speeds on this
-- instance: v_data_freshness executed in 9.6 s at one moment and 1.0 s a few
-- minutes later, with the same buffers. A read that fits at a quiet moment
-- dies at 8 s at a busy one, so the reads themselves have to be cheap. Since
-- 12 Sep (pg_stat_statements) the browser's role spent 94,391 s of the
-- database's 151,366 s; the masthead count alone 16,748 s over 7,529 calls.
--
-- 1. v_data_freshness: THE TIMESTAMPS ITS max() READS, INDEXED. Each of its
--    67 branches asks max(ts) of one table. sql/ad4_44 indexes that column on
--    every table of 1,000 rows or more - but decided which tables when it was
--    installed, so the tables that grew past 1,000 since were scanned whole on
--    every read: band_probabilities 112,620 rows, resolution_verdicts 32,050,
--    derived_model_forecast 36,243 and eight more. The eleven below are the
--    ones whose scan cost something measurable (9 Oct, the view's plan at a
--    quiet moment: 163, 133, 114, 52, 29, 26, 19, 18, 17, 14 and 9.6 ms) and
--    where an index takes no update's HOT path away (pg_stat_user_tables:
--    nine are never updated; band_probabilities and derived_model_forecast
--    are, but 25 of 66,347 and 444 of 19,077 of those updates were HOT).
--    Left out on purpose: derived_band_day_volume, whose upsert
--    sets computed_at = now() on each of its updates (881,105 to 9 Oct, 42 %
--    HOT), and markets, whose discovery (n8n P0.2) sets last_seen_at on
--    each of its upserts (16,332, 25 % HOT); derived_hit_tournament,
--    paper_trade_plans, fact_checkpoint_outcome and
--    derived_station_correction, each 9.3 ms or less.
--
--    Same name and shape as ad4_44's (ad4_ix_fresh_<table>, the column
--    DESC NULLS LAST), and skipped where an index already leads with the
--    column, so a reinstall of ad4_44 finds them and builds no second copy.
--    Rehearsed 9 Oct 20:45-20:52Z in transactions rolled back: 2.9 MiB, each built
--    in under 0.3 s; the view went from 7,001 / 263 / 71 ms (first, second,
--    third run in one connection) to 254 / 26 / 17 ms. The view is not
--    touched, so its rows are its rows.
--
-- 2. v_opportunities: STORED ROWS (the plan's own list for P6.5). It costs
--    about 2 s to compute (1.96 s for the masthead's count, 9 Oct), and
--    every page reads it or a view built on it. It becomes what the three
--    Predictive views became on 24 Sep (20260924040000):
--
--      v_opportunities_live  the full definition, unchanged (service role)
--      mv_opportunities      its rows, refreshed CONCURRENTLY
--      v_opportunities       select the same columns from mv_opportunities,
--                            replaced IN PLACE, so its grants and the five
--                            views built on it (v_trade_plan,
--                            v_city_reasoning, v_city_stats, v_band_ladder,
--                            v_data_health) stay bound and read stored rows
--
--    The wrapper re-applies the live view's one clock-dependent condition
--    (a market is shown until its resolution date has passed in the city's
--    own time zone) and its order, so between refreshes it differs from the
--    live view only by what was written since, never by the clock.
--
--    The strategies do not read the stored copy: scripts/signal_engine.py
--    reads v_opportunities_live, because it runs after the edge engine and
--    before the pipeline's refresh, and must see the edges just written.
--    Nothing else in scripts/, n8n/ or a function body reads v_opportunities
--    or a view built on it, except the morning brief (email, disabled) and
--    ad4_verify.
--
--    Refreshed by refresh_page_cache(): pg_cron at :12 and :42 (the :42 run
--    follows the :36 tick and P0.3's books at :24), and as the last step of
--    both pipelines, the intraday one after its edges, signals and orders.
--
--    Proved 9 Oct 20:45-20:52Z before applying, in one REPEATABLE READ snapshot,
--    the build below run and rolled back: v_opportunities 1,694 rows, and
--    every view built on it (v_trade_plan 1,694, v_city_reasoning 47,
--    v_city_stats 54, v_band_ladder 847, v_data_health 1), EXCEPT ALL 0 and
--    0 both ways against their rows before. The stored copy is 1.1 MiB.
--
-- The bodies are the ones in sql/ad4_89_page_cache.sql. Re-runnable; a
-- database without these tables or views (the contracts' fixture) is left
-- alone.
-- ===========================================================================

-- Replacing v_opportunities takes its exclusive lock, and every page read of
-- it (or of a view built on it) queues behind a waiting lock. Wait 5 s at
-- most; past that this fails whole and is run again at a quieter moment.
-- (The first apply, 9 Oct 21:17Z, ran past the SQL tool's 60 s beside a page
-- load whose reads timed out, and rolled back.)
set local lock_timeout = '5s';

-- 1. The freshness timestamps.
do $migration$
declare
  t      text;
  col    text;
  rel    regclass;
begin
  if to_regclass('public.data_freshness_spec') is null then
    raise notice '2.E: no data_freshness_spec here (sql/ad4_39); no freshness index';
    return;
  end if;
  foreach t in array array['resolution_verdicts', 'band_probabilities', 'derived_model_forecast',
                           'research_captures', 'weather_resolution_evidence', 'prediction_checkpoints',
                           'weather_resolution_attempts', 'anomalies', 'weather_forecast_models',
                           'ingest_log', 'fact_signal_outcome'] loop
    rel := to_regclass('public.' || t);
    continue when rel is null;
    select s.ts_column into col from public.data_freshness_spec s where s.table_name = t;
    continue when col is null or col = '-'
      or not exists (select 1 from pg_attribute a
                      where a.attrelid = rel and a.attname = col and a.attnum > 0 and not a.attisdropped);
    continue when exists (
      select 1 from pg_index i
        join pg_attribute a on a.attrelid = i.indrelid and a.attnum = i.indkey[0]
       where i.indrelid = rel and a.attname = col);
    execute format('create index if not exists %I on public.%I (%I desc nulls last)',
                   'ad4_ix_fresh_' || t, t, col);
  end loop;
end
$migration$;

-- 2. v_opportunities from stored rows.
do $migration$
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
end
$migration$;

-- 3. The refresh carries it.
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
  foreach m in array array['mv_opportunities', 'mv_prediction_ladder', 'mv_city_ladder_edges', 'mv_forecast_convergence_all', 'mv_city_hit_history'] loop
    continue when to_regclass('public.' || m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I', m);
    out := out || jsonb_build_object(m, round(extract(epoch from clock_timestamp() - t0) * 1000));
  end loop;
  perform public.log_ingest('refresh_page_cache', 'ok', 0, jsonb_build_object('ms', out));
  return out;
end $$;

comment on function public.refresh_page_cache() is
  'Refreshes the stored rows the pages read (plan v2 P6.5), without blocking a reader: the opportunities and the Predictive page''s views. pg_cron at :12 and :42; the pipelines call it after they write.';

revoke all on function public.refresh_page_cache() from public, anon, authenticated;
grant execute on function public.refresh_page_cache() to service_role;
