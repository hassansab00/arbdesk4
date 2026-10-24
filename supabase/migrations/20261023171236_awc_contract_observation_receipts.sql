-- A separate receipt-stamped authority snapshot; IEM report history is intact.
-- The existing generic writer and every existing kind retain their behavior.
alter table public.live_capture drop constraint if exists live_capture_kind_check;
alter table public.live_capture add constraint live_capture_kind_check check
  (kind in ('report','hourly','hourly2','models1','models2','books','focus_book',
            'wx_features','hourly0','models0','awc_metar'));

create or replace function public.capture_awc_metar(p_rows jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $fn$
declare v_new integer;
begin
  if p_rows is null or jsonb_typeof(p_rows)<>'array' or jsonb_array_length(p_rows)>100 then
    raise exception 'capture_awc_metar: a bounded array is required';
  end if;
  if exists(select 1 from jsonb_array_elements(p_rows) x
    where x->>'captured_at' is null
       or (x->>'captured_at')::timestamptz > clock_timestamp()+interval '5 minutes'
       or x->'payload'->>'schema' is distinct from 'awc-routine-day-v1'
       or nullif(x->'payload'->>'city_key','') is null
       or nullif(x->'payload'->>'date','') is null
       or nullif(x->'payload'->>'timezone','') is null
       or x->>'key' is distinct from (x->'payload'->>'city_key')||'|'||(x->'payload'->>'date')
       or coalesce(x->'payload'->>'rules_sha256','') !~ '^[a-f0-9]{64}$'
       or coalesce(x->'payload'->>'raw_sha256','') !~ '^[a-f0-9]{64}$'
       or coalesce(x->'payload'->>'station','') !~ '^[A-Z0-9]{4}$'
       or coalesce(x->'payload'->>'source_url','') not like 'https://aviationweather.gov/api/data/metar?%'
       or coalesce(x->'payload'->>'raw_gzip','')=''
       or (x->'payload'->>'date')::date is distinct from
          ((x->>'captured_at')::timestamptz at time zone (x->'payload'->>'timezone'))::date) then
    raise exception 'capture_awc_metar: invalid authority snapshot or receipt';
  end if;
  if exists(select 1 from jsonb_array_elements(p_rows) x group by x->>'key' having count(*)>1) then
    raise exception 'capture_awc_metar: successive snapshots need separate batches';
  end if;
  with ins as (
    insert into public.live_capture(kind,key,captured_at,payload)
    -- A delayed upload cannot backdate when the platform knew the response.
    -- The client receipt remains separately in the canonical payload.
    select 'awc_metar',x->>'key',greatest((x->>'captured_at')::timestamptz,clock_timestamp()),x->'payload'
    from jsonb_array_elements(p_rows) x
    where x->'payload' is distinct from (
      select l.payload from public.live_capture l where l.kind='awc_metar' and l.key=x->>'key'
      order by l.captured_at desc,l.capture_id desc limit 1)
    returning 1)
  select count(*) into v_new from ins;
  return jsonb_build_object('rows',jsonb_array_length(p_rows),'new',v_new);
end $fn$;
revoke all on function public.capture_awc_metar(jsonb) from public,anon,authenticated;
grant execute on function public.capture_awc_metar(jsonb) to service_role;
notify pgrst,'reload schema';

-- A source-specific floor may be below a special observation's temperature.
-- Keep physical all-source weather columns separate and all legacy rows exact.
do $view$ begin
  if to_regclass('public.v_city_observation_health') is null then return; end if;
  execute $sql$
create or replace view public.v_city_running_max as
with rules as (
  select m.city_key,m.resolution_date,
         bool_or(m.rules_text like '%https://aviationweather.gov/api/data/metar?%') as awc
  from public.markets m join public.cities c using(city_key)
  where m.resolution_date=(now() at time zone c.timezone)::date
    and coalesce(c.status,'active')='active'
  group by m.city_key,m.resolution_date
), receipt as (
  select distinct on(l.key) l.key,l.captured_at,l.payload
  from public.live_capture l
  where l.kind='awc_metar' and l.captured_at<=now()
    and l.captured_at>=now()-interval '90 minutes'
  order by l.key,l.captured_at desc,l.capture_id desc
), authority as (
  select r.city_key,r.resolution_date,r.awc,
         case when exists(select 1 from public.markets m join public.cities c using(city_key)
           where m.city_key=r.city_key and m.resolution_date=r.resolution_date
             and encode(sha256(convert_to(m.rules_text,'UTF8')),'hex')=l.payload->>'rules_sha256'
             and upper(substring(l.payload->>'contract_source_url' from '[?&]ids=([A-Za-z0-9]{4})(?:&|$)'))=l.payload->>'station'
             and position(l.payload->>'contract_source_url' in m.rules_text)>0
             and c.timezone=l.payload->>'timezone'
             and l.payload->>'schema'='awc-routine-day-v1') then l.payload end as payload
  from rules r left join receipt l on l.key=r.city_key||'|'||r.resolution_date::text
)
select h.city_key,h.local_date,
       case when a.awc then case when (a.payload->>'routine_count')::bigint>1
         and coalesce((a.payload->>'malformed_reports')::integer,0)=0 then 'series'
         when (a.payload->>'routine_count')::bigint>=1 then 'floor_only' else 'absent' end
         else h.running_max_basis end as running_max_basis,
       case when a.awc then h.timing_trustworthy and (a.payload->>'routine_count')::bigint>1
         and coalesce((a.payload->>'malformed_reports')::integer,0)=0
         and (a.payload->>'latest_eligible_at')::timestamptz>=now()-interval '3 hours'
         else h.timing_trustworthy end as timing_trustworthy,
       case when a.awc then (a.payload->>'qualifying_max_c')::numeric else
         coalesce(h.settlement_max_today_c,greatest(h.observed_max_today_c,h.stored_running_max_c,h.latest_temp_today_c)) end as running_max_c,
       h.observed_max_today_c,h.stored_running_max_c,h.latest_temp_today_c,h.latest_temp_c,
       h.readings_today,h.stored_max_below_latest,
       case when a.awc then 'Market high uses qualifying AWC routine reports; special reports remain weather inputs.' else h.note end as note,
       h.live_source_kind,
       case when a.awc then (a.payload->>'latest_eligible_at')::timestamptz else h.latest_reading_at end as latest_reading_at,
       case when a.awc then (a.payload->>'latest_eligible_c')::numeric else h.latest_reading_c end as latest_reading_c,
       case when a.awc then (a.payload->>'qualifying_max_c')::numeric else h.settlement_max_today_c end as settlement_max_today_c,
       greatest(h.observed_max_today_c,h.stored_running_max_c,h.latest_temp_today_c) as all_sources_max_c,
       case when a.awc then 'awc_routine' when h.settlement_max_today_c is not null then 'settlement_feed'
         when greatest(h.observed_max_today_c,h.stored_running_max_c,h.latest_temp_today_c) is not null then 'all_sources' end as running_max_source
from public.v_city_observation_health h left join authority a
on a.city_key=h.city_key and a.resolution_date=h.local_date
  $sql$;
end $view$;
