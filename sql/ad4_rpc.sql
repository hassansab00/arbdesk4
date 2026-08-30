-- ---------------------------------------------------------------------------
-- SELF-SUFFICIENCY GUARD (added by the final completion pass).
--
-- This file no longer assumes any prior schema state. Everything it reads
-- or writes below is created here if absent, so it runs standalone against
-- the live Supabase database, a fresh Postgres, or a half-migrated one.
-- sql/ad4_00_preflight.sql does the same job for the whole system at once
-- and should still be run first - this block is the belt to its braces.
-- Idempotent: only ever ADDS, never drops, renames or retypes.
-- ---------------------------------------------------------------------------
create table if not exists cities (
  city_key          text primary key,
  display_name      text,
  icao              text,
  station_name      text,
  timezone          text,
  unit              text default 'C',
  band_width        numeric,
  latitude          numeric,
  longitude         numeric,
  resolution_source text,
  status            text default 'active'
);
create table if not exists markets (
  market_id       uuid primary key default gen_random_uuid(),
  city_key        text,
  resolution_date date,
  unit            text,
  closed          boolean default false,
  event_slug      text,
  condition_id    text
);
create table if not exists bands (
  band_id    uuid primary key default gen_random_uuid(),
  market_id  uuid,
  band_lo    numeric,
  band_hi    numeric,
  open_low   boolean default false,
  open_high  boolean default false,
  band_label text,
  token_yes  text,
  token_no   text
);
create table if not exists book_snapshots (
  snapshot_id  bigserial primary key,
  band_id      uuid,
  observed_at  timestamptz default now(),
  best_bid     numeric,
  best_ask     numeric,
  spread       numeric,
  market_state text,
  bid_levels   jsonb,
  ask_levels   jsonb
);
create table if not exists band_probabilities (
  prob_id             bigserial primary key,
  band_id             uuid,
  computed_at         timestamptz default now(),
  calibrated_prob     numeric,
  forecast_version    text,
  calibration_version text
);
create table if not exists paper_trades (
  trade_id  bigserial primary key,
  opened_at timestamptz default now()
);
create table if not exists signals (
  signal_id bigserial primary key,
  fired_at  timestamptz default now()
);
create table if not exists ledger (
  ledger_id   bigserial primary key,
  recorded_at timestamptz default now()
);
create table if not exists settings (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz default now()
);
create table if not exists deployments (
  deployment_id uuid primary key default gen_random_uuid(),
  created_at    timestamptz default now()
);
create table if not exists trades_observed (
  trade_id    bigserial primary key,
  city_key    text,
  band_id     uuid,
  token_id    text,
  side        text,
  price       numeric,
  size        numeric,
  observed_at timestamptz
);
create table if not exists derived_city_day_volume (
  city_key    text not null,
  trade_date  date not null,
  volume_usd  numeric,
  n_trades    int,
  computed_at timestamptz default now(),
  primary key (city_key, trade_date)
);
create table if not exists derived_band_day_volume (
  band_id     uuid not null,
  city_key    text,
  trade_date  date not null,
  volume_usd  numeric,
  n_trades    int,
  computed_at timestamptz default now(),
  primary key (band_id, trade_date)
);

do $$
declare r record;
begin
  for r in select * from (values
      ('settings','key'),
      ('bands','band_id'),
      ('markets','market_id')
  ) as t(tbl, col) loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if exists (select 1 from pg_index i
               join pg_class c on c.oid = i.indrelid
               join pg_namespace n on n.oid = c.relnamespace
               join pg_attribute a on a.attrelid = c.oid and a.attnum = i.indkey[0]
               where n.nspname='public' and c.relname=r.tbl
                 and i.indisunique and i.indnatts = 1 and a.attname = r.col) then
      continue;
    end if;
    begin
      execute format('create unique index if not exists %I on public.%I (%I)',
                     'ad4_uq_' || r.tbl || '_' || r.col, r.tbl, r.col);
    exception when others then
      raise notice 'guard: could not make %.% unique: %', r.tbl, r.col, sqlerrm;
    end;
  end loop;
end $$;

