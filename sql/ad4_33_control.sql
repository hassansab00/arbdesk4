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
create or replace view public.v_signal_mark as
-- THE PAYLOAD, READ THREE TIMES INSTEAD OF EIGHT - AND THE LEGS, ONCE.
--
-- signals.payload is over 2 KB for 71% of signals (avg 3 KB, 20 MB of TOAST),
-- so it lives out of line, and Postgres re-reads it for EVERY reference: the
-- summed / legs / leg_ids expressions below named it eight times per row.
-- legs_settled and legs_yes were correlated subqueries in a CTE the planner
-- inlines, so each of the output columns that mentions legs_yes ran its own
-- copy. Together: 163,507 buffers for v_strategy_board's ten rows.
--
-- `raw` reads the three things the mark needs from the payload once each and
-- is materialised, so everything after it works on small values in memory;
-- one lateral counts both kinds of leg in a single pass. The band outcome
-- (v_fact_band_outcome_clean since plan v2 P4.4) is keyed by band_id, so the left join adds no rows and the two counts are the
-- two EXISTS counts they replace. Verified identical before it was applied -
-- 3,624 marks and the board's 10 rows, none differing in either direction -
-- and the board fell to 54,184 buffers.
with raw as materialized (
  select f.signal_id, f.strategy_id, f.side, f.price_at_fire, f.band_id, f.fired_at,
         s.payload ? 'band_ids'     as has_band_ids,
         s.payload ? 'basket_group' as has_basket_group,
         s.payload -> 'band_ids'    as band_ids
    from public.fact_signal_outcome f
    join public.signals s on s.signal_id = f.signal_id
   where f.action = 'ENTER'
     and f.side in ('YES', 'NO')
     and f.price_at_fire is not null
     and f.price_at_fire > 0
     and f.price_at_fire < 1
),
shaped as (
  select r.signal_id,
         r.strategy_id,
         r.side,
         r.price_at_fire                                            as price_at_fire,
         -- s2/s6 carry the whole basket in one signal; s8/s9 carry a leg each
         -- and tie them together with basket_group.
         r.has_band_ids and not r.has_basket_group                  as summed,
         case when r.has_band_ids and not r.has_basket_group
              then coalesce(jsonb_array_length(r.band_ids), 1)
              else 1 end                                            as legs,
         case when r.has_band_ids and not r.has_basket_group
              then r.band_ids
              else to_jsonb(array[r.band_id::text]) end             as leg_ids,
         r.fired_at
    from raw r
),
counted as (
  select h.*, n.legs_settled, n.legs_yes
    from shaped h
    -- Every leg must have settled, or the basket has no outcome yet.
    cross join lateral (
      select count(*) filter (where b.settled_yes is not null) as legs_settled,
             count(*) filter (where b.settled_yes)             as legs_yes
        from jsonb_array_elements_text(h.leg_ids) t(id)
        left join public.v_fact_band_outcome_clean b on b.band_id = t.id::uuid
    ) n
)
select c.signal_id,
       c.strategy_id,
       c.price_at_fire,
       case when c.summed then 'basket_fee_inclusive' else 'single_leg' end as mark_basis,
       c.legs::integer                                              as mark_legs,
       -- A YES position pays when the day lands on any leg it bought; a NO
       -- position pays when it lands on none of them.
       (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                                                                    as mark_won,
       round((case when (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                   then 1 else 0 end) - c.price_at_fire, 6)         as mark_gross_per_share,
       -- s2/s6 already priced the fee into price_at_fire. Charging it again
       -- here would be the same dollar counted twice.
       case when c.summed then 0
            else round(0.05 * c.price_at_fire * (1 - c.price_at_fire), 6) end
                                                                    as mark_fee_per_share,
       round((case when (case when c.side = 'YES' then c.legs_yes > 0 else c.legs_yes = 0 end)
                   then 1 else 0 end)
             - c.price_at_fire
             - case when c.summed then 0
                    else 0.05 * c.price_at_fire * (1 - c.price_at_fire) end, 6)
                                                                    as mark_net_per_share,
       -- When it fired: signal_engine._earned_weights reads the last 45 days
       -- by it, and got HTTP 400 on every run while the view had no such
       -- column (29 Sep audit, repair 5). Last, so no column before it moves.
       c.fired_at
  from counted c
 where c.legs_settled = c.legs;

comment on view public.v_signal_mark is
  'What ONE share of each settled signal, taken at the price that justified it, returned at settlement net of the venue fee, and when it fired. Desk-independent: no account, no cash, no approval. A signal whose legs have not all settled is absent rather than zero.';

revoke all on public.v_signal_mark from anon, authenticated;
grant select on public.v_signal_mark to service_role;


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
),
-- THE DESK-INDEPENDENT RECORD. No join to an account, an order or a fill.
scored as (
  select f.strategy_id,
         count(f.signal_correct)::int                         as n_scored,
         count(*) filter (where f.signal_correct)::int         as n_correct,
         count(m.signal_id)::int                              as n_marked,
         count(*) filter (where m.mark_won)::int              as n_mark_won,
         round(sum(m.price_at_fire), 4)                       as stake,
         round(sum(m.mark_net_per_share), 4)                  as mark_net,
         -- One strategy uses one convention, so max() names it without
         -- collapsing two different meanings into one row.
         max(m.mark_basis)                                    as mark_basis
    from fact_signal_outcome f
    left join public.v_signal_mark m on m.signal_id = f.signal_id
   group by f.strategy_id
)
select
  s.strategy_id,
  s.name,
  s.side,
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
  -- THE VERDICT NOW ASKS THE SIGNALS, NOT THE DESK. "Nothing has filled yet"
  -- described the desk's subscription list and read as a fact about the
  -- strategy; 30 days of it is how s3 and s6 looked unproven while 1,600
  -- settled signals of theirs sat in this same table.
  case
    when s.strategy_id = 'system'          then 'not a trading strategy'
    -- A RETIREMENT THAT ERASES ITS OWN REASON. Every arm below reads the
    -- record; `not s.enabled` used to short-circuit past all of them, so the
    -- moment a measured verdict was ACTED on - s1 at -7.6c on the dollar over
    -- 615 marked signals - the board stopped saying it and said "off"
    -- instead. The number stayed in return_on_stake_pct beside it; the
    -- sentence a reader actually reads did not. So a strategy carrying a
    -- retirement stamp states the stamp, which is the reason recorded at the
    -- time rather than one re-derived now from whatever has settled since.
    when not s.enabled
     and coalesce(s.extra, '{}'::jsonb) ? 'retired_on'
                                           then format('retired %s - %s',
                                                  s.extra ->> 'retired_on',
                                                  coalesce(s.extra ->> 'retired_record',
                                                           s.extra ->> 'retired_because'))
    when not s.enabled                     then 'off - it cannot propose anything'
    when coalesce(f.n_fired, 0) = 0        then 'on, but nothing has met its conditions in 30 days'
    when coalesce(sc.n_scored, 0) = 0      then 'firing, but nothing it fired has settled yet'
    -- 30 is where a binomial is usable under the normal approximation, which
    -- is the smallest sample a hit rate can honestly be quoted from.
    when coalesce(sc.n_scored, 0) < 30     then 'too few settled signals to judge - under 30'
    when coalesce(sc.stake, 0) = 0         then 'settled, but nothing it fired could be marked to a price'
    when coalesce(sc.mark_net, 0) > 0      then 'profitable on its own signals, before any desk'
    else                                        'losing on its own signals, before any desk'
  end                                                        as verdict,
  -- --- appended: the record that owes nothing to a desk -------------------
  coalesce(sc.n_scored, 0)                                   as settled_signals,
  coalesce(sc.n_correct, 0)                                  as correct_signals,
  case when coalesce(sc.n_scored, 0) > 0
       then round(100.0 * sc.n_correct / sc.n_scored, 1) end as hit_rate_pct,
  coalesce(sc.n_marked, 0)                                   as marked_signals,
  sc.stake                                                   as mark_stake_per_share,
  sc.mark_net                                                as mark_net_per_share,
  -- What a dollar committed to this strategy's own calls came back as. The
  -- number a hit rate cannot give you: 16% right at 10c beats 78% right at 95c.
  case when coalesce(sc.stake, 0) > 0
       then round(100.0 * sc.mark_net / sc.stake, 1) end     as return_on_stake_pct,
  sc.mark_basis,
  -- THE HIT RATE THAT IS TRUE OF A BASKET. hit_rate_pct above comes from
  -- signal_correct, which scores a signal against ITS OWN band - right for a
  -- single leg, and for s6 a five-band cover judged by one of its five. Live:
  -- s6 reads 8.8% there and wins 54% of the time here, and neither number is a
  -- typo. Both are kept because signal_correct is what the fitter was trained
  -- against and rewriting it would silently change that history.
  case when coalesce(sc.n_marked, 0) > 0
       then round(100.0 * sc.n_mark_won / sc.n_marked, 1) end as mark_win_rate_pct
from strategies s
left join fired  f  on f.strategy_id  = s.strategy_id
left join paid   p  on p.strategy_id  = s.strategy_id
left join scored sc on sc.strategy_id = s.strategy_id
order by s.enabled desc,
         case when coalesce(sc.stake, 0) > 0 then sc.mark_net / sc.stake end desc nulls last,
         s.strategy_id;

comment on view v_strategy_board is
  'Every strategy with its own record beside its switch. The record is its SIGNALS marked to settlement - no desk, no cash, no approval - because whether a desk subscribed is a deployment choice and not evidence about the strategy. The filled_* and net_pnl columns remain: those are the realised desk record, which is the only place real slippage appears.';


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
  end                                                                       as timing_note,

  -- THE DAY THIS ROW IS ABOUT, stated rather than implied.
  --
  -- Every column above - running_max_c, implied_max_c, minutes_to_peak,
  -- day_decided - describes ONE day: the one currently in progress in this
  -- city. Nothing said so, and v_trade_plan joined this view on city_key
  -- alone, so a market resolving tomorrow was handed today's running maximum
  -- and today's verdict. Tel Aviv read "Day decided - this band is settled,
  -- not traded" on a day that had not started; fourteen of tomorrow's
  -- twenty-two bands were flagged unreachable because TODAY did not get that
  -- warm; and s5 - whose whole premise is "the day is over and the maximum is
  -- locked" - fired on a 2026-09-17 market. A consumer can now join on this
  -- and get nothing for any other day, which is the correct answer.
  --
  -- Last in the select list on purpose: appending keeps `create or replace`
  -- working in place, and v_campaign_state and v_city_day_plan both hang off
  -- this chain - a cascade to add one column would take them with it.
  (w.local_now)::date                                                       as local_date
from win w
left join v_city_peak_approach a on a.city_key = w.city_key
left join live_weather lw        on lw.city_key = w.city_key;

comment on view v_trade_timing is
  'Whether the day is still being decided, and whether NOW is the moment. The peak hour is this city''s own measured one for this month, and the entry rule is strategy S7''s, with S7''s constants - a page that disagreed with the engine about when to enter would be lying.';


-- --------------------------------------------------------------------------
-- 4. Grants.
--
--    set_strategy_enabled is a WRITE. It used to be granted to anon, as a
--    deliberate exception, so the app could run the desk without a SQL
--    console. Plan v2 P1.2 removed the exception: the app now calls it through
--    its operator route (web/app/api/operator), which checks a signed-in
--    operator and uses the service key, so the switch still works from the
--    site and is no longer open to anyone holding the public key. The function
--    stays narrow: it flips one boolean on one row and refuses the system
--    pseudo-strategy; capital_cap_pct and max_concurrent stay out of reach.
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

  -- service_role only (plan v2 P1.2). This used to be granted to anon so the
  -- app could run the desk at all; the app now reaches it through its
  -- operator route, which checks a signed-in operator and uses the service
  -- key, so the switch works from the site without being open to the internet.
  revoke execute on function set_strategy_enabled(text, boolean) from public;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function set_strategy_enabled(text, boolean) to service_role;
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
