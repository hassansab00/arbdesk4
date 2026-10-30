-- Fixed-stake prospective paper experiment. City choices are private runtime
-- configuration; installation never enables trading or changes another desk.
insert into public.strategies(strategy_id,name,side,enabled,extra)
values('s14_day_max_prediction','Day Max Prediction','YES',false,
  '{"runner":"day_max_prediction","day_max":{"policy":"day-max-frozen-fixed-stake-v1","cities":[]}}')
on conflict(strategy_id) do nothing;

-- Deliberately no FK into archiveable hot tables. The original receipt stays
-- readable after their normal verified offload; nothing here prunes data.
create table if not exists public.day_max_prediction_entries(
  city_key text not null,
  target_date date not null,
  checkpoint_id uuid not null unique,
  account_id uuid not null,
  decision_id bigint not null unique,
  plan_id uuid unique,
  stake_ceiling numeric not null check(stake_ceiling > 0 and stake_ceiling <= 10),
  snapshot jsonb not null,
  created_at timestamptz not null default clock_timestamp(),
  primary key(city_key,target_date)
);
alter table public.day_max_prediction_entries enable row level security;
revoke all on public.day_max_prediction_entries from public,anon,authenticated,service_role;
grant select,insert,update on public.day_max_prediction_entries to service_role;

create or replace function arbdesk_private.day_max_immutable()
returns trigger language plpgsql set search_path='' as $$
begin
  if tg_op='UPDATE' and old.plan_id is null and new.plan_id is not null
     and (to_jsonb(old)-'plan_id')=(to_jsonb(new)-'plan_id') then return new; end if;
  raise exception 'Day Max receipt is immutable';
end $$;
revoke all on function arbdesk_private.day_max_immutable() from public,anon,authenticated;
drop trigger if exists day_max_immutable on public.day_max_prediction_entries;
create trigger day_max_immutable before update or delete on public.day_max_prediction_entries
for each row execute function arbdesk_private.day_max_immutable();
drop trigger if exists day_max_no_truncate on public.day_max_prediction_entries;
create trigger day_max_no_truncate before truncate on public.day_max_prediction_entries
for each statement execute function arbdesk_private.day_max_immutable();

create or replace function arbdesk_private.day_max_order_guard()
returns trigger language plpgsql security definer set search_path='' as $$
declare a public.paper_accounts; e public.day_max_prediction_entries;
begin
  if tg_op='UPDATE' and (old.strategy_id='s14_day_max_prediction' or new.strategy_id='s14_day_max_prediction')
     and (to_jsonb(old)-array['status','reason','lease_token','lease_until','result'])
       is distinct from (to_jsonb(new)-array['status','reason','lease_token','lease_until','result']) then
    raise exception 'Day Max order identity and size are fixed';
  end if;
  if new.strategy_id is distinct from 's14_day_max_prediction' or new.action='SELL' then return new; end if;
  select * into a from public.paper_accounts where account_id=new.account_id for update;
  if a.kind is distinct from 'shadow' or a.strategy_id is distinct from new.strategy_id then
    raise exception 'Day Max uses its own shadow ledger only'; end if;
  select * into e from public.day_max_prediction_entries
   where checkpoint_id=(new.context->>'checkpoint_id')::uuid and account_id=new.account_id
     and decision_id=(new.context->>'decision_id')::bigint;
  if not found or new.side is distinct from 'YES' or new.band_id::text is distinct from e.snapshot->>'top_band_id'
     or new.cash_ceiling is null or new.cash_ceiling<=0 or new.cash_ceiling>e.stake_ceiling then
    raise exception 'Day Max source bucket or ten-dollar ceiling invalid'; end if;
  if exists(select 1 from public.paper_orders o where o.strategy_id=new.strategy_id and o.action='BUY'
      and o.context->>'checkpoint_id'=e.checkpoint_id::text and o.order_id<>new.order_id) then
    raise exception 'Day Max already ordered this city date'; end if;
  return new;
end $$;
revoke all on function arbdesk_private.day_max_order_guard() from public,anon,authenticated;
drop trigger if exists day_max_order_guard on public.paper_orders;
create trigger day_max_order_guard before insert or update on public.paper_orders
for each row execute function arbdesk_private.day_max_order_guard();

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
  select * into p from public.prediction_checkpoints where checkpoint_id=p_checkpoint;
  if not found then raise exception 'Original frozen forecast unavailable'; end if;
  select * into e from public.day_max_prediction_entries where city_key=p.city_key and target_date=p.target_date;
  if found then return jsonb_build_object('allocated',false,'plan_id',e.plan_id,'reason','already_allocated'); end if;
  select x into choice from jsonb_array_elements(cfg->'cities') x where x->>'city_key'=p.city_key;
  if choice is null or choice->>'checkpoint' is distinct from p.checkpoint
     or (choice->>'stake_usd')::numeric is distinct from 10::numeric
     or p.checkpoint not in ('d1_eve','morning','noon','prepeak_2h','prepeak_1h') then
    raise exception 'City or checkpoint outside Day Max scope'; end if;
  if cfg->>'activated_at' is null or p.inputs_ok is distinct from true or p.frozen_at is null
     or p.frozen_at<(cfg->>'activated_at')::timestamptz or p.frozen_at>clock_timestamp()
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
    jsonb_build_object('policy',cfg->>'policy','selection',choice)::text,p.checkpoint_id,'prediction_checkpoints')
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
    values('day_max_prediction_entries','created_at',null,'paper',
      'Original frozen Day Max daily allocations; accumulating record, not a scheduled feed.')
    on conflict(table_name) do nothing;
  end if;
end $$;
