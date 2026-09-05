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
create table if not exists strategies (
  strategy_id    text primary key,
  name           text,
  side           text,
  origin         text,
  config         jsonb,
  conflict_class text,
  enabled        boolean default false,
  created_at     timestamptz default now()
);

do $$
declare r record;
begin
  for r in select * from (values
      ('strategies','strategy_id')
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
--    the exact gap that failed in production: universe / capital_cap_pct did not exist
  for r in
    select * from (values
      ('strategies','name','text'),
      ('strategies','side','text'),
      ('strategies','conflict_class','text'),
      ('strategies','enabled','boolean default false'),
      ('strategies','universe','jsonb'),
      ('strategies','regime_filter','jsonb'),
      ('strategies','capital_cap_pct','numeric'),
      ('strategies','max_concurrent','int'),
      ('strategies','extra','jsonb')
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
-- Task 8 - seed the `strategies` table. All six ship DISABLED.
-- Nothing trades until Hassan enables it.
--
-- NO SCHEMA ASSUMPTION ANY MORE. The guard block at the top of this file
-- creates `strategies` if absent and adds every column below if missing -
-- `universe` and `capital_cap_pct` are exactly the two that did not exist
-- on the live database and broke this file's first real run.
--
-- universe / regime_filter are jsonb arrays, not text[]: PostgREST hands
-- both back to scripts/strategies/base.py:StrategyConfig as a plain Python
-- list either way, and jsonb is what sql/ad4_00_preflight.sql guarantees
-- on a table whose original shape we do not control.
-- ===========================================================================

insert into strategies (strategy_id, name, side, universe, regime_filter, conflict_class,
                         capital_cap_pct, max_concurrent, enabled, extra)
values
  ('s1_buy_low_sell_signal', 'Buy-low / sell-on-signal', 'BOTH', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'directional', 5.0, 20, false,
   '{"entry_mode":"price","price_threshold":0.30,"lead_days_trigger":2,"target_margin_pp":0.05,"time_exit_hours":2,"min_liquidity_usd":200,"origin":"hassan"}'::jsonb),

  ('s2_combination_arb', 'Combination arb', 'BOTH', '["ALL"]'::jsonb,
   '["SHARP","NORMAL","UNCERTAIN"]'::jsonb, 'arb', 10.0, 10, false,
   '{"origin":"hassan","note":"pure arithmetic, no forecast dependency, so runs in more regimes than the directional strategies"}'::jsonb),

  ('s3_concentration', 'Concentration', 'YES', '["ALL"]'::jsonb,
   '["SHARP"]'::jsonb, 'directional', 5.0, 15, false,
   '{"max_mae_bands":1.0,"width_bands":2,"origin":"candidate","note":"all prior hit-rate claims void, unmeasured against AD4 data"}'::jsonb),

  ('s4_tail_fade', 'Tail fade', 'NO', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'directional', 3.0, 20, false,
   '{"tail_bands":2,"origin":"candidate","note":"only SOURCED support is the fee curve p(1-p)->0 at extremes"}'::jsonb),

  ('s5_running_max_lock', 'Running-max lock', 'YES', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'directional', 5.0, 10, false,
   '{"max_entry_price":0.90,"origin":"candidate","note":"seasonal gate mandatory - see derived_weather_peak.window_width_h"}'::jsonb),

  ('s6_anchor_insurance', 'Anchor + insurance', 'BOTH', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'basket', 8.0, 10, false,
   '{"width_bands":2,"target_profit_usd":50.0,"origin":"candidate"}'::jsonb),

  -- S7 and S8 come from Hassan's own manual trading, written down as rules.
  -- Both need sql/ad4_26_temp_trend.sql: without it the trend fields are null
  -- and neither fires, which is the correct behaviour for a strategy whose
  -- inputs are missing.
  ('s7_pre_peak_gradient', 'Pre-peak gradient entry', 'BOTH', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'directional', 5.0, 10, false,
   '{"entry_window_min":60,"max_reading_age_min":90,"min_slope_c_per_h":0.10,
     "max_entry_price":0.85,"origin":"hassan",
     "note":"enter under an hour before peak on the direction of travel, not the level. Mirror side sells bands a rolled-over day can no longer reach."}'::jsonb),

  ('s8_two_bucket_cover', 'Two-bucket cover', 'YES', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'basket', 8.0, 8, false,
   '{"max_pair_cost":0.70,"min_pair_prob":0.72,"min_liquidity_usd":100.0,"origin":"hassan",
     "note":"the two most likely ADJACENT buckets when the pair costs under 70c including fees, and one of them contains the forecast or the observation-implied max"}'::jsonb),

  -- S9 is the general form of S8: any contiguous run of 2-4 buckets, chosen on
  -- expected return per dollar rather than a fixed price cap. They overlap on
  -- purpose and the conflict layer decides; S8 encodes the specific sub-70c
  -- rule, S9 finds the best window whatever it costs.
  ('s9_ladder_basket', 'Ladder basket', 'YES', '["ALL"]'::jsonb,
   '["SHARP","NORMAL"]'::jsonb, 'basket', 8.0, 8, false,
   '{"max_buckets":4,"min_ev_per_dollar":0.08,"min_win_prob":0.55,
     "min_liquidity_usd":100.0,"max_total_cost":0.92,"origin":"hassan",
     "note":"buys the contiguous window of buckets with the highest expected return per dollar after fees, when it clears the floor and contains where the day is heading"}'::jsonb)
on conflict (strategy_id) do nothing;
