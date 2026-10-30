-- Prospective paper-only receipt of the last complete ladder before local midnight.
-- The server chooses the source. No old checkpoints or positions are rewritten.
create table if not exists public.day_max_day_ahead_calls(
  call_id uuid primary key,
  city_key text not null,
  target_date date not null,
  source_computed_at timestamptz not null,
  frozen_at timestamptz not null default clock_timestamp(),
  record jsonb not null,
  unique(city_key,target_date)
);
alter table public.day_max_day_ahead_calls enable row level security;
revoke all on public.day_max_day_ahead_calls from public,anon,authenticated,service_role;
grant select on public.day_max_day_ahead_calls to service_role;
drop policy if exists day_max_day_ahead_read on public.day_max_day_ahead_calls;
create policy day_max_day_ahead_read on public.day_max_day_ahead_calls for select to service_role using(true);
drop trigger if exists day_max_day_ahead_immutable on public.day_max_day_ahead_calls;
create trigger day_max_day_ahead_immutable before update or delete on public.day_max_day_ahead_calls
for each row execute function arbdesk_private.immutable_record();
drop trigger if exists day_max_day_ahead_no_truncate on public.day_max_day_ahead_calls;
create trigger day_max_day_ahead_no_truncate before truncate on public.day_max_day_ahead_calls
for each statement execute function arbdesk_private.immutable_record();

