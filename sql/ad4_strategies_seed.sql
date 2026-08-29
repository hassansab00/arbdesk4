-- ===========================================================================
-- Task 8 - seed the `strategies` table. All six ship DISABLED.
-- Nothing trades until Hassan enables it.
--
-- SCHEMA ASSUMPTION: `strategies` has columns matching StrategyConfig
-- (scripts/strategies/base.py) - strategy_id (PK), name, side, universe
-- (text[]), regime_filter (text[]), conflict_class, capital_cap_pct,
-- max_concurrent, enabled, extra (jsonb). Verify against the live schema;
-- this uses `on conflict (strategy_id) do nothing` so a mismatch surfaces
-- as a clean error rather than partial/duplicate rows.
-- ===========================================================================

insert into strategies (strategy_id, name, side, universe, regime_filter, conflict_class,
                         capital_cap_pct, max_concurrent, enabled, extra)
values
  ('s1_buy_low_sell_signal', 'Buy-low / sell-on-signal', 'BOTH', array['ALL'],
   array['SHARP','NORMAL'], 'directional', 5.0, 20, false,
   '{"entry_mode":"price","price_threshold":0.30,"lead_days_trigger":2,"target_margin_pp":0.05,"time_exit_hours":2,"min_liquidity_usd":200,"origin":"hassan"}'::jsonb),

  ('s2_combination_arb', 'Combination arb', 'BOTH', array['ALL'],
   array['SHARP','NORMAL','UNCERTAIN'], 'arb', 10.0, 10, false,
   '{"origin":"hassan","note":"pure arithmetic, no forecast dependency, so runs in more regimes than the directional strategies"}'::jsonb),

  ('s3_concentration', 'Concentration', 'YES', array['ALL'],
   array['SHARP'], 'directional', 5.0, 15, false,
   '{"max_mae_bands":1.0,"width_bands":2,"origin":"candidate","note":"all prior hit-rate claims void, unmeasured against AD4 data"}'::jsonb),

  ('s4_tail_fade', 'Tail fade', 'NO', array['ALL'],
   array['SHARP','NORMAL'], 'directional', 3.0, 20, false,
   '{"tail_bands":2,"origin":"candidate","note":"only SOURCED support is the fee curve p(1-p)->0 at extremes"}'::jsonb),

  ('s5_running_max_lock', 'Running-max lock', 'YES', array['ALL'],
   array['SHARP','NORMAL'], 'directional', 5.0, 10, false,
   '{"max_entry_price":0.90,"origin":"candidate","note":"seasonal gate mandatory - see derived_weather_peak.window_width_h"}'::jsonb),

  ('s6_anchor_insurance', 'Anchor + insurance', 'BOTH', array['ALL'],
   array['SHARP','NORMAL'], 'basket', 8.0, 10, false,
   '{"width_bands":2,"target_profit_usd":50.0,"origin":"candidate"}'::jsonb)
on conflict (strategy_id) do nothing;