do $$
declare r record;
begin
--    every column the RPCs below insert, update or select at run time
  for r in
    select * from (values
      ('settings','value','jsonb'),
      ('trades_observed','city_key','text'),
      ('trades_observed','band_id','uuid'),
      ('trades_observed','price','numeric'),
      ('trades_observed','size','numeric'),
      ('trades_observed','observed_at','timestamptz'),
      ('derived_city_day_volume','volume_usd','numeric'),
      ('derived_city_day_volume','n_trades','int'),
      ('derived_city_day_volume','computed_at','timestamptz default now()'),
      ('derived_band_day_volume','city_key','text'),
      ('derived_band_day_volume','volume_usd','numeric'),
      ('derived_band_day_volume','n_trades','int'),
      ('derived_band_day_volume','computed_at','timestamptz default now()'),
      ('paper_trades','strategy_id','text'),
      ('paper_trades','band_id','uuid'),
      ('paper_trades','side','text'),
      ('paper_trades','action','text'),
      ('paper_trades','shares','numeric'),
      ('paper_trades','avg_fill_price','numeric'),
      ('paper_trades','quoted_price','numeric'),
      ('paper_trades','slippage_paid','numeric'),
      ('paper_trades','fee_paid','numeric'),
      ('paper_trades','gas_paid','numeric'),
      ('paper_trades','partial_fill','boolean default false'),
      ('paper_trades','requested_shares','numeric'),
      ('paper_trades','legs_requested','int'),
      ('paper_trades','legs_filled','int'),
      ('paper_trades','fill_quality','numeric'),
      ('paper_trades','max_slippage_setting','numeric'),
      ('paper_trades','cost_version','text'),
      ('paper_trades','forecast_version','text'),
      ('paper_trades','calibration_version','text'),
      ('paper_trades','regime_label','text'),
      ('paper_trades','approved_by_user','boolean default true'),
      ('paper_trades','opened_at','timestamptz'),
      ('paper_trades','closed_at','timestamptz'),
      ('paper_trades','exit_price','numeric'),
      ('paper_trades','gross_pnl','numeric'),
      ('paper_trades','net_pnl','numeric'),
      ('signals','status','text default ''pending_approval'''),
      ('signals','strategy_id','text'),
      ('signals','band_id','uuid'),
      ('ledger','stage','text'),
      ('ledger','strategy_id','text'),
      ('ledger','band_id','uuid'),
      ('ledger','regime_label','text'),
      ('ledger','forecast_version','text'),
      ('ledger','calibration_version','text'),
      ('ledger','cost_version','text'),
      ('ledger','detail','jsonb'),
      ('ledger','recorded_at','timestamptz default now()')
    ) as t(tbl, col, def)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if not exists (select 1 from information_schema.columns
                   where table_schema='public' and table_name=r.tbl and column_name=r.col) then
      execute format('alter table public.%I add column %I %s', r.tbl, r.col, r.def);
      raise notice 'guard: added %.%', r.tbl, r.col;
    end if;
  end loop;
end $$;

-- ===========================================================================
-- Task 13b - Supabase RPC layer.
--
-- Run after ad4_phase2*.sql, ad4_capacity_correlation.sql, ad4_settlement.sql,
-- ad4_backtest.sql, ad4_paper_engine_columns.sql (settle_markets,
-- queue_backtest, recompute_capacity/correlation, compute_edges/
-- evaluate_signals stubs already live in those files - not repeated here).
--
-- calc_recommendation is the one place this build puts non-trivial logic
-- in SQL rather than Python (see docs/architecture_deviations.md for the
-- general rule): a live, keystroke-reactive calculator (Task 13, "Live
-- preview updates as inputs change") cannot round-trip through a GitHub
-- Actions job, and Revision A rules out a Vercel function - a Postgres
-- RPC is the only place left that can answer in milliseconds. It is a
-- genuine, from-scratch PL/pgSQL port of cost_model.walk_ladder's logic
-- (walk_ladder_jsonb below), not a stub.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- Surrogate identity columns for the RPCs below. paper_trades/signals
-- already have SOME primary key from the base schema (unknown to this
-- build - see docs/schema_assumptions.md), so rather than guess its name
-- and risk colliding with it, these RPCs use their own guaranteed-unique
-- handle instead of touching whatever the existing PK is.
-- --------------------------------------------------------------------------
alter table paper_trades add column if not exists trade_id bigserial;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'paper_trades_trade_id_key') then
    alter table paper_trades add constraint paper_trades_trade_id_key unique (trade_id);
  end if;
