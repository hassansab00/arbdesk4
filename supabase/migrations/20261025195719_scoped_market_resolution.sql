-- A city/checkpoint outcome check must not aggregate unrelated verdicts.
-- Keep the shared band view unchanged: its historical bulk joins need the
-- original global aggregation. Only scope the market view's proof lookups.
-- No rows, archives, grants, schedules, or prediction guards are changed.
create or replace view public.v_venue_market_resolution
with (security_invoker = true)
as
select m.market_id,m.city_key,m.resolution_date,
 count(b.band_id)::integer band_count,
 count(*) filter(where r.resolution_state='confirmed')::integer confirmed_bands,
 count(*) filter(where r.resolution_state='disputed')::integer disputed_bands,
 count(*) filter(where r.resolution_state='confirmed' and r.settled_yes)::integer yes_winners,
 max(r.confirmed_at) confirmed_at,
 case when count(b.band_id)=0 then 'missing_bands'
 when count(*) filter(where r.resolution_state='disputed')>0 then 'disputed'
 when count(*) filter(where r.resolution_state='confirmed')=count(b.band_id)
 and count(*) filter(where r.resolution_state='confirmed' and r.settled_yes)=1 then 'confirmed'
 when count(*) filter(where r.resolution_state='confirmed')>0 then 'partial'
 else 'unverified' end resolution_state,
 (array_agg(b.band_id order by b.band_id) filter(where r.resolution_state='confirmed' and r.settled_yes))[1] winning_band_id
from public.markets m
left join public.bands b on b.market_id=m.market_id
left join lateral (
 select max(e.captured_at) confirmed_at,
 case when count(distinct e.winning_token)=1 then min(e.winning_token)=b.token_yes end settled_yes,
 case when count(distinct e.winning_token)=1 then 'confirmed' else 'disputed' end resolution_state
 from public.resolution_verdicts e
 where e.condition_id=b.condition_id and e.token_yes=b.token_yes and e.token_no=b.token_no
 having count(*)>0
) r on true
group by m.market_id,m.city_key,m.resolution_date;

