-- ===========================================================================
-- READINESS READS ONE ROW PER BAND, NOT EVERY ROW EACH BAND EVER HAD.
--
-- v_execution_health is an aggregate over v_city_day_execution_readiness,
-- which is built on this view, and the City Readiness panel reads it on every
-- page that shows it. Measured 2026-09-22 as the browser's role, against the
-- 8-second statement limit anon and authenticated both carry:
--
--     v_execution_health          5,209 ms
--       latest_books              3,836 ms   24,014 snapshots for 869 bands
--       latest_edges                556 ms   13,134 edges
--       latest_probabilities        352 ms    6,567 probabilities
--
-- Each was a `distinct on` or a group-by over EVERY row a band has ever had,
-- to keep one - so it got slower every fifteen minutes, forever, and cold it
-- read tens of thousands of heap pages. Each is now a lateral probe on the
-- index that already exists for exactly this. Same rules - newest first with
-- the same tie-breaks, the same 12-hour window on tradeability - and the rest
-- of the view is byte-for-byte the definition from
-- 20260912234500_phase1c_operational_readiness.sql.
-- ===========================================================================
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
  -- ONE ROW PER BAND BY INDEX PROBE. `distinct on` over the join read every
  -- snapshot each band has ever had - 24,014 rows for 869 bands, sorted on
  -- disk - to keep 869. Measured 2026-09-22 as the browser's role: 3.8 s of
  -- v_execution_health's 5.2 s. The same newest-first rule, one backwards
  -- probe on ad4_ix_book_band_time per band.
  select s.city_key, s.resolution_date, s.band_id, b.observed_at, b.tradeable
  from band_scope s
  cross join lateral (
    select bs.observed_at, bs.tradeable
      from public.book_snapshots bs
     where bs.band_id = s.band_id
     order by bs.observed_at desc, bs.snapshot_id desc
     limit 1) b
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
  -- Same treatment: every probability row per band, sorted, to keep one.
  select s.city_key, s.resolution_date, s.band_id, p.computed_at
  from band_scope s
  cross join lateral (
    select bp.computed_at
      from public.band_probabilities bp
     where bp.band_id = s.band_id
     order by bp.computed_at desc, bp.prob_id desc
     limit 1) p
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
  -- The newest edge's time, and whether any edge in the last 12 hours was
  -- tradeable - the two numbers the old group-by computed over every edge the
  -- band ever had. The first is one probe; the second reads only 12 hours.
  select s.band_id, s.city_key, s.resolution_date,
         le.computed_at, rt.tradeable
  from band_scope s
  cross join lateral (
    select x.computed_at from public.edges x
     where x.band_id = s.band_id
     order by x.computed_at desc limit 1) le
  cross join lateral (
    select bool_or(coalesce(x.tradeable, false)) as tradeable
      from public.edges x
     where x.band_id = s.band_id
       and x.computed_at >= now() - interval '12 hours') rt
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

create or replace view public.v_city_day_execution_readiness
with (security_invoker = true)
as
with band_scope as (
  select m.city_key, m.resolution_date, b.band_id
  from public.markets m
  join public.bands b using (market_id)
  where not coalesce(m.closed, false)
    and m.resolution_date between current_date and current_date + 1
), latest_probability as (
  -- One probe per band, the same newest-first rule and tie-break - see the
  -- header of this file. The distinct-on read every probability each band
  -- ever had.
  select b.city_key, b.resolution_date, b.band_id, p.computed_at,
         p.pricing_eligible, p.pricing_block_reason, p.skill_source
  from band_scope b
  cross join lateral (
    select bp.computed_at, bp.pricing_eligible, bp.pricing_block_reason, bp.skill_source
      from public.band_probabilities bp
     where bp.band_id = b.band_id
     order by bp.computed_at desc, bp.prob_id desc
     limit 1) p
), quality as (
  select city_key, resolution_date,
         count(*) filter (where computed_at >= now() - interval '12 hours')::integer
           as fresh_probability_bands,
         count(*) filter (
           where computed_at >= now() - interval '12 hours'
             and pricing_eligible
         )::integer as pricing_eligible_bands,
         count(*) filter (
           where computed_at >= now() - interval '12 hours'
             and not pricing_eligible
         )::integer as cold_start_bands,
         array_remove(array_agg(distinct pricing_block_reason)
           filter (where computed_at >= now() - interval '12 hours'
                     and not pricing_eligible), null) as pricing_block_reasons
  from latest_probability
  group by city_key, resolution_date
)
select r.*,
       coalesce(q.pricing_eligible_bands, 0) as pricing_eligible_bands,
       coalesce(q.cold_start_bands, 0) as cold_start_bands,
       coalesce(q.pricing_block_reasons, '{}'::text[]) as pricing_block_reasons,
       case
         when r.readiness_state in ('no_market', 'blocked') then r.readiness_state
         when coalesce(q.fresh_probability_bands, 0) > 0
           and coalesce(q.pricing_eligible_bands, 0) = 0 then 'blocked'
         when coalesce(q.pricing_eligible_bands, 0) > 0
           and coalesce(q.pricing_eligible_bands, 0) < r.band_count
           then 'attention'
         else r.readiness_state
       end as execution_state,
       r.issues || array_remove(array[
         case when coalesce(q.fresh_probability_bands, 0) > 0
                    and coalesce(q.pricing_eligible_bands, 0) = 0
              then 'cold-start probability only; no city skill evidence' end,
         case when coalesce(q.pricing_eligible_bands, 0) > 0
                    and coalesce(q.pricing_eligible_bands, 0) < r.band_count
              then coalesce(q.pricing_eligible_bands, 0) || '/' || r.band_count ||
                   ' bands are eligible for execution' end
       ]::text[], null) as execution_issues
from public.v_city_day_readiness r
left join quality q using (city_key, resolution_date);

commit;
