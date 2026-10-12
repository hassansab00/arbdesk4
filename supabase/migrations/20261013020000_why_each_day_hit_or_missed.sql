-- ===========================================================================
-- Why each city-day hit or missed (13 Oct).
--
-- Hassan, 12 Oct: each day has to teach the model which weather made a
-- prediction hit or miss, and show it. tools/wxpredict/day_review.py writes,
-- every night, one row a settled city-day: the winning bucket, the station's
-- maximum and when it came against when it usually comes, the lead-1
-- forecast and its error, the seven models' maxima and the closest, what the
-- station reported between 09 and 17 local against what the forecast said
-- (cloud, dew point, wind, rain; each difference also as a z against the
-- city's own previous 60 days), and at each checkpoint the engine's, the
-- market's and wxrd-v2's top bucket and whether it won. The city popup reads
-- it.
--
-- Facts, not a fitted explanation: the record shows what differed and by how
-- much, and does not attribute the error to it. A night recomputes the last
-- three settled dates (a late settlement is reviewed again), so a row is
-- replaced, never appended; the mirror copies the table each night
-- (scripts/mirror_to_repo.py). Re-runnable.
-- ===========================================================================
create table if not exists public.derived_day_review (
  city_key          text not null,
  target_date       date not null,
  unit              text,
  winner_band_id    text,
  winner_label      text,
  station_max_c     numeric,
  peak_local_hour   numeric,
  usual_peak_hour   numeric,
  forecast_max_c    numeric,
  forecast_error_c  numeric,
  models            jsonb not null default '{}'::jsonb,
  closest_model     text,
  afternoon         jsonb not null default '{}'::jsonb,
  surprises         jsonb not null default '[]'::jsonb,
  calls             jsonb not null default '[]'::jsonb,
  why               text,
  version           text not null,
  computed_at       timestamptz not null default now(),
  primary key (city_key, target_date)
);

comment on table public.derived_day_review is
  '13 Oct: why each settled city-day hit or missed - the outcome, the lead-1 forecast and its error, the afternoon''s weather against the forecast (z against the city''s own 60 days), and each checkpoint''s engine, market and wxrd-v2 call. tools/wxpredict/day_review.py, nightly; the city popup reads it.';

alter table public.derived_day_review enable row level security;
revoke all on public.derived_day_review from public, anon, authenticated, service_role;
grant select on public.derived_day_review to anon, authenticated;
grant select, insert, update on public.derived_day_review to service_role;
drop policy if exists derived_day_review_read on public.derived_day_review;
create policy derived_day_review_read on public.derived_day_review for select
  to anon, authenticated, service_role using (true);
drop policy if exists derived_day_review_write on public.derived_day_review;
create policy derived_day_review_write on public.derived_day_review for insert
  to service_role with check (true);
drop policy if exists derived_day_review_rewrite on public.derived_day_review;
create policy derived_day_review_rewrite on public.derived_day_review for update
  to service_role using (true) with check (true);