create or replace function public.capture_day_max_day_ahead(p_city text,p_target date)
returns jsonb language plpgsql security definer set search_path='' as $$
declare cfg jsonb; choice jsonb; zone text; station text; m public.markets;
  stamp timestamptz:=clock_timestamp(); boundary timestamptz; source_at timestamptz;
  id uuid:=gen_random_uuid(); probs jsonb; raw_probs jsonb; ids jsonb; top text;
  head public.band_probabilities; path text; reference jsonb; packet jsonb; existing uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('day-max-da|'||p_city||'|'||p_target,0));
  select call_id into existing from public.day_max_day_ahead_calls where city_key=p_city and target_date=p_target;
  if found then return jsonb_build_object('captured',false,'call_id',existing,'reason','already_frozen'); end if;
  select s.extra->'day_max' into cfg from public.strategies s
    where s.strategy_id='s14_day_max_prediction' and s.enabled;
  select x into choice from jsonb_array_elements(cfg->'cities') x where x->>'city_key'=p_city;
  if cfg->>'policy' is distinct from 'day-max-frozen-fixed-stake-v1' or choice->>'checkpoint' is distinct from 'day_ahead'
    or (choice->>'stake_usd')::numeric is distinct from 10::numeric or cfg->>'activated_at' is null then
    raise exception 'Day Max city or early checkpoint unconfigured'; end if;
  select c.timezone,c.icao into zone,station from public.cities c where c.city_key=p_city and c.status='active';
  boundary:=p_target::timestamp at time zone zone;
  if boundary is null or stamp<boundary or stamp>boundary+interval '75 minutes'
     or stamp<(cfg->>'activated_at')::timestamptz
     or stamp<coalesce((cfg->>'selection_changed_at')::timestamptz,(cfg->>'activated_at')::timestamptz) then
    raise exception 'Outside prospective local-midnight capture window'; end if;
  select * into m from public.markets where city_key=p_city and resolution_date=p_target;
  if not found or m.closed is distinct from false or m.winning_band_id is not null or m.resolved_band_id is not null then
    raise exception 'Day Max target market unavailable or settled'; end if;
  -- A run must contain every bucket once, with one coherent forecast/header,
  -- and absolutely no observation floor from the target day.
  select q.computed_at into source_at from public.band_probabilities q join public.bands b using(band_id)
    where b.market_id=m.market_id and q.computed_at<boundary and q.computed_at>=boundary-interval '8 hours'
      and q.computed_at>=(cfg->>'activated_at')::timestamptz
    group by q.computed_at
    having count(*)=(select count(*) from public.bands where market_id=m.market_id)
      and count(*)=count(distinct q.band_id)
      and count(q.raw_prob)=count(*) and count(q.calibrated_prob)=count(*)
      and count(q.input_forecast_run)=count(*)
      and bool_and(q.pricing_eligible and q.observed_floor_c is null
        and q.input_forecast_run is not null and q.input_forecast_run<=q.computed_at
        and q.calibrated_prob between 0 and 1 and q.raw_prob between 0 and 1)
      and abs(sum(q.calibrated_prob)-1)<=.0001 and abs(sum(q.raw_prob)-1)<=.0001
      and count(distinct coalesce(q.forecast_version::text,'null'))=1
      and count(distinct coalesce(q.calibration_version::text,'null'))=1
      and count(distinct q.input_forecast_run)=1
      and count(distinct coalesce(q.centre_c::text,'null'))=1
      and count(distinct coalesce(q.sigma_c::text,'null'))=1
      and count(distinct coalesce(q.forecast_max_c::text,'null'))=1
    order by q.computed_at desc limit 1;
  if source_at is null then return jsonb_build_object('captured',false,'reason','no_complete_original_pre_midnight_ladder'); end if;
  select jsonb_object_agg(q.band_id::text,q.calibrated_prob),jsonb_object_agg(q.band_id::text,q.raw_prob),
      jsonb_agg(q.prob_id order by q.band_id)
    into probs,raw_probs,ids from public.band_probabilities q join public.bands b using(band_id)
    where b.market_id=m.market_id and q.computed_at=source_at;
  select q.* into head from public.band_probabilities q join public.bands b using(band_id)
    where b.market_id=m.market_id and q.computed_at=source_at order by q.band_id limit 1;
  select label into path from public.model_versions where version_id=head.forecast_version;
  select key into top from jsonb_each_text(probs) order by value::numeric desc,key limit 1;
  select to_jsonb(p)||jsonb_build_object('source','derived_weather_peak','timezone',zone) into reference
    from public.derived_weather_peak p where p.city_key=p_city and p.month=extract(month from p_target)
      and p.computed_at<=stamp and p.peak_hour_local>=0 and p.peak_hour_local<24;
  if reference is null or stamp >= (p_target::timestamp+round((reference->>'peak_hour_local')::numeric*60)*interval '1 minute') at time zone zone then
    return jsonb_build_object('captured',false,'reason','no_future_saved_peak'); end if;
  packet:=jsonb_build_object('checkpoint_id',id,'city_key',p_city,'target_date',p_target,'checkpoint','day_ahead',
    'decided_at',stamp,'frozen_at',stamp,'local_decision_time',p_target::timestamp,
    'engine_version',coalesce(path,head.forecast_version::text,'original-pricing'),
    'model_path','original_pre_midnight_pricing','priced_from',path,'probs',probs,'raw_probs',raw_probs,
    'top_band_id',top,'top_prob',(probs->>top)::numeric,'centre_c',head.centre_c,'sigma_c',head.sigma_c,
    'raw_forecast_c',head.forecast_max_c,'station',station,'inputs_ok',true,
    'forecast_issued_at',head.input_forecast_run,'input_as_of',head.input_forecast_run,
    'predictive_context',jsonb_build_object('source_kind','day_max_day_ahead_calls',
      'source_computed_at',source_at,'cutoff_at',boundary,'source_price_ids',ids,'source_pricing_header',to_jsonb(head),'timing_reference',reference));
  insert into public.day_max_day_ahead_calls(call_id,city_key,target_date,source_computed_at,frozen_at,record)
    values(id,p_city,p_target,source_at,stamp,packet);
  return jsonb_build_object('captured',true,'call_id',id,'source_computed_at',source_at,'frozen_at',stamp);
end $$;
revoke all on function public.capture_day_max_day_ahead(text,date) from public,anon,authenticated;
grant execute on function public.capture_day_max_day_ahead(text,date) to service_role;

