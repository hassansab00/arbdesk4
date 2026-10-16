-- P6.1/P6.5: samples ride the existing half-hourly page refresh. No new job.
-- P6.5: page-only samples; the live views and every engine reader stay live.
-- A sample's age is explicit. Health thresholds are evaluated on the current
-- clock, and a missed refresh raises instead of claiming the inputs are fine.
do $$
declare s record;
begin
  for s in select * from (values
    ('v_data_freshness','mv_page_data_freshness','table_name'),
    ('v_city_volume','mv_page_city_volume','city_key')
  ) x(source_name,copy_name,key_column) loop
    continue when to_regclass('public.' || s.source_name) is null;
    if to_regclass('public.' || s.copy_name) is null then
      execute format('create materialized view public.%I as select v.*, statement_timestamp() as sampled_at from public.%I v', s.copy_name,s.source_name);
      execute format('create unique index %I on public.%I (%I)',s.copy_name || '_key',s.copy_name,s.key_column);
    end if;
    execute format('revoke all on public.%I from public, anon, authenticated',s.copy_name);
    execute format('grant select on public.%I to service_role',s.copy_name);
  end loop;
end $$;

create or replace function public.page_sample_current(p_sampled_at timestamptz)
returns boolean language plpgsql stable set search_path = public, pg_temp as $$
begin
  if p_sampled_at is null or p_sampled_at < statement_timestamp() - interval '40 minutes' then
    raise exception 'Page status sample is overdue; refresh_page_status has not completed within forty minutes' using errcode='55000';
  end if;
  return true;
end $$;
revoke all on function public.page_sample_current(timestamptz) from public;
grant execute on function public.page_sample_current(timestamptz) to anon, authenticated, service_role;

do $$
begin
  if to_regclass('public.mv_page_data_freshness') is not null then
    execute $v$create or replace view public.v_page_data_freshness as
      select table_name,layer,plain_english,rows,rows_estimated,newest,
        round(extract(epoch from (now()-newest))/3600.0,1) as age_hours,fresh_hours,
        case when state in ('absent','empty') then state
             when newest is not null and fresh_hours is not null
                  and extract(epoch from (now()-newest))/3600.0 > fresh_hours then 'stale'
             else 'ok' end::text as state, sampled_at
      from public.mv_page_data_freshness where public.page_sample_current(sampled_at)$v$;
    revoke all on public.v_page_data_freshness from public;
    grant select on public.v_page_data_freshness to anon,authenticated,service_role;
  end if;
  if to_regclass('public.mv_page_city_volume') is not null then
    execute $v$create or replace view public.v_page_city_volume as
      select * from public.mv_page_city_volume where public.page_sample_current(sampled_at)$v$;
    revoke all on public.v_page_city_volume from public;
    grant select on public.v_page_city_volume to anon,authenticated,service_role;
  end if;
end $$;

create or replace function public.refresh_page_status()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare m text; t0 timestamptz; result jsonb := '{}'::jsonb;
begin
  if not pg_try_advisory_xact_lock(hashtextextended('arbdesk:page-status',0)) then
    return jsonb_build_object('skipped','refresh already running');
  end if;
  foreach m in array array['mv_page_data_freshness','mv_page_city_volume'] loop
    continue when to_regclass('public.' || m) is null;
    t0 := clock_timestamp();
    execute format('refresh materialized view concurrently public.%I',m);
    result := result || jsonb_build_object(m,round(extract(epoch from clock_timestamp()-t0)*1000));
  end loop;
  perform public.log_ingest('refresh_page_status','ok',0,jsonb_build_object('ms',result));
  return result;
end $$;
revoke all on function public.refresh_page_status() from public,anon,authenticated;
grant execute on function public.refresh_page_status() to service_role;

notify pgrst,'reload schema';
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
