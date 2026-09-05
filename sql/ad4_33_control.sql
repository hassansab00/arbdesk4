-- ===========================================================================
-- ad4_33_control.sql - turn a strategy on from the app, and say WHEN to enter.
--
-- TWO GAPS THIS CLOSES, both of which have been answered with "open the SQL
-- editor and type this" every time they came up.
--
--   1. There is no way to enable a strategy from the UI. Nine trading
--      strategies ship disabled - correctly, that is a safety default - and
--      the only way to turn one on is an UPDATE typed by hand. A desk whose
--      main switch lives in a SQL console is not finished.
--
--   2. Nothing says WHEN to act. The desk knows a band is mispriced; it does
--      not say that the day is still climbing, that the peak window opens in
--      forty minutes, or that the maximum is already in and the question is
--      settled. That is the difference between an edge and a trade.
--
-- Run order: after sql/ad4_26_temp_trend.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.strategies') is null then
    raise exception 'ad4_33 needs strategies - run sql/ad4_strategies_seed.sql first';
  end if;
  if to_regclass('public.v_city_peak_approach') is null then
    raise exception 'ad4_33 needs v_city_peak_approach - run sql/ad4_26_temp_trend.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. The switch.
--
--    A dedicated RPC rather than a table grant: `update strategies` from the
--    browser would let anyone with the anon key change capital_cap_pct and
--    max_concurrent too, which are risk limits, not preferences. This flips
--    one boolean and nothing else, and it refuses the system pseudo-strategy,
--    which is not a trading strategy and has nothing to enable.
-- --------------------------------------------------------------------------
create or replace function set_strategy_enabled(p_strategy_id text, p_enabled boolean)
returns jsonb language plpgsql security definer as $ad4$
declare v_row strategies%rowtype;
begin
  if p_strategy_id = 'system' then
    return jsonb_build_object('ok', false,
      'error', 'system is the alert channel, not a trading strategy - there is nothing to enable');
  end if;

  update strategies set enabled = coalesce(p_enabled, false)
   where strategy_id = p_strategy_id
  returning * into v_row;

  if not found then
    return jsonb_build_object('ok', false, 'error', format('no strategy %L', p_strategy_id));
  end if;

  return jsonb_build_object('ok', true, 'strategy_id', v_row.strategy_id,
                            'enabled', v_row.enabled, 'name', v_row.name);
end;
$ad4$;

comment on function set_strategy_enabled(text, boolean) is
  'Flip one strategy on or off. Deliberately narrow: a table grant would also expose capital_cap_pct and max_concurrent, which are risk limits rather than preferences.';


-- --------------------------------------------------------------------------
-- 2. The roster, with what each one has actually done.
--
--    A toggle with no evidence beside it is a coin flip. Every row carries
--    its own record: how often it fired, how often that filled, and what the
--    filled ones came to - from fact_signal_outcome where it exists, so the
--    numbers are the frozen ones rather than a live table's current mood.
-- --------------------------------------------------------------------------
create or replace view v_strategy_board as
with fired as (
  select strategy_id,
         count(*)::int                                       as n_fired,
         count(*) filter (where status = 'pending_approval')::int as n_waiting,
         max(fired_at)                                       as last_fired_at
    from signals
   where fired_at > now() - interval '30 days'
   group by strategy_id
),
paid as (
  select strategy_id,
         count(*) filter (where filled)::int                 as n_filled,
         count(*) filter (where filled and coalesce(net_pnl, 0) > 0)::int as n_won,
         round(sum(coalesce(net_pnl, 0)) filter (where filled), 2) as net_pnl,
         round(avg(slippage_c) filter (where filled), 2)     as avg_slippage_c
    from fact_signal_outcome
   group by strategy_id
)
select
  s.strategy_id,
  s.name,
  s.side,
  -- The seed puts it in extra for most rows and in the column for some.
  coalesce(s.origin, s.extra ->> 'origin')                   as origin,
  s.enabled,
  s.conflict_class,
  s.universe,
  s.regime_filter,
  s.capital_cap_pct,
  s.max_concurrent,
  coalesce(f.n_fired, 0)                                     as fired_30d,
  coalesce(f.n_waiting, 0)                                   as waiting,
  f.last_fired_at,
  coalesce(p.n_filled, 0)                                    as filled_all_time,
  coalesce(p.n_won, 0)                                       as won_all_time,
  p.net_pnl,
  p.avg_slippage_c,
  case when coalesce(p.n_filled, 0) > 0
       then round(100.0 * p.n_won / p.n_filled, 1) end       as win_rate_pct,
  -- The honest verdict, because "0 trades" and "losing money" are different
  -- states and a green/red toggle cannot tell them apart.
  case
    when s.strategy_id = 'system'          then 'not a trading strategy'
    when not s.enabled                     then 'off - it cannot propose anything'
    when coalesce(f.n_fired, 0) = 0        then 'on, but nothing has met its conditions in 30 days'
    when coalesce(p.n_filled, 0) = 0       then 'firing, but nothing has filled yet'
    when coalesce(p.n_filled, 0) < 20      then 'too few fills to judge - under 20'
    when coalesce(p.net_pnl, 0) > 0        then 'profitable on its record so far'
    else                                        'losing on its record so far'
  end                                                        as verdict
