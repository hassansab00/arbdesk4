-- AD4 55 - give the last 17 cities the coordinates their forecasts need.
--
-- WHY THIS EXISTS
--
-- `cities` holds 54 rows, all status='active', and every one of them has live
-- Polymarket markets. Only 37 had a latitude.
--
-- Both forecast feeds are addressed by lat/lon:
--
--   P1.5 Open-Meteo   Load cities ... &latitude=not.is.null
--   P1.3 NWS          Load cities ... &latitude=not.is.null
--
-- so the other 17 were filtered out before either workflow saw them. Not
-- failing - filtered, silently, which is why it never showed up as an error.
-- Fifteen of those cities were carrying 18-20 open markets apiece resolving
-- the next day: Toronto, Paris, Seoul, Moscow, Hong Kong, Manila, Buenos
-- Aires, Ankara, Kuala Lumpur, Shenzhen, Zhengzhou, Qingdao, Panama City,
-- Helsinki, Jinan. Roughly 290 live markets the desk was pricing with no
-- forward temperature forecast of any kind.
--
-- WHERE THESE NUMBERS COME FROM
--
-- Each city already names the station its market SETTLES on, in `cities.icao`.
-- The coordinate written here is that airport's own published position, so the
-- forecast is for the instrument the market is scored against rather than for
-- a city centre some distance away. That distinction is not pedantic: Paris
-- settles on LFPB (Le Bourget, north of the city), not Charles de Gaulle and
-- not central Paris, and Moscow settles on UUWW (Vnukovo, south-west), not
-- Sheremetyevo.
--
-- Hong Kong is the one exception and is marked as such below.
--
-- HOW TO CHECK THEM, BECAUSE COORDINATES ARE EASY TO GET WRONG QUIETLY
--
-- A misplaced coordinate does not error - it returns a plausible temperature
-- for the wrong place, which is the worst failure mode this project has.
-- Fifteen of these cities already have IEM observations at the same station,
-- so the check is direct: run P1.5, then compare each city's forecast for
-- today against what its station actually recorded. The query at the bottom
-- of this file does that. A city whose forecast sits several degrees off its
-- own observed range has a bad coordinate.
--
-- Idempotent: only fills a NULL, so re-running cannot overwrite a correction.

begin;

update public.cities set latitude = v.lat, longitude = v.lon
from (values
  -- city_key,        lat,        lon,          station
  ('buenos_aires',  -34.8222,  -58.5358),   -- SAEZ  Ezeiza
  ('manila',         14.5086,  121.0194),   -- RPLL  Ninoy Aquino
  ('panama_city',     8.9733,  -79.5556),   -- MPMG  Marcos A. Gelabert
  ('toronto',        43.6777,  -79.6248),   -- CYYZ  Pearson
  ('helsinki',       60.3172,   24.9633),   -- EFHK  Helsinki-Vantaa
  ('jinan',          36.8572,  117.2157),   -- ZSJN  Yaoqiang
  ('kuala_lumpur',    2.7456,  101.7072),   -- WMKK  KLIA
  ('moscow',         55.5915,   37.2615),   -- UUWW  Vnukovo
  ('seoul',          37.4602,  126.4407),   -- RKSI  Incheon
  ('shenzhen',       22.6393,  113.8108),   -- ZGSZ  Bao'an
  ('zhengzhou',      34.5197,  113.8408),   -- ZHCC  Xinzheng
  ('ankara',         40.1281,   32.9951),   -- LTAC  Esenboga
  ('paris',          48.9694,    2.4414),   -- LFPB  Le Bourget
  ('qingdao',        36.3614,  120.0864),   -- ZSQD  Jiaodong
  ('jakarta',        -6.2666,  106.8909),   -- WIHH  Halim Perdanakusuma
  ('lagos',           6.5774,    3.3212),   -- DNMM  Murtala Muhammed
  -- HONG KONG IS DIFFERENT and is the one row here not taken from an airport.
  -- Its `icao` is null, so there is no settlement station recorded to copy.
  -- This is the Hong Kong Observatory headquarters, which is the reading HK
  -- temperature records are quoted from. If the market turns out to settle on
  -- VHHH (Chek Lap Kok) instead, move this to 22.3080, 113.9185 - the airport
  -- runs about a degree cooler than the Observatory on a hot day, which is
  -- exactly the width of a temperature band.
  ('hong_kong',      22.3020,  114.1740)
) as v(city_key, lat, lon)
where cities.city_key = v.city_key
  and cities.latitude is null;

-- Hong Kong also had no timezone, and P1.5 buckets a daily maximum by the
-- city's own calendar day.
update public.cities
   set timezone = 'Asia/Hong_Kong'
 where city_key = 'hong_kong' and timezone is null;

commit;

-- ---------------------------------------------------------------------------
-- THE CHECK. Run this AFTER P1.5 has run once.
--
-- Every row should show a forecast within a few degrees of what that city's
-- own station has been recording. A large gap means the coordinate below is
-- pointing somewhere else.
select c.city_key,
       c.icao,
       round(f.forecast_max_c::numeric, 1)          as forecast_today_c,
       round(max(o.temp_c)::numeric, 1)             as observed_max_3d_c,
       round((f.forecast_max_c - max(o.temp_c))::numeric, 1) as gap_c
  from public.cities c
  join public.weather_forecasts f
    on f.city_key = c.city_key
   and f.model = 'open_meteo_forecast'
   and f.for_date = current_date
  left join public.weather_observations o
    on o.city_key = c.city_key
   and o.valid_at > now() - interval '3 days'
 where c.city_key in ('buenos_aires','manila','panama_city','toronto','helsinki',
                      'jinan','kuala_lumpur','moscow','seoul','shenzhen',
                      'zhengzhou','ankara','paris','qingdao','jakarta','lagos',
                      'hong_kong')
 group by c.city_key, c.icao, f.forecast_max_c
 order by abs(f.forecast_max_c - max(o.temp_c)) desc nulls last;
