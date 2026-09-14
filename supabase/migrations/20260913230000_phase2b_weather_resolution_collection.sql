-- Phase 2B: authoritative, append-only weather-resolution collection.
--
-- Successful final readings live in weather_resolution_evidence. This
-- companion table records every collection decision without weakening the
-- verified-outcome boundary. Neither table is ever rewritten.
begin;

create table if not exists public.weather_resolution_attempts (
  attempt_id text primary key,
  market_id uuid references public.markets(market_id),
  city_key text not null references public.cities(city_key),
  for_date date not null,
  source_authority text not null check (length(trim(source_authority)) > 0),
  station_id text,
  source_url text not null check (length(trim(source_url)) > 0),
  outcome_status text not null check (outcome_status in (
    'captured', 'unchanged', 'not_final', 'source_unavailable',
    'unsupported_source', 'parse_error', 'source_revised', 'dry_run'
  )),
  observed_max_c numeric check (observed_max_c between -100 and 70),
  payload_sha256 text check (payload_sha256 is null or length(payload_sha256) = 64),
  parser_version text not null check (length(trim(parser_version)) > 0),
  detail jsonb not null default '{}'::jsonb,
  captured_at timestamptz not null default clock_timestamp()
);

comment on table public.weather_resolution_attempts is
  'APPEND ONLY. Auditable authoritative outcome-collection attempts. Failures and not-final decisions never masquerade as successful evidence.';

create index if not exists weather_resolution_attempt_city_day_time
  on public.weather_resolution_attempts(city_key, for_date, captured_at desc);
create index if not exists weather_resolution_attempt_status_time
  on public.weather_resolution_attempts(outcome_status, captured_at desc);

alter table public.weather_resolution_attempts enable row level security;
revoke all on public.weather_resolution_attempts
  from public, anon, authenticated, service_role;
grant select, insert on public.weather_resolution_attempts to service_role;

drop trigger if exists weather_resolution_attempt_immutable
  on public.weather_resolution_attempts;
create trigger weather_resolution_attempt_immutable
  before update or delete on public.weather_resolution_attempts
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists weather_resolution_attempt_no_truncate
  on public.weather_resolution_attempts;
create trigger weather_resolution_attempt_no_truncate
  before truncate on public.weather_resolution_attempts
  for each statement execute function arbdesk_private.immutable_record();

create or replace view public.v_weather_resolution_collection_health
with (security_invoker = true)
as
select
  count(*)::bigint as attempts,
  count(*) filter (where outcome_status = 'captured')::bigint as captured,
  count(*) filter (where outcome_status = 'unchanged')::bigint as unchanged,
  count(*) filter (where outcome_status = 'not_final')::bigint as not_final,
  count(*) filter (where outcome_status in
    ('source_unavailable', 'unsupported_source', 'parse_error', 'source_revised'))::bigint
    as needs_attention,
  max(captured_at) as latest_attempt_at,
  max(captured_at) filter (where outcome_status = 'captured') as latest_capture_at
from public.weather_resolution_attempts;

revoke all on public.v_weather_resolution_collection_health
  from public, anon, authenticated, service_role;
grant select on public.v_weather_resolution_collection_health to service_role;

do $ad4$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec
      (table_name, ts_column, fresh_hours, layer, plain_english)
    values
      ('weather_resolution_attempts', 'captured_at', 30, 'databank',
       'Auditable attempts to collect final contract-authority weather outcomes.')
    on conflict (table_name) do update set
      ts_column = excluded.ts_column,
      fresh_hours = excluded.fresh_hours,
      layer = excluded.layer,
      plain_english = excluded.plain_english;
  end if;
end
$ad4$;

commit;
