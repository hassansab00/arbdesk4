-- Preserve exact comparison receipts across JSON/Python transport.
-- No source selection, freshness, model, grants or stored calls are changed.

create or replace function public.temperature_inputs()
returns jsonb language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare m arbdesk_private.temperature_models;v_now timestamptz:=clock_timestamp();packets jsonb;
begin
 select * into m from arbdesk_private.temperature_models order by registered_at desc limit 1;
 if not found then return jsonb_build_object('state','not_registered','packets','[]'::jsonb);end if;
 with due as materialized (
 select cm.market_id,cm.city_key,cm.resolution_date,cm.unit,h.horizon,c,
 arbdesk_private.temperature_rules(cm.market_id,v_now) rules
 from public.v_canonical_markets cm
 join public.cities c on c.city_key=cm.city_key
 cross join (values('day_ahead'),('d1_eve'),('morning')) h(horizon)
 where cm.city_key=any(m.cohort) and c.status='active' and not coalesce(cm.closed,false)
 and cm.resolution_date between (v_now at time zone c.timezone)::date and (v_now at time zone c.timezone)::date+1
 and v_now between arbdesk_private.temperature_slot(cm.resolution_date,c.timezone,h.horizon)
 and arbdesk_private.temperature_slot(cm.resolution_date,c.timezone,h.horizon)+interval '75 minutes'
 and cm.resolution_date<=m.evaluation_last
 and (m.artifact->'training'='null'::jsonb or cm.resolution_date>(m.artifact#>>'{training,through}')::date)
 and not exists(select 1 from public.temperature_calls t where t.model_sha256=m.model_sha256 and t.city_key=cm.city_key and t.target_date=cm.resolution_date and t.checkpoint=h.horizon)
 order by cm.city_key,cm.resolution_date,h.horizon limit 64
 ), receipts as materialized (
 select d.*,arbdesk_private.temperature_references(d.city_key,d.resolution_date,d.market_id,d.horizon,v_now) refs from due d
 )
 select coalesce(jsonb_agg(jsonb_build_object('as_of',v_now,'checkpoint',d.horizon,
 'market',jsonb_build_object('market_id',d.market_id,'city_key',d.city_key,'resolution_date',d.resolution_date,'unit',d.unit),
 'city',to_jsonb(d.c),'rules',d.rules,
 'references',d.refs,'references_receipt',d.refs::text,
 'weather',(select to_jsonb(w) from public.live_capture w where w.kind='hourly0' and w.key=d.city_key||'|snapshot'
 and greatest(w.captured_at,w.written_at)<=v_now and greatest(w.captured_at,w.written_at)>=v_now-interval '2 hours'
 order by greatest(w.captured_at,w.written_at) desc,w.capture_id desc limit 1),
 'observation',(select to_jsonb(w) from public.live_capture w where w.kind='awc_metar' and w.key=d.city_key||'|'||d.resolution_date::text
 and greatest(w.captured_at,w.written_at)<=v_now and greatest(w.captured_at,w.written_at)>=v_now-interval '90 minutes'
 order by greatest(w.captured_at,w.written_at) desc,w.capture_id desc limit 1),
 'peak_reference',(select jsonb_build_object('city_key',p.city_key,'timezone',(d.c).timezone,'month',p.month,'computed_at',p.computed_at,'peak_hour_local',p.peak_hour_local)
 from public.derived_weather_peak p where p.city_key=d.city_key and p.month=extract(month from d.resolution_date)::int and p.computed_at<=v_now order by p.computed_at desc limit 1),
 'bands',(select jsonb_agg(jsonb_build_object('band_id',b.band_id,'lo',b.band_lo,'hi',b.band_hi)) from public.v_canonical_bands b where b.market_id=d.market_id)
 )),'[]'::jsonb) into packets from receipts d;
 return jsonb_build_object('state','registered','artifact',m.artifact,'registered_at',m.registered_at,
 'protocol',m.protocol,'cohort',m.cohort,'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'packets',packets);
end $$;

create or replace function public.temperature_freeze(p_model text,p_rows jsonb)
returns jsonb language plpgsql security definer set search_path='' set statement_timeout='5s' as $$
declare m arbdesk_private.temperature_models;r jsonb;v jsonb;p jsonb;c public.cities;cm record;w public.live_capture;o public.live_capture;
 at timestamptz;decided timestamptz;ids text[];wanted text[];mass numeric;n int:=0;comparisons jsonb;original record;
 rules text;rules_at timestamptz;bounds jsonb;start_at timestamptz;end_at timestamptz;hours int;reference_receipt jsonb;forecast_peak timestamptz;seasonal_peak timestamptz;
begin
 select * into strict m from arbdesk_private.temperature_models where model_sha256=p_model;
 if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows)>64 then raise exception 'bounded temperature batch required';end if;
 for r in select value from jsonb_array_elements(p_rows) loop
 v:=r->'record';p:=r->'prediction';at:=clock_timestamp();decided:=(v->>'as_of')::timestamptz;
 select * into strict c from public.cities where city_key=v->>'city';
 select * into strict cm from public.v_canonical_markets where market_id=(v->>'market_id')::uuid;
 select * into strict w from public.live_capture where capture_id=(v->>'weather_capture_id')::bigint;
 select doc->>'rules_text',(doc->>'received_at')::timestamptz into rules,rules_at from(select arbdesk_private.temperature_rules(cm.market_id,decided) doc)x;
 if (not c.city_key=any(m.cohort) or c.status<>'active' or cm.closed is true
 or cm.city_key is distinct from c.city_key or cm.resolution_date is distinct from (v->>'date')::date or cm.unit is distinct from v->>'unit'
 or v->>'contract' is distinct from 'awc-routine-v1' or v->>'station' is distinct from c.icao
 or v->>'policy' is distinct from 'shared-temperature-normal-v1'
 or v->>'rules_sha256' is distinct from encode(sha256(convert_to(rules,'UTF8')),'hex')
 or rules_at is null or rules_at>decided or rules_at is distinct from (v->>'rules_received_at')::timestamptz
 or v->>'timezone' is distinct from c.timezone or v->>'checkpoint' not in ('day_ahead','d1_eve','morning')
 or not (m.registered_at<=decided and decided<=at and at<=decided+interval '5 minutes')
 or not (at between arbdesk_private.temperature_slot(cm.resolution_date,c.timezone,v->>'checkpoint') and arbdesk_private.temperature_slot(cm.resolution_date,c.timezone,v->>'checkpoint')+interval '75 minutes')
 or cm.resolution_date>m.evaluation_last or (m.artifact->'training'<>'null'::jsonb and cm.resolution_date<=(m.artifact#>>'{training,through}')::date)
 or w.kind<>'hourly0' or w.key<>c.city_key||'|snapshot' or not (greatest(w.captured_at,w.written_at) between decided-interval '2 hours' and decided)
 or w.payload->>'schema' is distinct from 'nwp-current-snapshot-v1'
 or jsonb_typeof(p->'probs') is distinct from 'object' or (p->>'sigma_c')::numeric not between .5 and 6
 or (p->>'centre_c')::numeric not between -98 and 78
 or exists(select 1 from public.v_venue_band_resolution b where b.market_id=cm.market_id and b.settled_yes is true)
 ) is not false then raise exception 'temperature freeze lacks original live evidence';end if;
 select jsonb_agg(jsonb_build_array(band_lo,band_hi) order by band_lo nulls first) into bounds from public.v_canonical_bands where market_id=cm.market_id;
 if v->'bounds' is distinct from bounds then raise exception 'temperature ladder geometry mismatch';end if;
 start_at:=cm.resolution_date::timestamp at time zone c.timezone;
 end_at:=(cm.resolution_date+1)::timestamp at time zone c.timezone;
 select count(distinct h.key::timestamp at time zone 'UTC') into hours from jsonb_each(w.payload->'hours') h
 where (h.key::timestamp at time zone 'UTC')>=start_at and (h.key::timestamp at time zone 'UTC')<end_at
 and mod(extract(epoch from h.key::timestamp at time zone 'UTC'),3600)=0
 and jsonb_typeof(h.value)='array' and jsonb_array_length(h.value)=7
 and not exists(select 1 from jsonb_array_elements(h.value) a where jsonb_typeof(a)<>'number');
 if hours<>(ceil(extract(epoch from end_at)/3600)-ceil(extract(epoch from start_at)/3600)) then
 raise exception 'incomplete received local weather day';end if;
 if (jsonb_array_length(v#>'{hourly,hours}')<>hours
 or exists(select 1 from jsonb_array_elements(v#>'{hourly,hours}') h
 where (h->>'utc')::numeric not between extract(epoch from start_at) and extract(epoch from end_at)-1
 or mod((h->>'utc')::numeric,3600)<>0
 or h->'values' is distinct from w.payload->'hours'->to_char(to_timestamp((h->>'utc')::double precision) at time zone 'UTC','YYYY-MM-DD"T"HH24:MI')
 or (h->>'captured_at')::timestamptz is distinct from w.captured_at or (h->>'written_at')::timestamptz is distinct from w.written_at)
 or (select count(distinct h->>'utc') from jsonb_array_elements(v#>'{hourly,hours}') h)<>hours) is not false
 then raise exception 'temperature hourly packet differs from received source';end if;
 if (w.payload#>>'{request_location,timezone}' is distinct from c.timezone
 or abs((w.payload#>>'{request_location,latitude}')::numeric-c.latitude)>.05
 or abs((w.payload#>>'{request_location,longitude}')::numeric-c.longitude)>.05) is not false
 then raise exception 'station weather location mismatch';end if;
 if v->>'checkpoint'='morning' then
 select ((cm.resolution_date::timestamp+make_interval(secs=>p.peak_hour_local::double precision*3600)) at time zone c.timezone) into seasonal_peak
 from public.derived_weather_peak p where p.city_key=c.city_key and p.month=extract(month from cm.resolution_date)::int
 and p.computed_at=(v#>>'{peak_reference,computed_at}')::timestamptz and p.computed_at<=decided
 and p.peak_hour_local=(v#>>'{peak_reference,peak_hour_local}')::numeric order by p.computed_at desc limit 1;
 select h.key::timestamp at time zone 'UTC' into forecast_peak from jsonb_each(w.payload->'hours') h
 where h.key::timestamp at time zone 'UTC'>=start_at and h.key::timestamp at time zone 'UTC'<end_at
 order by (h.value->>0)::numeric desc,h.key::timestamp at time zone 'UTC' limit 1;
 if seasonal_peak is null or forecast_peak is null or least(seasonal_peak,forecast_peak)<decided+interval '1 hour' then
 raise exception 'morning lacks original advance peak proof';end if;
 end if;
 if v->'floor_c'<>'null'::jsonb then
 select * into strict o from public.live_capture where capture_id=(v#>>'{observation,capture_id}')::bigint;
 if (v->>'checkpoint'<>'morning' or o.kind<>'awc_metar' or o.key<>c.city_key||'|'||cm.resolution_date::text
 or not(greatest(o.captured_at,o.written_at) between decided-interval '90 minutes' and decided)
 or o.payload->>'schema' is distinct from 'awc-routine-day-v1' or o.payload->>'station' is distinct from c.icao
 or o.payload->>'rules_sha256' is distinct from v->>'rules_sha256'
 or (o.payload->>'qualifying_max_c')::numeric is distinct from (v->>'floor_c')::numeric
 or o.payload->>'raw_sha256' is distinct from v#>>'{observation,raw_sha256}'
 or not ((o.payload->>'latest_eligible_at')::timestamptz between decided-interval '90 minutes' and decided)) is not false
 then raise exception 'floor lacks original qualified observation';end if;
 end if;
 select array_agg(value order by value) into ids from jsonb_array_elements_text(v->'band_ids');
 select array_agg(band_id::text order by band_id::text) into wanted from public.v_canonical_bands where market_id=cm.market_id;
 if ids is distinct from wanted or (select array_agg(key order by key) from jsonb_each(p->'probs')) is distinct from wanted
 or exists(select 1 from jsonb_each(p->'probs') q where jsonb_typeof(value)<>'number' or (value::text)::numeric not between 0 and 1)
 then raise exception 'temperature ladder identity mismatch';end if;
 select sum((value::text)::numeric) into mass from jsonb_each(p->'probs');
 if mass is null or abs(mass-1)>.00001 then raise exception 'temperature ladder not normalized';end if;
 -- Frozen source checkpoints are the matched served comparison. A missing
 -- one stays null; a later quote or different checkpoint is never substituted.
 comparisons:=arbdesk_private.temperature_references(c.city_key,cm.resolution_date,cm.market_id,v->>'checkpoint',decided);
 -- Keep PostgreSQL numeric precision across the JSON/Python transport.
  -- Legacy object clients still face the same exact equality check.
  if r ? 'references_receipt' then
   if jsonb_typeof(r->'references_receipt') is distinct from 'string' then
    raise exception 'temperature comparison receipt changed';end if;
   reference_receipt:=(r->>'references_receipt')::jsonb;
  else reference_receipt:=r->'references';end if;
  if comparisons is distinct from reference_receipt then raise exception 'temperature comparison receipt changed';end if;
 comparisons:=comparisons||jsonb_build_object('hourly_reference',r->'hourly_reference','raw_normal_reference',r->'raw_normal_reference');
 insert into public.temperature_calls(model_sha256,city_key,target_date,market_id,checkpoint,decided_at,frozen_at,record,prediction,comparisons)
 values(p_model,c.city_key,cm.resolution_date,cm.market_id,v->>'checkpoint',decided,at,v,p,comparisons) on conflict do nothing;
 if found then n:=n+1;end if;
 end loop;
 return jsonb_build_object('new',n,'requested',jsonb_array_length(p_rows));
end $$;
