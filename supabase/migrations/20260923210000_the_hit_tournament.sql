-- ===========================================================================
-- THE HIT TOURNAMENT'S RECORD (plan v2.1 P3.8)
--
-- scripts/hit_tournament.py scores, every night and per city, every pricing
-- recipe it can build (which forecast model or blend is the centre, which
-- bias correction, how wide) on the one thing the desk is paid for: the
-- probability it put on the bucket that settled. Out of sample - whatever
-- prices day d was fitted on days before d only - and against the live
-- engine's own price and the market's, as they stood at the same cutoff.
--
--   derived_hit_tournament  per city x checkpoint x lane x recipe: its
--                           forward score (the city's top recipes and the
--                           pooled champion; not every candidate)
--   derived_hit_recipe      per city x checkpoint x lane: the champion, its
--                           bounded parameters, version and state
--   derived_hit_summary     per unit x checkpoint x lane: champion, live
--                           engine and market side by side, with intervals
--
-- state is 'shadow' until a recipe passes its gate against the live engine
-- AND the engine reads it; neither is true on day one, and the table says so
-- rather than implying otherwise. Rule 11: every parameter is bounded, moves
-- at most a set step per night from the previous night's value (prev_params),
-- and every row carries the version of the recipe that produced it.
-- ===========================================================================

create table if not exists public.derived_hit_tournament (
  city_key          text        not null,
  checkpoint        text        not null,
  lane              text        not null,          -- asof | research
  recipe            text        not null,          -- e.g. centre=mean|bias=recent|width=1.25
  n_days            int         not null,
  log_loss          numeric,                       -- mean -ln p(winner), forward
  hit_rate          numeric,                       -- top pick = winner
  brier             numeric,
  rps               numeric,
  rank_in_city      int,
  is_pooled_champion boolean    not null default false,
  computed_at       timestamptz not null default now(),
  primary key (city_key, checkpoint, lane, recipe)
);

create table if not exists public.derived_hit_recipe (
  city_key          text        not null,
  checkpoint        text        not null,
  lane              text        not null,
  recipe            text        not null,
  params            jsonb       not null default '{}'::jsonb,   -- bounded, stepped
  prev_params       jsonb,
  recipe_version    text        not null,
  source            text        not null,          -- city | pooled
  state             text        not null default 'shadow',
  n_days            int         not null default 0,
  log_loss          numeric,
  hit_rate          numeric,
  live_log_loss     numeric,
  live_hit_rate     numeric,
  market_log_loss   numeric,
  market_hit_rate   numeric,
  gain_vs_live      numeric,                       -- mean per-day log-loss gain, common days
  gain_vs_live_lo   numeric,
  gain_vs_live_hi   numeric,
  n_vs_live         int,
  reason            text        not null default '',
  computed_at       timestamptz not null default now(),
  primary key (city_key, checkpoint, lane),
  constraint derived_hit_recipe_state check (state in ('shadow', 'live'))
);

create table if not exists public.derived_hit_summary (
  unit              text        not null,
  checkpoint        text        not null,
  lane              text        not null,
  n_days            int         not null,
  champion_hit_rate numeric,
  champion_log_loss numeric,
  live_hit_rate     numeric,
  live_log_loss     numeric,
  market_hit_rate   numeric,
  market_log_loss   numeric,
  uniform_log_loss  numeric,
  n_live_days       int,
  n_market_days     int,
  gain_vs_live      numeric,
  gain_vs_live_lo   numeric,
  gain_vs_live_hi   numeric,
  gain_vs_market    numeric,
  gain_vs_market_lo numeric,
  gain_vs_market_hi numeric,
  computed_at       timestamptz not null default now(),
  primary key (unit, checkpoint, lane)
);

comment on table public.derived_hit_recipe is
  'Per city and checkpoint: the pricing recipe that best predicts the winning bucket, forward-scored (plan v2.1 P3.8). state=shadow until it beats the live engine under the bootstrap gate and the engine reads it.';

alter table public.derived_hit_tournament enable row level security;
alter table public.derived_hit_recipe     enable row level security;
alter table public.derived_hit_summary    enable row level security;
revoke all on public.derived_hit_tournament from public, anon, authenticated;
revoke all on public.derived_hit_recipe     from public, anon, authenticated;
revoke all on public.derived_hit_summary    from public, anon, authenticated;
grant select, insert, update on public.derived_hit_tournament to service_role;
grant select, insert, update on public.derived_hit_recipe     to service_role;
grant select, insert, update on public.derived_hit_summary    to service_role;
