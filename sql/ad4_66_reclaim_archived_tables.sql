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