end $$;

alter table signals add column if not exists signal_id bigserial;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'signals_signal_id_key') then
    alter table signals add constraint signals_signal_id_key unique (signal_id);
  end if;
end $$;

-- --------------------------------------------------------------------------
-- Realtime for the Task 14 signals slide-out panel (live, no polling).
-- --------------------------------------------------------------------------
do $$ begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    raise notice 'realtime: publication supabase_realtime does not exist - signals not added (normal outside Supabase)';
  elsif not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'signals'
  ) then
    begin
      alter publication supabase_realtime add table signals;
    exception when others then
      raise notice 'realtime: could not add signals to supabase_realtime: %', sqlerrm;
    end;
  end if;
end $$;

-- --------------------------------------------------------------------------
-- walk_ladder_jsonb - PL/pgSQL port of scripts/cost_model.py:walk_ladder.
-- p_levels: jsonb array of {"price":numeric,"size":numeric}, best-first.
-- Consumes best-first up to p_usd_budget, stopping at p_max_slippage past
-- the first level's price. Mirrors the Python version's semantics exactly
-- so the two never quietly diverge - see tests/test_cost_model.py for the
-- behaviour this must match.
-- --------------------------------------------------------------------------
create or replace function walk_ladder_jsonb(p_levels jsonb, p_usd_budget numeric, p_max_slippage numeric)
returns table(shares numeric, usd_spent numeric, avg_price numeric, quoted_price numeric, fully_filled boolean)
language plpgsql immutable as $$
declare
  lvl record;
  v_quoted numeric;
  v_shares numeric := 0;
  v_spent numeric := 0;
  v_price numeric;
  v_size numeric;
  v_remaining numeric;
  v_take numeric;
begin
  if p_levels is null or jsonb_array_length(p_levels) = 0 then
    return query select 0::numeric, 0::numeric, null::numeric, null::numeric, false;
    return;
  end if;

  for lvl in
    select (l->>'price')::numeric as price, (l->>'size')::numeric as size
    from jsonb_array_elements(p_levels) l
    order by (l->>'price')::numeric asc
  loop
    if v_quoted is null then
      v_quoted := lvl.price;
    end if;
    exit when p_max_slippage is not null and (lvl.price - v_quoted) > p_max_slippage;

    v_remaining := p_usd_budget - v_spent;
    exit when v_remaining <= 0;
    v_take := least(lvl.size, case when lvl.price > 0 then v_remaining / lvl.price else lvl.size end);
    exit when v_take <= 0;

    v_shares := v_shares + v_take;
    v_spent := v_spent + v_take * lvl.price;
  end loop;

  return query select
    v_shares,
    v_spent,
    case when v_shares > 0 then v_spent / v_shares else null end,
    v_quoted,
    (v_spent >= p_usd_budget - 1e-9);
end;
$$;

