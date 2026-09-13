-- Phase 1D: complete probability ladders without pretending cold-start data
-- is tradeable, and give the book collector a durable per-band failure log.
-- Additive only: no collected, predictive, synthesis, or settlement row is
-- updated or deleted.
begin;

alter table public.band_probabilities
  add column if not exists skill_source text not null default 'legacy',
  add column if not exists pricing_eligible boolean not null default true,
  add column if not exists pricing_block_reason text;

create or replace view public.v_latest_prob
with (security_invoker = true)
as
select distinct on (bp.band_id)
  bp.*
from public.band_probabilities bp
order by bp.band_id, bp.computed_at desc, bp.prob_id desc;

revoke all on public.v_latest_prob from public;
grant select on public.v_latest_prob to anon, authenticated, service_role;

create table if not exists public.book_capture_attempts (
  attempt_id bigint generated always as identity primary key,
  execution_id text not null check (length(trim(execution_id)) > 0),
  band_id uuid not null references public.bands(band_id),
  token_id text,
  attempted_at timestamptz not null default clock_timestamp(),
  status text not null check (status in (
    'written', 'missing_token', 'http_error', 'empty_book',
    'parse_error', 'write_error', 'not_requested'
  )),
  http_status integer check (http_status is null or http_status between 100 and 599),
  error_code text,
  error_message text,
  response_shape jsonb not null default '{}'::jsonb,
  response_hash text,
  detail jsonb not null default '{}'::jsonb,
  unique (execution_id, band_id)
);

create index if not exists book_capture_attempts_time
  on public.book_capture_attempts(attempted_at desc);
create index if not exists book_capture_attempts_band_time
  on public.book_capture_attempts(band_id, attempted_at desc);
create index if not exists book_capture_attempts_failed_time
  on public.book_capture_attempts(status, attempted_at desc)
  where status <> 'written';

alter table public.book_capture_attempts enable row level security;
revoke all on public.book_capture_attempts from public, anon, authenticated, service_role;
grant select, insert on public.book_capture_attempts to service_role;
grant select (attempt_id, execution_id, band_id, attempted_at, status, http_status, error_code)
  on public.book_capture_attempts to anon, authenticated;

drop policy if exists book_capture_attempt_status_read on public.book_capture_attempts;
create policy book_capture_attempt_status_read on public.book_capture_attempts
  for select to anon, authenticated using (true);

drop trigger if exists book_capture_attempt_immutable on public.book_capture_attempts;
create trigger book_capture_attempt_immutable
  before update or delete on public.book_capture_attempts
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists book_capture_attempt_no_truncate on public.book_capture_attempts;
create trigger book_capture_attempt_no_truncate
  before truncate on public.book_capture_attempts
  for each statement execute function arbdesk_private.immutable_record();

create or replace view public.v_book_target_health
with (security_invoker = true)
as
with target_bands as (
  select m.resolution_date, m.city_key, m.market_id,
         b.band_id, b.band_index, b.band_label, b.token_yes
  from public.markets m
  join public.bands b using (market_id)
  where not coalesce(m.closed, false)
    and m.resolution_date between current_date and current_date + 1
), latest_book as (
  select distinct on (s.band_id)
         s.band_id, s.snapshot_id, s.observed_at, s.market_state, s.tradeable
  from public.book_snapshots s
  join target_bands t using (band_id)
  order by s.band_id, s.observed_at desc, s.snapshot_id desc
), latest_attempt as (
  select distinct on (a.band_id)
         a.band_id, a.execution_id, a.attempted_at, a.status,
         a.http_status, a.error_code
  from public.book_capture_attempts a
  join target_bands t using (band_id)
  order by a.band_id, a.attempted_at desc, a.attempt_id desc
)
select t.*, b.snapshot_id, b.observed_at, b.market_state, b.tradeable,
       a.execution_id, a.attempted_at, a.status as attempt_status,
       a.http_status, a.error_code,
       case
         when nullif(trim(t.token_yes), '') is null then 'missing_token'
         when b.observed_at >= now() - interval '2 hours' then 'captured'
         when a.status is not null and a.status <> 'written' then a.status
         when a.status = 'written' then 'stale_after_write'
         when b.observed_at is not null then 'not_refreshed'
         else 'not_attempted_or_unrecorded'
       end as capture_state
from target_bands t
left join latest_book b using (band_id)
left join latest_attempt a using (band_id);

create or replace view public.v_book_capture_health
with (security_invoker = true)
as
select resolution_date, capture_state, count(*)::integer as bands,
       count(distinct city_key)::integer as cities,
       max(coalesce(attempted_at, observed_at)) as latest_at
from public.v_book_target_health
group by resolution_date, capture_state;

revoke all on public.v_book_target_health, public.v_book_capture_health from public;
grant select on public.v_book_target_health, public.v_book_capture_health
  to anon, authenticated, service_role;

commit;
