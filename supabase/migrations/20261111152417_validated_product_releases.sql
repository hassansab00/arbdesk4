-- Immutable private release receipts; no observation, forecast or settlement writes.
create schema if not exists arbdesk_private;
create table if not exists arbdesk_private.predictive_product_releases (
 receipt_id bigint generated always as identity primary key,
 city_key text not null, checkpoint text not null,
 release_id text not null, kind text not null check(kind in ('early_ensemble','current_forecast')),
 decision text not null check(decision in ('activate','revoke')),
 recorded_at timestamptz not null default clock_timestamp(),
 expires_at timestamptz not null,
 release jsonb not null,
 unique(city_key,checkpoint,release_id,decision)
);
create index if not exists predictive_product_cell_receipt on arbdesk_private.predictive_product_releases(city_key,checkpoint,receipt_id desc);
alter table arbdesk_private.predictive_product_releases enable row level security;
revoke all on arbdesk_private.predictive_product_releases from public,anon,authenticated,service_role;

create table if not exists arbdesk_private.predictive_product_trials (
 receipt_id bigint generated always as identity primary key,
 city_key text not null, checkpoint text not null, identity text not null,
 trial_id text not null, snapshot_sha256 text not null,
 recorded_at timestamptz not null default clock_timestamp(), trial jsonb not null,
 unique(city_key,checkpoint,identity,trial_id,snapshot_sha256)
);
alter table arbdesk_private.predictive_product_trials enable row level security;
revoke all on arbdesk_private.predictive_product_trials from public,anon,authenticated,service_role;

create or replace function public.record_predictive_product_trial(p_city text,p_checkpoint text,p_identity text,p_trial jsonb,p_snapshot_sha256 text)
returns bigint language plpgsql security definer set search_path='' set statement_timeout='3s' as $$
declare r bigint; existing jsonb;
begin
 if coalesce(p_trial->>'id','') !~ '^[0-9a-f]{64}$' or coalesce(p_snapshot_sha256,'') !~ '^[0-9a-f]{64}$'
 or not exists(select 1 from public.derived_wx_city_calibration c
  where c.city_key=p_city and c.phase=p_checkpoint and c.model_version=p_identity
   and (c.state->'production_trial'=p_trial or c.state->'completed_production_trial'=p_trial))
 then raise exception 'trial differs from private fitted state'; end if;
 select receipt_id,trial into r,existing from arbdesk_private.predictive_product_trials
 where city_key=p_city and checkpoint=p_checkpoint and identity=p_identity
 and trial_id=p_trial->>'id' and snapshot_sha256=p_snapshot_sha256;
 if r is not null then
  if existing<>p_trial then raise exception 'immutable trial conflict'; end if;
  return r;
 end if;
 insert into arbdesk_private.predictive_product_trials(city_key,checkpoint,identity,trial_id,snapshot_sha256,trial)
 values(p_city,p_checkpoint,p_identity,p_trial->>'id',p_snapshot_sha256,p_trial) returning receipt_id into r;
 return r;
end $$;
revoke all on function public.record_predictive_product_trial(text,text,text,jsonb,text) from public,anon,authenticated;
grant execute on function public.record_predictive_product_trial(text,text,text,jsonb,text) to service_role;

create table if not exists arbdesk_private.current_forecast_artifacts (
 model_sha256 text primary key, compressed text not null, compressed_sha256 text not null,
 recorded_at timestamptz not null default clock_timestamp()
);
alter table arbdesk_private.current_forecast_artifacts enable row level security;
revoke all on arbdesk_private.current_forecast_artifacts from public,anon,authenticated,service_role;