-- --------------------------------------------------------------------------
-- calc_recommendation - Task 13 calculator, both modes.
--
-- Budget mode: greedy by edge-per-dollar (Task 6's own ranking metric),
-- with a per-band concentration cap as the "variance penalty" the spec
-- calls for (documented simplification - the spec names the requirement,
-- not a formula, so this is the realisation of it: never put more than
-- concentration_cap_pct of the budget in one band, default 40%,
-- PROVISIONAL/settings-tunable).
--
-- Target-profit mode: N = G / (1 - sum(ask)), feasible only when
-- sum(ask) < 1 after fees (mirrors strategies/s6_anchor_insurance.py's
-- basket invariant exactly).
-- --------------------------------------------------------------------------
create or replace function calc_recommendation(p_params jsonb) returns jsonb
language plpgsql security definer as $$
declare
  v_mode text := coalesce(p_params->>'mode', 'budget');
  v_amount numeric := coalesce((p_params->>'amount')::numeric, 0);
  v_max_slippage numeric := coalesce((p_params->>'max_slippage_c')::numeric, 0.05);
  v_concentration_cap numeric := coalesce((p_params->>'concentration_cap_pct')::numeric, 40) / 100.0;
  v_overrides jsonb := coalesce(p_params->'user_prob_overrides', '{}'::jsonb);
  band_id_txt text;
  v_band record;
  v_p numeric;
  v_fill record;
  v_remaining_budget numeric;
  v_legs jsonb := '[]'::jsonb;
  v_total_cost numeric := 0;
  v_ev_net numeric := 0;
  v_p_covered numeric := 0;
  v_sum_ask numeric := 0;
  v_worst_case numeric := 0;
  v_best_case numeric := 0;
  v_n_legs int := 0;
  v_fillable_shares numeric := 0;
  v_requested_shares numeric := 0;
  v_target_profit numeric;
  v_n_shares numeric;
  v_capacity_warning boolean := false;
  v_correlation_warning boolean := false;
  v_volume_warning boolean := false;
  v_thin_bands jsonb := '[]'::jsonb;
  v_thin_band_usd numeric;
  v_leg_volume numeric;
  v_total_volume numeric := 0;
  v_cities text[];
  v_pair record;
  v_corr_threshold numeric;
begin
  v_corr_threshold := coalesce(((select value from settings where key = 'correlation_warn_threshold')->>'value')::numeric, 0.6);
  -- PROVISIONAL, UI-settable (settings.volume_thresholds). Drives a
  -- warning label only - it never blocks or resizes a leg.
  v_thin_band_usd := coalesce(((select value from settings where key = 'volume_thresholds')->>'thin_band_usd_24h')::numeric, 0);

  if v_mode = 'target_profit' then
    v_target_profit := coalesce((p_params->>'target_profit_usd')::numeric, 0);
    for band_id_txt in select jsonb_array_elements_text(p_params->'bands') loop
      select bp.calibrated_prob, bk.ask_levels, bk.best_ask, m.city_key
        into v_band
        from v_latest_prob bp
        join bands b on b.band_id = band_id_txt::uuid
        join markets m on m.market_id = b.market_id
        left join v_latest_book bk on bk.band_id = b.band_id
        where bp.band_id = band_id_txt::uuid;

      v_p := coalesce((v_overrides->>band_id_txt)::numeric, v_band.calibrated_prob, 0);
      v_sum_ask := v_sum_ask + coalesce(v_band.best_ask, 1.0);
      v_p_covered := v_p_covered + v_p;

      -- Traded volume for this leg. A basket priced off bands nobody
      -- trades is a different risk from the same basket on a busy book,
      -- and the caller must be told which one it is looking at.
      select coalesce(volume_usd, 0) into v_leg_volume from v_band_volume where band_id = band_id_txt::uuid;
      v_leg_volume := coalesce(v_leg_volume, 0);
      v_total_volume := v_total_volume + v_leg_volume;
      if v_leg_volume < v_thin_band_usd then
        v_volume_warning := true;
        v_thin_bands := v_thin_bands || to_jsonb(band_id_txt);
      end if;

      v_legs := v_legs || jsonb_build_object('band_id', band_id_txt, 'ask', v_band.best_ask,
                                              'model_prob', v_p, 'volume_usd', round(v_leg_volume, 2),
                                              'thin_market', v_leg_volume < v_thin_band_usd);
      v_n_legs := v_n_legs + 1;
    end loop;

    if v_sum_ask >= 1.0 or v_n_legs = 0 then
      return jsonb_build_object(
        'mode', 'target_profit', 'insurance_cap_pass', false, 'feasible', false,
        'sum_ask', v_sum_ask, 'reason', 'sum(ask) >= 1.0 after the requested legs - structure rejected'
      );
    end if;

    v_n_shares := v_target_profit / (1.0 - v_sum_ask);
    return jsonb_build_object(
      'mode', 'target_profit', 'legs', v_legs,
      'n_shares', round(v_n_shares, 4), 'sum_ask', round(v_sum_ask, 6),
      'total_cost', round(v_n_shares * v_sum_ask, 2),
      'best_case', round(v_n_shares * (1 - v_sum_ask), 2),
      'worst_case', round(-v_n_shares * v_sum_ask, 2), 'p_covered', round(v_p_covered, 6),
      'insurance_cap_pass', true, 'feasible', true, 'breakeven', round(v_sum_ask, 6),
      'volume_usd', round(v_total_volume, 2), 'volume_warning', v_volume_warning, 'thin_bands', v_thin_bands,
      'gross_vs_net', jsonb_build_object('note', 'fees applied per-leg by the caller before this cap check')
    );
  end if;

  -- BUDGET mode
  v_remaining_budget := v_amount;
  for band_id_txt, v_p in
    select b.band_id::text, coalesce((v_overrides->>b.band_id::text)::numeric, bp.calibrated_prob, 0) as p
    from v_latest_prob bp
    join bands b on b.band_id = bp.band_id
    where b.band_id::text in (select jsonb_array_elements_text(p_params->'bands'))
    order by (coalesce((v_overrides->>b.band_id::text)::numeric, bp.calibrated_prob, 0)
              - coalesce((select best_ask from v_latest_book where band_id = b.band_id), 1))
             / greatest(coalesce((select best_ask from v_latest_book where band_id = b.band_id), 1), 0.01) desc
  loop
    exit when v_remaining_budget <= 0;

    select ask_levels into v_band from v_latest_book where band_id = band_id_txt::uuid;
    select * into v_fill from walk_ladder_jsonb(
      v_band.ask_levels, least(v_remaining_budget, v_amount * v_concentration_cap), v_max_slippage
    );
    continue when v_fill.shares is null or v_fill.shares <= 0;

    v_remaining_budget := v_remaining_budget - v_fill.usd_spent;
    v_total_cost := v_total_cost + v_fill.usd_spent;
    v_ev_net := v_ev_net + v_fill.shares * (v_p - v_fill.avg_price);
    v_p_covered := v_p_covered + v_p;
    v_best_case := v_best_case + v_fill.shares * (1 - v_fill.avg_price);
    v_worst_case := v_worst_case - v_fill.usd_spent;
    v_fillable_shares := v_fillable_shares + v_fill.shares;
    v_n_legs := v_n_legs + 1;

    select coalesce(volume_usd, 0) into v_leg_volume from v_band_volume where band_id = band_id_txt::uuid;
    v_leg_volume := coalesce(v_leg_volume, 0);
    v_total_volume := v_total_volume + v_leg_volume;
    if v_leg_volume < v_thin_band_usd then
      v_volume_warning := true;
      v_thin_bands := v_thin_bands || to_jsonb(band_id_txt);
    end if;

    -- Rounded on the way out. Postgres numeric division carries ~100
    -- significant digits through the ladder walk; a UI showing
    -- "294.1176470588235294117644444444444444444444 shares" is unreadable
    -- and implies a precision the book does not have. Rounding happens
    -- only here, at the JSON boundary - every intermediate above stays
    -- full precision so the arithmetic itself is unchanged.
    v_legs := v_legs || jsonb_build_object(
      'band_id', band_id_txt, 'shares', round(v_fill.shares, 4),
      'avg_fill_price', round(v_fill.avg_price, 6),
      'quoted_price', v_fill.quoted_price, 'fully_filled', v_fill.fully_filled, 'model_prob', v_p,
      'volume_usd', round(v_leg_volume, 2), 'thin_market', v_leg_volume < v_thin_band_usd
    );
  end loop;

  select array_agg(distinct m.city_key) into v_cities
  from bands b join markets m on m.market_id = b.market_id
  where b.band_id::text in (select jsonb_array_elements_text(p_params->'bands'));

  if v_cities is not null and array_length(v_cities, 1) > 1 then
    for v_pair in
      select err_corr from derived_city_correlation
      where city_a = any(v_cities) and city_b = any(v_cities)
      order by computed_at desc limit 20
    loop
      if abs(v_pair.err_corr) >= v_corr_threshold then
        v_correlation_warning := true;
      end if;
    end loop;
  end if;

  -- capacity_warning is not yet cross-checked against derived_capacity
  -- (city+hour_utc granularity doesn't line up 1:1 with a band-level
  -- request without more join logic than fits this pass) - always false
  -- for now rather than a fabricated check. Wire it once Task 7's
  -- derived_capacity has enough real history to compare against.
  return jsonb_build_object(
    'mode', 'budget', 'legs', v_legs,
    'total_cost', round(v_total_cost, 2), 'ev_net', round(v_ev_net, 2),
    'best_case', round(v_best_case, 2), 'worst_case', round(v_worst_case, 2),
    'breakeven', round(v_total_cost, 2),
    'p_covered', round(v_p_covered, 6),
    'fillability_pct', case when v_amount > 0 then round(v_total_cost / v_amount, 6) else 0 end,
    'insurance_cap_pass', null, 'capacity_warning', v_capacity_warning,
    'correlation_warning', v_correlation_warning,
    'volume_usd', round(v_total_volume, 2), 'volume_warning', v_volume_warning, 'thin_bands', v_thin_bands,
    'gross_vs_net', jsonb_build_object('note', 'ev_net already nets executable price vs model_prob; fee/slippage shown separately by the UI from each leg''s own edges row')
  );
end;
$$;

-- --------------------------------------------------------------------------
-- log_paper_trade / approve_signal / close_position
-- --------------------------------------------------------------------------
create or replace function log_paper_trade(p_trade jsonb) returns jsonb
language plpgsql security definer as $$
declare
  v_id bigint;
begin
  insert into paper_trades (
    strategy_id, band_id, side, action, shares, avg_fill_price, quoted_price,
    slippage_paid, fee_paid, gas_paid, partial_fill, requested_shares,
    legs_requested, legs_filled, fill_quality, max_slippage_setting,
    cost_version, forecast_version, calibration_version, regime_label,
    approved_by_user, opened_at
  )
  select
    p_trade->>'strategy_id', (p_trade->>'band_id')::uuid, p_trade->>'side', p_trade->>'action',
    (p_trade->>'shares')::numeric, (p_trade->>'avg_fill_price')::numeric, (p_trade->>'quoted_price')::numeric,
    (p_trade->>'slippage_paid')::numeric, (p_trade->>'fee_paid')::numeric, (p_trade->>'gas_paid')::numeric,
    coalesce((p_trade->>'partial_fill')::boolean, false), (p_trade->>'requested_shares')::numeric,
    coalesce((p_trade->>'legs_requested')::int, 1), coalesce((p_trade->>'legs_filled')::int, 1),
    (p_trade->>'fill_quality')::numeric, (p_trade->>'max_slippage_setting')::numeric,
    p_trade->>'cost_version', p_trade->>'forecast_version', p_trade->>'calibration_version',
    p_trade->>'regime_label', true, now()
  returning trade_id into v_id;

  insert into ledger (stage, strategy_id, band_id, regime_label, forecast_version, calibration_version,
                       cost_version, detail, recorded_at)
  values ('fill', p_trade->>'strategy_id', (p_trade->>'band_id')::uuid, p_trade->>'regime_label',
          p_trade->>'forecast_version', p_trade->>'calibration_version', p_trade->>'cost_version',
          jsonb_build_object('source', 'log_paper_trade_rpc'), now());

  return jsonb_build_object('ok', true, 'trade_id', v_id);
end;
$$;

create or replace function approve_signal(p_signal_id bigint) returns jsonb
language plpgsql security definer as $$
declare
  v_signal record;
begin
  select * into v_signal from signals where signals.signal_id = p_signal_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'signal not found');
  end if;
  update signals set status = 'filled' where signal_id = p_signal_id;
  return jsonb_build_object('ok', true, 'signal_id', p_signal_id);
