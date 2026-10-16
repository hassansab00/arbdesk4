-- P6.5: serialize existing scheduled and pipeline refreshes.
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
  -- Scheduled and pipeline refreshes share one transaction-scoped lock.
  -- A concurrent caller reports the skip; it never queues another full rebuild.
  if not pg_try_advisory_xact_lock(hashtextextended('arbdesk:heavy-page-cache', 0)) then
    return jsonb_build_object('skipped', 'refresh already running');
  end if;
  foreach m in array array['mv_opportunities', 'mv_prediction_ladder', 'mv_city_ladder_edges', 'mv_forecast_convergence_all', 'mv_city_hit_history',
                           'mv_prediction_hindsight', 'mv_edge_scaling', 'mv_calibration', 'mv_edge_realisation', 'mv_model_forecast_skill', 'mv_condition_skill', 'mv_checkpoint_scoreboard'] loop
    continue when to_regclass('public.' || m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I', m);
    out := out || jsonb_build_object(m, round(extract(epoch from clock_timestamp() - t0) * 1000));
  end loop;
  if to_regprocedure('public.refresh_page_status()') is not null then
    perform public.refresh_page_status();
  end if;
  perform public.log_ingest('refresh_page_cache', 'ok', 0, jsonb_build_object('ms', out));
  return out;
end $$;
revoke all on function public.refresh_page_cache() from public, anon, authenticated;
grant execute on function public.refresh_page_cache() to service_role;
