-- Server receipts are new evidence. Never manufacture them for old calls.
alter table public.prediction_checkpoints
  add column if not exists frozen_at timestamptz,
  add column if not exists input_as_of timestamptz,
  add column if not exists input_key text,
  add column if not exists input_sha256 text,
  add column if not exists raw_probs jsonb,
  add column if not exists baseline_probs jsonb,
  add column if not exists baseline_model text,
  add column if not exists calibration jsonb,
  add column if not exists predictive_context jsonb;
create or replace function arbdesk_private.freeze_prediction_before_outcome()
returns trigger language plpgsql security invoker set search_path='' as $$
declare
  receipt timestamptz := clock_timestamp(); tz text; plan timestamp; peak numeric;
  parent_at timestamptz; dist jsonb; best text; packet jsonb; ids jsonb;
begin
  select c.timezone into tz from public.cities c where c.city_key=new.city_key;
  if tz is null or receipt >= ((new.target_date+1)::timestamp at time zone tz) then
    raise exception 'Freeze refused: unknown city clock or completed local day';
  end if;
  if exists (select 1 from public.v_venue_market_resolution r
       where r.city_key=new.city_key and r.resolution_date=new.target_date and r.resolution_state='confirmed') then
    raise exception 'Freeze refused: venue outcome already confirmed';
  end if;
  if new.decided_at > receipt+interval '5 seconds' or new.decided_at < receipt-interval '15 minutes' then
    raise exception 'Freeze refused: backdated or future decision';
  end if;
  new.frozen_at := receipt;
  if tg_table_name='prediction_checkpoints' then
    perform pg_advisory_xact_lock(hashtextextended(new.city_key||'|'||new.target_date||'|'||new.checkpoint,0));
    if exists(select 1 from public.prediction_checkpoints p where p.city_key=new.city_key
        and p.target_date=new.target_date and p.checkpoint=new.checkpoint) then return null; end if;
    plan := case new.checkpoint when 'd1_eve' then new.target_date-1+time '18:00'
              when 'morning' then new.target_date+time '09:00'
              when 'noon' then new.target_date+time '12:00' end;
    if plan is null then
      select p.peak_hour_local into peak from public.derived_weather_peak p
        where p.city_key=new.city_key and p.month=extract(month from new.target_date);
      if peak is null then raise exception 'Freeze refused: no measured monthly peak'; end if;
      plan := new.target_date::timestamp + round(peak*60)*interval '1 minute'
          + case new.checkpoint when 'prepeak_2h' then interval '-2 hours'
              when 'prepeak_1h' then interval '-1 hour' when 'postpeak_1h' then interval '1 hour' end;
    end if;
    if new.local_decision_time is distinct from plan
       or receipt < (plan at time zone tz) or receipt > (plan at time zone tz)+interval '75 minutes' then
      raise exception 'Freeze refused: outside scheduled city-local capture window';
    end if;
    if new.reading_at > receipt or new.forecast_issued_at > receipt then
      raise exception 'Freeze refused: future input timestamp';
    end if;
    foreach dist in array array[new.probs,new.raw_probs,new.baseline_probs] loop
      if dist is null then continue; end if;
      if jsonb_typeof(dist)<>'object' or abs(public.ad4_probs_sum(dist)-1)>1e-4
        or exists(select 1 from jsonb_each_text(dist) e where e.value is null or e.value::numeric<0 or e.value::numeric>1)
        or (select array_agg(key order by key) from jsonb_each(dist)) is distinct from
           (select array_agg(key order by key) from jsonb_each(new.probs)) then
        raise exception 'Freeze refused: invalid or mismatched probability ladder';
      end if;
    end loop;
    select key into best from jsonb_each_text(new.probs) order by value::numeric desc,key limit 1;
    if new.top_band_id is distinct from best or new.top_prob is distinct from (new.probs->>best)::numeric then
      raise exception 'Freeze refused: top pick does not match frozen probabilities';
    end if;
    -- Old writers get receipt/time guards during deployment; they cannot
    -- enter the new learner without the paired causal input evidence.
    if new.priced_from like 'wxrd3:%' and (new.input_key is not null or new.raw_probs is not null) then
      select c.payload into packet from public.live_capture c where c.kind='wx_features' and c.key=new.input_key
        and c.written_at<=receipt order by c.captured_at desc,c.capture_id desc limit 1;
      select jsonb_agg(key order by key) into ids from jsonb_each(new.probs);
      if packet is null or packet->>'policy' is distinct from 'wxrd3-horizons-v1'
        or packet->>'version' is distinct from new.priced_from
        or packet->>'model_fit_through' is distinct from new.predictive_context->>'model_fit_through'
        or packet->>'row_sha256' is distinct from new.input_sha256
        or (packet->>'as_of')::timestamptz is distinct from new.input_as_of
        or packet->'row'->>'city_key' is distinct from new.city_key
        or packet->'row'->>'date' is distinct from new.target_date::text
        or packet->>'model_fit_through' is null or (packet->>'model_fit_through')::date>=new.target_date
        or new.input_as_of is null or new.input_as_of>receipt or new.input_as_of<receipt-interval '15 minutes'
        or new.raw_probs is null or new.baseline_probs is null or new.predictive_context is null
        or packet->'band_ids' is distinct from new.predictive_context->'band_ids'
        or (select jsonb_agg(value order by value) from jsonb_array_elements(packet->'band_ids')) is distinct from ids then
        raise exception 'Freeze refused: exact prior input packet or paired baseline missing';
      end if;
    end if;
    new.decided_at := receipt;
  else
    select p.decided_at into parent_at from public.prediction_checkpoints p where p.city_key=new.city_key
      and p.target_date=new.target_date and p.checkpoint=new.checkpoint order by p.decided_at,p.checkpoint_id limit 1;
    if parent_at is null or receipt>parent_at+interval '30 minutes' or new.decided_at<parent_at then
      raise exception 'Freeze refused: shadow lacks a recent original frozen call';
    end if;
  end if;
  return new;
