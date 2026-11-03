-- Isolated shared-weather forward experiment. New objects only: no existing
-- prediction schema, constraint, trigger, permission or trading setting changes.
-- One fixed twenty-date validation window; calls and scores join the nightly
-- repository mirror. Fitted weights stay private and are backed up separately.
create schema if not exists arbdesk_private;
create table if not exists arbdesk_private.shared_weather_models (
 model_sha256 text primary key check(model_sha256 ~ '^[0-9a-f]{64}$'),artifact jsonb not null,
 registered_at timestamptz not null default clock_timestamp(),evaluation_first date not null,
 evaluation_last date not null,cohort text[] not null,
 check(evaluation_last=evaluation_first+19),check(cardinality(cohort) between 20 and 64)
);
create table if not exists public.shared_weather_calls (
 call_id uuid primary key default gen_random_uuid(),model_sha256 text not null,city_key text not null,
 target_date date not null,checkpoint text not null check(checkpoint in ('d1_eve','morning')),
 source_checkpoint_id uuid not null,decided_at timestamptz not null,
 frozen_at timestamptz not null default clock_timestamp(),probs jsonb not null,incumbent jsonb not null,
 market jsonb,top_band_id text not null,incumbent_top text not null,context jsonb not null,
 unique(model_sha256,city_key,target_date,checkpoint),
 check(decided_at<=frozen_at and frozen_at<=decided_at+interval '5 minutes')
);
create index if not exists shared_weather_calls_receipt on public.shared_weather_calls(frozen_at,call_id);
create table if not exists public.shared_weather_scores (
 call_id uuid primary key references public.shared_weather_calls(call_id),winner_band_id text not null,
 settled_at timestamptz not null,scored_at timestamptz not null default clock_timestamp(),
 candidate jsonb not null,incumbent jsonb not null,market jsonb
);
create index if not exists shared_weather_scores_receipt on public.shared_weather_scores(scored_at,call_id);
create table if not exists arbdesk_private.shared_weather_status (
 singleton boolean primary key default true check(singleton),updated_at timestamptz not null default clock_timestamp(),detail jsonb not null
);
alter table arbdesk_private.shared_weather_models enable row level security;
alter table arbdesk_private.shared_weather_status enable row level security;
alter table public.shared_weather_calls enable row level security;
alter table public.shared_weather_scores enable row level security;
revoke all on arbdesk_private.shared_weather_models,arbdesk_private.shared_weather_status,
 public.shared_weather_calls,public.shared_weather_scores from public,anon,authenticated,service_role;
grant select on public.shared_weather_calls,public.shared_weather_scores to service_role;

create or replace function arbdesk_private.shared_weather_immutable()
returns trigger language plpgsql set search_path='' as $$
begin raise exception 'shared weather original evidence is immutable';end;
$$;
revoke all on function arbdesk_private.shared_weather_immutable() from public,anon,authenticated,service_role;
do $$ begin
 if not exists(select 1 from pg_trigger where tgrelid='public.shared_weather_calls'::regclass and tgname='shared_weather_original') then
  create trigger shared_weather_original before update or delete on public.shared_weather_calls for each row execute function arbdesk_private.shared_weather_immutable();end if;
 if not exists(select 1 from pg_trigger where tgrelid='public.shared_weather_scores'::regclass and tgname='shared_weather_original') then
  create trigger shared_weather_original before update or delete on public.shared_weather_scores for each row execute function arbdesk_private.shared_weather_immutable();end if;
 if not exists(select 1 from pg_trigger where tgrelid='arbdesk_private.shared_weather_models'::regclass and tgname='shared_weather_original') then
  create trigger shared_weather_original before update or delete on arbdesk_private.shared_weather_models for each row execute function arbdesk_private.shared_weather_immutable();end if;
end $$;

