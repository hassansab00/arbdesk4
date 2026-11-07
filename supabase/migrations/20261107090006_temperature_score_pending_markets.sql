-- Scope venue aggregation to original closed, unscored research markets.
-- Preserve all proof, clock, rule, geometry and metric gates; at most 64 scores per call.
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
