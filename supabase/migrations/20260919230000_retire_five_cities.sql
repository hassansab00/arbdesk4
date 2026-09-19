-- Retire five cities that cost work and return nothing.
--
-- NOTHING IS DELETED. Every observation, market, band, outcome and trade these
-- cities ever produced stays exactly where it is, and the cities row stays too
-- - only `status` moves. common.get_cities() filters on status='active', so
-- one word stops collection, pricing, settlement and the desk, and putting it
-- back is the same one word. That matters: "we dont need them right now" is a
-- reversible statement, and this is a reversible change.
--
-- TWO REASONS, measured on 2026-09-19.
--
-- THE VENUE DROPPED THEM. Polymarket has listed no market for these in months,
-- so there is nothing to price and nothing to settle:
--
--     dc        1 market ever, last resolved 2025-01-20   607 days ago
--     lagos     1 market ever, last resolved 2026-04-15   157 days ago
--     jakarta   2 markets ever, last resolved 2026-04-16  156 days ago
--
--   All three have healthy observation feeds and fitted models; they are being
--   retired because the market is gone, not because the data is bad. These are
--   also the ONLY three cities of 54 with no market in the last 30 days - the
--   same three at a 7-day window - so this retires the whole stale set, not an
--   arbitrary slice of it.
--
-- WE CANNOT OBSERVE THEM. Both of these are actively traded (29 and 28 markets
-- in the last 30 days), and neither has a usable temperature history, so
-- neither can ever carry a model:
--
--     hong_kong  icao IS NULL and station_name IS NULL - no station identifier
--                exists, so the METAR ingest has never requested it. Zero
--                observations in the entire history. Its resolution_source is
--                'HKO' (Hong Kong Observatory), which the ingest does not read.
--     jinan      icao='ZSJN' is set, but the station has returned 60 readings
--                total, all inside a four-minute window on 2026-08-26, and
--                nothing since - a feed 636 hours stale. All 109 of its cached
--                days are thin days (<12 readings), so not one is usable.
--                beijing, shanghai and qingdao each hold 2,152 observations
--                over the same window, so this is the station, not the ingest.
--
--   Pricing a market whose temperature we cannot see is a guess wearing a
--   probability. Retiring them is the honest option until a second source is
--   wired up; reversing it is one update once there is one.
update public.cities
   set status = 'retired',
       updated_at = now()
 where city_key in ('dc', 'jakarta', 'lagos', 'hong_kong', 'jinan')
   and status is distinct from 'retired';
