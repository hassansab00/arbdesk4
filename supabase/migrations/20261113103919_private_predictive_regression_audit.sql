-- Read-only, bounded operator audit of the existing original-call reports.
-- Never writes a prediction, fits a model, changes a gate or reconstructs a quote.
create or replace function public.predictive_regression_audit(
  p_city_key text default null,
  p_checkpoint text default null,
  p_limit integer default 50,
  p_after_city text default null,
  p_after_checkpoint text default null,
  p_flagged_only boolean default true
) returns jsonb language plpgsql stable security invoker
set search_path=pg_catalog,pg_temp
set statement_timeout='15s'
set work_mem='8MB' as $$
declare
  r record;
  v_recent jsonb;
  v_prior jsonb;
  v_flags jsonb;
  v_findings jsonb;
  v_learning jsonb;
  v_state jsonb;
  v_daily jsonb;
  v_rows jsonb := '[]'::jsonb;
  v_more boolean := false;
  v_city text;
  v_checkpoint text;
  v_result jsonb;
begin
  if p_limit is null or p_limit<1 or p_limit>100
     or p_flagged_only is null
     or (p_after_city is null)<>(p_after_checkpoint is null) then
    raise exception 'Use limit 1..100, a boolean flag filter and a complete city/checkpoint cursor'
      using errcode='22023';
  end if;
  for r in
    select city_key,checkpoint,as_of,report
    from public.derived_city_prediction_validation d
    where d.scope='served' and d.checkpoint<>'overall'
      and jsonb_typeof(d.report->'regression')='object'
      and (p_city_key is null or d.city_key=p_city_key)
      and (p_checkpoint is null or d.checkpoint=p_checkpoint)
      and (p_after_city is null or (d.city_key,d.checkpoint)>(p_after_city,p_after_checkpoint))
      and (not p_flagged_only or
        jsonb_array_length(case when jsonb_typeof(d.report#>'{regression,flags}')='array'
          then d.report#>'{regression,flags}' else '[]'::jsonb end)>0)
    order by d.city_key,d.checkpoint limit p_limit+1
  loop
    if jsonb_array_length(v_rows)>=p_limit then v_more:=true; exit; end if;
    v_recent:=coalesce(r.report#>'{regression,recent}','{}'::jsonb);
    v_prior:=coalesce(r.report#>'{regression,prior}','{}'::jsonb);
    v_flags:=r.report#>'{regression,flags}';
    v_findings:='[]'::jsonb;
    v_learning:='[]'::jsonb;
    if v_flags ? 'hit_rate_drop' and
       (v_recent->>'log_loss')::numeric < (v_prior->>'log_loss')::numeric then
      v_findings:=v_findings||'"hit_and_probability_scores_diverge"'::jsonb;
    end if;
    if v_flags ? 'market_outperforms' then
      v_findings:=v_findings||'"saved_market_outperforms_on_matched_calls"'::jsonb;
    end if;
    if v_flags ? 'correction_hurt' then
      v_findings:=v_findings||'"saved_correction_hurts_hits_and_log_loss"'::jsonb;
    end if;
    if coalesce((v_recent->>'verified')::integer,0)<coalesce((v_recent->>'n')::integer,0) then
      v_findings:=v_findings||'"recent_window_contains_legacy_unverified_calls"'::jsonb;
    end if;
    if coalesce((v_recent->>'pending')::integer,0)>0 then
      v_findings:=v_findings||'"recent_window_has_pending_calls"'::jsonb;
    end if;
    -- Exact version IDs often change on a daily fit/run; disclose their count
    -- without labeling each daily version as a different model recipe.
    v_recent:=(v_recent-'versions')||jsonb_build_object('version_count',
      (select count(*) from jsonb_object_keys(case when jsonb_typeof(v_recent->'versions')='object'
        then v_recent->'versions' else '{}'::jsonb end)));
    v_prior:=(v_prior-'versions')||jsonb_build_object('version_count',
      (select count(*) from jsonb_object_keys(case when jsonb_typeof(v_prior->'versions')='object'
        then v_prior->'versions' else '{}'::jsonb end)));
    for v_state in select value from jsonb_array_elements(
      (case when jsonb_typeof(r.report->'early_adaptation')='array' then r.report->'early_adaptation' else '[]'::jsonb end)||
      (case when jsonb_typeof(r.report->'ensemble')='array' then r.report->'ensemble' else '[]'::jsonb end)||
      (case when jsonb_typeof(r.report->'learning')='array' then r.report->'learning' else '[]'::jsonb end))
    loop
      v_learning:=v_learning||jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
        'policy',v_state->'policy','phase',v_state->'phase','model_version',v_state->'model_version',
        'as_of',v_state->'as_of','active',v_state->'active',
        'decision',v_state->'decision','reason',v_state->'reason',
        'train_dates',v_state->'train_dates','validation_dates',v_state->'validation_dates',
        'readiness',v_state->'readiness')));
    end loop;
    select coalesce(jsonb_agg(jsonb_build_object('date',x->'date','n',x->'n','pending',x->'pending','hits',x->'hits',
        'hit_rate',x->'hit_rate','early_comparison',x->'early_comparison') order by x->>'date'),'[]'::jsonb)
      into v_daily from jsonb_array_elements(case when jsonb_typeof(r.report->'recent_dates')='array'
        then r.report->'recent_dates' else '[]'::jsonb end) x
      where x->>'date'>=v_recent->>'start' and x->>'date'<v_recent->>'end_exclusive';
    v_rows:=v_rows||jsonb_build_array(jsonb_build_object(
      'city_key',r.city_key,'checkpoint',r.checkpoint,'source_as_of',r.as_of,
      'source_report_fingerprint',r.report->'_payload_sha256',
      'flags',v_flags,'findings',v_findings,'recent',v_recent,'prior',v_prior,
      'hit_rate_change',r.report#>'{regression,hit_rate_change}',
      'comparable_dates_ready',r.report#>'{regression,comparable_dates_ready}',
      'learning',v_learning,'retained_daily_details',v_daily,
      'daily_details_complete',
        (select coalesce(sum((x->>'n')::integer),0) from jsonb_array_elements(v_daily) x)=coalesce((v_recent->>'n')::integer,0)
        and (select count(*) from jsonb_array_elements(v_daily) x where (x->>'n')::integer>0)=coalesce((v_recent->>'dates')::integer,0)
        and (select coalesce(sum((x->>'pending')::integer),0) from jsonb_array_elements(v_daily) x)=coalesce((v_recent->>'pending')::integer,0),
      'causal_attribution_confirmed',false));
    v_city:=r.city_key;v_checkpoint:=r.checkpoint;
  end loop;
  v_result:=jsonb_build_object('policy','original-regression-audit-v1','rows',v_rows,
    'has_more',v_more,'next_cursor',case when v_more then
      jsonb_build_object('city_key',v_city,'checkpoint',v_checkpoint) else null end,
    'changes_serving',false,'changes_training_gates',false,
    'interpretation','Descriptive original-call evidence, not causal attribution or model approval. Market comparisons are saved probabilities, not executable returns. Correction pairs compare the saved raw centre and baseline corrected centre; served adaptations are scored separately. Version counts are not recipe counts. Daily detail is limited by the source report. Each cell retains its source timestamp.');
  if octet_length(v_result::text)>524288 then
    raise exception 'Audit exceeds 512 KiB; request a smaller page' using errcode='54000';
  end if;
  return v_result;
end $$;
revoke all on function public.predictive_regression_audit(text,text,integer,text,text,boolean)
  from public,anon,authenticated;
grant execute on function public.predictive_regression_audit(text,text,integer,text,text,boolean)
  to service_role;
notify pgrst,'reload schema';
