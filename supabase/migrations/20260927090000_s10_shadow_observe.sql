-- ===========================================================================
-- S10 IN SHADOW, OBSERVE ONLY (plan v2 P7.4 part 1)
--
-- The P7.3 replay judged the remaining-day model (P7.2 stage 1) on months it
-- was designed on. The clean test is days it has never seen: at each of the
-- tick's checkpoints, record what the model would have said, beside the
-- engine's own call, and grade both against the venue later. Nothing here
-- prices or trades.
--
--   s10_day1_inputs         the day-before forecast run for a city's local
--                           day, fetched once between 07:00 and 09:00 local
--                           (Open-Meteo Previous Runs `_previous_day1`: the
--                           series the model was trained on), with the seven
--                           models' day-ahead maxima; one row per city-day
--   s10_shadow_checkpoints  the model's ladder over the venue's buckets at a
--                           checkpoint; one row per city, day, checkpoint and
--                           model version
--
-- Both append-only (a mistake is a new model_version, never an edit), and the
-- service role's alone. Small by design (the database is over its size
-- target, plan P1.6): about 48 + 240 rows a day.
-- ===========================================================================

create table if not exists public.s10_day1_inputs (
  city_key         text        not null,
  local_date       date        not null,
  fetched_at       timestamptz not null default now(),
  hourly           jsonb       not null,
  models           jsonb,
  models_spread_c  numeric,
  source           text        not null default 'open-meteo-previous-runs:previous_day1',
  primary key (city_key, local_date),
  constraint s10_day1_inputs_hourly_is_an_object check (jsonb_typeof(hourly) = 'object')
);

comment on table public.s10_day1_inputs is
  'The day-before forecast run (hourly temperature, cloud, radiation) and the seven models'' day-ahead maxima for one city-day, fetched once before the morning checkpoint (plan v2 P7.4). Input to the remaining-day model in shadow.';

create table if not exists public.s10_shadow_checkpoints (
  checkpoint_id        uuid        primary key default gen_random_uuid(),
  city_key             text        not null,
  target_date          date        not null,
  checkpoint           text        not null,
  decided_at           timestamptz not null default now(),
  local_decision_time  timestamp   not null,
  model_hour           integer     not null,
  model_version        text        not null,
  contract             text        not null,
  probs                jsonb       not null,
  top_band_id          text        not null,
  top_prob             numeric     not null,
  median_c             numeric,
  q10_c                numeric,
  q90_c                numeric,
  running_max_c        numeric     not null,
  inputs               jsonb,
  unique (city_key, target_date, checkpoint, model_version),
  constraint s10_shadow_label check (checkpoint in
    ('morning', 'noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h')),
  constraint s10_shadow_ladder_sums_to_one check (
    jsonb_typeof(probs) = 'object' and abs(public.ad4_probs_sum(probs) - 1) <= 1e-4),
  constraint s10_shadow_top_is_on_the_ladder check (probs ? top_band_id)
);

create index if not exists s10_shadow_checkpoints_day
  on public.s10_shadow_checkpoints (city_key, target_date, checkpoint);

comment on table public.s10_shadow_checkpoints is
  'What the remaining-day model (plan v2 P7.2) would have called at each tick checkpoint, never priced or traded: the live out-of-sample test (P7.4 part 1).';

do $$
declare t text;
begin
  foreach t in array array['s10_day1_inputs', 's10_shadow_checkpoints'] loop
    execute format('drop trigger if exists %I_immutable on public.%I', t, t);
    execute format('create trigger %I_immutable before update or delete on public.%I
                    for each row execute function arbdesk_private.immutable_record()', t, t);
    execute format('drop trigger if exists %I_no_truncate on public.%I', t, t);
    execute format('create trigger %I_no_truncate before truncate on public.%I
                    for each statement execute function arbdesk_private.immutable_record()', t, t);
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from public, anon, authenticated, service_role', t);
    execute format('grant select, insert on public.%I to service_role', t);
  end loop;
end $$;
