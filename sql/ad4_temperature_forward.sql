-- Separate temperature experiment. No existing predictor, strategy, shared
-- experiment, source view or schedule is replaced. Service-only entry points.
create schema if not exists arbdesk_private;
create table if not exists arbdesk_private.temperature_models (
 model_sha256 text primary key check(model_sha256 ~ '^[0-9a-f]{64}$'),
 artifact jsonb not null, protocol jsonb not null, cohort text[] not null,
 registered_at timestamptz not null default clock_timestamp(),
 evaluation_first date not null,evaluation_last date not null,
 check(evaluation_last=evaluation_first+19),check(cardinality(cohort) between 20 and 64)
);
create table if not exists public.temperature_calls (
 call_id uuid primary key default gen_random_uuid(), model_sha256 text not null,
 city_key text not null,target_date date not null,market_id uuid not null,
 checkpoint text not null check(checkpoint in ('day_ahead','d1_eve','morning')),
 decided_at timestamptz not null,frozen_at timestamptz not null default clock_timestamp(),
 record jsonb not null,prediction jsonb not null,comparisons jsonb not null,
 unique(model_sha256,city_key,target_date,checkpoint),
 check(decided_at<=frozen_at and frozen_at<=decided_at+interval '5 minutes')
);
create index if not exists temperature_calls_receipt on public.temperature_calls(frozen_at,call_id);
create table if not exists public.temperature_scores (
 call_id uuid primary key references public.temperature_calls(call_id),winner_band_id text not null,
 confirmed_at timestamptz not null,scored_at timestamptz not null default clock_timestamp(),
 winner_interval jsonb not null,metrics jsonb not null
);
create index if not exists temperature_scores_receipt on public.temperature_scores(scored_at,call_id);
create table if not exists arbdesk_private.temperature_status (
 singleton boolean primary key default true check(singleton),updated_at timestamptz not null default clock_timestamp(),detail jsonb not null
);
create table if not exists arbdesk_private.temperature_fits (
 model_sha256 text primary key check(model_sha256 ~ '^[0-9a-f]{64}$'),artifact jsonb not null,
 recorded_at timestamptz not null default clock_timestamp()
);
create table if not exists arbdesk_private.temperature_learning_status (
 singleton boolean primary key default true check(singleton),updated_at timestamptz not null default clock_timestamp(),detail jsonb not null
);
create or replace function arbdesk_private.temperature_immutable()
returns trigger language plpgsql set search_path='' as $$
begin raise exception 'temperature evidence is immutable';end;$$;
revoke all on function arbdesk_private.temperature_immutable() from public,anon,authenticated,service_role;
do $$ declare t text;begin
 foreach t in array array['arbdesk_private.temperature_models','arbdesk_private.temperature_fits','public.temperature_calls','public.temperature_scores'] loop
 if not exists(select 1 from pg_trigger where tgrelid=t::regclass and tgname='temperature_original') then
 execute format('create trigger temperature_original before update or delete on %s for each row execute function arbdesk_private.temperature_immutable()',t);end if;
 if not exists(select 1 from pg_trigger where tgrelid=t::regclass and tgname='temperature_no_truncate') then
 execute format('create trigger temperature_no_truncate before truncate on %s for each statement execute function arbdesk_private.temperature_immutable()',t);end if;
 end loop;
end $$;
alter table arbdesk_private.temperature_models enable row level security;
alter table arbdesk_private.temperature_status enable row level security;
alter table arbdesk_private.temperature_fits enable row level security;
alter table arbdesk_private.temperature_learning_status enable row level security;
alter table public.temperature_calls enable row level security;
alter table public.temperature_scores enable row level security;
revoke all on arbdesk_private.temperature_models,arbdesk_private.temperature_status,public.temperature_calls,public.temperature_scores from public,anon,authenticated,service_role;
revoke all on arbdesk_private.temperature_fits,arbdesk_private.temperature_learning_status from public,anon,authenticated,service_role;
grant select on public.temperature_calls,public.temperature_scores to service_role;
drop policy if exists temperature_calls_reader on public.temperature_calls;
create policy temperature_calls_reader on public.temperature_calls for select to service_role using(true);
drop policy if exists temperature_scores_reader on public.temperature_scores;
create policy temperature_scores_reader on public.temperature_scores for select to service_role using(true);

