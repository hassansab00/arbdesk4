-- V1 keeps the original last-pre-midnight receipt definition. Native D-1
-- morning calls belong to a different checkpoint lineage in the side-aware v2.
create or replace view public.v_day_max_v1_original_calls with(security_invoker=true) as
select * from public.v_day_max_original_calls
where checkpoint<>'day_ahead' or predictive_context->>'source_kind'='day_max_day_ahead_calls';
revoke all on public.v_day_max_v1_original_calls from public,anon,authenticated;
grant select on public.v_day_max_v1_original_calls to service_role;
do $$ declare n bigint; body_hash text; begin
 select md5(prosrc) into body_hash from pg_proc where oid='public.publish_day_max_prediction(uuid,jsonb,jsonb)'::regprocedure;
 if body_hash not in ('cf192d3030eadaa2171e741dcb1ca50b','f3e7106b76473658cfc84a88ec8adb4e') then raise exception 'v1_writer_changed_review_required';end if;
 select count(*) into n from(
  (select * from public.v_day_max_original_calls where checkpoint<>'day_ahead' and target_date between current_date-1 and current_date+2
   except all select * from public.v_day_max_v1_original_calls where checkpoint<>'day_ahead' and target_date between current_date-1 and current_date+2)
  union all
  (select * from public.v_day_max_v1_original_calls where checkpoint<>'day_ahead' and target_date between current_date-1 and current_date+2
   except all select * from public.v_day_max_original_calls where checkpoint<>'day_ahead' and target_date between current_date-1 and current_date+2)
 ) differences;
 if n<>0 then raise exception 'v1_non_day_ahead_cohort_changed';end if;
