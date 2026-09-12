-- Phase 1C: truthful, read-only operational readiness.
--
-- This migration never updates collected evidence.  It composes the public
-- market, forecast, book, probability and edge streams into small views that
-- answer one question: can this city/date be evaluated now, and if not, why?
begin;

create or replace view public.v_city_day_readiness
with (security_invoker = true)
as
with
target_dates as (
  select (current_date + g.day_offset)::date as resolution_date
  from generate_series(0, 1) as g(day_offset)
),
roster as (
  select c.city_key, c.display_name, c.timezone, c.latitude, c.longitude,
         d.resolution_date
  from public.cities c
  cross join target_dates d
  where coalesce(c.status, 'active') = 'active'
),
market_scope as (
  select m.market_id, m.city_key, m.resolution_date
  from public.markets m
  join target_dates d using (resolution_date)
  where not coalesce(m.closed, false)
),
band_scope as (
  select b.band_id, m.city_key, m.resolution_date,
         case
           when coalesce(b.open_low, false) and b.band_hi is not null then true
           when coalesce(b.open_high, false) and b.band_lo is not null then true
           when not coalesce(b.open_low, false)
             and not coalesce(b.open_high, false)
             and b.band_lo < b.band_hi then true
           -- Historic collectors stored whole-number labels as zero-width
           -- source bounds. Phase 1B corrects them append-only. Readiness can
           -- recognize the same unambiguous grammar without exposing the
           -- private correction archive to the browser role.
           when b.band_lo = b.band_hi and (
             b.band_label ~* '^\s*-?[0-9]+(?:\.[0-9]+)?\s*(?:-\s*-?[0-9]+(?:\.[0-9]+)?)?\s*°?[CF](?:\s+or\s+(?:below|higher))?\s*$'
             or b.band_label ~* '^\s*(?:<=|>=|<|>|≤|≥)\s*-?[0-9]+(?:\.[0-9]+)?\s*°?[CF]\s*$'
           ) then true
           else false
         end as contract_valid
  from public.bands b
  join market_scope m using (market_id)
),
market_counts as (
  select city_key, resolution_date, count(*)::integer as market_count
  from market_scope group by city_key, resolution_date
),
band_counts as (
  select city_key, resolution_date,
         count(*)::integer as band_count,
         count(*) filter (where not contract_valid)::integer as invalid_band_count
  from band_scope group by city_key, resolution_date
),
forecast_counts as (
  select f.city_key, f.for_date as resolution_date,
         count(distinct f.model)::integer as forecast_models,
         count(distinct f.model) filter (where f.run_at >= now() - interval '12 hours')::integer
           as fresh_forecast_models,
         max(f.run_at) as forecast_at
  from public.weather_forecasts f
  join target_dates d on d.resolution_date = f.for_date
  group by f.city_key, f.for_date
),
latest_books as (
  select distinct on (b.band_id)
         s.city_key, s.resolution_date, b.band_id, b.observed_at, b.tradeable
  from public.book_snapshots b
  join band_scope s using (band_id)
  order by b.band_id, b.observed_at desc, b.snapshot_id desc
),
book_counts as (
  select city_key, resolution_date,
         count(*)::integer as book_bands,
         count(*) filter (where observed_at >= now() - interval '2 hours')::integer as fresh_book_bands,
         count(*) filter (
           where observed_at >= now() - interval '2 hours' and coalesce(tradeable, false)
         )::integer as executable_book_bands,
         max(observed_at) as book_at
  from latest_books group by city_key, resolution_date
),
latest_probabilities as (
  select distinct on (p.band_id)
         s.city_key, s.resolution_date, p.band_id, p.computed_at
  from public.band_probabilities p
  join band_scope s using (band_id)
  order by p.band_id, p.computed_at desc, p.prob_id desc
),
probability_counts as (
  select city_key, resolution_date,
         count(*)::integer as probability_bands,
         count(*) filter (where computed_at >= now() - interval '12 hours')::integer
           as fresh_probability_bands,
         max(computed_at) as probability_at
  from latest_probabilities group by city_key, resolution_date
),
latest_edges as (
  select e.band_id, s.city_key, s.resolution_date,
         max(e.computed_at) as computed_at,
         bool_or(coalesce(e.tradeable, false)) filter (
           where e.computed_at >= now() - interval '12 hours'
         ) as tradeable
  from public.edges e
  join band_scope s using (band_id)
  group by e.band_id, s.city_key, s.resolution_date
),
edge_counts as (
  select city_key, resolution_date,
         count(*) filter (where computed_at >= now() - interval '12 hours')::integer
           as fresh_edge_bands,
         count(*) filter (
           where computed_at >= now() - interval '12 hours' and coalesce(tradeable, false)
         )::integer as fresh_tradeable_edge_bands,
         max(computed_at) as edge_at
  from latest_edges group by city_key, resolution_date
),
base as (
  select r.*,
         coalesce(mc.market_count, 0) as market_count,
         coalesce(bc.band_count, 0) as band_count,
         coalesce(bc.invalid_band_count, 0) as invalid_band_count,
         coalesce(fc.forecast_models, 0) as forecast_models,
         coalesce(fc.fresh_forecast_models, 0) as fresh_forecast_models,
         fc.forecast_at,
         coalesce(bkc.book_bands, 0) as book_bands,
         coalesce(bkc.fresh_book_bands, 0) as fresh_book_bands,
         coalesce(bkc.executable_book_bands, 0) as executable_book_bands,
         bkc.book_at,
         coalesce(pc.probability_bands, 0) as probability_bands,
         coalesce(pc.fresh_probability_bands, 0) as fresh_probability_bands,
         pc.probability_at,
         coalesce(ec.fresh_edge_bands, 0) as fresh_edge_bands,
         coalesce(ec.fresh_tradeable_edge_bands, 0) as fresh_tradeable_edge_bands,
         ec.edge_at,
         lw.updated_at as live_weather_at
  from roster r
  left join market_counts mc using (city_key, resolution_date)
  left join band_counts bc using (city_key, resolution_date)
  left join forecast_counts fc using (city_key, resolution_date)
  left join book_counts bkc using (city_key, resolution_date)
  left join probability_counts pc using (city_key, resolution_date)
  left join edge_counts ec using (city_key, resolution_date)
  left join public.live_weather lw using (city_key)
),
diagnosed as (
  select b.*,
         array_remove(array[
           case when b.timezone is null then 'missing timezone' end,
           case when b.invalid_band_count > 0 then b.invalid_band_count || ' invalid contract band(s)' end,
           case when b.market_count > 0 and b.fresh_forecast_models = 0 then 'no forecast within 12h' end,
           case when b.band_count > 0 and b.fresh_book_bands = 0 then 'no order book within 2h' end,
           case when b.band_count > 0 and b.fresh_probability_bands = 0 then 'no probability within 12h' end,
           case when b.band_count > 0 and b.fresh_edge_bands = 0 then 'no edge evaluation within 12h' end
         ]::text[], null) as blocking_issues,
         array_remove(array[
           case when b.latitude is null or b.longitude is null then 'coordinates not verified' end,
           case when b.market_count > 0 and (b.live_weather_at is null or b.live_weather_at < now() - interval '3 hours')
             then 'no live weather within 3h' end,
           case when b.band_count > 0 and b.fresh_book_bands between 1 and b.band_count - 1
             then b.fresh_book_bands || '/' || b.band_count || ' bands have fresh books' end,
           case when b.band_count > 0 and b.fresh_probability_bands between 1 and b.band_count - 1
             then b.fresh_probability_bands || '/' || b.band_count || ' bands have fresh probabilities' end,
           case when b.band_count > 0 and b.fresh_edge_bands between 1 and b.band_count - 1
             then b.fresh_edge_bands || '/' || b.band_count || ' bands have fresh edge evaluation' end,
           case when b.band_count > 0 and b.executable_book_bands = 0
             then 'no executable token book right now' end
         ]::text[], null) as warnings
  from base b
)
select d.*,
       case
         when d.market_count = 0 then 'no_market'
         when cardinality(d.blocking_issues) > 0 then 'blocked'
         when cardinality(d.warnings) > 0 then 'attention'
         else 'ready'
       end as readiness_state,
       d.blocking_issues || d.warnings as issues,
       case when d.band_count = 0 then 0
            else round(100.0 * d.fresh_book_bands / d.band_count, 1) end as book_coverage_pct,
       case when d.band_count = 0 then 0
            else round(100.0 * d.fresh_probability_bands / d.band_count, 1) end as probability_coverage_pct