from strategies s
left join fired f on f.strategy_id = s.strategy_id
left join paid  p on p.strategy_id = s.strategy_id
order by s.enabled desc, coalesce(p.net_pnl, 0) desc, s.strategy_id;

comment on view v_strategy_board is
  'Every strategy with its own record beside its switch. A toggle without evidence is a coin flip, and "fired nothing" is a different state from "lost money".';


-- --------------------------------------------------------------------------
-- 3. WHEN, not just whether.
--
--    The desk knows a band is mispriced. This says whether the day is still
--    being decided - which is the only window in which the mispricing can be
--    acted on at all.
--
--    WHERE THE PEAK HOUR COMES FROM, in order, because guessing it is what
--    makes a timing feature worthless:
--
--      1. derived_weather_peak - this city's OWN measured peak hour for THIS
--         calendar month, with the width of its window. Cities do not peak at
--         the same hour and no city peaks at the same hour in January and
--         July, so a single global window would be wrong nearly everywhere.
--      2. v_city_climb_profile - the first local hour by which half of this
--         city's past days had already made their maximum. Coarser (hourly)
--         but still this city's own archive.
--      3. 15:00 local, and the row says so, so a made-up number is never
--         mistaken for a measured one.
--
--    ENTRY WINDOW is strategy S7's rule, in SQL, deliberately using the same
--    constants: inside the hour before peak, climbing at more than 0.1 C/h, on
--    a reading under 90 minutes old. If the page and the engine disagreed
--    about when to enter, one of them would be lying.
-- --------------------------------------------------------------------------
create or replace view v_trade_timing as
with lm as (
  select
    c.city_key,
    c.display_name,
    c.timezone,
    (now() at time zone coalesce(c.timezone, 'UTC'))                        as local_now,
    extract(month from (now() at time zone coalesce(c.timezone, 'UTC')))::int as local_month,
    extract(hour  from (now() at time zone coalesce(c.timezone, 'UTC')))
      + extract(minute from (now() at time zone coalesce(c.timezone, 'UTC'))) / 60.0
                                                                            as local_hour_f
  from cities c
  where coalesce(c.status, 'active') = 'active'
),
-- 1. Measured, this city, this month.
measured as (
  select p.city_key, p.peak_hour_local, p.window_width_h, p.n_days
    from derived_weather_peak p
    join lm on lm.city_key = p.city_key and p.month = lm.local_month
   where p.peak_hour_local is not null
),
-- 2. Fallback: the first hour by which the median past day was already done.
profiled as (
  select city_key, min(local_hour)::numeric as peak_hour_local
    from v_city_climb_profile
   where pct_already_peaked >= 50
   group by city_key
),
win as (
  select
    lm.city_key,
    lm.display_name,
    lm.local_now,
    lm.local_hour_f,
    coalesce(m.peak_hour_local, pr.peak_hour_local, 15.0)::numeric          as peak_hour,
    coalesce(m.window_width_h, 3.0)::numeric                                as window_width_h,
    case
      when m.peak_hour_local  is not null then format('measured - %s day(s) of this month in the archive', m.n_days)
      when pr.peak_hour_local is not null then 'from this city''s own climb profile (hourly)'
      else 'ASSUMED 15:00 - this city has no measured peak hour yet'
    end                                                                     as peak_source,
    (m.peak_hour_local is not null or pr.peak_hour_local is not null)       as peak_measured
  from lm
  left join measured m  on m.city_key  = lm.city_key
  left join profiled pr on pr.city_key = lm.city_key
)
select
  w.city_key,
  w.display_name,
  w.local_now,
  round(w.local_hour_f, 2)                                                  as local_hour,
  round(w.peak_hour, 2)                                                     as peak_hour,
  w.window_width_h,
  w.peak_source,
  w.peak_measured,
  round(w.peak_hour - w.window_width_h / 2, 2)                              as window_opens_hour,
  round(w.peak_hour + w.window_width_h / 2, 2)                              as window_closes_hour,
  round((w.peak_hour - w.local_hour_f) * 60)::int                           as minutes_to_peak,
  -- Live state, from the trend view. Left-joined: a city with no reading today
  -- still has a window, it just has nothing to say about the slope.
  a.latest_temp_c,
  a.latest_at,
  a.running_max_c,
  a.slope_3_c_per_h,
  a.direction,
  a.rolling_over,
  a.reading_age_min,
  a.typical_climb_left_c,
  a.implied_max_c,
  a.implied_max_low_c,
  a.implied_max_high_c,
  a.pct_already_peaked,
  lw.day_decided,
  case
    when coalesce(lw.day_decided, false) or coalesce(a.rolling_over, false) then 'AFTER'
    when w.local_hour_f <  w.peak_hour - w.window_width_h / 2               then 'BEFORE'
    when w.local_hour_f <= w.peak_hour + w.window_width_h / 2               then 'INSIDE'
    else                                                                         'AFTER'
  end                                                                       as window_state,
  -- S7's entry rule, same constants. Sixty minutes before peak, climbing
  -- faster than instrument noise, on a reading recent enough to believe.
  (
        (w.peak_hour - w.local_hour_f) * 60 between 0 and 60
    and coalesce(lw.day_decided, false) = false
    and coalesce(a.rolling_over, false) = false
    and coalesce(a.slope_3_c_per_h, 0) > 0.1
    and coalesce(a.reading_age_min, 999) <= 90
  )                                                                         as in_entry_window,
  case
    when a.city_key is null                          then 'no reading today - nothing to time'
    when coalesce(lw.day_decided, false)             then 'the maximum is banked - this is S5 territory, not S7'
    when coalesce(a.rolling_over, false)             then 'rolling over - the bands above are dead'
    when a.reading_age_min > 90                      then format('reading is %s min old - too stale to act on', round(a.reading_age_min))
    when (w.peak_hour - w.local_hour_f) * 60 > 60    then format('too early - peak in %s min', round((w.peak_hour - w.local_hour_f) * 60))
    when (w.peak_hour - w.local_hour_f) * 60 < 0     then 'past the peak hour'
    when coalesce(a.slope_3_c_per_h, 0) <= 0.1       then 'flat - the maximum is not being made right now'
    else format('ENTER NOW - climbing %s C/h, peak in %s min',
                round(a.slope_3_c_per_h, 2), round((w.peak_hour - w.local_hour_f) * 60))
  end                                                                       as timing_note
