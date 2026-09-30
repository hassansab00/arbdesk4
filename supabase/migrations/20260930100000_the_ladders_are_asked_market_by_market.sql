-- ===========================================================================
-- THE LADDERS ARE ASKED MARKET BY MARKET (plan v2.2 P4.7, finished 30 Sep;
-- docs/handoff-2026-09-30/02_FIX_SPECS.md F1 with the 30 Sep supplement).
--
-- Measured 30 Sep 09:21Z, as anon: every US city's newest day in
-- v_city_hit_history was 28 Sep, as were Mexico City's and Panama City's. The
-- venue was not late: its own closedTime for US ladders came a median 1.2-1.4 h
-- after the local day ended (25-29 Sep, max 4.6 h), while
-- markets.resolution_verified_at came 22.8-24.1 h after it. The hourly step
-- asked the venue one band at a time and reached 2-34 of ~150 bands a run.
--
-- scripts/confirm_queue.py asks one Gamma request per LADDER, oldest local day
-- end first, and backs off a ladder the venue has not closed. This file holds
-- what that needs to survive from one run to the next, and what it measures:
--
--   market_confirmation_attempts      one row per market asked: how often, the
--                                     last answer, the consecutive unresolved
--                                     answers the backoff reads, and three
--                                     timestamps - the venue's own latest
--                                     closedTime (venue-supplied), the last poll
--                                     that saw the ladder not fully resolved,
--                                     and the first that saw it fully resolved.
--                                     The last two BRACKET when the venue made
--                                     it available to us; neither is the
--                                     venue's resolution time.
--   record_market_confirmation_attempts(jsonb)
--                                     the only writer; keeps first-seen values
--                                     and counts the attempt (service_role)
--   v_outcome_pipeline                every market of the last 14 days from its
--                                     local day end to the page: venue closed,
--                                     proof complete, banked, on the page - and
--                                     the ones still pending, which stay in the
--                                     denominator
--
-- NOT HERE: clock_expected_jobs rows for venue_confirm_queue, databank_bands
-- and the tick's hourly P4.7_confirm_recent. v_run_arrivals joins every
-- dispatch of the last 7 days to the expected jobs, and the watchdog counts a
-- miss due in the last 24 h, so rows added before the code runs would report
-- ~24 runs that could not have logged the job as failures. They are added
-- once the code has run for a day (plan v2.2 P4.7 acceptance).
--
-- Nothing here decides an outcome. A proof is still paper_settlement.verify()'s
-- (Gamma and the CLOB agree on one winner), and a ladder is still banked whole
-- or not at all (databank.bank_bands). Idempotent.
-- ===========================================================================

create table if not exists public.market_confirmation_attempts (
  market_id                   uuid primary key,
  city_key                    text not null,
  resolution_date             date not null,
  local_day_end               timestamptz not null,
  first_asked_at              timestamptz not null,
  last_asked_at               timestamptz not null,
  attempts                    integer not null default 1 check (attempts >= 1),
  unresolved_streak           integer not null default 0 check (unresolved_streak >= 0),
  last_outcome                text not null
    check (last_outcome in ('not_closed', 'partial', 'resolved', 'proven', 'failed')),
  last_trigger                text,
  bands_total                 integer,
  bands_proven                integer,
  venue_closed_at             timestamptz,
  last_unresolved_at          timestamptz,
  first_all_resolved_seen_at  timestamptz,
  last_error                  text
);

comment on table public.market_confirmation_attempts is
  'Plan v2.2 P4.7 (30 Sep): one row per market scripts/confirm_queue.py has asked the venue about. unresolved_streak drives its backoff; venue_closed_at is the latest closedTime the venue reports for the ladder (venue-supplied); last_unresolved_at and first_all_resolved_seen_at bracket when the whole ladder became available to us (our polls, not the venue''s resolution time). Written only by record_market_confirmation_attempts().';

