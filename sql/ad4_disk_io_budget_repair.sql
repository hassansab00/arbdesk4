-- Reduce repeated cache rebuilds and replace routine full-table rewrites.
-- No research, frozen-call, paper-ledger or archive rows are changed.
set local lock_timeout = '5s';
set local statement_timeout = '120s';

create index if not exists ad4_ix_ingest_log_job_status_finished
  on public.ingest_log (job, status, finished_at desc)
  where finished_at is not null;
create index if not exists ad4_ix_ingest_log_job_log_id
  on public.ingest_log (job, log_id desc);
create index if not exists ad4_ix_prediction_checkpoints_frozen_order
  on public.prediction_checkpoints (city_key, target_date, checkpoint, decided_at, checkpoint_id);

create schema if not exists arbdesk_private;
create table if not exists arbdesk_private.page_cache_refresh_state (
  cache_group text primary key check (cache_group in ('fast','research')),
  source_token text not null,
  refreshed_at timestamptz not null
);
alter table arbdesk_private.page_cache_refresh_state enable row level security;
revoke all on arbdesk_private.page_cache_refresh_state from public, anon, authenticated;
grant select, insert, update on arbdesk_private.page_cache_refresh_state to service_role;

-- Statistics are an inexpensive, eventually consistent invalidation hint.
-- The hour bucket also forces clock-dependent views to advance when idle.
-- Exclude receipt/state writes so a refresh cannot invalidate itself.
create or replace function arbdesk_private.page_cache_source_token()
returns text language sql stable security definer
set search_path = pg_catalog, pg_temp as $$
  select md5(concat_ws('|',
    date_trunc('hour', statement_timestamp() at time zone 'UTC')::text,
    (select stats_reset::text from pg_stat_database where datname=current_database()),
    (select string_agg(concat_ws(':',s.relid,s.n_tup_ins,s.n_tup_upd,s.n_tup_del),',' order by s.relid)
       from pg_stat_all_tables s join pg_class c on c.oid=s.relid
      where s.schemaname='public' and s.relname<>'ingest_log'
        and c.relkind in ('r','p'))));
$$;
revoke all on function arbdesk_private.page_cache_source_token() from public, anon, authenticated;

create or replace function arbdesk_private.refresh_page_cache_group(p_group text)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
set work_mem = '8MB' as $$
declare
  v_token text;
  v_names text[];
  m text;
  t0 timestamptz;
  result jsonb := '{}'::jsonb;
