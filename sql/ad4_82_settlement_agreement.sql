-- ===========================================================================
-- HOW OFTEN OUR THERMOMETER AGREES WITH THE VENUE'S SETTLEMENT.
--
-- Every strategy that asks "which band did the day land in" is standing on
-- one number: the observed daily maximum. Measured on 2026-09-22 against the
-- venue's own declared winners, that number is wrong about a quarter of the
-- time - and NOT at random. Where it misses, it misses by exactly one band.
--
-- WHAT WAS RULED OUT, in order, before writing this:
--
--   THE STATION.        Every market's rules_text names its source station as
--                       a site id: "the highest temperature recorded by NOAA
--                       at the London City Airport Station ...
--                       weather.gov/wrh/timeseries?site=eglc". Extracted and
--                       compared against cities.icao for all 48 active
--                       cities: 48 of 48 match. We read the right station.
--   THE DAY BOUNDARY.   Local day 75.0% vs UTC day 70.8%. Local is right and
--                       is what we already use.
--   ROUNDING.           Rounding our readings to whole degrees the way the
--                       page displays them makes agreement WORSE, 75.0% ->
--                       73.2%, whether rounded per reading or after the max.
--   THE UNIT.           This one is real but small. observed_max_c is stored
--                       in Celsius and compared to an F-market's bands by
--                       converting back, and the round trip costs 2.9 points:
--                       75.0% on the station's native unit against 72.1% via
--                       Celsius. Austin's 100.4F is exactly 38.0C and
--                       Dallas's 98.6F exactly 37.0C - whole-Celsius values
--                       wearing a decimal.
--   THE READER.         scripts/weather_outcomes.py already fetches the exact
--                       NOAA page the rules name, hourly_only honoured, with
--                       a payload hash. Its answers agree 76.2%. Reading the
--                       venue's own named source barely beats reading the
--                       station feed, which is the finding that matters: the
--                       residual is not our temperature.
--
-- SO THE RESIDUAL IS NOT EXPLAINED, AND THAT IS WHY THIS IS A VIEW RATHER
-- THAN A FIX. A number nobody can correct yet still has to be a number
-- everybody can see, per city, because it is not uniform across the roster -
-- and a strategy that reads the running maximum in a city where we agree 95%
-- of the time is doing something quite different from the same strategy in a
-- city where we agree half the time.
--
-- WHAT WAS FIXED. Coverage, which was the larger half. Banked agreement read
-- 52.1% against 76.2% for the same reader, because most band rows carried no
-- observed maximum at all - the authority covers roughly four days in five
-- and nothing was banked for the rest. The station feed agrees 75.0% on its
-- own, so falling back to it beats banking a null, as long as the row says
-- which source answered. That is obs_source on fact_band_outcome.
--
-- Run order: after sql/ad4_81_paper_desk_integrity.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. The station feed's own answer for a city-day, in the unit the market
--    settles in.
--
--    NATIVE UNIT, NOT CELSIUS. temp_f is what IEM returns for the station and
--    temp_c is f_to_c() of it (scripts/ingest_observations.py); converting
--    back to compare against an F market's bands costs 2.9 points of
--    agreement for nothing. Both are published here so a caller that needs
--    Celsius - the fitter, the feature cache - is not forced through F.
-- --------------------------------------------------------------------------
create or replace view v_station_day_max as
select
  o.city_key,
  (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date  as for_date,
  max(o.temp_c)                                                as max_c,
  max(o.temp_f)                                                as max_f,
  count(*)                                                     as n_readings,
  max(o.valid_at)                                              as last_reading_at,
  min(o.station)                                               as station,
  -- THE ROUTINE REPORT ONLY, which is the column the venue settles on. See
  -- 20260922180000_the_venue_reads_the_hourly_column.sql: the eleven US ASOS
  -- sites file about 125 observations a day and the venue reads 24 of them,
  -- so max() over the five-minute feed catches spikes the "Temp" column never
  -- shows and moves the day into the next band up. 76.8% -> 90.9% on those
  -- cities. A four-minute tolerance because a station that files at :53 files
  -- at :51 when it is busy.
  --
  -- Null report_minute means we have not seen enough of that station's
  -- history to know its filing minute, and then the honest answer is the
  -- whole feed rather than an empty one.
  max(o.temp_c) filter (where c.report_minute is null
                           or abs(extract(minute from o.valid_at)::int
                                  - c.report_minute) <= 4)     as max_c_hourly,
  max(o.temp_f) filter (where c.report_minute is null
                           or abs(extract(minute from o.valid_at)::int
                                  - c.report_minute) <= 4)     as max_f_hourly,
  count(*) filter (where c.report_minute is null
                      or abs(extract(minute from o.valid_at)::int
                             - c.report_minute) <= 4)          as n_hourly
from weather_observations o
join cities c on c.city_key = o.city_key
where o.temp_c is not null
group by 1, 2;

comment on view v_station_day_max is
  'The station feed''s daily maximum per city-day, in both units. max_f is the station''s native reading; max_c is the conversion. Comparing an F market''s bands against max_f rather than against a Celsius round trip is worth 2.9 points of settlement agreement.';


-- --------------------------------------------------------------------------
-- 2. Agreement, per city.
--
--    One row per city: of the ladders the venue has settled, how often does
--    the band it declared the winner actually contain the maximum we
--    measured, and when it does not, which way are we wrong.
--
--    `bias` is ours minus theirs in BANDS, not degrees, because a degree
--    means something different in a 1C market and a 2F one. Positive means
--    we read above the winning band - our thermometer is running hot
--    relative to whatever the venue settled on.
-- --------------------------------------------------------------------------
create or replace view v_settlement_agreement as
with win as (
  select f.band_id, f.city_key, f.for_date,
         f.band_lo, f.band_hi, f.open_low, f.open_high,
         m.unit
    from v_coherent_band_outcome f
    join bands bd  on bd.band_id  = f.band_id
    join markets m on m.market_id = bd.market_id
   where f.settled_yes
     and f.band_lo is not null and f.band_hi is not null
),
j as (
  select w.*,
         -- The routine report, not the whole feed: this view answers "did we
         -- name the band the venue settled on", and the venue settles on the
         -- hourly column.
         case when w.unit = 'F' then s.max_f_hourly else s.max_c_hourly end as ours
    from win w
    left join v_station_day_max s
           on s.city_key = w.city_key and s.for_date = w.for_date
),
scored as (
  select j.*,
         band_contains(band_lo, band_hi, open_low, open_high, ours)     as agrees,
         case when ours is null then null
              when ours >= band_hi then  1
              when ours <  band_lo then -1
              else 0 end                                               as direction,
         nullif(band_hi - band_lo, 0)                                   as band_width
    from j
)
select
  city_key,
  count(*)                                                   as settled_ladders,
  count(ours)                                                as with_a_reading,
  count(*) filter (where agrees)                             as agreed,
  case when count(ours) > 0
       then round(100.0 * count(*) filter (where agrees) / count(ours), 1)
  end                                                        as agreement_pct,
  count(*) filter (where direction =  1)                     as we_read_above,
  count(*) filter (where direction = -1)                     as we_read_below,
  -- Signed, in bands. Rounded to two places because a tenth of a band is
  -- below what this sample can resolve.
  round(avg(case when direction = 1  then  (ours - band_hi) / band_width
                 when direction = -1 then -((band_lo - ours) / band_width)
                 else 0 end), 2)                             as bias_bands,
  case
    when count(ours) = 0                                     then 'no readings - nothing to compare'
    when count(ours) < 10                                    then 'too few settled ladders to judge - under 10'
    when 100.0 * count(*) filter (where agrees) / count(ours) >= 90
         then 'trustworthy - the observation names the winning band 9 times in 10'
    when 100.0 * count(*) filter (where agrees) / count(ours) >= 70
         then 'usable with a haircut - it misses about one in four, by one band'
    else      'do not trade this city on the observation - it disagrees more often than not'
  end                                                        as verdict,
  -- The number a strategy should actually multiply by. A running-max rule in
  -- a city we get right half the time is half a rule.
  case when count(ours) >= 10
       then round(1.0 * count(*) filter (where agrees) / count(ours), 4)
  end                                                        as observation_trust
from scored
group by city_key;

comment on view v_settlement_agreement is
  'Per city, how often the maximum we measured falls inside the band the venue declared the winner. observation_trust is that rate as a fraction, and it is the weight any strategy reading the running maximum should carry - the roster is not uniform and a rule is only as good as the thermometer under it.';


-- --------------------------------------------------------------------------
-- 3. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_station_day_max to %I', r);
      execute format('grant select on v_settlement_agreement to %I', r);
    end if;
  end loop;
end
$ad4$;
