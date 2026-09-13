-- Phase 1D: authoritative metadata is recorded as evidence, never guessed
-- into the city roster. A correction is a new immutable revision; collected
-- forecasts, observations and research outputs are never rewritten.
begin;

create table if not exists public.city_metadata_evidence (
  evidence_id uuid primary key default gen_random_uuid(),
  city_key text not null references public.cities(city_key),
  field_group text not null check (field_group in (
    'coordinates', 'timezone', 'resolution_station', 'source_capability'
  )),
  verification_state text not null check (verification_state in (
    'candidate', 'verified', 'rejected'
  )),
  latitude numeric check (latitude is null or latitude between -90 and 90),
  longitude numeric check (longitude is null or longitude between -180 and 180),
  timezone text,
  station_id text,
  source_name text not null check (length(trim(source_name)) > 0),
  authority_url text,
  evidence jsonb not null default '{}'::jsonb,
  source_payload_hash text not null check (length(trim(source_payload_hash)) > 0),
  supersedes uuid references public.city_metadata_evidence(evidence_id),
  recorded_at timestamptz not null default clock_timestamp(),
  verified_at timestamptz,
  check (field_group <> 'coordinates' or verification_state = 'rejected'
         or (latitude is not null and longitude is not null)),
  check (field_group <> 'timezone' or verification_state = 'rejected'
         or nullif(trim(timezone), '') is not null),
  check (verification_state <> 'verified'
         or (verified_at is not null and nullif(trim(authority_url), '') is not null)),
  unique (city_key, field_group, source_payload_hash, verification_state)
);

create index if not exists city_metadata_evidence_latest
  on public.city_metadata_evidence(city_key, field_group, recorded_at desc);

alter table public.city_metadata_evidence enable row level security;
revoke all on public.city_metadata_evidence from public, anon, authenticated, service_role;
grant select, insert on public.city_metadata_evidence to service_role;
-- Coordinates, timezones and verification states are public operational
-- metadata. Raw evidence and hashes remain server-only.
grant select (
  evidence_id, city_key, field_group, verification_state, latitude, longitude,
  timezone, station_id, source_name, authority_url, supersedes, recorded_at,
  verified_at
) on public.city_metadata_evidence to anon, authenticated;
drop policy if exists city_metadata_status_read on public.city_metadata_evidence;
create policy city_metadata_status_read on public.city_metadata_evidence
  for select to anon, authenticated using (true);

drop trigger if exists city_metadata_evidence_immutable on public.city_metadata_evidence;
create trigger city_metadata_evidence_immutable
  before update or delete on public.city_metadata_evidence
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists city_metadata_evidence_no_truncate on public.city_metadata_evidence;
create trigger city_metadata_evidence_no_truncate
  before truncate on public.city_metadata_evidence
  for each statement execute function arbdesk_private.immutable_record();

-- Preserve a review queue for values that appeared after Phase 1 found them
-- missing. These are deliberately candidates, not verified facts: the row
-- records what needs checking without inventing an authority.
insert into public.city_metadata_evidence(
  city_key, field_group, verification_state, latitude, longitude, timezone,
  source_name, evidence, source_payload_hash
)
select c.city_key,
       case when q.issue_code = 'active_city_missing_coordinates'
            then 'coordinates' else 'timezone' end,
       'candidate', c.latitude, c.longitude, c.timezone,
       'phase1d_quality_review',
       jsonb_build_object(
         'reason', 'value is present now but was missing when Phase 1 inspected it; verify against an authoritative station source',
         'original_finding', q.evidence,
         'finding_detected_at', q.detected_at
       ),
       md5(c.city_key || ':' || q.issue_code || ':' ||
           coalesce(c.latitude::text, '') || ':' || coalesce(c.longitude::text, '') || ':' ||
           coalesce(c.timezone, ''))
from public.cities c
join public.proprietary_data_quality_flags q
  on q.source_relation = 'cities'
 and q.source_key ->> 'city_key' = c.city_key
 and q.issue_code in ('active_city_missing_coordinates', 'active_city_missing_timezone')
where (q.issue_code = 'active_city_missing_coordinates'
       and c.latitude is not null and c.longitude is not null)
   or (q.issue_code = 'active_city_missing_timezone'
       and nullif(trim(c.timezone), '') is not null)
on conflict (city_key, field_group, source_payload_hash, verification_state) do nothing;

create or replace view public.v_city_metadata_verification
with (security_invoker = true)
as
with latest as (
  select distinct on (e.city_key, e.field_group)
         e.city_key, e.field_group, e.verification_state, e.latitude,
         e.longitude, e.timezone, e.station_id, e.source_name,
         e.authority_url, e.recorded_at, e.verified_at
  from public.city_metadata_evidence e
  order by e.city_key, e.field_group, e.recorded_at desc, e.evidence_id desc
), coord as (
  select * from latest where field_group = 'coordinates'
), tz as (
  select * from latest where field_group = 'timezone'
)
select c.city_key, c.display_name,
       coalesce(coord.latitude, c.latitude) as latitude,
       coalesce(coord.longitude, c.longitude) as longitude,
       coalesce(tz.timezone, c.timezone) as timezone,
       case
         when coord.verification_state = 'verified' then 'verified'
         when coord.verification_state = 'candidate' then 'candidate'
         when coord.verification_state = 'rejected' then 'rejected'
         when c.latitude is null or c.longitude is null then 'missing'
         else 'legacy_present'
       end as coordinate_state,
       case
         when tz.verification_state = 'verified' then 'verified'
         when tz.verification_state = 'candidate' then 'candidate'
         when tz.verification_state = 'rejected' then 'rejected'
         when nullif(trim(c.timezone), '') is null then 'missing'
         else 'legacy_present'
       end as timezone_state,
       coord.authority_url as coordinate_authority_url,
       tz.authority_url as timezone_authority_url,
       greatest(coord.recorded_at, tz.recorded_at) as latest_evidence_at
from public.cities c
left join coord using (city_key)
left join tz using (city_key);

create or replace view public.v_city_metadata_health
with (security_invoker = true)
as
select count(*)::integer as cities,
       count(*) filter (where coordinate_state = 'verified')::integer as coordinates_verified,
       count(*) filter (where coordinate_state = 'candidate')::integer as coordinates_to_verify,
       count(*) filter (where coordinate_state = 'missing')::integer as coordinates_missing,
       count(*) filter (where timezone_state = 'verified')::integer as timezones_verified,
       count(*) filter (where timezone_state = 'candidate')::integer as timezones_to_verify,
       count(*) filter (where timezone_state = 'missing')::integer as timezones_missing
from public.v_city_metadata_verification;

revoke all on public.v_city_metadata_verification, public.v_city_metadata_health from public;
grant select on public.v_city_metadata_verification, public.v_city_metadata_health
  to anon, authenticated, service_role;

commit;
