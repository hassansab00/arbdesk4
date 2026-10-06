-- ===========================================================================
-- EACH CITY HAS A DAILY STATUS (WXPredict build, wave F, step F.2; Hassan,
-- 6 Oct: "Give each city a daily status ... Display the reason").
--
--   candidate     fresh, complete data; the forecasts agree; enough settled
--                 history at the checkpoint
--   watch         the forecasts disagree, or too few model forecasts are in
--                 to tell (a day's lead-1 rows arrive with the nightly run)
--   insufficient  too little settled history at this checkpoint
--   unavailable   no open market, stale station reports or no usable forecast
--
-- The worst state that applies wins (unavailable, insufficient, watch,
-- candidate); every reason that applies is listed, the deciding one first.
--
-- WHY THE WATCH RULE IS ONE RULE. tools/focus/status_conditions.py tested the
-- three conditions Hassan named on 19,227 city-days before the sealed test
-- (14 Jul 2025 - 30 Aug 2026), by a rule fixed before computing: a condition
-- counts when, on its top-fifth days, the bias-corrected day-ahead forecast's
-- bucket hit rate is lower, with the 98.75% interval of the difference wholly
-- below zero (date-clustered bootstrap). Committed output:
-- data/eval/focus/status_conditions_2026-10-06.json.
--
--   the seven models' lead-1 maxima span >= 4.8 C   25.2% against 33.4%   adopted
--   cloud near half cover (uncertain clearance)     33.4% against 31.3%   not adopted
--   pressure change since the day before (a front) 31.3% against 31.9%   not adopted
--   wind speed change since the day before          30.3% against 32.1%   not adopted
--
-- So a city is on Watch when its forecasts disagree, and the page says that
-- cloud and wind were tested and did not predict misses. Live cloud and wind
-- forecasts are not recorded for most cities anyway (weather_forecast_features
-- holds the NWS cities only).
--
-- THE THRESHOLDS ARE A VERSIONED RECORD, not constants in the view: one
-- append-only row per version in city_status_rules, carrying the study's
-- sha256, and every status row says which version decided it. The status-v1
-- thresholds and where each comes from:
--   disagreement_c 4.8, min_models 4, lead_days 1: the study above (whole-day
--     maxima, as weather_forecast_models.forecast_max_c holds them live)
--   min_settled_days 10: stated, not fitted. Three hits in ten days has a
--     95% Wilson interval of 11%-60%; fewer days say less than that
--   station_stale_hours 3: measured 6 Oct, over 7 days 1 of 12,157 gaps
--     between consecutive IEM reports exceeded 3 h
--   forecast_stale_hours 24: open_meteo_forecast runs every 3 h; a day
--     without one is a broken ingest
-- None is learned, so none moves by itself (Rule 11): a change is a new
-- version with its own evidence.
--
-- SEASONAL MEMBERSHIP NEVER ENTERS. Nothing here reads focus_sets, and nothing
-- that prices reads this view (tests/test_city_status.py).
-- ===========================================================================
begin;

create table if not exists public.city_status_rules (
  version       text primary key check (version ~ '^status-v[0-9]+$'),
  params        jsonb not null,
  method_path   text not null,
  method_sha256 text not null check (method_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at   timestamptz not null default clock_timestamp(),
  check (params ?& array['disagreement_c', 'min_models', 'lead_days', 'min_settled_days',
                         'station_stale_hours', 'forecast_stale_hours'])
);

comment on table public.city_status_rules is
  'The thresholds of the daily city status, one append-only row per version (WXPredict build F.2). method_path / method_sha256 name the committed study each version comes from. v_city_status reads the newest.';

drop trigger if exists city_status_rules_immutable on public.city_status_rules;
create trigger city_status_rules_immutable before update or delete on public.city_status_rules
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists city_status_rules_no_truncate on public.city_status_rules;
create trigger city_status_rules_no_truncate before truncate on public.city_status_rules
  for each statement execute function arbdesk_private.immutable_record();

alter table public.city_status_rules enable row level security;
revoke all on public.city_status_rules from public, anon, authenticated, service_role;
grant select on public.city_status_rules to anon, authenticated;
grant select, insert on public.city_status_rules to service_role;
drop policy if exists city_status_rules_read on public.city_status_rules;
create policy city_status_rules_read on public.city_status_rules for select
  to anon, authenticated, service_role using (true);
drop policy if exists city_status_rules_insert on public.city_status_rules;
create policy city_status_rules_insert on public.city_status_rules for insert
  to service_role with check (true);

insert into public.city_status_rules (version, params, method_path, method_sha256)
values ('status-v1',
        jsonb_build_object(
          'disagreement_c', 4.8, 'min_models', 4, 'lead_days', 1,
          'min_settled_days', 10, 'station_stale_hours', 3, 'forecast_stale_hours', 24,
          'tested_not_adopted', jsonb_build_array('cloud_uncertainty', 'pressure_change', 'wind_change')),
        'data/eval/focus/status_conditions_2026-10-06.json',
        'ee4d3f1007d4b526c711fdc86b2571a666b0db28137a65d681f377925be3d55a')
on conflict (version) do nothing;

-- ---------------------------------------------------------------------------
-- v_prediction_hindsight GAINS ONE COLUMN, appended (F.5). The focus
-- comparison scores the mean probability each call put on the winning bucket
-- (docs/FOCUS_PREREG.md, "Metrics"), and both halves of this view already
-- read it: v_city_hit_history.model_prob_on_winner and
-- v_checkpoint_calls.model_prob_on_winner. Every existing column is the same
-- expression as 20261004140000; CREATE OR REPLACE keeps the grants and the
-- summary view built on it. Proven before applying: in one REPEATABLE READ
-- snapshot, the old columns of the new definition EXCEPT ALL the live view
-- are empty in both directions.
-- ---------------------------------------------------------------------------
do $mig$
begin
  if to_regclass('public.v_prediction_hindsight') is null or to_regclass('public.v_checkpoint_calls') is null then
    raise notice 'v_prediction_hindsight not installed here; nothing to replace';
    return;
  end if;
  execute $v$
create or replace view public.v_prediction_hindsight as
select h.city_key,
       h.for_date,
       h.model_call                                    as predicted_band,
       round(100::numeric * h.model_call_prob, 1)      as predicted_pct,
       h.winner                                        as actual_band,
       round(h.observed_max_c, 1)                      as observed_max_c,
       -- the priced centre, as the checkpoint rows below (audit repair 3)
       round(h.centre_c, 1)                            as forecast_max_c,
       round(h.observed_max_c - h.centre_c, 1)         as forecast_error_c,
       h.model_hit                                     as hit,
       round(h.sigma_c, 2)                             as stated_sigma_c,
       h.regime_label,
       'banked'::text                                  as outcome_source,
       'day_ahead'::text                               as called_when,
       0                                               as call_order,
       false                                           as after_peak,
       h.called_at,
       h.market_call                                   as market_band,
       h.market_hit,
       -- appended (4 Oct): the checkpoint's scheduled local time and the
       -- engine version that made the call; the day-ahead call has neither
       null::timestamp                                 as scheduled_local,
       null::text                                      as engine_version,
       -- appended (6 Oct, F.5): the probability the call put on the bucket
       -- that won, which docs/FOCUS_PREREG.md scores beside the hit rate
       h.model_prob_on_winner                          as prob_on_winner
  from public.v_city_hit_history h
union all
select c.city_key,
       c.for_date,
       c.called_band,
       round(100::numeric * c.called_prob, 1),
       c.winner_band,
       round(o.observed_max_c, 1),
       round(c.centre_c, 1),
       round(o.observed_max_c - c.centre_c, 1),
       c.hit,
       round(c.sigma_c, 2),
       null::text,
       'venue'::text,
       c.checkpoint,
       c.checkpoint_order,
       c.after_peak,
       c.decided_at,
       c.market_band,
       c.market_hit,
       c.local_decision_time,
       c.engine_version,
       c.model_prob_on_winner
  from public.v_checkpoint_calls c
  left join public.v_city_hit_history o on o.city_key = c.city_key and o.for_date = c.for_date
 where c.first_call
  $v$;
end
$mig$;

-- The status view reads the market, station, forecast and hindsight tables; a
-- database without them (the contract fixture) gets the rules table only.
do $mig$
begin
  if to_regclass('public.cities') is null or to_regclass('public.markets') is null
     or to_regclass('public.weather_observations') is null
     or to_regclass('public.weather_forecasts') is null
     or to_regclass('public.weather_forecast_models') is null
     or to_regclass('public.v_prediction_hindsight') is null then
    raise notice 'v_city_status: a relation it reads is not installed here; not created';
    return;
  end if;
  execute $v$
create or replace view public.v_city_status with (security_invoker = false) as
with r as (
  select version,
         (params ->> 'disagreement_c')::numeric       as disagreement_c,
         (params ->> 'min_models')::int               as min_models,
         (params ->> 'lead_days')::int                as lead_days,
         (params ->> 'min_settled_days')::int         as min_settled_days,
         (params ->> 'station_stale_hours')::numeric  as station_stale_hours,
         (params ->> 'forecast_stale_hours')::numeric as forecast_stale_hours
    from public.city_status_rules
   order by recorded_at desc, version desc
   limit 1
),
-- each active city's own today and tomorrow
d as (
  select c.city_key, c.unit, (now() at time zone coalesce(c.timezone, 'UTC'))::date + k.k as target_date
    from public.cities c
    cross join (values (0), (1)) k(k)
   where coalesce(c.status, 'active') = 'active'
),
cp(checkpoint, checkpoint_order, after_peak) as (
  values ('day_ahead', 0, false), ('d1_eve', 1, false), ('morning', 2, false), ('noon', 3, false),
         ('prepeak_2h', 4, false), ('prepeak_1h', 5, false), ('postpeak_1h', 6, true)
),
mkt as (
  select m.city_key, m.resolution_date,
         bool_or(not coalesce(m.closed, false)) as any_open
    from public.markets m
   where m.resolution_date >= current_date - 1
   group by 1, 2
),
station as (
  select o.city_key, max(o.valid_at) as last_report
    from public.weather_observations o
   where o.source in ('IEM', 'NWS') and o.valid_at > now() - interval '2 days'
   group by 1
),
fc as (
  select f.city_key, f.for_date, max(f.run_at) as newest_run
    from public.weather_forecasts f
   where f.model = 'open_meteo_forecast' and f.for_date >= current_date - 1
   group by 1, 2
),
models as (
  select x.city_key, x.for_date, count(*) as n_models,
         max(x.forecast_max_c) - min(x.forecast_max_c) as span_c
    from (select distinct on (w.city_key, w.for_date, w.model) w.city_key, w.for_date, w.model, w.forecast_max_c
            from public.weather_forecast_models w, r
           where w.lead_days = r.lead_days and w.for_date >= current_date - 1
             and w.forecast_max_c is not null
           order by w.city_key, w.for_date, w.model, w.run_at desc) x
   group by 1, 2
),
settled as (
  select h.city_key, h.called_when as checkpoint, count(*) filter (where h.hit is not null) as days
    from public.v_prediction_hindsight h
   group by 1, 2
),
facts as (
  select d.city_key, d.target_date, cp.checkpoint, cp.checkpoint_order, cp.after_peak,
         r.version, r.min_settled_days, r.disagreement_c, r.min_models,
         mkt.resolution_date is not null as market_listed,
         coalesce(mkt.any_open, false) as market_open,
         station.last_report,
         round((extract(epoch from now() - station.last_report) / 3600.0)::numeric, 1) as station_age_h,
         station.last_report is null
           or now() - station.last_report > make_interval(secs => (r.station_stale_hours * 3600)::double precision)
           as station_stale,
         fc.newest_run as forecast_run_at,
         fc.newest_run is null
           or now() - fc.newest_run > make_interval(secs => (r.forecast_stale_hours * 3600)::double precision)
           as forecast_stale,
         coalesce(models.n_models, 0)::int as n_models,
         round(models.span_c, 1) as models_span_c,
         coalesce(settled.days, 0)::int as settled_days
    from d
    cross join cp
    cross join r
    left join mkt     on mkt.city_key = d.city_key and mkt.resolution_date = d.target_date
    left join station on station.city_key = d.city_key
    left join fc      on fc.city_key = d.city_key and fc.for_date = d.target_date
    left join models  on models.city_key = d.city_key and models.for_date = d.target_date
    left join settled on settled.city_key = d.city_key and settled.checkpoint = cp.checkpoint
),
why as (
  select f.*,
         array_remove(array[
           case when not f.market_listed then 'No market listed for this day'
                when not f.market_open then 'Market closed' end,
           case when f.last_report is null then 'No station report in two days'
                when f.station_stale then format('Station reports stale (last %s h ago)', f.station_age_h) end,
           case when f.forecast_stale then 'No usable forecast in the last day' end
         ], null) as unavailable_why,
         case when f.settled_days < f.min_settled_days
              then format('Too little settled history at this checkpoint (%s of %s days)',
                          f.settled_days, f.min_settled_days) end as insufficient_why,
         array_remove(array[
           case when f.n_models >= f.min_models and f.models_span_c >= f.disagreement_c
                then format('Forecasts disagree (the models span %s C)', f.models_span_c) end,
           case when f.n_models = 0
                then 'Model forecasts for this day not in yet (the nightly run brings them)'
                when f.n_models < f.min_models
                then format('Too few model forecasts to judge agreement (%s of 7)', f.n_models) end
         ], null) as watch_why
    from facts f
)
select w.city_key, w.target_date, w.checkpoint, w.checkpoint_order, w.after_peak,
       case when cardinality(w.unavailable_why) > 0 then 'unavailable'
            when w.insufficient_why is not null then 'insufficient'
            when cardinality(w.watch_why) > 0 then 'watch'
            else 'candidate' end as status,
       w.unavailable_why || array_remove(array[w.insufficient_why], null) || w.watch_why as reasons,
       coalesce((w.unavailable_why || array_remove(array[w.insufficient_why], null) || w.watch_why)[1],
                format('Fresh data, forecasts agree (span %s C), %s settled days',
                       w.models_span_c, w.settled_days)) as reason,
       w.market_open, w.station_age_h, w.forecast_run_at,
       w.n_models, w.models_span_c, w.disagreement_c,
       w.settled_days, w.min_settled_days,
       w.version as rules_version
  from why w
  $v$;
  execute $v$
comment on view public.v_city_status is
  'Each active city''s daily status for its local today and tomorrow, at every checkpoint (day_ahead and the six intraday ones): candidate, watch, insufficient or unavailable, with every reason that applies (WXPredict build F.2). Thresholds from the newest city_status_rules version, named in rules_version. Never reads focus_sets; nothing that prices reads it.';
revoke all on public.v_city_status from public, anon, authenticated;
grant select on public.v_city_status to anon, authenticated, service_role;
  $v$;
end
$mig$;

commit;