create or replace function public.shared_weather_register(p_artifact jsonb,p_cohort text[])
returns jsonb language plpgsql security definer set search_path='' as $$
declare m arbdesk_private.shared_weather_models;v_now timestamptz:=clock_timestamp();
begin
 if (p_artifact->>'policy' is distinct from 'shared-early-weather-v1'
 or p_artifact->'changes_serving' is distinct from 'false'::jsonb
 or p_artifact->'eligible_for_promotion' is distinct from 'false'::jsonb
 or not ((p_artifact->>'fit_started_at')::timestamptz<=(p_artifact->>'available_at')::timestamptz and (p_artifact->>'available_at')::timestamptz<=v_now)
 or not ((p_artifact#>>'{training,evidence_cutoff}')::timestamptz<=(p_artifact->>'fit_started_at')::timestamptz)
 or (p_artifact#>>'{training,dates}')::int<7 or (p_artifact#>>'{training,rows}')::int<200
 or (p_artifact#>>'{training,through}')::date>=(v_now at time zone 'UTC')::date
 or cardinality(p_cohort) is null or cardinality(p_cohort) not between 20 and 64
 or (select count(distinct x) from unnest(p_cohort) x)<>cardinality(p_cohort)
 or exists(select 1 from unnest(p_cohort) x where not exists(select 1 from public.cities c where c.city_key=x))) is not false then
  raise exception 'invalid shared weather registration';end if;
 insert into arbdesk_private.shared_weather_models(model_sha256,artifact,evaluation_first,evaluation_last,cohort)
 values(p_artifact->>'sha256',p_artifact,(v_now at time zone 'UTC')::date+2,(v_now at time zone 'UTC')::date+21,p_cohort) on conflict do nothing;
 select * into strict m from arbdesk_private.shared_weather_models where model_sha256=p_artifact->>'sha256';
 if m.artifact<>p_artifact or m.cohort<>p_cohort then raise exception 'model identity conflict';end if;
 return jsonb_build_object('model_sha256',m.model_sha256,'registered_at',m.registered_at,
 'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'cohort',m.cohort);
end $$;

create or replace function public.shared_weather_inputs()
returns jsonb language plpgsql security definer set search_path='' as $$
declare m arbdesk_private.shared_weather_models;result jsonb;
begin
 select * into m from arbdesk_private.shared_weather_models order by registered_at desc limit 1;
 if not found then return jsonb_build_object('state','not_registered','calls','[]'::jsonb);end if;
 with selected as materialized (
 select p as original,jsonb_build_object('timezone',c.timezone,'unit',c.unit,'display_name',c.display_name) city,to_jsonb(w) weather
 from public.prediction_checkpoints p join public.cities c using(city_key)
 left join lateral (select l.* from public.live_capture l where l.kind='hourly0' and l.key=p.city_key||'|snapshot'
 and l.captured_at<=p.decided_at and l.written_at<=p.decided_at
 order by greatest(l.captured_at,l.written_at) desc,l.captured_at desc,l.capture_id desc limit 1) w on true
 where p.decided_at>=clock_timestamp()-interval '5 minutes' and p.decided_at>=m.registered_at
 and p.checkpoint in ('d1_eve','morning') and p.city_key=any(m.cohort)
 and p.target_date>(m.artifact#>>'{training,through}')::date and p.target_date<=m.evaluation_last
 and not exists(select 1 from public.prediction_checkpoints e where e.city_key=p.city_key and e.target_date=p.target_date
 and e.checkpoint=p.checkpoint and (e.decided_at,e.checkpoint_id)<(p.decided_at,p.checkpoint_id))
 and not exists(select 1 from public.shared_weather_calls h where h.model_sha256=m.model_sha256 and h.city_key=p.city_key
 and h.target_date=p.target_date and h.checkpoint=p.checkpoint)
 ),definitions as materialized (
 select b.band_id::text band_id,b.band_lo,b.band_hi,b.band_label from public.v_canonical_bands b
 join (select distinct k.band_id from selected p cross join lateral jsonb_object_keys((p.original).probs) k(band_id)) needed
 on b.band_id::text=needed.band_id
 )
 select coalesce(jsonb_agg(jsonb_build_object('call',to_jsonb(p.original),'city',p.city,'weather',p.weather,
 'bands',(select jsonb_agg(jsonb_build_object('band_id',b.band_id,'lo',b.band_lo,'hi',b.band_hi,'label',b.band_label))
 from definitions b where (p.original).probs ? b.band_id))),'[]'::jsonb) into result from selected p;
 return jsonb_build_object('state','registered','artifact',m.artifact,'registered_at',m.registered_at,
 'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'cohort',m.cohort,'calls',result);
end $$;

-- Matched market probabilities come from the same original saved quotes,
-- never from a caller-supplied distribution or a later book.
create or replace function arbdesk_private.shared_weather_market(quotes jsonb,ladder jsonb)
returns jsonb language plpgsql immutable set search_path='' as $$
declare bid text;b numeric;a numeric;price numeric;total numeric:=0;out jsonb:='{}'::jsonb;
begin
 for bid in select key from jsonb_each(ladder) loop
  b:=(quotes->bid->>'bid')::numeric;a:=(quotes->bid->>'ask')::numeric;
  if (b is null and a is null) or (b is not null and b not between 0 and 1)
   or (a is not null and a not between 0 and 1) or b>a then return null;end if;
  price:=greatest((coalesce(b,0)+coalesce(a,1))/2,0.000001);
  out:=out||jsonb_build_object(bid,price);total:=total+price;
 end loop;
 if total=0 then return null;end if;
 select jsonb_object_agg(key,(value::text)::numeric/total) into out from jsonb_each(out);return out;
end $$;
revoke all on function arbdesk_private.shared_weather_market(jsonb,jsonb) from public,anon,authenticated,service_role;

create or replace function public.shared_weather_freeze(p_model text,p_rows jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare m arbdesk_private.shared_weather_models;r jsonb;p public.prediction_checkpoints;w public.live_capture;c public.cities;
 at timestamptz;v_sum numeric;n int:=0;top_id text;v_start timestamptz;v_end timestamptz;
 expected_hours int;complete_hours int;forecast_peak timestamptz;reference_peak timestamptz;
begin
 select * into strict m from arbdesk_private.shared_weather_models where model_sha256=p_model;
 if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows)>64 then raise exception 'bounded shared weather batch required';end if;
 for r in select value from jsonb_array_elements(p_rows) loop
  select * into strict p from public.prediction_checkpoints where checkpoint_id=(r->>'source_checkpoint_id')::uuid;
  select * into strict c from public.cities where city_key=p.city_key;
  select * into strict w from public.live_capture where capture_id=(r->>'weather_capture_id')::bigint;
  at:=clock_timestamp();
  if (p.checkpoint not in ('d1_eve','morning') or p.inputs_ok is not true or not p.city_key=any(m.cohort)
  or p.target_date>m.evaluation_last or p.target_date<=(m.artifact#>>'{training,through}')::date
  or not (m.registered_at<=p.decided_at and p.decided_at<=p.frozen_at and p.frozen_at<=at and at<=p.decided_at+interval '5 minutes')
  or p.target_date<(at at time zone c.timezone)::date
  or (p.checkpoint='d1_eve' and p.target_date<>(at at time zone c.timezone)::date+1)
  or (p.checkpoint='morning' and p.target_date<>(at at time zone c.timezone)::date)
  or w.kind<>'hourly0' or w.key<>p.city_key||'|snapshot'
  or greatest(w.captured_at,w.written_at)>p.decided_at or greatest(w.captured_at,w.written_at)<p.decided_at-interval '36 hours'
  or jsonb_typeof(r->'probs') is distinct from 'object' or jsonb_typeof(r->'context') is distinct from 'object'
  or exists(select 1 from public.fact_checkpoint_outcome f where f.checkpoint_id=p.checkpoint_id)
  or exists(select 1 from public.prediction_checkpoints e where e.city_key=p.city_key and e.target_date=p.target_date
  and e.checkpoint=p.checkpoint and (e.decided_at,e.checkpoint_id)<(p.decided_at,p.checkpoint_id))) is not false then
   raise exception 'shared weather freeze lacks original forward evidence';end if;
  v_start:=p.target_date::timestamp at time zone c.timezone;
  v_end:=(p.target_date+1)::timestamp at time zone c.timezone;
  expected_hours:=ceil(extract(epoch from v_end)/3600)-ceil(extract(epoch from v_start)/3600);
  if w.payload->>'schema' is distinct from 'nwp-current-snapshot-v1'
   or jsonb_typeof(w.payload->'hours') is distinct from 'object' then raise exception 'complete original weather snapshot required';end if;
  select count(distinct (h.key::timestamp at time zone 'UTC')) into complete_hours from jsonb_each(w.payload->'hours') h
   where (h.key::timestamp at time zone 'UTC')>=v_start and (h.key::timestamp at time zone 'UTC')<v_end
   and mod(extract(epoch from (h.key::timestamp at time zone 'UTC')),3600)=0
   and jsonb_typeof(h.value)='array' and jsonb_array_length(h.value)=7
   and not exists(select 1 from jsonb_array_elements(h.value) value where jsonb_typeof(value)<>'number');
  if complete_hours<>expected_hours then raise exception 'incomplete original local weather day';end if;
  if p.checkpoint='morning' then
   if ((p.predictive_context#>>'{timing_reference,city_key}') is distinct from p.city_key
    or (p.predictive_context#>>'{timing_reference,timezone}') is distinct from c.timezone
    or (p.predictive_context#>>'{timing_reference,month}')::int<>extract(month from p.target_date)::int
    or not ((p.predictive_context#>>'{timing_reference,computed_at}')::timestamptz<=p.decided_at)
    or not ((p.predictive_context#>>'{timing_reference,peak_hour_local}')::numeric between 0 and 23.999999)) is not false
    then raise exception 'original seasonal peak proof required';end if;
   reference_peak:=(p.target_date::timestamp+make_interval(secs=>((p.predictive_context#>>'{timing_reference,peak_hour_local}')::numeric*3600)::double precision)) at time zone c.timezone;
   select (h.key::timestamp at time zone 'UTC') into forecast_peak from jsonb_each(w.payload->'hours') h
    where (h.key::timestamp at time zone 'UTC')>=v_start and (h.key::timestamp at time zone 'UTC')<v_end
    order by (h.value->>0)::numeric desc,(h.key::timestamp at time zone 'UTC') limit 1;
   if reference_peak<p.decided_at+interval '1 hour' or forecast_peak<p.decided_at+interval '1 hour' then
    raise exception 'morning call is too close to a known peak';end if;
  end if;
  if (select array_agg(key order by key) from jsonb_each(r->'probs')) is distinct from (select array_agg(key order by key) from jsonb_each(p.probs))
  or exists(select 1 from jsonb_each(r->'probs') x where jsonb_typeof(x.value)<>'number' or (x.value::text)::numeric not between 0 and 1)
  then raise exception 'candidate ladder identity or probability mismatch';end if;
  select sum((value::text)::numeric) into v_sum from jsonb_each(r->'probs');
  if v_sum is null or abs(v_sum-1)>0.0001 then raise exception 'candidate ladder not normalized';end if;
  select key into top_id from jsonb_each(r->'probs') order by (value::text)::numeric desc,key limit 1;
  insert into public.shared_weather_calls(model_sha256,city_key,target_date,checkpoint,source_checkpoint_id,decided_at,frozen_at,
   probs,incumbent,market,top_band_id,incumbent_top,context)
  values(p_model,p.city_key,p.target_date,p.checkpoint,p.checkpoint_id,p.decided_at,at,r->'probs',p.probs,arbdesk_private.shared_weather_market(p.market,p.probs),top_id,p.top_band_id,r->'context') on conflict do nothing;
  if found then n:=n+1;end if;
 end loop;
 return jsonb_build_object('new',n,'requested',jsonb_array_length(p_rows));
end $$;

create or replace function arbdesk_private.shared_weather_metrics(p jsonb,w text,t text default null)
returns jsonb language sql immutable set search_path='' as $$
 select case when p is null or p='null'::jsonb then null else jsonb_build_object(
 'hit',coalesce(t,(select key from jsonb_each(p) order by (value::text)::numeric desc,key limit 1))=w,
 'log_loss',-ln(greatest(coalesce((p->>w)::numeric,0),0.000000000001)),
 'brier',(select sum(power((value::text)::numeric-case when key=w then 1 else 0 end,2)) from jsonb_each(p))) end
$$;
revoke all on function arbdesk_private.shared_weather_metrics(jsonb,text,text) from public,anon,authenticated,service_role;

create or replace function public.shared_weather_score()
returns integer language plpgsql security definer set search_path='' as $$
declare n int;
begin
 insert into public.shared_weather_scores(call_id,winner_band_id,settled_at,candidate,incumbent,market)
 select s.call_id,f.winner_band_id,f.settled_at,
 arbdesk_private.shared_weather_metrics(s.probs,f.winner_band_id,s.top_band_id),
 arbdesk_private.shared_weather_metrics(s.incumbent,f.winner_band_id,s.incumbent_top),
 arbdesk_private.shared_weather_metrics(s.market,f.winner_band_id)
 from public.shared_weather_calls s join public.fact_checkpoint_outcome f on f.checkpoint_id=s.source_checkpoint_id
 join public.cities c on c.city_key=s.city_key
 where f.ladder_has_winner and f.city_key=s.city_key and f.target_date=s.target_date and f.checkpoint=s.checkpoint
 and s.probs ? f.winner_band_id and f.settled_at>s.frozen_at and f.settled_at<=clock_timestamp()
 and s.target_date<(clock_timestamp() at time zone c.timezone)::date
 and not exists(select 1 from public.shared_weather_scores old where old.call_id=s.call_id) on conflict do nothing;
 get diagnostics n=row_count;return n;
end $$;

create or replace function public.shared_weather_heartbeat(p_detail jsonb)
returns void language sql security definer set search_path='' as $$
 insert into arbdesk_private.shared_weather_status(singleton,detail) values(true,p_detail)
 on conflict(singleton) do update set detail=excluded.detail,updated_at=clock_timestamp()
$$;

-- Safe projection for a read-only server route. It returns no coefficients,
-- private feature vectors, artifact bodies or raw weather input payloads.
create or replace function public.shared_weather_snapshot()
returns jsonb language plpgsql security definer set search_path='' as $$
declare m arbdesk_private.shared_weather_models;calls jsonb;scores jsonb;
begin
 select * into m from arbdesk_private.shared_weather_models order by registered_at desc limit 1;
 if not found then return jsonb_build_object('state','not_registered','calls','[]'::jsonb,'scores','[]'::jsonb);end if;
 select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into calls from (
 select s.city_key,s.target_date,s.checkpoint,s.decided_at,s.frozen_at,s.top_band_id,s.incumbent_top,s.context,
 s.probs->s.top_band_id as top_probability,f.candidate->'hit' as hit
 from public.shared_weather_calls s left join public.shared_weather_scores f using(call_id)
 where s.model_sha256=m.model_sha256 order by s.target_date desc,s.decided_at desc,s.city_key limit 100) q;
 select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into scores from (
 select s.checkpoint,count(*)::int calls,count(f.call_id)::int settled,
 count(distinct s.target_date) filter(where f.call_id is not null)::int settled_dates,
 count(*) filter(where (f.candidate->>'hit')::boolean)::int candidate_hits,
 count(*) filter(where (f.incumbent->>'hit')::boolean)::int incumbent_hits,
 count(*) filter(where f.market is not null)::int market_pairs,
 count(*) filter(where f.market is not null and (f.candidate->>'hit')::boolean)::int candidate_market_hits,
 count(*) filter(where (f.market->>'hit')::boolean)::int market_hits,
 avg((f.candidate->>'log_loss')::numeric) candidate_log_loss,avg((f.incumbent->>'log_loss')::numeric) incumbent_log_loss
 from public.shared_weather_calls s left join public.shared_weather_scores f using(call_id)
 where s.model_sha256=m.model_sha256 and s.target_date between m.evaluation_first and m.evaluation_last group by s.checkpoint) q;
 return jsonb_build_object('state','forward_test','model',jsonb_build_object('sha256',m.model_sha256,'registered_at',m.registered_at,
 'training',m.artifact->'training','evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'cohort',m.cohort,'changes_serving',false),
 'calls',calls,'scores',scores,'status',(select jsonb_build_object('updated_at',updated_at,'detail',detail) from arbdesk_private.shared_weather_status where singleton));
end $$;

do $$ declare fn text;begin
 foreach fn in array array['shared_weather_register(jsonb,text[])','shared_weather_inputs()','shared_weather_freeze(text,jsonb)',
 'shared_weather_score()','shared_weather_heartbeat(jsonb)','shared_weather_snapshot()'] loop
 execute 'revoke all on function public.'||fn||' from public,anon,authenticated';
 execute 'grant execute on function public.'||fn||' to service_role';end loop;
end $$;
