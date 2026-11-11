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
