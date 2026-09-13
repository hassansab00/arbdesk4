-- Phase 1D: distinguish a visible cold-start probability from a probability
-- proven enough to be used for paper execution. Phase 1C's original views
-- remain available; the UI moves to this stricter execution contract.
begin;

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
  select distinct on (p.band_id)
         b.city_key, b.resolution_date, p.band_id, p.computed_at,
         p.pricing_eligible, p.pricing_block_reason, p.skill_source
  from public.band_probabilities p
  join band_scope b using (band_id)
  order by p.band_id, p.computed_at desc, p.prob_id desc
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

create or replace view public.v_execution_health
with (security_invoker = true)
as
select now() as generated_at,
       count(*)::integer as target_city_days,
       count(*) filter (where execution_state = 'ready')::integer as ready,
       count(*) filter (where execution_state = 'attention')::integer as attention,
       count(*) filter (where execution_state = 'blocked')::integer as blocked,
       count(*) filter (where execution_state = 'no_market')::integer as no_market,
       count(*) filter (where resolution_date = current_date and execution_state = 'ready')::integer as ready_today,
       count(*) filter (where resolution_date = current_date and execution_state = 'blocked')::integer as blocked_today,
       sum(cold_start_bands)::integer as cold_start_bands,
       case
         when count(*) filter (where execution_state = 'blocked') > 0 then 'blocked'
         when count(*) filter (where execution_state = 'attention') > 0 then 'attention'
         else 'ready'
       end as state,
       case
         when count(*) filter (where execution_state = 'blocked') > 0
           then count(*) filter (where execution_state = 'blocked') ||
                ' city-day(s) lack execution-ready evidence'
         when count(*) filter (where execution_state = 'attention') > 0
           then count(*) filter (where execution_state = 'attention') ||
                ' city-day(s) need attention'
         else 'all current city-days are execution-ready'
       end as verdict
from public.v_city_day_execution_readiness;

revoke all on public.v_city_day_execution_readiness, public.v_execution_health from public;
grant select on public.v_city_day_execution_readiness, public.v_execution_health
  to anon, authenticated, service_role;

commit;
