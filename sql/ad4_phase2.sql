-- ===========================================================================
-- AD4 PHASE 2 SCHEMA
-- Run after ad4_schema.sql, ad4_functions*.sql, ad4_phase1_tables.sql
-- Run this whole file in the Supabase SQL editor. Idempotent: every
-- create/alter uses IF NOT EXISTS / OR REPLACE so it is safe to re-run.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- COST MODEL PARAMETERS
-- Fees are SOURCED from Polymarket: fee = shares * rate * p * (1-p)
-- weather rate = 0.05, taker only, maker rebate 0.25
-- --------------------------------------------------------------------------
create table if not exists cost_params (
  version_id     uuid primary key default gen_random_uuid(),
  label          text not null,
  taker_fee_rate numeric not null default 0.05,
  maker_fee_rate numeric not null default 0.0,
  maker_rebate   numeric not null default 0.25,
  gas_usd        numeric not null default 0.01,
  active         boolean not null default false,
  created_at     timestamptz not null default now()
);
comment on table cost_params is
  'SOURCED from Polymarket fee schedule 30 Mar 2026. Weather markets: taker only, rate 0.05, peak 1.25% at p=0.50. Makers pay zero and earn rebate.';

insert into cost_params (label, taker_fee_rate, maker_fee_rate, maker_rebate, gas_usd, active)
values ('polymarket_weather_2026_03', 0.05, 0.0, 0.25, 0.01, true)
on conflict do nothing;

-- --------------------------------------------------------------------------
-- BAND PROBABILITIES
-- Note: band_probabilities already exists in the base schema. This adds the
-- columns the probability engine needs.
-- --------------------------------------------------------------------------
alter table band_probabilities add column if not exists forecast_max_c numeric;
alter table band_probabilities add column if not exists bias_applied_c numeric;
alter table band_probabilities add column if not exists sigma_c numeric;
alter table band_probabilities add column if not exists lead_days int;
alter table band_probabilities add column if not exists lattice_applied boolean default false;
alter table band_probabilities add column if not exists confidence numeric;
alter table band_probabilities add column if not exists regime_label text;

comment on column band_probabilities.bias_applied_c is
  'Station bias correction from derived_forecast_skill.bias_c. The gap between grid forecast and what the airfield actually reads. This is the proprietary edge.';
comment on column band_probabilities.sigma_c is
  'Distribution width, derived from measured MAE. Honest uncertainty, not assumed.';
comment on column band_probabilities.lattice_applied is
  'Whole-number resolution layer. Polymarket settles on an integer; 27.4 and 27.6 are the same weather and different money.';

-- --------------------------------------------------------------------------
-- EDGES — computed per band per side, net of costs
-- --------------------------------------------------------------------------
create table if not exists edges (
  edge_id            bigserial primary key,
  band_id            uuid not null references bands(band_id) on delete cascade,
  computed_at        timestamptz not null default now(),
  side               text not null check (side in ('YES','NO')),
  model_prob         numeric,
  market_price       numeric,          -- executable, depth-weighted
  quoted_price       numeric,          -- top of book, for reference
  edge_pp            numeric,          -- probability points, gross
  edge_net_pp        numeric,          -- after fees + spread
  edge_per_dollar    numeric,
  fillable_usd_2c    numeric,
  fillable_usd_5c    numeric,
  fillable_usd_10c   numeric,
  est_fee            numeric,
  est_slippage       numeric,
  book_snapshot_id   bigint references book_snapshots(snapshot_id),
  prob_id            bigint references band_probabilities(prob_id),
  confidence         numeric,
  regime_label       text,
  tradeable          boolean not null default false,
  block_reason       text
);
create index if not exists idx_edges_band_time on edges (band_id, computed_at desc);
create index if not exists idx_edges_time on edges (computed_at desc);
comment on column edges.market_price is
  'Depth-weighted executable price from walking the ladder. NEVER top-of-book - that produces recommendations that cannot be filled.';
comment on column edges.block_reason is
  'Why this is not tradeable: below_7c_yes / far_from_forecast / dead_band / no_book / stale_data / anomaly';

-- --------------------------------------------------------------------------
-- ANOMALY GUARD
-- If the model says 90% and the market says 10%, something is broken -
-- stale forecast, misparsed band, wrong station, unit error. Nothing else
-- in the system is suspicious of its own good news.
-- --------------------------------------------------------------------------
create table if not exists anomaly_rules (
  rule_id     text primary key,
  description text,
  threshold   numeric,
  action      text check (action in ('block','warn','log')),
  active      boolean not null default true
);

insert into anomaly_rules (rule_id, description, threshold, action) values
  ('implausible_edge',   'Edge exceeds threshold in probability points - treat as data quality alert', 0.40, 'block'),
  ('stale_forecast',     'Forecast older than N hours',                                                12,   'block'),
  ('stale_book',         'Book snapshot older than N minutes',                                         60,   'warn'),
  ('prob_sum_drift',     'Band probabilities sum deviates from 1 by more than threshold',              0.02, 'warn'),
  ('bias_exceeds_mae',   'Station bias >= MAE suggests systematic error not noise - investigate',      0.95, 'warn')
on conflict (rule_id) do nothing;

-- --------------------------------------------------------------------------
-- CORRELATION — cities under one synoptic system are ONE bet, not many
-- --------------------------------------------------------------------------
create table if not exists derived_city_correlation (
  city_a       text not null references cities(city_key),
  city_b       text not null references cities(city_key),
  computed_at  timestamptz not null default now(),
  n_days       int,
  err_corr     numeric,     -- correlation of FORECAST ERRORS, not temperatures
  primary key (city_a, city_b, computed_at)
);
comment on table derived_city_correlation is
  'Correlation of forecast ERRORS. Cities whose errors move together share risk even if geographically distant. Ten European positions under one system is one position.';

