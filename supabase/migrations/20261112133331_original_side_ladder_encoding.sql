-- Parse the actual frozen intraday encoding (JSON band IDs plus delimited
-- bounds) as well as the legacy delimited IDs. No original call, quote,
-- registration, probability, policy, account or settlement is rewritten.
create or replace function arbdesk_private.original_side_ladder_matches(ctx jsonb, ladder jsonb)
returns boolean language plpgsql immutable set search_path='' as $$
declare ids text[]; lows text[]; highs text[]; i integer; band jsonb;
begin
 if not ctx ? 'band_ids' then return true; end if;
 if jsonb_typeof(ctx->'band_ids')='array' then
  ids:=array(select jsonb_array_elements_text(ctx->'band_ids'));
 elsif jsonb_typeof(ctx->'band_ids')='string' then
  ids:=string_to_array(ctx->>'band_ids',';');
 else return false; end if;
 if cardinality(ids) is distinct from jsonb_array_length(ladder)
  or cardinality(ids)=0
  or (select count(distinct id) from unnest(ids) id)<>cardinality(ids) then return false;end if;
 if ctx ? 'bands_lo' then
  if jsonb_typeof(ctx->'bands_lo')='array' then lows:=array(select jsonb_array_elements_text(ctx->'bands_lo'));
  elsif jsonb_typeof(ctx->'bands_lo')='string' then lows:=string_to_array(ctx->>'bands_lo',';');
  else return false;end if;
  if cardinality(lows) is distinct from cardinality(ids) then return false;end if;
 end if;
 if ctx ? 'bands_hi' then
  if jsonb_typeof(ctx->'bands_hi')='array' then highs:=array(select jsonb_array_elements_text(ctx->'bands_hi'));
  elsif jsonb_typeof(ctx->'bands_hi')='string' then highs:=string_to_array(ctx->>'bands_hi',';');
  else return false;end if;
  if cardinality(highs) is distinct from cardinality(ids) then return false;end if;
 end if;
 for i in 1..cardinality(ids) loop
  select x into band from jsonb_array_elements(ladder) x where x->>'band_id'=ids[i];
  if not found then return false;end if;
  if lows is not null and nullif(lows[i],'')::numeric is distinct from (band->>'band_lo')::numeric then return false;end if;
  if highs is not null and nullif(highs[i],'')::numeric is distinct from (band->>'band_hi')::numeric then return false;end if;
 end loop;
 return true;
 exception when invalid_text_representation or numeric_value_out_of_range then return false;
end $$;
revoke all on function arbdesk_private.original_side_ladder_matches(jsonb,jsonb) from public,anon,authenticated;
grant execute on function arbdesk_private.original_side_ladder_matches(jsonb,jsonb) to service_role;

create or replace function arbdesk_private.original_side_source(p_call uuid)
returns jsonb language plpgsql set search_path='' as $$
declare p public.prediction_checkpoints; b record; m record; c record; r record;
 ctx jsonb; stamp timestamptz:=clock_timestamp(); scheduled timestamptz; start_at timestamptz;
 cutoff timestamptz; candidate timestamptz; hour numeric; ref jsonb; k text; reason text:='eligible';
 ladder jsonb; rule_text text; rule_received timestamptz; receipt_id bigint; source_kind text:='prediction_checkpoints';
