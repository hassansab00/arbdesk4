-- ===========================================================================
-- ENGINE VARIANTS IN SHADOW, OBSERVE ONLY (P1.1's candidate, 4 Oct 2026)
--
-- docs/P11_DA_FLOOR_PREREG.md pre-registers a forward test of `da_floor`: at
-- each same-day checkpoint, the day-ahead call's centre and width cut by the
-- observed floor, against the ladder the engine serves. scripts/variant_shadow.py
-- writes one row here beside every same-day call the tick writes, with the
-- inputs it was built from, so the ladder can be recomputed and graded later.
-- Nothing here prices or trades.
--
-- Append-only (a changed definition is a new variant_version, never an edit)
-- and the service role's alone, like s10_shadow_checkpoints. Small: at most
-- five rows per city-day, about 240 a day.
-- ===========================================================================

create table if not exists public.variant_shadow_checkpoints (
  shadow_id            uuid        primary key default gen_random_uuid(),
  city_key             text        not null,
  target_date          date        not null,
  checkpoint           text        not null,
  variant              text        not null,
  variant_version      text        not null,
  decided_at           timestamptz not null default now(),
  engine_version       text        not null,
  day_ahead_centre_c   numeric     not null,
  day_ahead_sigma_c    numeric     not null,
  day_ahead_priced_at  timestamptz not null,
  day_ahead_lead_days  integer,
  floor_c              numeric,
  q_down               numeric,
  q_up                 numeric,
  served_centre_c      numeric,
  served_sigma_c       numeric,
  served_calibrated    boolean     not null default false,
  unit                 text        not null,
  probs                jsonb       not null,
  top_band_id          text        not null,
  top_prob             numeric     not null,
  unique (city_key, target_date, checkpoint, variant),
  constraint variant_shadow_label check (checkpoint in
    ('morning', 'noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h')),
  constraint variant_shadow_version_names_the_variant check (variant_version like variant || ':%'),
  constraint variant_shadow_width_is_positive check (day_ahead_sigma_c > 0),
  constraint variant_shadow_call_before_the_day check (day_ahead_priced_at < decided_at),
  constraint variant_shadow_q_with_a_floor check (
    (floor_c is null and q_down is null and q_up is null)
    or (floor_c is not null and q_down is not null and q_up is not null
        and q_down between 0 and 0.5 and q_up between 0 and 0.5)),
  constraint variant_shadow_unit check (unit in ('C', 'F')),
  constraint variant_shadow_ladder_sums_to_one check (
    jsonb_typeof(probs) = 'object' and abs(public.ad4_probs_sum(probs) - 1) <= 1e-4),
  constraint variant_shadow_top_is_on_the_ladder check (probs ? top_band_id)
);

create index if not exists variant_shadow_checkpoints_day
  on public.variant_shadow_checkpoints (city_key, target_date, checkpoint);

comment on table public.variant_shadow_checkpoints is
  'Engine variants recorded beside each same-day tick checkpoint, never priced or traded. da_floor:v1 = the day-ahead call''s centre and width (the last band_probabilities pricing before local midnight) cut by the served floor with the engine''s q (docs/P11_DA_FLOOR_PREREG.md, scripts/variant_shadow.py).';

do $$
begin
  drop trigger if exists variant_shadow_checkpoints_immutable on public.variant_shadow_checkpoints;
  create trigger variant_shadow_checkpoints_immutable before update or delete on public.variant_shadow_checkpoints
    for each row execute function arbdesk_private.immutable_record();
  drop trigger if exists variant_shadow_checkpoints_no_truncate on public.variant_shadow_checkpoints;
  create trigger variant_shadow_checkpoints_no_truncate before truncate on public.variant_shadow_checkpoints
    for each statement execute function arbdesk_private.immutable_record();
end $$;

alter table public.variant_shadow_checkpoints enable row level security;
revoke all on public.variant_shadow_checkpoints from public, anon, authenticated, service_role;
grant select, insert on public.variant_shadow_checkpoints to service_role;