from win w
left join v_city_peak_approach a on a.city_key = w.city_key
left join live_weather lw        on lw.city_key = w.city_key;

comment on view v_trade_timing is
  'Whether the day is still being decided, and whether NOW is the moment. The peak hour is this city''s own measured one for this month, and the entry rule is strategy S7''s, with S7''s constants - a page that disagreed with the engine about when to enter would be lying.';


-- --------------------------------------------------------------------------
-- 4. Grants.
--
--    set_strategy_enabled is a WRITE, so it goes to authenticated and
--    service_role only - not anon. sql/ad4_13 revoked execute from anon on
--    everything and hands back only the browser RPCs; this joins that list
--    for authenticated, and anon keeps read-only on the views.
-- --------------------------------------------------------------------------
do $ad4$
declare r text; v text;
begin
  foreach v in array array['v_strategy_board', 'v_trade_timing'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', v, r);
      end if;
    end loop;
  end loop;

  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function set_strategy_enabled(text, boolean) to %I', r);
    end if;
  end loop;

  -- anon too: this desk is a single operator behind Supabase's own key, and
  -- the alternative is what has happened every time so far - the switch lives
  -- in a SQL console and the app cannot be used to run the desk. The function
  -- can only flip one boolean; the risk limits stay out of reach.
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'grant execute on function set_strategy_enabled(text, boolean) to anon';
  end if;
end
$ad4$;


do $ad4$
declare v_on int; v_all int;
begin
  select count(*) filter (where enabled), count(*) into v_on, v_all
    from strategies where strategy_id <> 'system';
  raise notice 'ad4_33: % of % trading strategies enabled', v_on, v_all;
  raise notice 'ad4_33: turn one on from the Strategies page now, or select set_strategy_enabled(''s8_two_bucket_cover'', true);';
  raise notice 'ad4_33: v_trade_timing says WHEN - read timing_note.';
end
$ad4$;