-- Raw market metadata is deliberately separate from canonical unit/bounds.
create or replace function arbdesk_private.temperature_rules(p_market uuid,p_at timestamptz)
returns jsonb language sql stable set search_path='' as $$
 select jsonb_build_object('rules_text',mk.rules_text,
 'receipt_id',case when rr.rules_text=mk.rules_text then rr.receipt_id end,
 'received_at',coalesce(mk.rules_fetched_at,case when rr.rules_text=mk.rules_text then rr.received_at end))
 from public.markets mk left join lateral(select r.* from public.market_rule_receipts r
 where r.market_id=mk.market_id and r.received_at<=p_at order by r.received_at desc,r.receipt_id desc limit 1)rr on true
 where mk.market_id=p_market
$$;
revoke all on function arbdesk_private.temperature_rules(uuid,timestamptz) from public,anon,authenticated,service_role;

create or replace function arbdesk_private.temperature_protocol() returns jsonb language sql immutable set search_path='' as $protocol$
 select $spec${"automatic_promotion":false,"calibration":"fixed probability bins and 80% intervals; bucket labels give coverage bounds","changes_serving":false,"comparisons":["incumbent","raw_normal_reference","hourly_reference","market"],"confidence":0.95,"date_cluster_bootstrap_replicates":2000,"fixed_cohort_coverage":0.9,"future_dates":20,"future_window_start_policy":"at least two UTC target dates ahead; every cohort day-ahead slot must follow registration","horizons":["day_ahead","d1_eve","morning"],"market_quote_policy":"all bands require both contemporaneous bid and ask; non-crossed normalized midpoints","minimum_calls_per_horizon":200,"minimum_cities":20,"point_comparisons":["hourly_reference","raw_normal_reference"],"point_gate":"same date/city/call/coverage minima; positive lower 95% paired-date CRPS gain and no MAE regression","point_metrics":"MAE and CRPS only on independently verified complete source points","policy":"shared-temperature-evaluation-v1","primary":"paired_date_mean_log_loss","promotion":"all_horizons_all_comparisons_positive_lower_log_loss_gain; no_hit_or_brier_regression","saved_raw_centre":"additional controlled comparison where originally frozen; never reconstructed","seed":470,"training_start":"earliest registered future-window start; warmups retained but excluded"}$spec$::jsonb
$protocol$;
revoke all on function arbdesk_private.temperature_protocol() from public,anon,authenticated,service_role;

