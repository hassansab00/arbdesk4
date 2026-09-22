-- ===========================================================================
-- THE BACKTEST ASKED THE DATABASE FIFTEEN THOUSAND TIMES.
--
-- pipeline_daily's "Queued backtests" step has been timing out at 30 minutes,
-- failing the whole daily run and spending 30 metered Actions minutes a day
-- to produce nothing. The queued run asks for 9 strategies x 30 days x 48
-- cities; the last two that completed were 2 strategies x 14 days and took 14
-- and 29 minutes.
--
-- The cost is not the simulation. runner.py fetched the newest book snapshot
-- ONE BAND AT A TIME:
--
--     for b in bands:
--         rest("book_snapshots", ... band_id=eq.<one> ... limit 1)
--
-- which for that run is about 15,800 sequential HTTPS round-trips. At the
-- ~100ms each costs over a link, that alone is 26 minutes before a single
-- trade is simulated.
--
-- The one-at-a-time loop was not careless - it replaced a single read that
-- pulled EVERY snapshot of every band up to the decision instant, hit
-- PostgREST's 1,000-row cap, and silently returned no book for whichever
-- bands sorted last. That is why it is a function rather than a wider filter:
-- `distinct on` is what makes one request per ladder both complete and
-- bounded, and PostgREST cannot express it.
--
-- ad4_ix_book_band_time (band_id, observed_at desc) already exists, so this
-- is an index scan per band inside one round-trip rather than one round-trip
-- per band.
--
-- RETURNS SETOF book_snapshots rather than a named column list, deliberately:
-- the caller passes the row straight to the simulator, which reads the whole
-- book, and a hand-written column list here would go stale the next time a
-- depth column is added.
--
-- Idempotent: one function, no writes, no schema change.
-- ===========================================================================

create or replace function public.book_as_of(p_band_ids uuid[], p_as_of timestamptz)
returns setof public.book_snapshots
language sql
stable
security definer
set search_path = public
as $ad4$
  select distinct on (b.band_id) b.*
    from public.book_snapshots b
   where b.band_id = any(p_band_ids)
     and b.observed_at <= p_as_of
   order by b.band_id, b.observed_at desc;
$ad4$;

comment on function public.book_as_of(uuid[], timestamptz) is
  'The newest book snapshot per band as of an instant, one request for a whole ladder. The backtest read these one band at a time - about 15,800 round-trips for a 30-day run, which is what made pipeline_daily time out.';

do $ad4$
declare r text;
begin
  -- service_role ONLY. Nothing in web/ calls this; the backtest runner and
  -- the archive job reach it with the service key. A SECURITY DEFINER function
  -- that anon can reach is a Supabase advisor finding and, here, a pointless
  -- one to add - so it is never granted rather than granted and revoked.
  foreach r in array array['service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function public.book_as_of(uuid[], timestamptz) to %I', r);
    end if;
  end loop;
end
$ad4$;