end $$;
revoke all on function arbdesk_private.freeze_prediction_before_outcome() from public,anon,authenticated;
grant execute on function arbdesk_private.freeze_prediction_before_outcome() to service_role;
do $$ declare t text; begin
  foreach t in array array['prediction_checkpoints','variant_shadow_checkpoints','s10_shadow_checkpoints'] loop
    if to_regclass('public.'||t) is null then continue; end if;
    execute format('alter table public.%I add column if not exists frozen_at timestamptz',t);
    execute format('alter table public.%I alter column decided_at set default clock_timestamp()',t);
    execute format('drop trigger if exists freeze_before_outcome on public.%I',t);
    execute format('create trigger freeze_before_outcome before insert on public.%I for each row execute function arbdesk_private.freeze_prediction_before_outcome()',t);
  end loop;
end $$;
create table if not exists public.derived_wx_city_calibration (
  model_version text not null, city_key text not null, phase text not null,
  as_of date not null, version text not null, state jsonb not null,
  computed_at timestamptz not null default clock_timestamp(), primary key(model_version,city_key,phase)
);
create or replace function arbdesk_private.stamp_city_calibration() returns trigger
language plpgsql set search_path='' as $$ begin new.computed_at:=clock_timestamp(); return new; end $$;
revoke all on function arbdesk_private.stamp_city_calibration() from public,anon,authenticated;
drop trigger if exists stamp_city_calibration on public.derived_wx_city_calibration;
create trigger stamp_city_calibration before insert or update on public.derived_wx_city_calibration
for each row execute function arbdesk_private.stamp_city_calibration();
alter table public.derived_wx_city_calibration enable row level security;
revoke all on public.derived_wx_city_calibration from public,anon,authenticated,service_role;
grant select,insert,update on public.derived_wx_city_calibration to service_role;
create table if not exists public.derived_city_prediction_validation (
  city_key text not null, checkpoint text not null, scope text not null,
  as_of timestamptz not null, report jsonb not null, primary key(city_key,checkpoint,scope)
);
alter table public.derived_city_prediction_validation enable row level security;
revoke all on public.derived_city_prediction_validation from public,anon,authenticated,service_role;
grant select on public.derived_city_prediction_validation to anon,authenticated;
grant select,insert,update on public.derived_city_prediction_validation to service_role;
drop policy if exists validation_read on public.derived_city_prediction_validation;
create policy validation_read on public.derived_city_prediction_validation for select to anon,authenticated using(true);
create or replace view public.v_city_prediction_validation with(security_invoker=true) as
  select city_key,checkpoint,scope,as_of,report from public.derived_city_prediction_validation;
revoke all on public.v_city_prediction_validation from public,anon,authenticated;
grant select on public.v_city_prediction_validation to anon,authenticated,service_role;
create or replace view public.v_wx_frozen_learning with(security_invoker=true) as
select p.city_key,p.target_date,p.checkpoint,p.decided_at,p.frozen_at,p.input_as_of,p.input_sha256,
       p.priced_from as model_version,p.raw_probs,p.probs,p.baseline_probs,p.baseline_model,
       p.calibration,p.predictive_context,c.timezone,c.unit,
       f.winner_band_id as winning_band_id,f.settled_at,f.banked_at as label_received_at
