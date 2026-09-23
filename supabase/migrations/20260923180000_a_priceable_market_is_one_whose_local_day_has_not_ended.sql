-- ===========================================================================
-- A PRICEABLE MARKET IS ONE WHOSE LOCAL DAY HAS NOT ENDED (plan v2 P3.2).
--
-- probability_engine and edge_engine chose markets with closed = false and
-- resolution_date >= dt.date.today() - the RUNNER'S date, which is UTC. A daily
-- maximum settles on the city's LOCAL day, so that rule was wrong both ways.
-- Measured 23 Sep over the last 7 days of band_probabilities:
--
--   5,401 rows priced after the market's local day had ended (A7) - cities
--         ahead of UTC, whose local day ends before the UTC one
--     310 market-runs skipped while the market's local day was still live -
--         Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Mexico City,
--         Panama City, San Francisco and Seattle, dropped from 00:00Z until
--         their own midnight
--
-- One view, so every consumer shares the rule: an active city's market that is
-- not closed and whose resolution_date is on or after the city's local today.
-- At 17:25Z on 23 Sep it selected the same 80 markets as the old rule (the two
-- differ only in the hours above), and no retired city had an open market.
-- ===========================================================================

create or replace view public.v_priceable_markets as
select m.market_id, m.city_key, m.resolution_date, m.unit, m.correction_id
from public.v_canonical_markets m
join public.cities c on c.city_key = m.city_key
where not coalesce(m.closed, false)
  and c.status = 'active'
  and c.timezone is not null
  and m.resolution_date >= (now() at time zone c.timezone)::date;

comment on view public.v_priceable_markets is
  'Plan v2 P3.2: the markets an engine may price - active city, not closed, local day not over. probability_engine and edge_engine read this instead of a UTC resolution_date filter.';

revoke all on public.v_priceable_markets from public, anon, authenticated;
grant select on public.v_priceable_markets to service_role;