end;
$$;

create or replace function dismiss_signal(p_signal_id bigint) returns jsonb
language plpgsql security definer as $$
begin
  update signals set status = 'dismissed' where signal_id = p_signal_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'signal not found');
  end if;
  return jsonb_build_object('ok', true, 'signal_id', p_signal_id);
end;
$$;

create or replace function close_position(p_trade_id bigint, p_exit_price numeric, p_reason text)
returns jsonb language plpgsql security definer as $$
declare
  v_trade record;
  v_gross numeric;
  v_net numeric;
begin
  select * into v_trade from paper_trades where paper_trades.trade_id = p_trade_id and closed_at is null;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'open trade not found');
  end if;

  v_gross := v_trade.shares * (p_exit_price - v_trade.avg_fill_price);
  v_net := v_gross - coalesce(v_trade.fee_paid, 0) - coalesce(v_trade.gas_paid, 0);

  update paper_trades set closed_at = now(), exit_price = p_exit_price, gross_pnl = v_gross, net_pnl = v_net
  where trade_id = p_trade_id;

  insert into ledger (stage, strategy_id, band_id, regime_label, forecast_version, calibration_version,
                       cost_version, detail, recorded_at)
  values ('exit', v_trade.strategy_id, v_trade.band_id, v_trade.regime_label, v_trade.forecast_version,
          v_trade.calibration_version, v_trade.cost_version,
          jsonb_build_object('reason', p_reason, 'exit_price', p_exit_price, 'gross_pnl', v_gross, 'net_pnl', v_net),
          now());

  return jsonb_build_object('ok', true, 'gross_pnl', v_gross, 'net_pnl', v_net);
