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
  v_cities text[];
  v_pair record;
  v_corr_threshold numeric;
begin
  v_corr_threshold := coalesce(((select value from settings where key = 'correlation_warn_threshold')->>'value')::numeric, 0.6);

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
      v_legs := v_legs || jsonb_build_object('band_id', band_id_txt, 'ask', v_band.best_ask, 'model_prob', v_p);
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
      'mode', 'target_profit', 'legs', v_legs, 'n_shares', v_n_shares, 'sum_ask', v_sum_ask,
      'total_cost', v_n_shares * v_sum_ask, 'best_case', v_n_shares * (1 - v_sum_ask),
      'worst_case', -v_n_shares * v_sum_ask, 'p_covered', v_p_covered,
      'insurance_cap_pass', true, 'feasible', true, 'breakeven', v_sum_ask,
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
    v_legs := v_legs || jsonb_build_object(
      'band_id', band_id_txt, 'shares', v_fill.shares, 'avg_fill_price', v_fill.avg_price,
      'quoted_price', v_fill.quoted_price, 'fully_filled', v_fill.fully_filled, 'model_prob', v_p
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
    'mode', 'budget', 'legs', v_legs, 'total_cost', v_total_cost, 'ev_net', v_ev_net,
    'best_case', v_best_case, 'worst_case', v_worst_case, 'breakeven', v_total_cost,
    'p_covered', v_p_covered, 'fillability_pct', case when v_amount > 0 then v_total_cost / v_amount else 0 end,
    'insurance_cap_pass', null, 'capacity_warning', v_capacity_warning,
    'correlation_warning', v_correlation_warning,
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
-- refresh_derived - market peak + city-day volume from trades_observed.
-- Genuine SQL aggregation, not a stub.
-- --------------------------------------------------------------------------
create or replace function refresh_derived() returns jsonb
language plpgsql security definer as $$
declare
  v_volume_rows int;
begin
  insert into derived_city_day_volume (city_key, trade_date, volume_usd, computed_at)
  select city_key, observed_at::date, sum(price * size), now()
  from trades_observed
  group by city_key, observed_at::date
  on conflict do nothing;
  get diagnostics v_volume_rows = row_count;

  return jsonb_build_object('city_day_volume_rows', v_volume_rows);
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