-- Preserve every existing decision source and allow this retained receipt too.
do $$ declare expression text; begin
  select pg_get_expr(conbin,conrelid) into expression from pg_constraint
    where conrelid='public.decisions'::regclass and conname='decisions_prediction_source';
  if expression is null then raise exception 'Original prediction-source constraint unavailable'; end if;
  if position('day_max_day_ahead_calls' in expression)=0 then
    alter table public.decisions drop constraint decisions_prediction_source;
    execute 'alter table public.decisions add constraint decisions_prediction_source check (('||expression||') or prediction_source=''day_max_day_ahead_calls'')';
  end if;
end $$;

-- A private reader exposes the same checkpoint shape without injecting a new
-- horizon into the global checkpoint scoreboard or its immutable old rows.
create or replace view public.v_day_max_original_calls with(security_invoker=true) as
select p.* from public.prediction_checkpoints p
union all
select r.* from public.day_max_day_ahead_calls c
cross join lateral jsonb_populate_record(null::public.prediction_checkpoints,c.record) r
where r.checkpoint_id=c.call_id and r.city_key=c.city_key and r.target_date=c.target_date;
revoke all on public.v_day_max_original_calls from public,anon,authenticated;
grant select on public.v_day_max_original_calls to service_role;

do $$ declare differences bigint; begin
  select count(*) into differences from (
    (select * from public.prediction_checkpoints except all select * from public.v_day_max_original_calls where checkpoint<>'day_ahead')
    union all
    (select * from public.v_day_max_original_calls where checkpoint<>'day_ahead' except all select * from public.prediction_checkpoints)
  ) d;
  if differences<>0 then raise exception 'Original checkpoint reader changed'; end if;
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
  select * into p from public.v_day_max_original_calls where checkpoint_id=p_checkpoint;
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

do $$ begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec(table_name,ts_column,fresh_hours,layer,plain_english)
    values('day_max_day_ahead_calls','frozen_at',null,'paper','Original pre-midnight Day Max ladders, received prospectively; accumulating record.')
    on conflict(table_name) do nothing;
  end if;
end $$;
do $migration$
declare old_def text; opts text[]; option_sql text; differences bigint;
begin
  if to_regclass('public.data_freshness_spec') is null then return; end if;
  insert into public.data_freshness_spec(table_name,ts_column,fresh_hours,layer,plain_english)
    values('day_max_day_ahead_calls','frozen_at',null,'paper','Original pre-midnight Day Max ladders, received prospectively; accumulating record.')
    on conflict(table_name) do nothing;
  if to_regclass('public.v_data_freshness') is null then return; end if;
  old_def:=regexp_replace(pg_get_viewdef('public.v_data_freshness'::regclass,true),';\s*$','');
  if position('day_max_day_ahead_calls' in old_def)>0 then return; end if;
  select reloptions into opts from pg_class where oid='public.v_data_freshness'::regclass;
  option_sql:=case when opts is null then '' else ' with ('||array_to_string(opts,',')||')' end;
  execute 'create or replace view public.v_data_freshness'||option_sql||' as select * from ('||old_def||') previous union all
    select ''day_max_day_ahead_calls''::text,''paper''::text,
      ''Original pre-midnight Day Max ladders, received prospectively; accumulating record.''::text,
      count(*)::bigint,false,max(frozen_at),round(extract(epoch from(now()-max(frozen_at)))/3600,1),null::numeric,
      case when count(*)=0 then ''empty'' else ''ok'' end from public.day_max_day_ahead_calls';
  execute 'select count(*) from ((('||old_def||') except all (select * from public.v_data_freshness where table_name<>''day_max_day_ahead_calls''))
    union all ((select * from public.v_data_freshness where table_name<>''day_max_day_ahead_calls'') except all ('||old_def||'))) d' into differences;
  if differences<>0 then raise exception 'Existing freshness rows changed'; end if;
end $migration$;