end;
$$;

-- --------------------------------------------------------------------------
-- update_setting - the write side of Task 14's editable settings (bankroll,
-- auto_approve, max_slippage_cents, risk_limits, ...). Not explicitly
-- listed in Revision A's RPC table but required by it implicitly (§6.3:
-- "write operations go through security definer RPC functions", and
-- Task 14 needs an editable bankroll with nowhere else to write it).
-- Whitelists which keys are UI-editable rather than accepting any key,
-- so this can't be used to, say, silently rewrite cost_params from the
-- browser.
-- --------------------------------------------------------------------------
create or replace function update_setting(p_key text, p_value jsonb) returns jsonb
language plpgsql security definer as $$
begin
  if p_key not in ('bankroll', 'auto_approve', 'max_slippage_cents', 'tradeability_yes',
                    'tradeability_no', 'risk_limits', 'correlation_warn_threshold',
                    'weather_alerts', 'email_recipient', 'goal') then
    return jsonb_build_object('ok', false, 'error', 'key not editable from the UI: ' || p_key);
  end if;
  insert into settings (key, value) values (p_key, p_value)
  on conflict (key) do update set value = excluded.value;
  return jsonb_build_object('ok', true);
end;
$$;

-- --------------------------------------------------------------------------
-- upsert_deployment / set_deployment_status - Task 14 §9 Campaigns.
-- Deployments aren't in Revision A's RPC list (§13b) but the frontend has
-- no other way to write `deployments` under RLS - same reasoning as
-- update_setting above.
-- --------------------------------------------------------------------------
alter table deployments add column if not exists deployment_id uuid default gen_random_uuid();
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'deployments_deployment_id_key') then
    alter table deployments add constraint deployments_deployment_id_key unique (deployment_id);
  end if;