-- --------------------------------------------------------------------------
-- CAPACITY
-- --------------------------------------------------------------------------
create table if not exists derived_capacity (
  city_key      text not null references cities(city_key),
  computed_at   timestamptz not null default now(),
  hour_utc      int,
  usd_at_2c     numeric,
  usd_at_5c     numeric,
  usd_at_10c    numeric,
  usd_full      numeric,
  live_bands    int,
  primary key (city_key, computed_at, hour_utc)
);
comment on table derived_capacity is
  'Capacity is a CURVE not a number. How much is available depends on how much price impact is acceptable - a trading decision, not a measurement.';

-- --------------------------------------------------------------------------
-- STRATEGY CONFLICT LOG
-- --------------------------------------------------------------------------
create table if not exists strategy_conflicts (
  conflict_id  bigserial primary key,
  detected_at  timestamptz not null default now(),
  band_id      uuid references bands(band_id),
  strategy_a   text,
  strategy_b   text,
  kind         text,
  resolution   text
);

-- --------------------------------------------------------------------------
-- PAPER TRADE additions
-- --------------------------------------------------------------------------
alter table paper_trades add column if not exists max_slippage_setting numeric;
alter table paper_trades add column if not exists fill_quality numeric;
alter table paper_trades add column if not exists legs_requested int;
alter table paper_trades add column if not exists legs_filled int;
alter table paper_trades add column if not exists approved_by_user boolean default true;

-- --------------------------------------------------------------------------
-- SETTINGS seed - PROVISIONAL values are Claude placeholders with NO
-- evidential basis. They are UI-settable and must be labelled as such.
-- --------------------------------------------------------------------------
insert into settings (key, value) values
  ('bankroll', '{"amount": null, "currency":"USD", "compounding": false}'::jsonb),
  ('max_slippage_cents', '{"value": 5, "provisional": true, "origin":"claude_invented", "note":"NO evidential basis. UI-settable. Tune once live-hour book data exists."}'::jsonb),
  ('tradeability_yes', '{"min_price": 0.07, "require_near_forecast": true, "max_bands_from_centre": 4, "origin":"hassan"}'::jsonb),
  ('tradeability_no', '{"strategy_decides": true, "origin":"hassan"}'::jsonb),
  ('risk_limits', '{"max_per_band_pct": 5, "max_per_city_day_pct": 15, "max_open_exposure_pct": 60, "max_daily_loss_pct": 10, "provisional": true}'::jsonb),
  ('correlation_warn_threshold', '{"value": 0.6, "provisional": true}'::jsonb)
on conflict (key) do update set value = excluded.value;

-- --------------------------------------------------------------------------
-- VIEWS for the API layer
--
-- Dropped-then-created rather than `create or replace`: Postgres refuses
-- `create or replace view` if the replacement would drop or reorder a
-- column the current view has (e.g. after sql/ad4_phase2_ranking.sql has
-- already added `score` to v_opportunities, re-running this file's
-- narrower definition would error "cannot drop columns from view").
-- `drop view if exists ... cascade` makes re-running this file safe
-- regardless of what later files have already layered on top - nothing
-- else in this schema is built on top of these views, so cascade has
-- nothing to actually cascade to in practice.
--
-- OPERATIONAL NOTE: dropping and recreating a view also drops any grants
-- on it. If you ever re-run this file (or ad4_phase2_ranking.sql) after
-- ad4_rls.sql has already run, re-run ad4_rls.sql again afterward too -
-- it's idempotent - so `anon` regains SELECT on the recreated views.
-- --------------------------------------------------------------------------
drop view if exists v_opportunities cascade;
drop view if exists v_latest_edge cascade;
drop view if exists v_latest_prob cascade;
drop view if exists v_latest_book cascade;

create view v_latest_book as
select distinct on (bs.band_id) bs.*
from book_snapshots bs
order by bs.band_id, bs.observed_at desc;

create view v_latest_prob as
select distinct on (bp.band_id) bp.*
from band_probabilities bp
order by bp.band_id, bp.computed_at desc;

create view v_latest_edge as
select distinct on (e.band_id, e.side) e.*
from edges e
order by e.band_id, e.side, e.computed_at desc;

create view v_opportunities as
select
  e.edge_id, e.side, e.model_prob, e.market_price, e.edge_net_pp,
  e.edge_per_dollar, e.fillable_usd_5c, e.confidence, e.regime_label,
  e.tradeable, e.block_reason,
  b.band_id, b.band_label, b.band_lo, b.band_hi, b.open_low, b.open_high,
  b.token_yes, b.token_no,
  m.city_key, m.resolution_date, m.unit,
  c.display_name, c.icao, c.station_name, c.timezone, c.band_width,
  bk.best_bid, bk.best_ask, bk.spread, bk.market_state
from v_latest_edge e
join bands b   on b.band_id = e.band_id
join markets m on m.market_id = b.market_id
join cities c  on c.city_key = m.city_key
left join v_latest_book bk on bk.band_id = b.band_id
where m.resolution_date >= current_date;

-- ===========================================================================
-- VERIFY (run separately after the above completes)
-- ===========================================================================
-- select table_name from information_schema.tables
-- where table_schema='public'
--   and table_name in ('cost_params','edges','anomaly_rules',
--                      'derived_city_correlation','derived_capacity','strategy_conflicts');
-- -- expect 6 rows
--
-- select count(*) from v_opportunities;
-- -- expect > 0 once Task 4 and 6 have run; 0 is fine now
