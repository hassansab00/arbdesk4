-- ===========================================================================
-- THE VENUE READS THE HOURLY COLUMN. WE WERE FEEDING IT FIVE-MINUTE DATA.
--
-- Every market's rules_text names its own resolution source, and the wording
-- is exact:
--
--   "the temperature range that contains the highest temperature recorded by
--    NOAA at the London City Airport Station in degrees Celsius on 22 Sep '26
--    ... specifically the highest reading under the "Temp" column for all
--    times on this day, available here:
--    https://www.weather.gov/wrh/timeseries?site=eglc"
--
-- That page is the station's ROUTINE HOURLY report. Extracting the site id
-- from all 48 active cities' rules and comparing it to cities.icao: 48 of 48
-- match, so we have never had the wrong station. What we have is the wrong
-- SAMPLE of it.
--
-- IEM returns every observation a station files. For the eleven US ASOS
-- sites that is about 125 a day - the five-minute feed plus specials - and
-- for the rest it is 24, the routine hourly METAR and nothing else. So the
-- roster splits, and it splits the two opposite ways:
--
--     feed             ladders   our max lands in the winning band
--     hourly (24/day)      507   90.0%    and when it misses we read BELOW
--     5-minute (125/day)    99   76.8%    and when it misses we read ABOVE
--
--   US cities: 16 misses, 16 of them us reading HIGH, none low. A
--   five-minute spike the "Temp" column never shows is still a reading to
--   max(), and one spike moves the day into the next band.
--
--   Everyone else: 30 misses, 27 of them us reading LOW. With 24 samples the
--   true peak falls between reports. That one is not fixable from this feed
--   and is not what this migration claims to fix.
--
-- TAKING THE MAXIMUM OVER THE ROUTINE REPORT ONLY - the reading at the
-- station's own filing minute, which is what the page prints - moves the
-- five-minute cities from 76.8% to 90.9%, and costs the hourly ones 0.7
-- points (3 ladders in 460) because for them the filter is almost a no-op.
-- Weighted across the roster: 88.5% -> 90.2%.
--
-- HOW THE FILING MINUTE IS KNOWN. A station files its routine METAR at the
-- same minute every hour - :50, :52, :55 - set by the site, not the clock.
-- It is read from the station's own history rather than assumed, stored on
-- the city, and re-read by the same statement whenever this runs.
--
-- WHAT THIS DOES NOT DO. It does not touch a single stored observation.
-- Every reading IEM has ever returned stays exactly where it is, including
-- the five-minute ones - they are the right input for the trend, the slope
-- and the running maximum during the day, and the wrong input for one
-- question only: which band settled. This adds the column that lets the two
-- questions have two answers.
--
-- Idempotent: one nullable column, and a re-read of the filing minute.
-- ===========================================================================

alter table public.cities add column if not exists report_minute smallint;

comment on column public.cities.report_minute is
  'The minute past the hour at which this city''s station files its routine METAR. The venue settles on that report; the five-minute feed between them is ours alone.';

do $ad4$
begin
  if to_regclass('public.weather_observations') is null then
    raise notice 'no weather_observations - leaving report_minute unset';
    return;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'weather_observations'
                    and column_name = 'valid_at') then
    return;
  end if;

  update public.cities c
     set report_minute = m.modal_minute
    from (
      select o.city_key,
             mode() within group (order by extract(minute from o.valid_at)::int) as modal_minute,
             count(*) as n
        from public.weather_observations o
       where o.valid_at > now() - interval '90 days'
       group by o.city_key
      having count(*) >= 48        -- two days of hourly reports, or it is a guess
    ) m
   where m.city_key = c.city_key
     and c.report_minute is distinct from m.modal_minute;
end
$ad4$;