from diagnosed d;

comment on view public.v_city_day_readiness is
  'Read-only readiness for every active city for today and tomorrow. Missing inputs block evaluation; no positive trade signal does not.';

create or replace view public.v_operational_health
with (security_invoker = true)
as
select
  now() as generated_at,
  count(*)::integer as target_city_days,
  count(*) filter (where readiness_state = 'ready')::integer as ready,
  count(*) filter (where readiness_state = 'attention')::integer as attention,
  count(*) filter (where readiness_state = 'blocked')::integer as blocked,
  count(*) filter (where readiness_state = 'no_market')::integer as no_market,
  count(*) filter (where resolution_date = current_date and readiness_state = 'ready')::integer as ready_today,
  count(*) filter (where resolution_date = current_date and readiness_state = 'blocked')::integer as blocked_today,
  max(forecast_at) as latest_forecast_at,
  max(book_at) as latest_book_at,
  max(probability_at) as latest_probability_at,
  max(edge_at) as latest_edge_at,
  jsonb_build_object(
    'missing_timezone', count(*) filter (where timezone is null),
    'missing_coordinates', count(*) filter (where latitude is null or longitude is null),
    'missing_fresh_forecast', count(*) filter (where market_count > 0 and fresh_forecast_models = 0),
    'missing_fresh_books', count(*) filter (where band_count > 0 and fresh_book_bands = 0),
    'missing_fresh_probabilities', count(*) filter (where band_count > 0 and fresh_probability_bands = 0),
    'missing_fresh_edge_evaluation', count(*) filter (where band_count > 0 and fresh_edge_bands = 0)
  ) as blocker_counts,
  case
    when count(*) filter (where readiness_state = 'blocked') > 0 then 'blocked'
    when count(*) filter (where readiness_state = 'attention') > 0 then 'attention'
    else 'ready'
  end as state,
  case
    when count(*) filter (where readiness_state = 'blocked') > 0
      then count(*) filter (where readiness_state = 'blocked') || ' city-day(s) lack required current evidence'
    when count(*) filter (where readiness_state = 'attention') > 0
      then count(*) filter (where readiness_state = 'attention') || ' city-day(s) need attention'
    else 'all current city-days are ready'
  end as verdict
