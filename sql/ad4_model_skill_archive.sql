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