begin
 select * into p from public.prediction_checkpoints where checkpoint_id=p_call;
 if not found then
  select x.* into p from public.day_max_day_ahead_calls d cross join lateral jsonb_populate_record(null::public.prediction_checkpoints,d.record) x where d.call_id=p_call;
  source_kind:='day_max_day_ahead_calls';
 end if;
 if p.checkpoint_id is null then raise exception 'original_call_unavailable'; end if;
 -- Select first BEFORE eligibility. Native day_ahead and legacy midnight receipts are distinct sources.
 if source_kind='prediction_checkpoints' and exists(select 1 from public.prediction_checkpoints x where x.city_key=p.city_key and x.target_date=p.target_date
   and x.checkpoint=p.checkpoint and (x.decided_at,x.checkpoint_id)<(p.decided_at,p.checkpoint_id)) then raise exception 'not_first_original_call'; end if;
 select * into b from public.v_canonical_bands where band_id::text=p.top_band_id;
 if not found then raise exception 'original_band_unavailable'; end if;
 select * into m from public.v_canonical_markets where market_id=b.market_id;
 select * into c from public.cities where city_key=p.city_key;
 if m.city_key is distinct from p.city_key or m.resolution_date is distinct from p.target_date or c.timezone is null then raise exception 'wrong_call_market_date_or_timezone'; end if;
 ctx:=coalesce(p.predictive_context,'{}');
 scheduled:=p.local_decision_time at time zone c.timezone;
 start_at:=p.target_date::timestamp at time zone c.timezone;
 if p.checkpoint in ('prepeak_2h','prepeak_1h') then cutoff:=scheduled+case when p.checkpoint='prepeak_2h' then interval '2 hours' else interval '1 hour' end; end if;
 foreach k in array array['clim_peak_hour','fch_peak_local_hour'] loop
  hour:=(ctx->>k)::numeric;
  if hour>=0 and hour<24 then candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone c.timezone;cutoff:=least(cutoff,candidate); end if;
 end loop;
 ref:=ctx->'timing_reference';
 if ref->>'source'='derived_weather_peak' and ref->>'city_key'=p.city_key and ref->>'timezone'=c.timezone
  and (ref->>'month')::int=extract(month from p.target_date) and (ref->>'computed_at')::timestamptz<=p.frozen_at then
  hour:=(ref->>'peak_hour_local')::numeric;
  if hour>=0 and hour<24 then candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone c.timezone;cutoff:=least(cutoff,candidate); end if;
 end if;
 if p.frozen_at<start_at then cutoff:=start_at; end if;
 select jsonb_agg(jsonb_build_object('band_id',bb.band_id,'band_label',bb.band_label,'condition_id',bb.condition_id,
  'token_yes',bb.token_yes,'token_no',bb.token_no,'band_lo',bb.band_lo,'band_hi',bb.band_hi,'open_low',bb.open_low,'open_high',bb.open_high) order by bb.band_id)
 into ladder from public.v_canonical_bands bb where bb.market_id=b.market_id;
 select rr.* into r from public.market_rule_receipts rr where rr.market_id=b.market_id and rr.received_at<=p.frozen_at order by rr.received_at desc,rr.receipt_id desc limit 1;
 if found then rule_text:=r.rules_text;rule_received:=r.received_at;receipt_id:=r.receipt_id;
 else select mm.rules_text,mm.rules_fetched_at into rule_text,rule_received from public.markets mm where mm.market_id=b.market_id and mm.rules_fetched_at<=p.frozen_at;end if;
 if p.frozen_at is null or p.inputs_ok is distinct from true or p.frozen_at>stamp or p.decided_at>p.frozen_at then reason:='unverified_freeze';
 elsif p.checkpoint not in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h') then reason:='ineligible_checkpoint';
 elsif scheduled is null or p.decided_at<scheduled or p.frozen_at>scheduled+interval '75 minutes' then reason:='late_checkpoint_capture';
 elsif coalesce(p.input_as_of,p.forecast_issued_at) is null or coalesce(p.input_as_of,p.forecast_issued_at)>p.frozen_at
  or p.forecast_issued_at>p.frozen_at or (ctx#>>'{forecast_input,run_at}')::timestamptz>p.frozen_at
  or (ctx#>>'{forecast_input,station_combination,inputs_newest_run_at}')::timestamptz>p.frozen_at then reason:='future_or_missing_input';
 elsif rule_received is null or not arbdesk_private.original_side_rule_identity(rule_text,p.station,m.unit,p.target_date,c.timezone) then reason:='unverified_original_rule_station_receipt';
 elsif ctx->>'rules_sha256' is not null and ctx->>'rules_sha256'<>encode(sha256(convert_to(rule_text,'UTF8')),'hex') then reason:='mismatched_original_rule';
 elsif (select count(*) from jsonb_object_keys(p.probs))<>jsonb_array_length(ladder) or exists(select 1 from jsonb_array_elements(ladder) l where not p.probs ? (l->>'band_id')) then reason:='changed_original_ladder';
 elsif not arbdesk_private.original_side_ladder_matches(ctx,ladder) then reason:='changed_original_ladder';
 elsif exists(select 1 from jsonb_each_text(p.probs) v where not coalesce(v.value::numeric between 0 and 1,false))
   or (select sum(value::numeric) from jsonb_each_text(p.probs)) not between .999 and 1.001
   or (p.probs->>p.top_band_id)::numeric is distinct from (select max(value::numeric) from jsonb_each_text(p.probs)) then reason:='invalid_original_probability_ladder';
 elsif cutoff is null or p.frozen_at>=cutoff then reason:='at_or_after_saved_peak';
 elsif stamp>=cutoff or p.frozen_at<stamp-interval '90 minutes' then reason:='entry_window_expired';
 elsif m.closed is distinct from false then reason:='market_not_open'; end if;
 return jsonb_build_object('call_id',p.checkpoint_id,'city_key',p.city_key,'target_date',p.target_date,'checkpoint',p.checkpoint,
  'source_kind',source_kind,'frozen_at',p.frozen_at,'cutoff_at',cutoff,'eligibility',reason,
  'source',jsonb_build_object('checkpoint_id',p.checkpoint_id,'city_key',p.city_key,'target_date',p.target_date,'checkpoint',p.checkpoint,
   'decided_at',p.decided_at,'local_decision_time',p.local_decision_time,'engine_version',p.engine_version,'priced_from',p.priced_from,'model_path',p.model_path,
   'probs',p.probs,'frozen_at',p.frozen_at,'input_as_of',coalesce(p.input_as_of,p.forecast_issued_at),'input_sha256',p.input_sha256,
   'full_source_sha256',encode(sha256(convert_to(to_jsonb(p)::text,'UTF8')),'hex'),
   'predictive_context',jsonb_build_object('source_computed_at',ctx->'source_computed_at')),
  'identity',jsonb_build_object('market_id',b.market_id,'predicted_band_id',p.top_band_id,'predicted_band_label',b.band_label,
   'event_slug',m.event_slug,'condition_id',b.condition_id,'token_yes',b.token_yes,'token_no',b.token_no,'station',p.station,'timezone',c.timezone,
   'rule_receipt_id',receipt_id,'rules_received_at',rule_received,
   'rules_sha256',encode(sha256(convert_to(rule_text,'UTF8')),'hex'),
   'ladder_sha256',encode(sha256(convert_to(ladder::text,'UTF8')),'hex'),'unit',m.unit));
end $$;