end $$;
create or replace function public.publish_day_max_prediction(p_checkpoint uuid,p_legs jsonb,p_evidence jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare s public.strategies; a public.paper_accounts; p public.prediction_checkpoints;
  b public.bands; m public.markets; e public.day_max_prediction_entries;
  cfg jsonb; choice jsonb; leg jsonb; ctx jsonb; ref jsonb; zone text;
  scheduled timestamptz; start_at timestamptz; cutoff timestamptz; candidate timestamptz;
  hour numeric; k text; d bigint; plan uuid; top numeric; total numeric; edge numeric;
begin
  select * into s from public.strategies where strategy_id='s14_day_max_prediction';
  cfg:=s.extra->'day_max';
  if not found or s.enabled is distinct from true
      or cfg->>'policy' is distinct from 'day-max-frozen-fixed-stake-v1' then
    raise exception 'Day Max disabled or unconfigured'; end if;
  select * into a from public.paper_accounts where strategy_id=s.strategy_id and kind='shadow' for update;
  if not found or a.status is distinct from 'active' or a.mode is distinct from 'automatic' or a.entries_paused then
    raise exception 'Day Max own paper ledger not automatic'; end if;
  select * into p from public.v_day_max_v1_original_calls where checkpoint_id=p_checkpoint;
  if not found then raise exception 'Original frozen forecast unavailable'; end if;
  select * into e from public.day_max_prediction_entries where city_key=p.city_key and target_date=p.target_date;
  if found then return jsonb_build_object('allocated',false,'plan_id',e.plan_id,'reason','already_allocated'); end if;
  select x into choice from jsonb_array_elements(cfg->'cities') x where x->>'city_key'=p.city_key;
  if choice is null or choice->>'checkpoint' is distinct from p.checkpoint
     or (choice->>'stake_usd')::numeric is distinct from 10::numeric
     or p.checkpoint not in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h') then
    raise exception 'City or checkpoint outside Day Max scope'; end if;
  if cfg->>'activated_at' is null or p.inputs_ok is distinct from true or p.frozen_at is null
     or p.frozen_at<(cfg->>'activated_at')::timestamptz or p.frozen_at<coalesce((cfg->>'selection_changed_at')::timestamptz,(cfg->>'activated_at')::timestamptz) or p.frozen_at>clock_timestamp()
     or p.frozen_at<clock_timestamp()-interval '90 minutes'
     or p.decided_at>p.frozen_at then raise exception 'Frozen call stale, future or before activation'; end if;
  if exists(select 1 from public.prediction_checkpoints x where x.city_key=p.city_key and x.target_date=p.target_date
      and x.checkpoint=p.checkpoint and (x.decided_at,x.checkpoint_id)<(p.decided_at,p.checkpoint_id)) then
    raise exception 'Not the first original frozen call'; end if;
  select c.timezone into zone from public.cities c where c.city_key=p.city_key;
  scheduled:=p.local_decision_time at time zone zone;
  start_at:=p.target_date::timestamp at time zone zone;
  if zone is null or scheduled is null or p.decided_at<scheduled
     or p.frozen_at>scheduled+interval '75 minutes' or clock_timestamp()>=((p.target_date+1)::timestamp at time zone zone)
     then raise exception 'Late checkpoint capture or city day ended'; end if;
  ctx:=coalesce(p.predictive_context,'{}');
  if p.checkpoint='day_ahead' and (
      ctx->>'source_kind' is distinct from 'day_max_day_ahead_calls'
      or (ctx->>'cutoff_at')::timestamptz is distinct from start_at
      or (ctx->>'source_computed_at')::timestamptz is null
      or (ctx->>'source_computed_at')::timestamptz>=start_at
      or (ctx->>'source_computed_at')::timestamptz<start_at-interval '8 hours'
      or coalesce(p.input_as_of,p.forecast_issued_at)>(ctx->>'source_computed_at')::timestamptz
      or p.running_max_c is not null or scheduled is distinct from start_at) then
    raise exception 'Day-ahead source is not original pre-midnight pricing'; end if;
  if p.checkpoint in ('prepeak_2h','prepeak_1h') then
    cutoff:=scheduled+case when p.checkpoint='prepeak_2h' then interval '2 hours' else interval '1 hour' end;
  end if;
  foreach k in array array['clim_peak_hour','fch_peak_local_hour'] loop
    hour:=(ctx->>k)::numeric;
    if hour>=0 and hour<24 then
      candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone zone;
      cutoff:=least(cutoff,candidate);
    end if;
  end loop;
  ref:=ctx->'timing_reference';
  if ref->>'source'='derived_weather_peak' and ref->>'city_key'=p.city_key and ref->>'timezone'=zone
      and (ref->>'month')::int=extract(month from p.target_date)
      and (ref->>'computed_at')::timestamptz<=p.frozen_at then
    hour:=(ref->>'peak_hour_local')::numeric;
    if hour>=0 and hour<24 then
      candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone zone;
      cutoff:=least(cutoff,candidate);
    end if;
  end if;
  if p.frozen_at<start_at then cutoff:=start_at; end if;
  if cutoff is null or p.frozen_at>=cutoff or clock_timestamp()>=cutoff then
    raise exception 'At or after saved expected peak'; end if;
  if coalesce(p.input_as_of,p.forecast_issued_at,(ctx#>>'{forecast_input,run_at}')::timestamptz) is null
     or coalesce(p.input_as_of,p.forecast_issued_at)>p.frozen_at
     or (ctx#>>'{forecast_input,run_at}')::timestamptz>p.frozen_at then
    raise exception 'Forecast input is later than freeze'; end if;
  select max(value::numeric),sum(value::numeric) into top,total from jsonb_each_text(p.probs);
  if top is null or top<0 or top>1 or total not between .999 and 1.001
     or (p.probs->>p.top_band_id)::numeric is distinct from top then
    raise exception 'Frozen top bucket not modal'; end if;
  if jsonb_typeof(p_legs) is distinct from 'array' or jsonb_array_length(p_legs)<>1 then
    raise exception 'One Day Max YES leg required'; end if;
  leg:=p_legs->0;
  if leg->>'side' is distinct from 'YES' or leg->>'band_id' is distinct from p.top_band_id
     or (leg->>'cash_ceiling')::numeric is null or (leg->>'cash_ceiling')::numeric<=0
     or (leg->>'cash_ceiling')::numeric>10 then raise exception 'Day Max ten-dollar ceiling or bucket invalid'; end if;
  select * into b from public.bands where band_id=p.top_band_id::uuid;
  select * into m from public.markets where market_id=b.market_id;
  if m.city_key is distinct from p.city_key or m.resolution_date is distinct from p.target_date
     or m.closed is distinct from false then raise exception 'Frozen bucket market unavailable'; end if;
  insert into public.decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code,
    target_usd,held_usd,n_signals,params_version,prediction_id,prediction_source)
  values(gen_random_uuid(),now(),s.strategy_id,p.city_key,p.target_date,'BUY','day_max_frozen',10,0,1,
    jsonb_build_object('policy',cfg->>'policy','selection',choice)::text,p.checkpoint_id,case when p.checkpoint='day_ahead' then 'day_max_day_ahead_calls' else 'prediction_checkpoints' end)
  returning decision_id into d;
  insert into public.day_max_prediction_entries(city_key,target_date,checkpoint_id,account_id,decision_id,stake_ceiling,snapshot)
  values(p.city_key,p.target_date,p.checkpoint_id,a.account_id,d,10,
    jsonb_build_object('top_band_id',p.top_band_id,'top_prob',p.top_prob,'probs',p.probs,
      'frozen_at',p.frozen_at,'decided_at',p.decided_at,'checkpoint',p.checkpoint,'model_path',p.model_path,
      'priced_from',p.priced_from,'predictive_context',p.predictive_context,'selection',choice));
  -- The generic queue's normal risk rails remain authoritative. Recompute
  -- the probability edge from the original receipt, never caller-supplied p.
  edge:=top-(leg->>'limit_price')::numeric-coalesce((p_evidence->>'quoted_cost_per_basket')::numeric
    -(leg->>'limit_price')::numeric,0);
  plan:=public.publish_engine_plan(a.account_id,d,p_legs,coalesce(p_evidence,'{}')||jsonb_build_object(
    'checkpoint_id',p.checkpoint_id,'source','day_max_frozen','net_edge_per_share',edge,'fixed_stake_usd',10,
    'legs_p',jsonb_build_object(p.top_band_id||':YES',jsonb_build_object('p',top))));
  update public.day_max_prediction_entries set plan_id=plan where checkpoint_id=p.checkpoint_id;
  return jsonb_build_object('allocated',true,'plan_id',plan,'decision_id',d);
end $$;
revoke all on function public.publish_day_max_prediction(uuid,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.publish_day_max_prediction(uuid,jsonb,jsonb) to service_role;