begin
  if p_group='fast' then
    v_names := array['mv_opportunities','mv_prediction_ladder','mv_city_ladder_edges'];
  elsif p_group='research' then
    v_names := array['mv_forecast_convergence_all','mv_city_hit_history',
      'mv_prediction_hindsight','mv_edge_scaling','mv_calibration','mv_edge_realisation',
      'mv_model_forecast_skill','mv_condition_skill','mv_checkpoint_scoreboard'];
  else
    raise exception 'Unknown cache group %',p_group using errcode='22023';
  end if;
  -- Use the existing shared lock: fast and research work cannot overlap.
  if not pg_try_advisory_xact_lock(hashtextextended('arbdesk:heavy-page-cache',0)) then
    return jsonb_build_object('skipped','refresh already running');
  end if;
  v_token := arbdesk_private.page_cache_source_token();
  if exists(select 1 from arbdesk_private.page_cache_refresh_state
      where cache_group=p_group and source_token=v_token) then
    return jsonb_build_object('unchanged',true);
  end if;
  foreach m in array v_names loop
    continue when to_regclass('public.'||m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I',m);
    result := result || jsonb_build_object(m,round(extract(epoch from clock_timestamp()-t0)*1000));
  end loop;
  insert into arbdesk_private.page_cache_refresh_state(cache_group,source_token,refreshed_at)
    values(p_group,v_token,clock_timestamp())
    on conflict(cache_group) do update
    set source_token=excluded.source_token,refreshed_at=excluded.refreshed_at;
  return result;
end $$;
revoke all on function arbdesk_private.refresh_page_cache_group(text) from public, anon, authenticated;

create or replace function public.refresh_page_cache()
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
set work_mem = '8MB' as $$
declare result jsonb;
begin
  result := arbdesk_private.refresh_page_cache_group('fast');
  if result ? 'skipped' then return result; end if;
  perform public.refresh_page_status();
  perform public.log_ingest('refresh_page_cache','ok',0,jsonb_build_object('ms',result,'group','fast'));
  return result;
end $$;
revoke all on function public.refresh_page_cache() from public, anon, authenticated;
grant execute on function public.refresh_page_cache() to service_role;

create or replace function arbdesk_private.refresh_research_cache()
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
set work_mem = '8MB' as $$
declare result jsonb;
begin
  result := arbdesk_private.refresh_page_cache_group('research');
  perform public.log_ingest('refresh_research_cache',
    case when result ? 'skipped' then 'attention' else 'ok' end,0,
    jsonb_build_object('ms',result,'group','research'));
  return result;
end $$;
revoke all on function arbdesk_private.refresh_research_cache() from public, anon, authenticated;

-- Existing request workers already retry only results carrying "skipped".
-- An unchanged-source result is successful, contains no skipped key and
-- completes the durable request without another cron attempt.

-- Routine autovacuum makes dead pages reusable without rewriting live rows.
-- It does not promise to shrink the allocated file on disk.
do $$
declare j record; t text;
begin
  if to_regclass('cron.job') is not null then
    for j in select jobid from cron.job where command ~* 'vacuum[[:space:](]+.*full' loop
      perform cron.unschedule(j.jobid);
    end loop;
  end if;
  foreach t in array array[
    'research_captures','paper_resolution_evidence','book_snapshots','edges',
    'weather_observations','weather_forecasts','trades_observed','decisions',
    'paper_book_evidence','weather_forecast_features','signals',
    'weather_forecast_models','band_probabilities','derived_city_correlation',
    'derived_model_forecast','live_capture','derived_forecast_skill_model'] loop
    if to_regclass('public.'||t) is not null then
      execute format('alter table public.%I set (autovacuum_enabled=true, autovacuum_vacuum_scale_factor=0.05, autovacuum_vacuum_threshold=1000, autovacuum_analyze_scale_factor=0.05, autovacuum_analyze_threshold=500)',t);
    end if;
  end loop;
end $$;

create or replace function public.request_reclaim(p_table text)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp as $$
declare
  v_allowed constant text[] := array[
    'research_captures','paper_resolution_evidence','book_snapshots','edges',
    'weather_observations','weather_forecasts','trades_observed','decisions',
    'paper_book_evidence','weather_forecast_features','signals',
    'weather_forecast_models','band_probabilities','derived_city_correlation',
    'derived_model_forecast','live_capture','derived_forecast_skill_model'];
begin
  if p_table is null or not(p_table=any(v_allowed)) then
    raise exception 'request_reclaim: % is not a table the archive prunes',p_table using errcode='22023';
  end if;
  perform public.log_ingest('request_reclaim','ok',0,
    jsonb_build_object('table',p_table,'mode','autovacuum','completed',false));
  return jsonb_build_object('ok',true,'table',p_table,'mode','autovacuum','completed',false);
end $$;
comment on function public.request_reclaim(text) is
  'Acknowledges ordinary autovacuum maintenance after archive pruning. Does not schedule a full rewrite or claim completed space reclamation.';
revoke all on function public.request_reclaim(text) from public, anon, authenticated;
grant execute on function public.request_reclaim(text) to service_role;

do $$ begin
  if to_regclass('cron.job') is not null then
    perform cron.schedule('ad4_refresh_page_cache','42 * * * *',
      'set statement_timeout = ''120s''; select public.refresh_page_cache()');
    perform cron.schedule('ad4_refresh_page_status','12 * * * *',
      'set statement_timeout = ''120s''; select public.refresh_page_status()');
    perform cron.schedule('ad4_refresh_research_cache','47 3,9,15,21 * * *',
      'set statement_timeout = ''240s''; select arbdesk_private.refresh_research_cache()');
  end if;
end $$;
update public.job_sla set cadence='hourly at :42',cadence_minutes=60
 where job='refresh_page_cache';
notify pgrst,'reload schema';
