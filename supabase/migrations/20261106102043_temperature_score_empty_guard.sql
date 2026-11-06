-- No history is changed. Return before settlement planning when no research call can be scored.
create or replace function public.temperature_score()
returns integer language plpgsql security definer set search_path='' set statement_timeout='4s' as $$
declare n int;
begin
 -- Avoid planning or reading the global settlement surface before there is
 -- a completed local day with an unscored research call.
 if not exists(select 1 from public.temperature_calls t join public.cities city using(city_key)
 where t.target_date<(clock_timestamp() at time zone city.timezone)::date
 and not exists(select 1 from public.temperature_scores s where s.call_id=t.call_id)) then return 0;end if;
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
 join public.v_venue_band_resolution b on b.market_id=t.market_id and b.settled_yes is true and b.resolution_state='confirmed'
 join public.v_canonical_bands c on c.band_id=b.band_id
 where t.record->>'rules_sha256'=encode(sha256(convert_to(rules.doc->>'rules_text','UTF8')),'hex')
 and (select jsonb_agg(jsonb_build_array(band_lo,band_hi) order by band_lo nulls first) from public.v_canonical_bands cb where cb.market_id=t.market_id)=t.record->'bounds'
 and t.target_date<(clock_timestamp() at time zone city.timezone)::date and b.confirmed_at>t.frozen_at and b.confirmed_at<=clock_timestamp()
 and t.prediction->'probs' ? b.band_id::text
 and not exists(select 1 from public.v_venue_band_resolution other where other.market_id=b.market_id and other.settled_yes is true and other.band_id<>b.band_id)
 and not exists(select 1 from public.temperature_scores old where old.call_id=t.call_id) on conflict do nothing;
 get diagnostics n=row_count;return n;
end $$;
revoke all on function public.temperature_score() from public,anon,authenticated;
grant execute on function public.temperature_score() to service_role;