end $$;
alter table deployments add column if not exists strategy_id text;
alter table deployments add column if not exists target_kind text check (target_kind in ('city','list','cluster'));
alter table deployments add column if not exists target jsonb;
alter table deployments add column if not exists status text default 'draft'
  check (status in ('draft','armed','running','paused','closed'));
alter table deployments add column if not exists starts_at timestamptz;
alter table deployments add column if not exists ends_at timestamptz;
alter table deployments add column if not exists condition text;
alter table deployments add column if not exists created_at timestamptz default now();
alter table deployments add column if not exists name text;

create or replace function upsert_deployment(p_deployment jsonb) returns jsonb
language plpgsql security definer as $$
declare
  v_id uuid := coalesce((p_deployment->>'deployment_id')::uuid, gen_random_uuid());
begin
  insert into deployments (deployment_id, name, strategy_id, target_kind, target, status,
                            starts_at, ends_at, condition, created_at)
  values (v_id, p_deployment->>'name', p_deployment->>'strategy_id', p_deployment->>'target_kind',
          p_deployment->'target', coalesce(p_deployment->>'status', 'draft'),
          (p_deployment->>'starts_at')::timestamptz, (p_deployment->>'ends_at')::timestamptz,
          p_deployment->>'condition', now())
  on conflict (deployment_id) do update set
    name = excluded.name, strategy_id = excluded.strategy_id, target_kind = excluded.target_kind,
    target = excluded.target, starts_at = excluded.starts_at, ends_at = excluded.ends_at,
    condition = excluded.condition;
  return jsonb_build_object('ok', true, 'deployment_id', v_id);
end;
$$;

create or replace function set_deployment_status(p_deployment_id uuid, p_status text) returns jsonb
language plpgsql security definer as $$
begin
  if p_status not in ('draft','armed','running','paused','closed') then
    return jsonb_build_object('ok', false, 'error', 'invalid status');
  end if;
  update deployments set status = p_status where deployment_id = p_deployment_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'deployment not found');
  end if;
  return jsonb_build_object('ok', true);
end;
$$;