create or replace function public.temperature_register(p_artifact jsonb,p_cohort text[],p_protocol jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare m arbdesk_private.temperature_models;v_now timestamptz:=clock_timestamp();first_day date;
begin
 if (p_artifact->>'policy' is distinct from 'shared-temperature-normal-v1'
 or p_artifact->>'state' not in ('unfitted_reference','fitted_candidate')
 or p_artifact->'changes_serving' is distinct from 'false'::jsonb
 or p_artifact->'eligible_for_promotion' is distinct from 'false'::jsonb
 or not ((p_artifact->>'fit_started_at')::timestamptz<=(p_artifact->>'available_at')::timestamptz and (p_artifact->>'available_at')::timestamptz<=v_now)
 or (p_artifact->>'state'='unfitted_reference' and p_artifact->'training' is distinct from 'null'::jsonb)
 or (p_artifact->>'state'='fitted_candidate' and not (
 (p_artifact#>>'{training,dates}')::int>=7 and (p_artifact#>>'{training,rows}')::int>=200
 and (p_artifact#>>'{training,cities_per_date_min}')::int>=20
 and (p_artifact#>>'{training,through}')::date<(v_now at time zone 'UTC')::date
 and (p_artifact#>>'{training,evidence_cutoff}')::timestamptz<=(p_artifact->>'fit_started_at')::timestamptz))
 or p_protocol is distinct from arbdesk_private.temperature_protocol()
 or p_protocol->>'policy' is distinct from 'shared-temperature-evaluation-v1'
 or p_protocol->'automatic_promotion' is distinct from 'false'::jsonb
 or (p_protocol->>'future_dates')::int<>20
 or cardinality(p_cohort) not between 20 and 64
 or (select count(distinct x) from unnest(p_cohort) x)<>cardinality(p_cohort)
 or exists(select 1 from unnest(p_cohort) x where not exists(select 1 from public.cities c where c.city_key=x and c.status='active'))
 ) is not false then raise exception 'invalid temperature registration';end if;
 first_day:=(v_now at time zone 'UTC')::date+2;
 while exists(select 1 from public.cities c where c.city_key=any(p_cohort) and arbdesk_private.temperature_slot(first_day,c.timezone,'day_ahead')<v_now) loop first_day:=first_day+1;end loop;
 insert into arbdesk_private.temperature_models(model_sha256,artifact,protocol,cohort,evaluation_first,evaluation_last)
 values(p_artifact->>'sha256',p_artifact,p_protocol,p_cohort,first_day,first_day+19) on conflict do nothing;
 select * into strict m from arbdesk_private.temperature_models where model_sha256=p_artifact->>'sha256';
 if m.artifact<>p_artifact or m.protocol<>p_protocol or m.cohort<>p_cohort then raise exception 'temperature registration identity conflict';end if;
 return jsonb_build_object('model_sha256',m.model_sha256,'registered_at',m.registered_at,'cohort',m.cohort,
 'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'state',m.artifact->>'state');
end $$;

create or replace function arbdesk_private.temperature_slot(target date,zone text,horizon text)
returns timestamptz language sql immutable set search_path='' as $$
 select ((target+case when horizon='morning' then 0 else -1 end)::timestamp+
 make_interval(hours=>case when horizon='d1_eve' then 18 else 9 end)) at time zone zone
$$;
revoke all on function arbdesk_private.temperature_slot(date,text,text) from public,anon,authenticated,service_role;

create or replace function arbdesk_private.temperature_market(quotes jsonb,ladder jsonb)
returns jsonb language plpgsql immutable set search_path='' as $$
begin
 if quotes is null or quotes='null'::jsonb or ladder is null or ladder='null'::jsonb then return null;end if;
 if exists(select 1 from jsonb_each(ladder) b where
 jsonb_typeof(quotes->b.key->'bid') is distinct from 'number' or jsonb_typeof(quotes->b.key->'ask') is distinct from 'number'
 or (quotes->b.key->>'bid')::numeric not between 0 and 1 or (quotes->b.key->>'ask')::numeric not between 0 and 1
 or (quotes->b.key->>'bid')::numeric>(quotes->b.key->>'ask')::numeric) then return null;end if;
 return arbdesk_private.shared_weather_market(quotes,ladder);
end $$;
revoke all on function arbdesk_private.temperature_market(jsonb,jsonb) from public,anon,authenticated,service_role;

-- All comparisons are available at this decision, not fetched after settlement.
-- The raw normal reference is explicitly uncalibrated. A separately saved
-- controlled raw-centre alternative is retained only where it really exists.
create or replace function arbdesk_private.temperature_references(city text,target date,market uuid,horizon text,at timestamptz)
returns jsonb language plpgsql set search_path='' as $$
declare original record;current_call record;raw record;quotes jsonb;probabilities jsonb;raw_ids jsonb;bands int;complete int;
begin
 select p.checkpoint_id,p.probs,p.market,p.predictive_context,p.decided_at into original from public.prediction_checkpoints p
 where p.city_key=city and p.target_date=target and p.checkpoint=horizon and p.inputs_ok is true
 and p.decided_at between at-interval '5 minutes' and at and p.frozen_at<=at order by p.decided_at,p.checkpoint_id limit 1;
 select * into current_call from public.current_ladders p where p.city_key=city and p.target_date=target and p.market_id=market
 and p.published_at<=at and p.priced_at between at-interval '2 hours' and at;
 with selected as materialized (
 select b.band_id,p.* from public.v_canonical_bands b left join lateral(
 select bp.prob_id,bp.computed_at,bp.forecast_max_c,bp.sigma_c,bp.calibrated_prob,bp.input_forecast_run,bp.pricing_eligible
 from public.band_probabilities bp where bp.band_id=b.band_id and bp.computed_at<=at
 order by bp.computed_at desc,bp.prob_id desc limit 1) p on true where b.market_id=market
 ) select count(*) total,count(*) filter(where pricing_eligible is true and computed_at>=at-interval '6 hours' and input_forecast_run<=computed_at
 and calibrated_prob between 0 and 1) valid,min(computed_at) oldest,max(computed_at) newest,
 min(forecast_max_c) raw_min,max(forecast_max_c) raw_max,min(sigma_c) sigma_min,max(sigma_c) sigma_max,
 jsonb_object_agg(band_id::text,calibrated_prob) probs,jsonb_agg(prob_id order by band_id) ids into raw from selected;
 probabilities:=null;raw_ids:=null;
 if raw.total>=2 and raw.total=raw.valid and raw.oldest=raw.newest then probabilities:=raw.probs;raw_ids:=raw.ids;end if;
 if original.probs is not null then quotes:=original.market;
 else
 select jsonb_object_agg(b.band_id::text,jsonb_build_object('bid',s.best_bid,'ask',s.best_ask)) into quotes
 from public.v_canonical_bands b left join lateral(select x.best_bid,x.best_ask from public.book_snapshots x
 where x.band_id=b.band_id and x.observed_at between at-interval '90 minutes' and at order by x.observed_at desc,x.snapshot_id desc limit 1)s on true
 where b.market_id=market;
 end if;
 return jsonb_build_object('incumbent',coalesce(original.probs,current_call.ladder,probabilities),
 'incumbent_source',case when original.probs is not null then 'original_checkpoint' when current_call.ladder is not null then 'current_ladder' else 'stored_pricing' end,
 'source_checkpoint_id',original.checkpoint_id,'incumbent_decided_at',coalesce(original.decided_at,current_call.priced_at,raw.newest),
 'source_probability_ids',raw_ids,
 'raw_forecast_c',case when probabilities is not null and raw.raw_min=raw.raw_max then raw.raw_min end,
 'raw_sigma_c',case when probabilities is not null and raw.sigma_min=raw.sigma_max and raw.sigma_min>0 then raw.sigma_min end,
 'raw_source_at',raw.newest,
 'market',arbdesk_private.temperature_market(quotes,coalesce(original.probs,current_call.ladder,probabilities)),
 'saved_raw_centre',original.predictive_context#>'{early_forecast_audit,raw_centre_probs}');
end $$;
revoke all on function arbdesk_private.temperature_references(text,date,uuid,text,timestamptz) from public,anon,authenticated,service_role;

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

create or replace function public.temperature_score()
returns integer language plpgsql security definer set search_path='' set statement_timeout='4s' as $$
declare n int;due_ids uuid[];market_ids uuid[];
begin
 -- Scope the original eligible call set before expanding venue history.
 -- Use the existing resolution view with an explicit market-array predicate:
 -- a join to its grouped output otherwise scans unrelated bands/verdicts.
 select array_agg(x.call_id) into due_ids from (
  select t.call_id from public.temperature_calls t join public.cities city using(city_key)
  where t.target_date<(clock_timestamp() at time zone city.timezone)::date
  and not exists(select 1 from public.temperature_scores s where s.call_id=t.call_id)
  order by t.frozen_at,t.call_id
 ) x;
 if due_ids is null then return 0;end if;
 select array_agg(distinct t.market_id) into market_ids from public.temperature_calls t where t.call_id=any(due_ids);
 with resolutions as materialized (
  select b.* from public.v_venue_band_resolution b where b.market_id=any(market_ids)
 )
 insert into public.temperature_scores(call_id,winner_band_id,confirmed_at,winner_interval,metrics)
 select t.call_id,b.band_id::text,b.confirmed_at,jsonb_build_object('lo',c.band_lo,'hi',c.band_hi,'unit',t.record->>'unit'),
 jsonb_build_object('candidate',arbdesk_private.shared_weather_metrics(t.prediction->'probs',b.band_id::text),
 'incumbent',arbdesk_private.shared_weather_metrics(t.comparisons->'incumbent',b.band_id::text),
 'market',arbdesk_private.shared_weather_metrics(t.comparisons->'market',b.band_id::text),
 'raw_normal_reference',arbdesk_private.shared_weather_metrics(t.comparisons->'raw_normal_reference',b.band_id::text),
 'hourly_reference',arbdesk_private.shared_weather_metrics(t.comparisons->'hourly_reference',b.band_id::text),
 'saved_raw_centre',arbdesk_private.shared_weather_metrics(t.comparisons->'saved_raw_centre',b.band_id::text))
 from public.temperature_calls t join public.cities city using(city_key)
 cross join lateral(select arbdesk_private.temperature_rules(t.market_id,clock_timestamp()) doc) rules
 join resolutions b on b.market_id=t.market_id and b.settled_yes is true and b.resolution_state='confirmed'
 join public.v_canonical_bands c on c.band_id=b.band_id
 where t.call_id=any(due_ids) and t.record->>'rules_sha256'=encode(sha256(convert_to(rules.doc->>'rules_text','UTF8')),'hex')
 and (select jsonb_agg(jsonb_build_array(band_lo,band_hi) order by band_lo nulls first) from public.v_canonical_bands cb where cb.market_id=t.market_id)=t.record->'bounds'
 and t.target_date<(clock_timestamp() at time zone city.timezone)::date and b.confirmed_at>t.frozen_at and b.confirmed_at<=clock_timestamp()
 and t.prediction->'probs' ? b.band_id::text
 and not exists(select 1 from resolutions other where other.market_id=b.market_id and other.settled_yes is true and other.band_id<>b.band_id)
 and not exists(select 1 from public.temperature_scores old where old.call_id=t.call_id)
 order by t.frozen_at,t.call_id limit 64 on conflict do nothing;
 get diagnostics n=row_count;return n;
end $$;
create or replace function public.temperature_heartbeat(p_detail jsonb)
returns void language sql security definer set search_path='' as $$
 insert into arbdesk_private.temperature_status(singleton,detail) values(true,p_detail)
 on conflict(singleton) do update set detail=excluded.detail,updated_at=clock_timestamp()
$$;
create or replace function public.temperature_snapshot()
returns jsonb language sql security definer set search_path='' as $$
 select jsonb_build_object('models',coalesce((select jsonb_agg(jsonb_build_object('sha256',m.model_sha256,'state',m.artifact->>'state',
 'registered_at',m.registered_at,'training',m.artifact->'training','protocol',m.protocol,'cohort',m.cohort,
 'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,'changes_serving',false)) from arbdesk_private.temperature_models m),'[]'::jsonb),
 'calls',(select count(*) from public.temperature_calls),'scored',(select count(*) from public.temperature_scores),
 'status',(select jsonb_build_object('updated_at',updated_at,'detail',detail) from arbdesk_private.temperature_status where singleton),
 'learning',(select jsonb_build_object('updated_at',updated_at,'detail',detail) from arbdesk_private.temperature_learning_status where singleton))
$$;
-- A separate candidate fit never changes a registered trial or serving model.
-- Self-contained immutable packets avoid rereading years of weather history.
create or replace function public.temperature_learning_inputs()
returns jsonb language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare result jsonb;v_now timestamptz:=clock_timestamp();
begin
 if (select count(*) from public.temperature_calls where frozen_at>=v_now-interval '30 days')>6000 then
 return jsonb_build_object('state','bounded_row_limit','calls','[]'::jsonb,'cutoff',v_now);end if;
 select coalesce(jsonb_agg(jsonb_build_object('call',to_jsonb(t),'score',to_jsonb(s))),'[]'::jsonb) into result
 from public.temperature_calls t left join public.temperature_scores s using(call_id)
 where t.frozen_at>=v_now-interval '30 days';
 return jsonb_build_object('state','ready','calls',result,'cutoff',v_now,
 'training_first',(select min(evaluation_first) from arbdesk_private.temperature_models),
 'last_fit',(select jsonb_build_object('evidence_sha256',artifact#>>'{training,evidence_sha256}','sources',artifact->'sources') from arbdesk_private.temperature_fits order by recorded_at desc limit 1));
end $$;
create or replace function public.temperature_export(p_model text default null)
returns jsonb language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare m arbdesk_private.temperature_models;v_now timestamptz:=clock_timestamp();calls jsonb;points jsonb;
begin
 select * into strict m from arbdesk_private.temperature_models where p_model is null or model_sha256=p_model order by registered_at desc limit 1;
 if (select count(*) from public.temperature_calls where model_sha256=m.model_sha256)>6000 then raise exception 'bounded temperature export required';end if;
 select coalesce(jsonb_agg(jsonb_build_object('call',to_jsonb(t),'score',to_jsonb(s)) order by t.target_date,t.city_key,t.checkpoint),'[]'::jsonb)
 into calls from public.temperature_calls t left join public.temperature_scores s using(call_id) where t.model_sha256=m.model_sha256;
 select coalesce(jsonb_agg(to_jsonb(e)),'[]'::jsonb) into points from public.v_verified_weather_outcomes v
 join public.weather_resolution_evidence e using(evidence_id)
 where v.parser_version='weather-outcomes-v6' and exists(select 1 from public.temperature_calls t
 where t.model_sha256=m.model_sha256 and t.city_key=v.city_key and t.target_date=v.for_date);
 return jsonb_build_object('as_of',v_now,'artifact',m.artifact,
 'fits',(select coalesce(jsonb_agg(to_jsonb(f)),'[]'::jsonb) from arbdesk_private.temperature_fits f),
 'registration',jsonb_build_object('model_sha256',m.model_sha256,
 'state',m.artifact->>'state','registered_at',m.registered_at,'evaluation_first',m.evaluation_first,'evaluation_last',m.evaluation_last,
 'cohort',m.cohort,'protocol',m.protocol),'calls',calls,'points',points);
end $$;
create or replace function public.temperature_fit_report(p_artifact jsonb,p_detail jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
 if p_artifact is not null and p_artifact<>'null'::jsonb then
 if (p_artifact->>'policy' is distinct from 'shared-temperature-normal-v1' or p_artifact->>'state' is distinct from 'fitted_candidate'
 or p_artifact->'changes_serving' is distinct from 'false'::jsonb or p_artifact->'eligible_for_promotion' is distinct from 'false'::jsonb
 or not ((p_artifact#>>'{training,dates}')::int>=7 and (p_artifact#>>'{training,rows}')::int>=200
 and (p_artifact#>>'{training,cities_per_date_min}')::int>=20
 and (p_artifact#>>'{training,evidence_cutoff}')::timestamptz<=(p_artifact->>'fit_started_at')::timestamptz
 and (p_artifact->>'available_at')::timestamptz<=clock_timestamp())) is not false then raise exception 'invalid unregistered temperature fit';end if;
 insert into arbdesk_private.temperature_fits(model_sha256,artifact) values(p_artifact->>'sha256',p_artifact) on conflict do nothing;
 if not exists(select 1 from arbdesk_private.temperature_fits where model_sha256=p_artifact->>'sha256' and artifact=p_artifact) then
 raise exception 'private temperature fit identity conflict';end if;
 end if;
 insert into arbdesk_private.temperature_learning_status(singleton,detail) values(true,p_detail)
 on conflict(singleton) do update set updated_at=clock_timestamp(),detail=excluded.detail;
 return jsonb_build_object('stored_fit',p_artifact is not null and p_artifact<>'null'::jsonb,'changes_serving',false);
end $$;
do $$ declare fn text;begin
 foreach fn in array array['temperature_register(jsonb,text[],jsonb)','temperature_inputs()','temperature_freeze(text,jsonb)',
 'temperature_score()','temperature_heartbeat(jsonb)','temperature_snapshot()','temperature_learning_inputs()','temperature_fit_report(jsonb,jsonb)','temperature_export(text)'] loop
 execute 'revoke all on function public.'||fn||' from public,anon,authenticated';
 execute 'grant execute on function public.'||fn||' to service_role';end loop;
end $$;