create or replace function public.register_current_forecast_artifact(p_sha256 text,p_model text,p_compressed text)
returns text language plpgsql security definer set search_path='' set statement_timeout='3s' as $$
declare existing text; compressed_hash text;
begin
 if coalesce(p_sha256,'') !~ '^[0-9a-f]{64}$' or octet_length(p_model)>8388608
 or length(p_compressed)>699052 or octet_length(decode(p_compressed,'base64'))>524288
 or encode(sha256(convert_to(p_model,'UTF8')),'hex') is distinct from p_sha256
 then raise exception 'invalid or oversized private artifact'; end if;
 compressed_hash:=encode(sha256(decode(p_compressed,'base64')),'hex');
 select compressed into existing from arbdesk_private.current_forecast_artifacts where model_sha256=p_sha256;
 if found then
  if existing<>p_compressed then raise exception 'immutable private artifact conflict'; end if;
  return p_sha256;
 end if;
 insert into arbdesk_private.current_forecast_artifacts(model_sha256,compressed,compressed_sha256)
 values(p_sha256,p_compressed,compressed_hash);
 return p_sha256;
end $$;

create or replace function public.read_current_forecast_artifact(p_sha256 text)
returns jsonb language sql stable security definer set search_path='' set statement_timeout='3s' as $$
 select to_jsonb(a) from arbdesk_private.current_forecast_artifacts a where model_sha256=p_sha256
$$;
revoke all on function public.register_current_forecast_artifact(text,text,text),public.read_current_forecast_artifact(text) from public,anon,authenticated;
grant execute on function public.register_current_forecast_artifact(text,text,text),public.read_current_forecast_artifact(text) to service_role;

create or replace function public.publish_predictive_product(p_release jsonb)
returns bigint language plpgsql security definer set search_path='' set statement_timeout='3s' as $$
declare p jsonb:=p_release->'proof'; r bigint; existing jsonb; ref text; field text;
begin
 if p_release->>'policy' is distinct from 'validated-product-v1'
  or coalesce(p_release->>'kind','') not in ('early_ensemble','current_forecast')
  or coalesce(p_release->>'city_key','')='' or coalesce(p_release->>'station','')=''
  or coalesce(p_release->>'unit','') not in ('C','F')
  or coalesce(p_release->>'checkpoint','') not in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h')
  or coalesce(p_release->>'release_id','') !~ '^[0-9a-f]{64}$'
  or coalesce(p_release->>'payload_sha256','') !~ '^[0-9a-f]{64}$'
  or coalesce(p_release->>'proof_sha256','') !~ '^[0-9a-f]{64}$'
  or coalesce(p_release->>'code_sha256','') !~ '^[0-9a-f]{64}$'
  or jsonb_typeof(p_release->'payload') is distinct from 'object'
  or p->>'policy' is distinct from 'validated-product-v1'
  or p->>'original_frozen_calls' is distinct from 'true'
  or not coalesce((p->>'fit_last')::date<(p->>'validation_first')::date
    and (p->>'validation_first')::date<=(p->>'validation_last')::date
    and (p->>'validation_last')::date<(p->>'as_of')::date
    and (p->>'as_of')::date<=current_date
    and (p->>'as_of')::date>=current_date-2
    and (p->>'training_dates')::int>=20 and (p->>'dates')::int>=10
    and (p->>'dates')::int<=(p->>'calls')::int
    and (p->>'calls')::numeric between .9*(p->>'expected_calls')::numeric and (p->>'expected_calls')::numeric
    and (p->>'expected_calls')::numeric>0
    and (p->>'candidate_hits')::numeric/(p->>'calls')::numeric>=.6, false)
 then raise exception 'unqualified production release'; end if;
 foreach field in array array['training_dates','dates','calls','expected_calls','candidate_hits','incumbent_hits','market_hits','candidate_brier','incumbent_brier','market_brier','candidate_nll','incumbent_nll','market_nll','incumbent_gain_lower90','market_gain_lower90'] loop
  if jsonb_typeof(p->field) is distinct from 'number' then raise exception 'typed finite production metric required'; end if;
 end loop;
 foreach ref in array array['incumbent','market'] loop
  if not coalesce((p->>(ref||'_hits'))::int>=0
   and (p->>'candidate_hits')::int>(p->>(ref||'_hits'))::int
   and (p->>'candidate_hits')::int<=(p->>'calls')::int
   and (p->>'candidate_brier')::numeric>=0 and (p->>'candidate_brier')::numeric<(p->>(ref||'_brier'))::numeric
   and (p->>'candidate_nll')::numeric>=0 and (p->>'candidate_nll')::numeric<(p->>(ref||'_nll'))::numeric
   and (p->>(ref||'_gain_lower90'))::numeric>0,false)
  then raise exception 'production benchmark failed'; end if;
 end loop;
 if p_release->>'kind'='early_ensemble' and not exists(
  select 1 from public.derived_wx_city_calibration c
  where c.city_key=p_release->>'city_key' and c.phase=p_release->>'checkpoint'
   and c.model_version=p_release->>'identity'
   and (c.state->'production_trial'=p_release->'payload'->'production_trial'
     or c.state->'completed_production_trial'=p_release->'payload'->'production_trial')
   and p_release->'payload'->'production_trial'->>'decision'='approved'
   and p_release->'payload'->'production_trial'->'proof'=p
   and exists(select 1 from arbdesk_private.predictive_product_trials t
     where t.city_key=c.city_key and t.checkpoint=c.phase and t.identity=c.model_version
       and t.trial=p_release->'payload'->'production_trial')
 ) then raise exception 'pinned production trial differs from private fit'; end if;
 select receipt_id,release into r,existing from arbdesk_private.predictive_product_releases
 where city_key=p_release->>'city_key' and checkpoint=p_release->>'checkpoint'
 and release_id=p_release->>'release_id' and decision='activate';
 if r is not null then
  if existing<>p_release then raise exception 'immutable release conflict'; end if;
  return r;
 end if;
 insert into arbdesk_private.predictive_product_releases(city_key,checkpoint,release_id,kind,decision,expires_at,release)
 values(p_release->>'city_key',p_release->>'checkpoint',p_release->>'release_id',p_release->>'kind','activate',
  ((p->>'as_of')::date+14)::timestamptz,p_release) returning receipt_id into r;
 return r;
