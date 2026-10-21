-- Superseded per-model measurements go through the verified repo archive.
-- Preserve each city/model/lead/evidence scope's latest full row at any age.
-- No prediction checkpoint, weather observation or outcome is modified.
create or replace view public.v_prunable_model_skill
with (security_invoker=true) as
select jsonb_build_array(f.city_key,f.model,f.lead_days,
         to_char(f.computed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US'))::text as skill_key,
       f.city_key,f.model,f.computed_at,f.lead_days,f.n_days,f.mae_c,f.bias_c,
       f.p90_abs_err_c,f.mae_bands,f.pct_within_one_band,f.band_width_c,f.evidence_scope,
       f.computed_at as captured_at
from public.derived_forecast_skill_model f
where exists(select 1 from public.derived_forecast_skill_model n
  where n.city_key=f.city_key and n.model=f.model and n.lead_days=f.lead_days
    and n.evidence_scope is not distinct from f.evidence_scope
    and n.computed_at>f.computed_at);
comment on view public.v_prunable_model_skill is
  'Superseded model skill measurements only; latest per city/model/lead/evidence scope always stays. captured_at aliases computed_at for archive date filtering. Service role only.';
revoke all on public.v_prunable_model_skill from public,anon,authenticated;
grant select on public.v_prunable_model_skill to service_role;

create or replace function public.prune_model_skill(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before timestamptz default null,
  p_expected_rows bigint default null
) returns jsonb language plpgsql security definer
set search_path=public,pg_temp as $fn$
declare
  v_before timestamptz;
  v_unmirrored timestamptz := (date_trunc('day',now() at time zone 'UTC')-interval '1 day') at time zone 'UTC';
  v_doomed bigint; v_young bigint; v_gone bigint;
  v_latest jsonb; v_after jsonb;
begin
  if p_keep_days is null or p_keep_days<7 then
    return jsonb_build_object('ok',false,'error','keep_days must be at least 7');
  end if;
  if p_dry_run is null then
    return jsonb_build_object('ok',false,'error','p_dry_run must be explicit');
  end if;
  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok',false,'error','p_expected_rows is required for a committed prune');
  end if;
  if exists(select 1 from public.proprietary_data_manifests
            where dataset in ('model_skill','derived_forecast_skill_model')) then
    return jsonb_build_object('ok',false,'error','signed model skill dataset requires archive-aware verification');
  end if;
  v_before := least(coalesce(p_before,now()-make_interval(days=>p_keep_days)),
                    now()-make_interval(days=>p_keep_days));
  if not p_dry_run then
    perform set_config('lock_timeout','2s',true);
    lock table public.derived_forecast_skill_model in share row exclusive mode;
  end if;
  select count(*),count(*) filter(where computed_at >= v_unmirrored)
    into v_doomed,v_young from public.v_prunable_model_skill where computed_at<v_before;
  if v_young>0 then
    return jsonb_build_object('ok',false,'error','rows have not reached the repo mirror','would_delete',v_doomed);
  end if;
  if p_expected_rows is not null and v_doomed<>p_expected_rows then
    return jsonb_build_object('ok',false,'error','archive row count mismatch','would_delete',v_doomed);
  end if;
  if p_dry_run then
    return jsonb_build_object('ok',true,'dry_run',true,'would_delete',v_doomed,'older_than',v_before);
  end if;
  select jsonb_agg(to_jsonb(s) order by city_key,model,lead_days,evidence_scope nulls first)
    into v_latest from (
      select distinct on(city_key,model,lead_days,evidence_scope) *
      from public.derived_forecast_skill_model
      order by city_key,model,lead_days,evidence_scope,computed_at desc
    ) s;
  delete from public.derived_forecast_skill_model f
  using public.v_prunable_model_skill p
  where f.city_key=p.city_key and f.model=p.model and f.lead_days=p.lead_days
    and f.computed_at=p.computed_at and p.computed_at<v_before;
  get diagnostics v_gone = row_count;
  if v_gone<>v_doomed then
    raise exception 'prune_model_skill: counted %, deleted %; rollback',v_doomed,v_gone;
  end if;
  select jsonb_agg(to_jsonb(s) order by city_key,model,lead_days,evidence_scope nulls first)
    into v_after from (
      select distinct on(city_key,model,lead_days,evidence_scope) *
      from public.derived_forecast_skill_model
      order by city_key,model,lead_days,evidence_scope,computed_at desc
    ) s;
  if v_latest is distinct from v_after then
    raise exception 'prune_model_skill: latest full rows changed; rollback';
  end if;
  return jsonb_build_object('ok',true,'deleted',v_gone,'older_than',v_before,
                           'note','verified repo archive retains every removed measurement');
end $fn$;
revoke all on function public.prune_model_skill(integer,boolean,timestamptz,bigint) from public,anon,authenticated;
grant execute on function public.prune_model_skill(integer,boolean,timestamptz,bigint) to service_role;

create or replace function public.request_reclaim(p_table text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowed constant text[] := array[
    'research_captures', 'paper_resolution_evidence', 'book_snapshots', 'edges',
    'weather_observations', 'weather_forecasts', 'trades_observed', 'decisions',
    -- appended (28 Sep), so every table above keeps its two-minute slot
    'paper_book_evidence', 'weather_forecast_features', 'signals',
    -- appended (29 Sep, P1.6 phase 2)
    'weather_forecast_models', 'band_probabilities',
    -- appended (7 Oct, WXPredict build 2.A)
    'derived_city_correlation', 'derived_model_forecast',
    -- appended (11 Oct, WXPredict WP3)
    'live_capture',
    'derived_forecast_skill_model'];
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

-- Quiet-window backstop; the archive also asks for reclaim after pruning.
do $backstop$ begin
  if to_regprocedure('cron.schedule(text,text,text)') is not null then
    perform cron.schedule(
      'ad4_reclaim_derived_forecast_skill_model',
      '25 3 * * *',
      'VACUUM (FULL, ANALYZE) public.derived_forecast_skill_model'
    );
  end if;
end $backstop$;


insert into public.job_sla (job, cadence_minutes, cadence, scheduled_by) values
  ('archive_model_skill', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock')
on conflict (job) do update set cadence_minutes=excluded.cadence_minutes,
 cadence=excluded.cadence,scheduled_by=excluded.scheduled_by;