from public.prediction_checkpoints p join public.fact_checkpoint_outcome f using(checkpoint_id)
join public.cities c on c.city_key=p.city_key
where p.priced_from like 'wxrd3:%' and p.frozen_at is not null and p.raw_probs is not null
  and p.input_as_of<=p.frozen_at and p.frozen_at<((p.target_date+1)::timestamp at time zone c.timezone)
  and f.settled_at>p.frozen_at and clock_timestamp()>=((p.target_date+1)::timestamp at time zone c.timezone)
  and not exists(select 1 from public.prediction_checkpoints q where q.city_key=p.city_key
     and q.target_date=p.target_date and q.checkpoint=p.checkpoint
     and (q.decided_at,q.checkpoint_id)<(p.decided_at,p.checkpoint_id));
revoke all on public.v_wx_frozen_learning from public,anon,authenticated;
grant select on public.v_wx_frozen_learning to service_role;
do $$ begin
  if to_regclass('public.v_prediction_hindsight_live') is not null then
    execute $v$create or replace view public.v_day_ahead_frozen_validation with(security_invoker=true) as
      select city_key,for_date,called_at,hit,predicted_pct,prob_on_winner,engine_version,forecast_error_c
      from public.v_prediction_hindsight_live where called_when='day_ahead'$v$;
    revoke all on public.v_day_ahead_frozen_validation from public,anon,authenticated;
    grant select on public.v_day_ahead_frozen_validation to service_role;
  end if;
end $$;
-- Add health rows while preserving every existing freshness row and option.
do $migration$
declare old_def text; opts text[]; option_sql text; additions text; peak_diff bigint;
begin
  if to_regclass('public.data_freshness_spec') is null then return; end if;
  insert into public.data_freshness_spec(table_name,ts_column,fresh_hours,layer,plain_english) values
    ('derived_wx_city_calibration','computed_at',36,'model','Each city and peak phase calibration, held until unseen dates validate it.'),
    ('derived_city_prediction_validation','as_of',36,'model','Original frozen calls scored by city and horizon after venue confirmation.')
    on conflict(table_name) do update set ts_column=excluded.ts_column,fresh_hours=excluded.fresh_hours,layer=excluded.layer,plain_english=excluded.plain_english;
  if to_regclass('public.v_data_freshness') is null then return; end if;
  old_def := regexp_replace(pg_get_viewdef('public.v_data_freshness'::regclass,true),';\s*$','');
  if position('derived_city_prediction_validation' in old_def)>0 then return; end if;
  select reloptions into opts from pg_class where oid='public.v_data_freshness'::regclass;
  option_sql:=case when opts is null then '' else ' with ('||array_to_string(opts,',')||')' end;
  additions:=$q$
    select 'derived_wx_city_calibration'::text,'model'::text,
      'Each city and peak phase calibration, held until unseen dates validate it.'::text,
      count(*)::bigint,false,max(computed_at),round(extract(epoch from (now()-max(computed_at)))/3600,1),36::numeric,
      case when count(*)=0 then 'empty' when max(computed_at)<now()-interval '36 hours' then 'stale' else 'ok' end
    from public.derived_wx_city_calibration
    union all
    select 'derived_city_prediction_validation','model',
      'Original frozen calls scored by city and horizon after venue confirmation.',
      count(*)::bigint,false,max(as_of),round(extract(epoch from (now()-max(as_of)))/3600,1),36::numeric,
      case when count(*)=0 then 'empty' when max(as_of)<now()-interval '36 hours' then 'stale' else 'ok' end
    from public.derived_city_prediction_validation$q$;
  execute 'create or replace view public.v_data_freshness'||option_sql||' as select * from ('||old_def||') old_rows union all '||additions;
  if exists (select 1 from pg_class where oid='public.v_data_freshness'::regclass) then
    execute 'select count(*) from ((('||old_def||') except all (select * from public.v_data_freshness where table_name not in (''derived_wx_city_calibration'',''derived_city_prediction_validation''))) union all ((select * from public.v_data_freshness where table_name not in (''derived_wx_city_calibration'',''derived_city_prediction_validation'')) except all ('||old_def||'))) differences' into strict peak_diff;
    if peak_diff<>0 then raise exception 'Existing freshness rows changed: %',peak_diff; end if;
  end if;
end $migration$;
