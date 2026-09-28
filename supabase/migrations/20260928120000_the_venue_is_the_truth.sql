-- Plan v2.4 P3.10 part 3.1: the venue's own truth for a settled city-day.
--
-- The desk's forecast corrections (P3.9 station_correction, P2.9 station_mos)
-- learn from derived_city_day_features.max_c, the station maximum as the desk
-- read it. The model is scored on the venue's winner, and the two disagree.
-- Measured 28 Sep:
--   * C cities before Sep: that maximum names a LOWER bucket than the venue's
--     winner on 10.3% of 5,936 city-days (docs/MODEL_VS_MARKET_2026-09-28.md,
--     Q6), before P2.1 read every METAR;
--   * F cities, 24 Aug-27 Sep: it differs from the venue's own reading on 132
--     of 311 city-days, 0.21 C too high on average, and 141 of them are whole
--     Celsius, which cannot place a 2 F bucket.
-- Trained on the venue's truth instead, the same recipe lost its low bias
-- (+0.112 -> +0.010 buckets) and priced the ladder better at 08:00 (log loss
-- +0.0079 [+0.0013, +0.0140], 7,069 city-days).
--
-- The label of a city-day, in Celsius:
--   venue_reading   the venue's page as read and verified
--                   (v_verified_weather_outcomes, every city since 24 Aug), when
--                   the venue has not confirmed a winner yet, or when its whole
--                   reading lies in the confirmed winner's bucket (1,078 of 1,091
--                   C and 308 of 308 F city-days on 28 Sep; the other 13 read one
--                   bucket above the winner, and the settlement is the authority)
--   winning_bucket  otherwise the confirmed winner's bucket, as the midpoint of
--                   the whole readings it holds: [k, k+1) C -> k; [2j, 2j+2) F ->
--                   2j + 0.5 F. An open bucket (a tail) names no value.
-- A city-day whose record names more than one winner has no bucket label.
-- One row per (city_key, for_date). Service role only: the learners read it.
-- Re-runnable.

create or replace view public.v_venue_truth with (security_invoker = true) as
with reading as (
  select v.city_key, v.for_date, v.observed_max_c::double precision as reading_c
  from public.v_verified_weather_outcomes v
  where v.observed_max_c is not null
),
winner as (
  select o.city_key, o.for_date, min(o.band_id::text)::uuid as band_id, count(*) as winners
  from public.v_fact_band_outcome_clean o
  where o.settled_yes
  group by o.city_key, o.for_date
),
bucket as (
  select w.city_key, w.for_date, b.band_lo::double precision as band_lo,
         b.band_hi::double precision as band_hi, m.unit
  from winner w
  join public.v_canonical_bands b on b.band_id = w.band_id
  join public.v_canonical_markets m on m.market_id = b.market_id
  where w.winners = 1
),
joined as (
  select coalesce(r.city_key, k.city_key) as city_key, coalesce(r.for_date, k.for_date) as for_date,
         r.reading_c, k.band_lo, k.band_hi, k.unit, k.city_key is not null as confirmed,
         case when r.reading_c is null then null
              when k.unit = 'F' then floor(r.reading_c * 9 / 5 + 32 + 0.5)
              else floor(r.reading_c + 0.5) end as whole_reading
  from reading r
  full join bucket k on k.city_key = r.city_key and k.for_date = r.for_date
),
labelled as (
  select j.city_key, j.for_date,
         case
           when j.reading_c is not null and (not j.confirmed
                or ((j.band_lo is null or j.whole_reading >= j.band_lo)
                    and (j.band_hi is null or j.whole_reading < j.band_hi))) then j.reading_c
           when j.confirmed and j.band_lo is not null and j.band_hi is not null then
             case when j.unit = 'F' then ((j.band_lo + j.band_hi - 1) / 2 - 32) * 5 / 9
                  else (j.band_lo + j.band_hi - 1) / 2 end
         end as label_c,
         case
           when j.reading_c is not null and (not j.confirmed
                or ((j.band_lo is null or j.whole_reading >= j.band_lo)
                    and (j.band_hi is null or j.whole_reading < j.band_hi))) then 'venue_reading'
           else 'winning_bucket'
         end as source
  from joined j
)
select city_key, for_date, label_c, source
from labelled
where label_c is not null;

revoke all on public.v_venue_truth from public, anon, authenticated;
grant select on public.v_venue_truth to service_role;

comment on view public.v_venue_truth is
  'Plan v2.4 P3.10 part 3.1: the venue''s truth for a settled city-day, in C - its verified reading when that lies in the confirmed winner''s bucket (or no winner is confirmed yet), else the winning bucket''s midpoint reading. What the forecast corrections learn from.';
