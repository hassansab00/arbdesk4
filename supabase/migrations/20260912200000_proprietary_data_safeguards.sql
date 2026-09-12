-- Phase 0: preserve ArbDesk's proprietary research record before repairs.
-- Additive only: this migration never updates or deletes a source row.
begin;

create schema if not exists arbdesk_private;
-- Existing authenticated paper-command wrappers intentionally call private
-- implementation functions in this schema. Preserve that USAGE grant while
-- keeping the schema unavailable to the anonymous role.
revoke all on schema arbdesk_private from public, anon;

create or replace function arbdesk_private.immutable_record() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'Append-only record; write a linked correction instead';
end
$$;
revoke all on function arbdesk_private.immutable_record() from public;

create table if not exists public.proprietary_data_manifests (
  manifest_id uuid primary key default gen_random_uuid(),
  command_key text not null unique,
  dataset text not null,
  scope jsonb not null,
  row_count bigint not null check (row_count >= 0),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  code_version text not null,
  captured_at timestamptz not null default clock_timestamp()
);
create index if not exists proprietary_manifest_dataset_time
  on public.proprietary_data_manifests(dataset, captured_at desc);
alter table public.proprietary_data_manifests enable row level security;
revoke all on public.proprietary_data_manifests from public, anon, authenticated, service_role;
grant select, insert on public.proprietary_data_manifests to service_role;
drop trigger if exists proprietary_manifest_immutable on public.proprietary_data_manifests;
create trigger proprietary_manifest_immutable
  before update or delete on public.proprietary_data_manifests
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists proprietary_manifest_no_truncate on public.proprietary_data_manifests;
create trigger proprietary_manifest_no_truncate
  before truncate on public.proprietary_data_manifests
  for each statement execute function arbdesk_private.immutable_record();

create table if not exists public.proprietary_data_corrections (
  correction_id uuid primary key default gen_random_uuid(),
  source_relation text not null,
  source_key jsonb not null,
  original_payload_hash text not null,
  corrected_values jsonb not null,
  reason text not null check (length(trim(reason)) > 0),
  evidence jsonb not null default '{}'::jsonb,
  code_version text not null,
  recorded_at timestamptz not null default clock_timestamp()
);
create index if not exists proprietary_correction_source_time
  on public.proprietary_data_corrections(source_relation, recorded_at desc);
alter table public.proprietary_data_corrections enable row level security;
revoke all on public.proprietary_data_corrections from public, anon, authenticated, service_role;
grant select, insert on public.proprietary_data_corrections to service_role;
drop trigger if exists proprietary_correction_immutable on public.proprietary_data_corrections;
create trigger proprietary_correction_immutable
  before update or delete on public.proprietary_data_corrections
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists proprietary_correction_no_truncate on public.proprietary_data_corrections;
create trigger proprietary_correction_no_truncate
  before truncate on public.proprietary_data_corrections
  for each statement execute function arbdesk_private.immutable_record();

create table if not exists public.proprietary_data_quality_flags (
  flag_id uuid primary key default gen_random_uuid(),
  source_relation text not null,
  source_key jsonb not null,
  issue_code text not null,
  severity text not null check (severity in ('info', 'warning', 'critical')),
  evidence jsonb not null default '{}'::jsonb,
  detector_version text not null,
  detected_at timestamptz not null default clock_timestamp()
);
create index if not exists proprietary_quality_source_time
  on public.proprietary_data_quality_flags(source_relation, detected_at desc);
alter table public.proprietary_data_quality_flags enable row level security;
revoke all on public.proprietary_data_quality_flags from public, anon, authenticated, service_role;
grant select, insert on public.proprietary_data_quality_flags to service_role;
drop trigger if exists proprietary_quality_immutable on public.proprietary_data_quality_flags;
create trigger proprietary_quality_immutable
  before update or delete on public.proprietary_data_quality_flags
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists proprietary_quality_no_truncate on public.proprietary_data_quality_flags;
create trigger proprietary_quality_no_truncate
  before truncate on public.proprietary_data_quality_flags
  for each statement execute function arbdesk_private.immutable_record();

-- Freeze the settled research facts. Their writers already use
-- resolution=ignore-duplicates, so INSERT is sufficient for safe retries.
do $phase0$
declare
  t text;
begin
  foreach t in array array[
    'fact_forecast_outcome',
    'fact_band_outcome',
    'fact_signal_outcome'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;
    execute format('drop trigger if exists proprietary_fact_immutable on public.%I', t);
    execute format(
      'create trigger proprietary_fact_immutable before update or delete on public.%I '
      'for each row execute function arbdesk_private.immutable_record()', t);
    execute format('drop trigger if exists proprietary_fact_no_truncate on public.%I', t);
    execute format(
      'create trigger proprietary_fact_no_truncate before truncate on public.%I '
      'for each statement execute function arbdesk_private.immutable_record()', t);
    execute format('revoke update, delete, truncate on public.%I from service_role, anon, authenticated', t);
    execute format('grant select, insert on public.%I to service_role', t);
  end loop;
end
$phase0$;

-- Copy the proprietary facts that pre-date research_captures into the already
-- immutable revision archive. This is a copy, never a move or rewrite.
do $phase0$
declare
  t text;
begin
  if to_regclass('public.research_captures') is null then
    raise exception 'research_captures is required before proprietary safeguards';
  end if;
  foreach t in array array[
    'band_probabilities',
    'signals',
    'model_versions',
    'fact_forecast_outcome',
    'fact_band_outcome',
    'fact_signal_outcome'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;
    execute format($copy$
      with source_rows as (
        select to_jsonb(s) as payload from public.%I s
      )
      insert into public.research_captures(
        command_key, engine_version, provenance,
        source_relation, source_key, payload, payload_hash
      )
      select
        'phase0-baseline:' || %L || ':' || md5(payload::text),
        'phase0-data-protection-v1',
        'historical_import',
        %L,
        coalesce(
          payload->>'prob_id', payload->>'signal_id', payload->>'version_id',
          payload->>'band_id', payload->>'city_key', 'row'
        ) || ':' || coalesce(payload->>'for_date', ''),
        payload,
        md5(payload::text)
      from source_rows
      on conflict do nothing
    $copy$, t, t, t);
  end loop;
end
$phase0$;

commit;
