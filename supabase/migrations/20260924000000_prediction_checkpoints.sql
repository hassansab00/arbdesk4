-- ===========================================================================
-- WHAT WE SAID, WHEN WE SAID IT (plan v2 P4.1)
--
-- The desk's own record of its calls could be rewritten by its next run:
-- databank.py freezes the NEWEST band_probabilities row with no time limit,
-- and 302 of 496 frozen ladders were priced after the city's local day had
-- closed (audit, 23 Sep) - a scoreboard that grades the answer written after
-- the thermometer had spoken. This table holds one immutable row per city,
-- target date, checkpoint and engine version: the full ladder the engine
-- published at a fixed moment on the city's own clock (P4.2), with the market
-- beside it. Nothing updates it; a mistake is corrected by a new row under a
-- new engine_version, never by an edit.
--
-- Written by the hourly tick (P6.1, P4.2). Scored against the venue winner by
-- fact_checkpoint_outcome (P4.3). Read by the service role only.
-- ===========================================================================

create or replace function public.ad4_probs_sum(p jsonb)
returns numeric
language sql
immutable
set search_path = ''
as $$
  select coalesce(sum((v)::numeric), 0) from jsonb_each_text(p) as e(k, v)
$$;

create table if not exists public.prediction_checkpoints (
  checkpoint_id        uuid        primary key default gen_random_uuid(),
  city_key             text        not null,
  target_date          date        not null,
  checkpoint           text        not null,
  decided_at           timestamptz not null default now(),
  local_decision_time  timestamp   not null,
  engine_version       text        not null,
  model_path           text        not null,
  probs                jsonb       not null,
  top_band_id          text        not null,
  top_prob             numeric     not null,
  second_prob          numeric,
  centre_c             numeric,
  sigma_c              numeric,
  running_max_c        numeric,
  reading_at           timestamptz,
  reading_source       text,
  forecast_issued_at   timestamptz,
  forecast_model       text,
  market               jsonb,
  market_top_band_id   text,
  inputs_ok            boolean     not null default true,
  block_reason         text,
  unique (city_key, target_date, checkpoint, engine_version),
  constraint prediction_checkpoints_label check (checkpoint in
    ('d1_eve', 'morning', 'noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h')),
  constraint prediction_checkpoints_ladder_sums_to_one check (
    jsonb_typeof(probs) = 'object' and abs(public.ad4_probs_sum(probs) - 1) <= 1e-4),
  constraint prediction_checkpoints_top_is_on_the_ladder check (probs ? top_band_id),
  constraint prediction_checkpoints_blocked_says_why check (inputs_ok or block_reason is not null)
);

create index if not exists prediction_checkpoints_day
  on public.prediction_checkpoints (city_key, target_date, checkpoint);

comment on table public.prediction_checkpoints is
  'One immutable row per city, target date, checkpoint and engine version: the ladder the engine published at a fixed local time, with the market beside it (plan v2 P4.1). The honest record the scoreboard grades.';

drop trigger if exists prediction_checkpoints_immutable on public.prediction_checkpoints;
create trigger prediction_checkpoints_immutable before update or delete on public.prediction_checkpoints
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists prediction_checkpoints_no_truncate on public.prediction_checkpoints;
create trigger prediction_checkpoints_no_truncate before truncate on public.prediction_checkpoints
  for each statement execute function arbdesk_private.immutable_record();

alter table public.prediction_checkpoints enable row level security;
revoke all on public.prediction_checkpoints from public, anon, authenticated, service_role;
grant select, insert on public.prediction_checkpoints to service_role;
revoke all on function public.ad4_probs_sum(jsonb) from public, anon, authenticated;
grant execute on function public.ad4_probs_sum(jsonb) to service_role;
