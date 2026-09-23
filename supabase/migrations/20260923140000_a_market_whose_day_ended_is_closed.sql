-- ===========================================================================
-- A MARKET WHOSE LOCAL DAY HAS ENDED IS CLOSED (plan v2 P2.4).
--
-- Measured 23 Sep, 15:55Z, on the live database:
--
--     markets                                        1,855
--     closed = false, local day already ended          413
--     closed = false, ended more than a day ago (A5)   379
--     winning_band_id or resolved_band_id non-null       0
--     ...while v_venue_market_resolution calls         851 of them 'confirmed'
--
-- `closed` is written only by market discovery (P0.2), which polls today and
-- tomorrow and upserts whatever Gamma says. A market it stops polling keeps
-- the last value forever, and a market it does poll can be set back to false,
-- because Gamma keeps a market open until UMA rules on it.
--
-- THE RULE is Hassan's, 23 Sep: "as long as their local date/day ended, they
-- need to be closed, but if the temp wasn't settled, then there's a real
-- issue." So:
--
--   1. a_market_whose_day_ended_stays_closed: on every insert or update, a
--      market whose resolution_date is before its city's local today is
--      closed. A discovery upsert can no longer reopen it. closed_time and
--      closed_reason record when and why it closed.
--   2. refresh_market_state(): the same rule for markets nobody writes to,
--      plus the resolution fields, from the venue's own confirmed settlement
--      (v_venue_market_resolution, which requires every band confirmed and
--      exactly one YES). Only null fields are filled; nothing already there
--      is overwritten. Runs hourly inside the existing
--      ad4_refresh_venue_band_resolution cron job, so no new schedule.
--   3. "the temp wasn't settled" is sql/ad4_87_market_settlement_gaps.sql,
--      which lists every ended market with no settled temperature.
--
-- Nothing is deleted. Rows change only in markets, and only in closed,
-- closed_time, closed_reason and the four resolution fields when null.
-- markets is not one of the six tables research capture copies.
-- ===========================================================================

alter table public.markets add column if not exists closed_time timestamptz;
alter table public.markets add column if not exists closed_reason text;

comment on column public.markets.closed_time is
  'When this row first became closed = true. Null for a market closed before 23 Sep 2026 (P2.4), when this column was added.';
comment on column public.markets.closed_reason is
  '''local_day_ended'' when the city''s local date passed resolution_date; ''venue'' when discovery or settlement closed it first.';

-- The one definition of "its day has ended". A city with no timezone (a city
-- discovery has only just created, status pending_review) is never ended:
-- the rule cannot be applied without knowing where the day is.
create or replace function public.market_day_ended(p_city_key text, p_resolution_date date)
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
  select coalesce(
    (select p_resolution_date < (now() at time zone c.timezone)::date
       from public.cities c
      where c.city_key = p_city_key and c.timezone is not null),
    false);
$$;

comment on function public.market_day_ended(text, date) is
  'True when resolution_date is before the city''s local today. The rule P2.4 closes markets by.';

create or replace function public.a_market_whose_day_ended_stays_closed()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_ended boolean := public.market_day_ended(new.city_key, new.resolution_date);
begin
  if v_ended then
    new.closed := true;
  end if;
  if coalesce(new.closed, false)
     and (tg_op = 'INSERT' or not coalesce(old.closed, false)) then
    new.closed_time := coalesce(new.closed_time, now());
    new.closed_reason := coalesce(new.closed_reason,
                                  case when v_ended then 'local_day_ended' else 'venue' end);
  end if;
  return new;
end $$;

drop trigger if exists a_market_whose_day_ended_stays_closed on public.markets;
create trigger a_market_whose_day_ended_stays_closed
  before insert or update on public.markets
  for each row execute function public.a_market_whose_day_ended_stays_closed();

create or replace function public.refresh_market_state()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_closed integer;
  v_resolved integer;
begin
  -- The trigger does the work: it sees the ended day and stamps the reason.
  update public.markets m
     set closed = true
   where not m.closed
     and public.market_day_ended(m.city_key, m.resolution_date);
  get diagnostics v_closed = row_count;

  -- The venue's confirmed winner, onto markets whose resolution fields are
  -- empty. A market that already carries a winner keeps it; if the two ever
  -- differ, v_market_settlement_gaps says so.
  update public.markets m
     set winning_band_id        = r.winning_band_id,
         resolved_band_id       = coalesce(m.resolved_band_id, r.winning_band_id),
         resolution_verified_at = coalesce(m.resolution_verified_at, r.confirmed_at),
         resolution_source_used = coalesce(m.resolution_source_used, 'venue_evidence')
    from public.v_venue_market_resolution r
   where r.market_id = m.market_id
     and r.resolution_state = 'confirmed'
     and r.winning_band_id is not null
     and m.winning_band_id is null
     and m.resolved_band_id is null;
  get diagnostics v_resolved = row_count;

  return jsonb_build_object('closed', v_closed, 'resolved', v_resolved, 'at', now());
end $$;

comment on function public.refresh_market_state() is
  'Plan v2 P2.4. Closes every market whose local day has ended and copies the venue''s confirmed winner onto markets whose resolution fields are empty. Hourly, inside the ad4_refresh_venue_band_resolution cron job.';

revoke all on function public.refresh_market_state() from public, anon, authenticated;
grant execute on function public.refresh_market_state() to service_role;
revoke all on function public.market_day_ended(text, date) from public;
grant execute on function public.market_day_ended(text, date) to anon, authenticated, service_role;

-- Hourly, in the job that already refreshes the venue resolution (it runs
-- first, so the winners copied here are at most as old as that refresh). No
-- new schedule. Guarded: the test harness has no pg_cron.
do $$
declare v_job bigint;
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    select jobid into v_job from cron.job where jobname = 'ad4_refresh_venue_band_resolution';
    if v_job is not null then
      perform cron.alter_job(v_job, command :=
        'select public.refresh_venue_band_resolution(); select public.refresh_market_state();');
    end if;
  end if;
end $$;

-- Bring every row to the rule now rather than at the next hour.
select public.refresh_market_state();