-- service_role only, like clock_expected_jobs: no access is widened here. The
-- page can say a day is pending from markets and cities, which it already reads.
alter table public.market_confirmation_attempts enable row level security;
revoke all on public.market_confirmation_attempts from public, anon, authenticated;
grant select on public.market_confirmation_attempts to service_role;

create index if not exists market_confirmation_attempts_day
  on public.market_confirmation_attempts (resolution_date);

create or replace function public.record_market_confirmation_attempts(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  n integer;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'record_market_confirmation_attempts: p_rows must be a json array';
  end if;
  insert into public.market_confirmation_attempts as t (
    market_id, city_key, resolution_date, local_day_end, first_asked_at, last_asked_at,
    attempts, unresolved_streak, last_outcome, last_trigger, bands_total, bands_proven,
    venue_closed_at, last_unresolved_at, first_all_resolved_seen_at, last_error)
  select (r->>'market_id')::uuid,
         r->>'city_key',
         (r->>'resolution_date')::date,
         (r->>'local_day_end')::timestamptz,
         (r->>'asked_at')::timestamptz,
         (r->>'asked_at')::timestamptz,
         1,
         case when r->>'outcome' in ('not_closed', 'partial', 'failed') then 1 else 0 end,
         r->>'outcome',
         r->>'trigger',
         (r->>'bands_total')::integer,
         (r->>'bands_proven')::integer,
         (r->>'venue_closed_at')::timestamptz,
         case when r->>'outcome' in ('not_closed', 'partial') then (r->>'asked_at')::timestamptz end,
         case when r->>'outcome' = 'resolved' then (r->>'asked_at')::timestamptz end,
         r->>'error'
    from jsonb_array_elements(p_rows) r
  on conflict (market_id) do update set
    last_asked_at              = greatest(t.last_asked_at, excluded.last_asked_at),
    attempts                   = t.attempts + 1,
    unresolved_streak          = case when excluded.unresolved_streak = 1
                                      then t.unresolved_streak + 1 else 0 end,
    last_outcome               = excluded.last_outcome,
    last_trigger               = excluded.last_trigger,
    bands_total                = excluded.bands_total,
    bands_proven               = greatest(t.bands_proven, excluded.bands_proven),
    venue_closed_at            = greatest(t.venue_closed_at, excluded.venue_closed_at),
    last_unresolved_at         = greatest(t.last_unresolved_at, excluded.last_unresolved_at),
    first_all_resolved_seen_at = coalesce(t.first_all_resolved_seen_at,
                                          excluded.first_all_resolved_seen_at),
    last_error                 = excluded.last_error;
  get diagnostics n = row_count;
  return n;
end $$;

comment on function public.record_market_confirmation_attempts(jsonb) is
  'Plan v2.2 P4.7: records one confirm_queue attempt per market. Keeps the first time a whole ladder was seen resolved, the latest unresolved poll and the venue''s latest closedTime; counts attempts; resets the backoff streak on an answer. service_role only.';

revoke all on function public.record_market_confirmation_attempts(jsonb) from public, anon, authenticated;
grant execute on function public.record_market_confirmation_attempts(jsonb) to service_role;

-- ---------------------------------------------------------------------------
-- From the city's day end to the page, every market of the last 14 days.
-- state: day_not_ended | awaiting_venue | venue_resolved_proof_incomplete |
--        confirmed_not_banked | banked_not_on_page | on_page
-- A pending market stays in the view with its hours so far, so a lag computed
-- from here counts the slow cases instead of dropping them.
-- ---------------------------------------------------------------------------
-- v_city_hit_history comes from sql/ad4_85 (and ad4_89), never a migration,
-- so a database without the page (the contract fixture) gets an empty "on the
-- page" list instead of a failed migration.
do $do$
declare
  listed text := case when to_regclass('public.v_city_hit_history') is not null
    then 'select distinct h.city_key, h.for_date from public.v_city_hit_history h where h.for_date >= current_date - 14'
    else 'select null::text as city_key, null::date as for_date where false' end;
begin
  execute format($v$
create or replace view public.v_outcome_pipeline
with (security_invoker = true)
as
with m as (
  select mk.market_id, mk.city_key, c.unit, mk.resolution_date,
         ((mk.resolution_date + 1)::timestamp at time zone c.timezone) as local_day_end,
         mk.resolution_verified_at
    from public.markets mk
    join public.cities c on c.city_key = mk.city_key
   where mk.resolution_date >= current_date - 14
     and mk.resolution_date <= current_date
     and c.timezone is not null
),
banked as (
  select b.market_id, min(f.captured_at) as banked_at, count(*)::integer as bands_banked
    from public.fact_band_outcome f
    join public.bands b on b.band_id = f.band_id
   where f.for_date >= current_date - 14
   group by b.market_id
),
refreshed as (
  select l.started_at
    from public.ingest_log l
   where l.job = 'refresh_page_cache' and l.status = 'ok'
     and l.started_at >= current_date - 15
),
listed as (
  %s
)
select m.market_id, m.city_key, m.unit, m.resolution_date, m.local_day_end,
       a.venue_closed_at, a.last_unresolved_at, a.first_all_resolved_seen_at,
       a.attempts, a.last_outcome, a.last_asked_at, a.bands_total, a.bands_proven,
       m.resolution_verified_at as proof_complete_at,
       bk.banked_at, bk.bands_banked,
       (select min(r.started_at) from refreshed r where r.started_at >= bk.banked_at) as page_refreshed_at,
       (ls.city_key is not null) as on_page,
       case
         when m.local_day_end > now()                   then 'day_not_ended'
         when ls.city_key is not null                   then 'on_page'
         when bk.banked_at is not null                  then 'banked_not_on_page'
         when m.resolution_verified_at is not null      then 'confirmed_not_banked'
         when a.first_all_resolved_seen_at is not null  then 'venue_resolved_proof_incomplete'
         else 'awaiting_venue'
       end as state,
       round((extract(epoch from (coalesce(bk.banked_at, now()) - m.local_day_end)) / 3600)::numeric, 2)
         as hours_day_end_to_banked_or_now
  from m
  left join public.market_confirmation_attempts a on a.market_id = m.market_id
  left join banked bk on bk.market_id = m.market_id
  left join listed ls on ls.city_key = m.city_key and ls.for_date = m.resolution_date
$v$, listed);
end $do$;

comment on view public.v_outcome_pipeline is
  'Plan v2.2 P4.7 (30 Sep): each market of the last 14 days from its local day end to /predictive. venue_closed_at is the venue''s own closedTime; last_unresolved_at / first_all_resolved_seen_at bracket when our polls first saw the whole ladder resolved; proof_complete_at is markets.resolution_verified_at (the latest proof''s capture, set hourly by refresh_market_state); banked_at the first fact_band_outcome row; page_refreshed_at the first page-cache refresh after it; on_page whether v_city_hit_history lists the city-day (it needs an observed maximum and a winner). Pending markets stay in the view.';

-- A new view inherits the schema's default grants; this one is for the
-- operator's SQL and the checks, not the browser.
revoke all on public.v_outcome_pipeline from public, anon, authenticated;
grant select on public.v_outcome_pipeline to service_role;

-- Tracked for freshness like every table a job fills (sql/ad4_39).
do $$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english)
    values ('market_confirmation_attempts', 'last_asked_at', 26, 'databank',
            'Each ended market''s ladder as the confirmation queue last asked the venue about it: how often, what it found, and when the venue closed it.')
    on conflict (table_name) do update set
      ts_column = excluded.ts_column, fresh_hours = excluded.fresh_hours,
      layer = excluded.layer, plain_english = excluded.plain_english;
  end if;
end $$;