from public.v_city_day_readiness;

comment on view public.v_operational_health is
  'Small operational summary derived from city/day evidence, not merely whether tables contain rows.';

-- Keep the original v_data_health contract, but stop claiming that an empty
-- optional/history table proves a job never ran or that any stale derived
-- table proves all jobs stopped.
create or replace view public.v_data_health
with (security_invoker = true)
as
select
  count(*) filter (where state = 'absent') as absent,
  count(*) filter (where state = 'empty') as empty,
  count(*) filter (where state = 'stale') as stale,
  count(*) filter (where state = 'ok') as ok,
  count(*) as tracked,
  max(newest) as newest_write,
  case
    when count(*) filter (where state = 'absent') > 0
      then 'missing database objects: ' || count(*) filter (where state = 'absent')
    when count(*) filter (where state = 'stale') > 0 and count(*) filter (where state = 'empty') > 0
      then 'attention: ' || count(*) filter (where state = 'stale') || ' stale, '
        || count(*) filter (where state = 'empty') || ' empty'
    when count(*) filter (where state = 'stale') > 0
      then 'attention: ' || count(*) filter (where state = 'stale') || ' stale'
    when count(*) filter (where state = 'empty') > 0
      then 'operational inputs may be current; ' || count(*) filter (where state = 'empty') || ' datasets empty'
    else 'healthy'
  end as verdict
from public.v_data_freshness;

-- n8n versions do not all fill the same legacy counter. Prefer the explicit
-- columns, safely parse known detail counters, and downgrade a nominal "ok"
-- run to attention when it reports failed items.
create or replace view public.v_workflow_runs
with (security_invoker = true)
as
with latest as (
  select distinct on (l.job) l.*
  from public.ingest_log l
  where l.job is not null
  order by l.job, l.logged_at desc, l.log_id desc
), parsed as (
  select l.*,
    case when l.detail->>'requested' ~ '^[0-9]+$' then (l.detail->>'requested')::integer
         when l.detail->>'n_requested' ~ '^[0-9]+$' then (l.detail->>'n_requested')::integer end as requested_count,
    case when l.detail->>'failed' ~ '^[0-9]+$' then (l.detail->>'failed')::integer
         when l.detail->>'cities_failed' ~ '^[0-9]+$' then (l.detail->>'cities_failed')::integer end as failed_count,
    coalesce(
      l.rows, l.rows_written,
      case when l.detail->>'written' ~ '^[0-9]+$' then (l.detail->>'written')::integer end,
      case when l.detail->>'forecasts' ~ '^[0-9]+$' then (l.detail->>'forecasts')::integer end,
      case when l.detail->>'cities_ok' ~ '^[0-9]+$' then (l.detail->>'cities_ok')::integer end,
      case when l.detail->>'n_markets' ~ '^[0-9]+$' then (l.detail->>'n_markets')::integer end
    ) as completed_count
  from latest l
)
select
  p.job,
  case
    when lower(coalesce(p.status, '')) = 'error' then 'error'
    when lower(coalesce(p.status, '')) = 'attention' then 'attention'
    when coalesce(p.failed_count, 0) > 0 then 'attention'
    when coalesce(p.requested_count, 0) > 0 and coalesce(p.completed_count, 0) = 0 then 'attention'
    else p.status
  end as status,
  p.completed_count as rows,
  p.detail,
  p.logged_at,
  p.detail->>'trigger' as trigger,
  p.detail->>'summary' as summary,
  p.requested_count as requested,
  p.completed_count as completed,
  p.failed_count as failed
from parsed p;

comment on view public.v_workflow_runs is
  'Latest run per workflow with normalized work counters and attention when a nominally successful run reports failed items.';

revoke all on public.v_city_day_readiness, public.v_operational_health,
  public.v_data_health, public.v_workflow_runs from public;
grant select on public.v_city_day_readiness, public.v_operational_health,
  public.v_data_health, public.v_workflow_runs to anon, authenticated, service_role;

commit;
