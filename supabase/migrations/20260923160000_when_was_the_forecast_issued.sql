-- ===========================================================================
-- WHEN WAS THE FORECAST ISSUED? (plan v2 P2.6)
--
-- weather_forecasts.run_at means three different things, one per writer.
-- Read from the writers (n8n/P1.3, P1.4, P1.5 templates;
-- scripts/ingest_forecasts.py) and measured on the live table, 23 Sep:
--
--   source                     rows    run_at is
--   api.weather.gov           2,609    NWS's own updateTime - the issuance.
--                                      observed_at, our fetch, is 2.28 h later
--                                      on average.
--   open-meteo               41,744    `now` at fetch time (run_at =
--                                      observed_at on every row). The API does
--                                      not state the model run, so the fetch
--                                      is the latest time it can have been
--                                      issued.
--   open-meteo-previous-runs 32,830    SYNTHETIC: midnight UTC of
--                                      for_date - lead. For a city west of UTC
--                                      that is the previous local evening, and
--                                      on 10,920 rows the stated lead does not
--                                      match the local issue date it implies.
--                                      Which run each hourly value came from is
--                                      not verified (open-meteo.com is not
--                                      reachable from the build machine; plan
--                                      P7.2 carries that check).
--
-- The plan's rule: the true issue time where it is known, otherwise the
-- ingest time, and say which. That rule is a function of `source` alone, so
-- it is a view, not a backfilled column: rewriting 77,183 rows would add
-- about 16 MB of dead tuples to a database at 131% of its tier.
--
--   issued_at          the time the forecast can first have been known
--   issued_at_source   provider_update_time | ingest_time |
--                      ingest_time_true_issue_unverified | run_at_unclassified
--   issued_local_date  issued_at on the city's calendar
--   issued_lead_days   for_date - issued_local_date, the lead as the city saw it
--   same_day_issue     issued on or after the local day it forecasts
--   (the last three are null on previous-runs rows: their issued_at is an
--   ingest bound weeks after the fact, and a date derived from it would call
--   every one of the 32,830 "same day" - measured in a rolled-back rehearsal)
--
-- CONSEQUENCE, stated because it is deliberate: an as-of read (backtest,
-- replay, P4 checkpoints) now sees a previous-runs row only from the moment
-- it was ingested. Until P7.2 proves which run each value came from, a
-- backtest cannot use a forecast whose issue time nobody can show.
--
-- security_invoker: it reads with the caller's own rights on
-- weather_forecasts and cities, and grants nothing new.
-- ===========================================================================

create or replace view public.v_forecast_issued
with (security_invoker = true)
as
select
  f.forecast_id, f.city_key, f.model, f.run_at, f.observed_at, f.for_date,
  f.lead_days, f.forecast_max_c, f.variables, f.source,
  i.issued_at,
  i.issued_at_source,
  -- Unknown, not derived, where issued_at is only an ingest bound on a row
  -- ingested weeks later: that bound is right for an as-of cut and says
  -- nothing about which day the forecast was made on.
  case when i.true_time_known then (i.issued_at at time zone c.timezone)::date end
                                                              as issued_local_date,
  case when i.true_time_known then f.for_date - (i.issued_at at time zone c.timezone)::date end
                                                              as issued_lead_days,
  case when i.true_time_known then (i.issued_at at time zone c.timezone)::date >= f.for_date end
                                                              as same_day_issue
from public.weather_forecasts f
left join public.cities c on c.city_key = f.city_key
cross join lateral (
  select
    case f.source
      when 'open-meteo-previous-runs' then f.observed_at
      else f.run_at
    end as issued_at,
    case f.source
      when 'api.weather.gov'          then 'provider_update_time'
      when 'open-meteo'               then 'ingest_time'
      when 'open-meteo-previous-runs' then 'ingest_time_true_issue_unverified'
      else 'run_at_unclassified'
    end as issued_at_source,
    f.source is distinct from 'open-meteo-previous-runs' as true_time_known
) i;

comment on view public.v_forecast_issued is
  'weather_forecasts with the time each forecast can first have been known (plan v2 P2.6). Every as-of filter reads issued_at, never run_at: run_at is NWS''s issuance, Open-Meteo''s fetch time, or a synthetic midnight, depending on the source.';

revoke all on public.v_forecast_issued from public, anon, authenticated;
grant select on public.v_forecast_issued to service_role;