end $$;

create or replace function public.revoke_predictive_product(p_receipt_id bigint,p_reason text)
returns bigint language plpgsql security definer set search_path='' set statement_timeout='3s' as $$
declare v arbdesk_private.predictive_product_releases; r bigint;
begin
 if length(trim(coalesce(p_reason,'')))<8 then raise exception 'rollback reason required'; end if;
 select * into strict v from arbdesk_private.predictive_product_releases where receipt_id=p_receipt_id and decision='activate';
 insert into arbdesk_private.predictive_product_releases(city_key,checkpoint,release_id,kind,decision,expires_at,release)
 values(v.city_key,v.checkpoint,v.release_id,v.kind,'revoke',v.expires_at,
 jsonb_build_object('reason',p_reason,'revoked_receipt_id',v.receipt_id))
 on conflict(city_key,checkpoint,release_id,decision) do nothing returning receipt_id into r;
 return r;
end $$;

create or replace function public.predictive_product_inputs(p_cities text[])
returns setof jsonb language plpgsql stable security definer set search_path='' set statement_timeout='3s' as $$
begin
 if coalesce(cardinality(p_cities),0)>60 then raise exception 'bounded city roster required'; end if;
 return query select to_jsonb(r) from (
 select distinct on(city_key,checkpoint) * from arbdesk_private.predictive_product_releases
 where city_key=any(p_cities) order by city_key,checkpoint,receipt_id desc) r;
end $$;

create or replace function public.predictive_product_status()
returns setof jsonb language sql stable security definer set search_path='' set statement_timeout='3s' as $$
 select jsonb_build_object('city_key',city_key,'checkpoint',checkpoint,'kind',kind,
 'receipt_id',receipt_id,'release_id',release_id,'recorded_at',recorded_at,'expires_at',expires_at,
 'status',case when decision='revoke' then 'revoked' when expires_at<=now() then 'expired' else 'approved' end)
 from (select distinct on(city_key,checkpoint) * from arbdesk_private.predictive_product_releases
 order by city_key,checkpoint,receipt_id desc limit 360) r