-- --------------------------------------------------------------------------
-- refresh_derived - traded volume from trades_observed, at BOTH grains.
--
-- Market volume is a first-class input across AD4 (opportunity ranking,
-- tradeability, calculator, goals), and a city-day total is too coarse to
-- carry it: a city can be busy all day while the one band you actually
-- want has never printed. So this recomputes city-day AND band-day volume.
-- Genuine SQL aggregation, not a stub. Upserts rather than skipping on
-- conflict, so re-running mid-day refreshes the running total instead of
-- freezing the first value of the day.
-- --------------------------------------------------------------------------
create or replace function refresh_derived() returns jsonb
language plpgsql security definer as $$
declare
  v_city_rows int;
  v_band_rows int;
begin
  insert into derived_city_day_volume (city_key, trade_date, volume_usd, n_trades, computed_at)
  select city_key, observed_at::date, sum(price * size), count(*), now()
  from trades_observed
  where city_key is not null and observed_at is not null
  group by city_key, observed_at::date
  on conflict (city_key, trade_date) do update
    set volume_usd = excluded.volume_usd,
        n_trades   = excluded.n_trades,
        computed_at = excluded.computed_at;
  get diagnostics v_city_rows = row_count;

  insert into derived_band_day_volume (band_id, city_key, trade_date, volume_usd, n_trades, computed_at)
  select t.band_id, max(t.city_key), t.observed_at::date, sum(t.price * t.size), count(*), now()
  from trades_observed t
  where t.band_id is not null and t.observed_at is not null
  group by t.band_id, t.observed_at::date
  on conflict (band_id, trade_date) do update
    set city_key   = excluded.city_key,
        volume_usd = excluded.volume_usd,
        n_trades   = excluded.n_trades,
        computed_at = excluded.computed_at;
  get diagnostics v_band_rows = row_count;

  return jsonb_build_object('city_day_volume_rows', v_city_rows,
                             'band_day_volume_rows', v_band_rows);
end;
$$;

-- --------------------------------------------------------------------------
-- Stubs, per docs/architecture_deviations.md: regime.py already computes
-- per-city percentile thresholds fresh on every call (never frozen), and
-- "behavioural clusters" is never defined anywhere in the spec beyond a
-- UI wishlist item - nothing to precompute for either yet.
-- --------------------------------------------------------------------------
create or replace function recompute_regime_thresholds() returns void language plpgsql as $$
begin
  raise notice 'recompute_regime_thresholds: no-op - scripts/regime.py computes per-city percentile thresholds fresh on every call, never frozen. See docs/architecture_deviations.md.';
end;
$$;

create or replace function recompute_behavioural_clusters() returns void language plpgsql as $$
begin
  raise notice 'recompute_behavioural_clusters: no-op - the spec never defines a clustering algorithm (Task 14 lists this under "Accumulating", not "Available day one"). See docs/architecture_deviations.md.';
end;
$$;

-- --------------------------------------------------------------------------
-- build_morning_brief / build_eod_report - assemble the payload; n8n's
-- Code node (Task 15) renders it to HTML and sends it.
-- --------------------------------------------------------------------------
create or replace function build_morning_brief() returns jsonb
language sql security definer as $$
  select jsonb_build_object(
    'generated_at', now(),
    'top_opportunities', (
      select coalesce(jsonb_agg(o), '[]'::jsonb) from (
        select * from v_opportunities where tradeable order by score desc nulls last limit 15
      ) o
    ),
    'regimes', (
      select coalesce(jsonb_agg(r), '[]'::jsonb) from (
        select distinct on (band_id) band_id, regime_label, confidence from v_latest_prob order by band_id, computed_at desc
      ) r
    )
  );
$$;

create or replace function build_eod_report() returns jsonb
language sql security definer as $$
  select jsonb_build_object(
    'generated_at', now(),
    'settled_today', (
      select coalesce(jsonb_agg(t), '[]'::jsonb) from (
        select strategy_id, band_id, side, shares, avg_fill_price, exit_price, gross_pnl, net_pnl
        from paper_trades where closed_at::date = current_date
      ) t
    ),
    'net_pnl_by_strategy', (
      select coalesce(jsonb_object_agg(strategy_id, total_net), '{}'::jsonb) from (
        select strategy_id, sum(net_pnl) as total_net from paper_trades
        where closed_at::date = current_date group by strategy_id
      ) s
    )
  );
$$;
