-- ===========================================================================
-- ad4_62_settled_history_ungated.sql - THE SETTLED HISTORY, BACK ON THE PAGE.
--
-- Safe to run any time. Creates two read-only views. Changes no data, alters
-- nothing that already exists.
--
-- /predictive showed no settled history at all: the scatter had no dots, the
-- decay curve no line, the hit-rate table no rows. The archive meanwhile held
--
--     fact_forecast_outcome   2,268 rows, 49 cities, 11 days
--     fact_band_outcome       5,605 rows
--
-- Phase 2A introduced a verification gate: v_verified_fact_forecast_outcome
-- INNER JOINs v_verified_weather_outcomes, which reads
-- weather_resolution_evidence - and that table has ZERO rows, because the
-- capture that fills it has never run. 2,268 x 0 = 0. Everything downstream
-- (v_prediction_scorecard, v_forecast_convergence, v_calibration,
-- v_edge_realisation, v_edge_scaling) inherits the emptiness.
--
-- The gate is a good rule. Requiring an independent record of the settling
-- observation before scoring against it is exactly right, and these views do
-- NOT change it: the Phase 2A views are untouched and still mean what they
-- said.
--
-- But an empty page is not more truthful than a labelled one. The desk has
-- been scoring against its own observed maximum since it started; that is the
-- same figure, and hiding it entirely until a second source agrees loses
-- eleven days of real evidence to show nothing instead.
--
-- So these publish the SAME measurements over the SAME source table, with the
-- corroboration carried as a column rather than used as a filter:
--
--     verified        per row - is this outcome independently corroborated
--     n_verified      per group - how many of its days are
--     fully_verified  per group - all of them
--
-- The page prints the ratio above the panels. When the evidence capture runs,
-- these views go green on their own and nothing needs changing.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.fact_forecast_outcome') is null then
    raise exception 'ad4_62 needs fact_forecast_outcome - run sql/ad4_18_databank.sql first';
  end if;
  if to_regclass('public.v_verified_weather_outcomes') is null then
    raise exception 'ad4_62 needs v_verified_weather_outcomes - apply the Phase 2A migrations first';
  end if;
end $ad4$;

-- --------------------------------------------------------------------------
-- Accuracy per city, model and lead. Same aggregation as
-- v_prediction_scorecard, over every settled outcome instead of only the
-- corroborated ones.
-- --------------------------------------------------------------------------
create or replace view v_prediction_scorecard_all as
select city_key, model, lead_days,
       count(*)::int                                     as n_days,
       count(*) filter (where verified)::int              as n_verified,
       bool_and(verified)                                 as fully_verified,
       round(avg(abs_error_c), 3)                         as mae_c,
       round(avg(error_c), 3)                             as bias_c,
       round(stddev_samp(error_c), 3)                     as error_sd_c,
       round(max(abs_error_c), 2)                         as worst_c,
       -- Hit rate is floor-to-floor: did the day land in the bucket the
       -- forecast pointed at. It is the only accuracy the market pays for.
       round(100.0 * count(*) filter (where floor(forecast_max_c) = floor(observed_max_c))::numeric
             / nullif(count(*), 0)::numeric, 1)           as hit_rate_pct,
       round(100.0 * count(*) filter (where abs_error_c <= 1.0)::numeric
             / nullif(count(*), 0)::numeric, 1)           as within_1c_pct,
       min(for_date)                                      as since,
       max(for_date)                                      as until
from (
  select f.*,
         exists (select 1 from v_verified_weather_outcomes w
                  where w.city_key = f.city_key and w.for_date = f.for_date
                    and abs(f.observed_max_c - w.observed_max_c) <= 0.01) as verified
  from fact_forecast_outcome f
  where f.for_date >= current_date - 365
) x
group by city_key, model, lead_days
having count(*) >= 5
order by city_key, model, lead_days;

comment on view v_prediction_scorecard_all is
  'Forecast accuracy per city, model and lead over every settled outcome, with corroboration reported (n_verified / fully_verified) rather than used as a filter. v_prediction_scorecard is the corroborated-only version and is unchanged.';

-- --------------------------------------------------------------------------
-- Forecast against what the day actually did.
-- --------------------------------------------------------------------------
drop view if exists v_forecast_convergence_all;
create view v_forecast_convergence_all as
with latest as (
  select distinct on (city_key, for_date, model, lead_days)
         city_key, for_date, model, lead_days, forecast_max_c, run_at
    from weather_forecasts
   where for_date >= current_date - 45
     and for_date <= current_date + 16
     and forecast_max_c is not null
   order by city_key, for_date, model, lead_days, run_at desc
),
observed as (
  select f.city_key, f.for_date,
         max(f.observed_max_c) as observed_max_c,
         bool_or(exists (select 1 from v_verified_weather_outcomes w
                          where w.city_key = f.city_key and w.for_date = f.for_date
                            and abs(f.observed_max_c - w.observed_max_c) <= 0.01)) as verified
    from fact_forecast_outcome f
   where f.for_date >= current_date - 45
   group by f.city_key, f.for_date
)
select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at,
       o.observed_max_c,
       case when o.observed_max_c is not null
            then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
       l.for_date < current_date        as is_past,
       o.observed_max_c is not null     as is_settled,
       coalesce(o.verified, false)      as verified
from latest l
left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;

comment on view v_forecast_convergence_all is
  'Every forecast in the window against what the day actually did, settled or not, with corroboration carried as a column. v_forecast_convergence is the corroborated-only version and is unchanged.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_prediction_scorecard_all to %I', r);
      execute format('grant select on v_forecast_convergence_all to %I', r);
    end if;
  end loop;
end $ad4$;

do $ad4$
declare v_groups int; v_settled int; v_verified int;
begin
  select count(*) into v_groups from v_prediction_scorecard_all;
  select count(*) filter (where is_settled), count(*) filter (where verified)
    into v_settled, v_verified from v_forecast_convergence_all;
  raise notice 'ad4_62: % scorecard group(s), % settled convergence row(s), % corroborated',
               v_groups, v_settled, v_verified;
  if v_settled > 0 and v_verified = 0 then
    raise notice 'ad4_62: nothing is corroborated yet - weather_resolution_evidence is empty. The page says so above the panels.';
  end if;
end $ad4$;