$$;
revoke all on function public.publish_predictive_product(jsonb),public.revoke_predictive_product(bigint,text),
 public.predictive_product_inputs(text[]),public.predictive_product_status() from public,anon,authenticated;
grant execute on function public.publish_predictive_product(jsonb),public.revoke_predictive_product(bigint,text),
 public.predictive_product_inputs(text[]) to service_role;
grant execute on function public.predictive_product_status() to anon,authenticated,service_role;

notify pgrst, 'reload schema';

-- Preserve every old-writer guard while admitting receipt-bound releases.
CREATE OR REPLACE FUNCTION arbdesk_private.freeze_prediction_before_outcome()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  receipt timestamptz := clock_timestamp(); tz text; plan timestamp; peak numeric;
  parent_at timestamptz; dist jsonb; best text; packet jsonb; ids jsonb;
  product jsonb; release_row arbdesk_private.predictive_product_releases; incumbent jsonb; incumbent_label text;
begin
  select c.timezone into tz from public.cities c where c.city_key=new.city_key;
  if tz is null or receipt >= ((new.target_date+1)::timestamp at time zone tz) then
    raise exception 'Freeze refused: unknown city clock or completed local day';
  end if;
  if exists (select 1 from public.v_venue_market_resolution r
       where r.city_key=new.city_key and r.resolution_date=new.target_date and r.resolution_state='confirmed') then
    raise exception 'Freeze refused: venue outcome already confirmed';
  end if;
  if new.decided_at > receipt+interval '5 seconds' or (tg_table_name='prediction_checkpoints' and new.decided_at < receipt-interval '15 minutes') then
    raise exception 'Freeze refused: backdated or future decision';
  end if;
  new.frozen_at := receipt;
  if tg_table_name='prediction_checkpoints' then
    perform pg_advisory_xact_lock(hashtextextended(new.city_key||'|'||new.target_date||'|'||new.checkpoint,0));
    if exists(select 1 from public.prediction_checkpoints p where p.city_key=new.city_key
        and p.target_date=new.target_date and p.checkpoint=new.checkpoint) then return null; end if;
    plan := case new.checkpoint when 'day_ahead' then new.target_date-1+time '09:00'
              when 'd1_eve' then new.target_date-1+time '18:00'
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
    if new.input_key is not null or new.raw_probs is not null then
      select c.payload into packet from public.live_capture c where c.kind='wx_features' and c.key=new.input_key
        and c.written_at<=receipt order by c.captured_at desc,c.capture_id desc limit 1;
      select jsonb_agg(key order by key) into ids from jsonb_each(new.probs);
      if packet is null or packet->>'policy' is distinct from 'wxrd3-horizons-v1'
        or packet->>'version' is distinct from coalesce(new.predictive_context->>'raw_model_version',new.priced_from)
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

    -- A product call retains its primary selector/input evidence. Validate
    -- the private release first, then compare that retained selector with the
    -- PRE-PRODUCT incumbent, rather than relabelling the served prediction.
    product:=new.predictive_context->'product_release';
    incumbent:=new.probs; incumbent_label:=new.priced_from;
    if product is not null then
      select * into release_row from arbdesk_private.predictive_product_releases r
        where r.city_key=new.city_key and r.checkpoint=new.checkpoint
        order by r.receipt_id desc limit 1;
      if product->>'policy' is distinct from 'validated-product-v1'
        or product->>'city_key' is distinct from new.city_key
        or product->>'target_date' is distinct from new.target_date::text
        or product->>'checkpoint' is distinct from new.checkpoint
        or release_row.receipt_id is null or release_row.decision<>'activate'
        or product->>'receipt_id' is distinct from release_row.receipt_id::text
        or product->>'release_id' is distinct from release_row.release_id
        or product->>'kind' is distinct from release_row.kind
        or (product->>'recorded_at')::timestamptz is distinct from release_row.recorded_at
        or release_row.recorded_at>receipt or release_row.expires_at<=receipt
        or release_row.recorded_at>new.decided_at
        or product->'served_probs' is distinct from new.probs
        or product->>'payload_sha256' is distinct from release_row.release->>'payload_sha256'
        or product->>'proof_sha256' is distinct from release_row.release->>'proof_sha256'
        or release_row.release->>'station' is distinct from new.station
        or new.priced_from is distinct from 'predictive-product:'||release_row.kind||':'||release_row.release_id
        or (release_row.release->'proof'->>'validation_last')::date>=new.target_date then
        raise exception 'Freeze refused: invalid, stale or mismatched product release receipt';
      end if;
      if release_row.kind='early_ensemble' and new.predictive_context->'ensemble_early'->'production_reference_probs' is distinct from new.probs then
        raise exception 'Freeze refused: next-trial benchmark differs from served incumbent';
      end if;
      incumbent:=product->'incumbent_probs';
      incumbent_label:=product->>'incumbent_priced_from';
      if jsonb_typeof(incumbent) is distinct from 'object' or incumbent_label is null
        or abs(public.ad4_probs_sum(incumbent)-1)>1e-4
        or exists(select 1 from jsonb_each_text(incumbent) e where e.value is null or e.value::numeric<0 or e.value::numeric>1)
        or (select array_agg(key order by key) from jsonb_each(incumbent)) is distinct from
           (select array_agg(key order by key) from jsonb_each(new.probs)) then
        raise exception 'Freeze refused: invalid original product incumbent';
      end if;
      if release_row.kind='current_forecast' then
        if packet is null or new.predictive_context->>'raw_model_version' is null
          or packet->'row'->>'station' is distinct from release_row.release->>'station'
          or packet->'row'->>'unit' is distinct from release_row.release->>'unit'
          or packet->>'current_forecasts_sha256' is null or packet->>'current_receipts_sha256' is null
          or packet->>'current_forecasts_sha256' is distinct from new.predictive_context->>'current_forecasts_sha256'
          or packet->>'current_receipts_sha256' is distinct from new.predictive_context->>'current_receipts_sha256'
          or release_row.release->>'identity' is distinct from new.city_key||'|'||new.station||'|'||
             coalesce(nullif(packet->'row'->>'observation_contract',''),'legacy')||'|'||(new.predictive_context->>'raw_model_version') then
          raise exception 'Freeze refused: product current-model input identity mismatch';
        end if;
      elsif new.checkpoint not in ('day_ahead','d1_eve','morning')
        or new.predictive_context->'product_ensemble_serving'->>'policy' is distinct from 'early-seven-source-v1'
        or new.predictive_context->'product_ensemble_serving'->>'model_version' is distinct from release_row.release->>'identity'
        or new.predictive_context->'product_ensemble_serving'->>'production_trial_id' is distinct from release_row.release->'payload'->'production_trial'->>'id'
        or new.predictive_context->'product_ensemble_serving'->>'production_weights_sha256' is null
        or new.predictive_context->'product_ensemble_serving'->>'production_weights_sha256' is distinct from release_row.release->'payload'->'production_trial'->>'weights_sha256'
        or new.predictive_context->'product_ensemble_serving'->'production_candidate_probs' is distinct from new.probs
        or new.predictive_context->'product_ensemble_serving'->'incumbent_probs' is distinct from incumbent
        or new.predictive_context->'product_ensemble_serving'->>'station' is distinct from new.station
        or new.predictive_context->'product_ensemble_serving'->>'unit' is distinct from release_row.release->>'unit'
        or new.predictive_context->'product_ensemble_serving'->>'captured_at' is null
        or new.predictive_context->'product_ensemble_serving'->>'rules_received_at' is null
        or length(coalesce(new.predictive_context->'product_ensemble_serving'->>'rules_sha256',''))<>64
        or (new.predictive_context->'product_ensemble_serving'->>'captured_at')::timestamptz>receipt
        or new.predictive_context->'product_ensemble_serving'->>'input_oldest' is null
        or new.predictive_context->'product_ensemble_serving'->>'input_newest' is null
        or not ((new.predictive_context->'product_ensemble_serving'->>'input_oldest')::timestamptz
          <=(new.predictive_context->'product_ensemble_serving'->>'input_newest')::timestamptz
          and (new.predictive_context->'product_ensemble_serving'->>'input_newest')::timestamptz
          <=(new.predictive_context->'product_ensemble_serving'->>'captured_at')::timestamptz
          and (new.predictive_context->'product_ensemble_serving'->>'captured_at')::timestamptz
          -(new.predictive_context->'product_ensemble_serving'->>'input_oldest')::timestamptz<=interval '8 hours')
        or (new.predictive_context->'product_ensemble_serving'->>'rules_received_at')::timestamptz>
             (new.predictive_context->'product_ensemble_serving'->>'captured_at')::timestamptz then
        raise exception 'Freeze refused: product seven-source input identity mismatch';
      end if;
    elsif coalesce(new.priced_from,'') like 'predictive-product:%' then
      raise exception 'Freeze refused: product release evidence missing';
    end if;

    -- Validate a selector receipt without relabelling the chosen baseline.
    if new.predictive_context ? 'raw_model_version' then
      if new.predictive_context->'model_selection'->>'policy' is distinct from 'city-horizon-selector-v1'
         or new.predictive_context->'model_selection'->>'checkpoint' is distinct from new.checkpoint
         or coalesce(new.predictive_context->'model_selection'->>'selected','') not in ('wxrd3','baseline')
         or (coalesce((new.predictive_context->'model_selection'->>'active')::boolean,false) and (
             new.predictive_context->'model_selection'->>'fit_at' is null
             or new.predictive_context->'model_selection'->>'validated_through' is null
             or (new.predictive_context->'model_selection'->>'fit_at')::timestamptz > new.input_as_of
             or (new.predictive_context->'model_selection'->>'validated_through')::date >= new.target_date)) then
        raise exception 'Freeze refused: invalid city-horizon model selection receipt';
      end if;
      if new.predictive_context->'model_selection'->>'selected'='baseline' then
        if incumbent is distinct from new.baseline_probs
           or incumbent_label is distinct from regexp_replace(new.baseline_model,'^priced_from:','')
           or (new.checkpoint in ('noon','prepeak_2h','prepeak_1h','postpeak_1h')
               and coalesce((new.predictive_context->'model_selection'->>'active')::boolean,false) is not true) then
          raise exception 'Freeze refused: baseline selection does not match its frozen predictor';
        end if;
      elsif incumbent_label is distinct from new.predictive_context->>'raw_model_version' then
        raise exception 'Freeze refused: raw selection does not match its frozen predictor';
      end if;
    end if;
    new.decided_at := receipt;
  else
    select p.decided_at into parent_at from public.prediction_checkpoints p where p.city_key=new.city_key
      and p.target_date=new.target_date and p.checkpoint=new.checkpoint order by p.decided_at,p.checkpoint_id limit 1;
    if parent_at is null or new.decided_at<parent_at or (receipt>parent_at+interval '30 minutes' and not (new.decided_at=parent_at and receipt<=parent_at+interval '6 hours')) then
      raise exception 'Freeze refused: shadow lacks a recent original frozen call';
    end if;
  end if;
  return new;
end $function$
;


revoke all on function arbdesk_private.freeze_prediction_before_outcome() from public,anon,authenticated;
grant execute on function arbdesk_private.freeze_prediction_before_outcome() to service_role;
