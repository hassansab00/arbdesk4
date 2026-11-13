-- One venue-band reduction per health read. Preserve the verified outcome
-- predicates, original facts, invoker permissions and ten public aggregates.
create or replace view public.v_outcome_evidence_health
with (security_invoker = true) as
with band_resolution as materialized (
  select band_id,market_id,resolution_state='confirmed' as confirmed,
         resolution_state='disputed' as disputed,settled_yes
  from public.v_venue_band_resolution
), market_resolution as materialized (
  select m.market_id,
     count(b.band_id)>0
     and count(*) filter(where r.disputed)=0
     and count(*) filter(where r.confirmed)=count(b.band_id)
     and count(*) filter(where r.confirmed and r.settled_yes)=1 as confirmed
  from public.markets m
  left join public.bands b on b.market_id=m.market_id
  left join band_resolution r on r.band_id=b.band_id
  group by m.market_id
), coherence as materialized (
  select city_key,for_date,coherent from public.v_band_outcome_coherence
), clean_band_facts as materialized (
  select band_id,city_key,for_date,settled_yes
  from public.v_fact_band_outcome_clean
), venue_stats as (
  select count(*)::bigint n,max(captured_at) latest from public.paper_resolution_evidence
), weather_stats as (
  select count(*)::bigint n,max(captured_at) latest from public.weather_resolution_evidence
)
select
  (select count(*) from public.fact_band_outcome) as raw_band_facts,
  (select count(*) from clean_band_facts f
     join band_resolution br on br.band_id=f.band_id
     join market_resolution mr on mr.market_id=br.market_id
     join coherence c on c.city_key=f.city_key and c.for_date=f.for_date
    where br.confirmed and mr.confirmed and c.coherent
      and f.settled_yes is not distinct from br.settled_yes) as verified_band_facts,
  (select count(*) from public.fact_band_outcome f
     join band_resolution r using(band_id)
    where r.confirmed
      and f.settled_yes is distinct from r.settled_yes) as mismatched_band_facts,
  (select count(*) from public.fact_forecast_outcome) as raw_forecast_facts,
  (select count(*) from public.v_verified_fact_forecast_outcome) as verified_forecast_facts,
  venue_stats.n as venue_evidence_rows,
  weather_stats.n as weather_evidence_rows,
  (select count(*) from market_resolution where confirmed) as confirmed_markets,
  venue_stats.latest as latest_venue_evidence_at,
  weather_stats.latest as latest_weather_evidence_at
from venue_stats cross join weather_stats;

-- Local to the existing status function; no global memory setting changes.
do $$ begin
  if to_regprocedure('public.refresh_page_status()') is not null then
    alter function public.refresh_page_status() set work_mem='8MB';
  end if;
end $$;
notify pgrst,'reload schema';
