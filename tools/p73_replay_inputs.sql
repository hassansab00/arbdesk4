-- Plan v2 P7.3: the database half of the replay's inputs, one JSON document.
-- Run with the Supabase SQL tool (read only) and save the answer; the repo half
-- (observations, the day-before forecast runs, the models' spread) comes from
-- data/ and the P2.9/P7.2 inputs. scripts/backtest/replay_checkpoints.py reads it.
--
--   markets  every city-day the venue confirmed (v_venue_market_resolution,
--            confirmed, one winner): its ladder id, winner, unit and zone
--   bands    their canonical ladders
--   peaks    derived_weather_peak (the tick's peak-relative checkpoints)
--   q        cities' fitted measurement layer - NOT used by the replay's
--            headline (fitted 23 Sep on settlements that include these days);
--            exported so the effect can be shown separately
--   mids     book mids for those bands between 06:00 and 21:00 local on the
--            day: [band_id, epoch seconds, mid]
with w as (
  select r.market_id, r.city_key, r.resolution_date, r.winning_band_id, c.unit, c.timezone
    from public.v_venue_market_resolution r
    join public.cities c using (city_key)
   where r.resolution_state = 'confirmed' and r.winning_band_id is not null
)
select json_build_object(
  'exported_at', now(),
  'markets', (select json_agg(json_build_array(market_id, city_key, resolution_date, winning_band_id, unit, timezone)
                              order by resolution_date, city_key) from w),
  'bands', (select json_agg(json_build_array(b.band_id, b.market_id, b.band_lo, b.band_hi, b.open_low, b.open_high)
                            order by b.market_id, b.band_index)
              from public.v_canonical_bands b where b.market_id in (select market_id from w)),
  'peaks', (select json_agg(json_build_array(city_key, month, peak_hour_local)) from public.derived_weather_peak),
  'q', (select json_agg(json_build_array(city_key, observation_q_down, observation_q_up)) from public.cities),
  'mids', (select json_agg(json_build_array(s.band_id, extract(epoch from s.observed_at)::bigint,
                                            coalesce(s.mid, (s.best_bid + s.best_ask) / 2)))
             from w
             join public.v_canonical_bands b on b.market_id = w.market_id
             join public.book_snapshots s on s.band_id = b.band_id
              and s.observed_at >= (w.resolution_date::timestamp + interval '6 hours') at time zone w.timezone
              and s.observed_at <  (w.resolution_date::timestamp + interval '21 hours') at time zone w.timezone
            where coalesce(s.mid, (s.best_bid + s.best_ask) / 2) is not null)
) as replay_inputs;
