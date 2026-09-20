-- ===========================================================================
-- ad4_77_board_conditions.sql - WHAT THE BOARD CAN SUPPORT RIGHT NOW.
--
-- "i want all confiured and ready to deploy wen selected."
--
-- They are all configured. All nine are implemented under scripts/strategies/,
-- all nine are in REGISTRY, all nine are enabled, and on "Wide edge, all US"
-- all nine are ticked. What is NOT true is that ticking one makes it act, and
-- the page said so in a way that read like a fault: "nothing has met its
-- conditions". That sentence names no condition, so there is nothing to do
-- about it.
--
-- Measured 20 Sep, of 54 city-days on the board:
--
--     day decided                21    the day's maximum is settled
--     station-backed             12    a real thermometer, not a model grid
--     running max from a SERIES   40    more than one reading behind the max
--     ALL THREE (s5's gate)        1
--     inside a pre-peak window     6    s7's entire trigger
--
-- s5 can therefore act on ONE city right now, and it has never fired. That is
-- not a bug and not a missing configuration: s5 locks a running maximum, and
-- locking one on a model's interpolation would be locking a number nobody
-- measured. 42 of 54 cities have no station feed, so s5 is structurally a
-- twelve-city strategy that further needs the day decided. Worth knowing
-- before waiting on it.
--
-- WHAT THIS VIEW IS AND IS NOT. It is the COUNTS, measured from live_weather,
-- in one place so the page does not recompute them. It is deliberately NOT a
-- reimplementation of any strategy's entry test: those live in Python, they
-- are what v_trade_plan.would_fire already reports, and a second copy in SQL
-- would drift from the first. The page pairs these counts with a one-line
-- statement per strategy of which condition it depends on - documentation of
-- the code, kept beside the code it documents, exactly like WHAT_IT_DOES.
--
-- RUN ORDER: after ad4_live_weather_timing.sql. Re-runnable.
-- ===========================================================================

create or replace view public.v_board_conditions as
select
  count(*)::integer                                             as cities,
  count(*) filter (where day_decided)::integer                  as day_decided,
  count(*) filter (where obs_source is not null
                      or source_kind = 'station')::integer      as station_backed,
  count(*) filter (where running_max_basis = 'series')::integer as running_max_series,
  -- s5's gate exactly as signal_engine.py builds it, and the only reason a
  -- copy is acceptable here: it is three boolean columns, it is asserted
  -- against the engine by tests/test_the_board_conditions_are_measured.py,
  -- and the number it produces is otherwise invisible.
  count(*) filter (where day_decided
                     and (obs_source is not null or source_kind = 'station')
                     and running_max_basis = 'series')::integer as lock_eligible,
  count(*) filter (where minutes_to_peak between 0 and 240)::integer as inside_peak_window,
  max(updated_at)                                               as measured_at
from public.live_weather;

comment on view public.v_board_conditions is
  'How many city-days on the board currently satisfy each precondition a strategy can depend on. The counts only - entry tests stay in scripts/strategies and are reported by v_trade_plan.would_fire.';

grant select on public.v_board_conditions to anon, authenticated, service_role;
