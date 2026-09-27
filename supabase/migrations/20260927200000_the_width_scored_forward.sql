-- ===========================================================================
-- THE WIDTH AROUND THE CORRECTED CENTRE, SCORED FORWARD
-- (plan v2.3 P3.9 part 3, part 2)
--
-- Since #230 every station-corrected price records the stored width beside
-- the width it was served with (band_probabilities.station_width_c, sigma_c).
-- scripts/station_width_score.py takes each market the venue has confirmed,
-- its engine's LAST pricing before the city's local day began, and re-prices
-- that ladder with the stored width around the same centre. Only the width
-- differs, and each row proves it: re-priced with the served width, the
-- ladder must reproduce the stored probabilities (reproduce_max_abs).
--
-- One row per market, written once:
--   log_loss_*   the venue ladder's multiclass log loss, the winner's
--                probability floored at 1e-6 (the engine's PROB_FLOOR)
--   brier_*      multiclass Brier over the ladder
--   hit_*        the top pick is the winner (ties by band id, as the tick)
--   crps_*, cover80_*   against the whole-day station maximum, where one
--                existed when the market was scored (all four null together)
--
-- The nightly verdict (logged as P3.9_width_score) is the date-block
-- bootstrap of the log-loss gain over these rows. settings.station_width_pricing
-- goes on only when its lower 90% bound is above zero.
--
-- Append-only, service role only. Re-runnable.
-- ===========================================================================

create table if not exists public.fact_station_width_score (
  market_id          uuid        primary key,
  city_key           text        not null,
  for_date           date        not null,
  unit               text        not null check (unit in ('C', 'F')),
  priced_at          timestamptz not null,
  lead_days          int         not null check (lead_days >= 1),
  forecast_version   uuid,
  centre_c           numeric     not null,
  served_sigma_c     numeric     not null check (served_sigma_c > 0),
  station_width_c    numeric     not null check (station_width_c > 0),
  n_bands            int         not null check (n_bands > 0),
  winner_band_id     uuid        not null,
  reproduce_max_abs  numeric     not null check (reproduce_max_abs >= 0),
  p_winner_served    numeric     not null,
  p_winner_width     numeric     not null,
  log_loss_served    numeric     not null,
  log_loss_width     numeric     not null,
  brier_served       numeric     not null,
  brier_width        numeric     not null,
  hit_served         boolean     not null,
  hit_width          boolean     not null,
  station_max_c      numeric,
  crps_served        numeric,
  crps_width         numeric,
  cover80_served     boolean,
  cover80_width      boolean,
  scored_at          timestamptz not null default clock_timestamp(),
  constraint fact_station_width_score_station_whole check (
    (station_max_c is null) = (crps_served is null)
    and (station_max_c is null) = (crps_width is null)
    and (station_max_c is null) = (cover80_served is null)
    and (station_max_c is null) = (cover80_width is null))
);

create index if not exists fact_station_width_score_day
  on public.fact_station_width_score (for_date);

comment on table public.fact_station_width_score is
  'Plan v2.3 P3.9 part 3: per confirmed market, the engine''s last day-ahead ladder as served against the same ladder re-priced with the stored station width around the same centre: log loss, Brier, top-pick hit on the venue''s winner, and CRPS and 80% coverage against the whole-day station maximum. Written once by scripts/station_width_score.py. Append-only.';

drop trigger if exists fact_station_width_score_immutable on public.fact_station_width_score;
create trigger fact_station_width_score_immutable before update or delete on public.fact_station_width_score
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists fact_station_width_score_no_truncate on public.fact_station_width_score;
create trigger fact_station_width_score_no_truncate before truncate on public.fact_station_width_score
  for each statement execute function arbdesk_private.immutable_record();

alter table public.fact_station_width_score enable row level security;
revoke all on public.fact_station_width_score from public, anon, authenticated, service_role;
grant select, insert on public.fact_station_width_score to service_role;

do $$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english)
    values ('fact_station_width_score', 'scored_at', 30, 'databank',
            'Each settled market''s day-ahead ladder as served, against the same ladder with the station width fitted to the corrected forecast''s own errors: which one the venue''s answer favoured.')
    on conflict (table_name) do update set
      ts_column     = excluded.ts_column,
      fresh_hours   = excluded.fresh_hours,
      layer         = excluded.layer,
      plain_english = excluded.plain_english;
  end if;
end $$;
