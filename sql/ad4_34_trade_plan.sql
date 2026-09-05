-- ===========================================================================
-- ad4_34_trade_plan.sql - from "this band is mispriced" to "do this, now".
--
-- WHAT WAS MISSING. The Opportunities page listed edges. An edge is not a
-- trade. Three things stood between them and every one of them lived only
-- inside the Python strategy engine, where the page could not see it:
--
--   WHICH STRATEGY WOULD TAKE THIS. Nine strategies ship disabled, so the
--   board showed a mispriced band and nothing that would act on it. Worse,
--   an operator could not tell whether turning s7 on would have changed
--   anything, because nothing said which bands s7 even looks at.
--
--   WHEN. An edge on a band whose day is already decided is a memory. An edge
--   forty minutes before the peak with the line still climbing is the trade.
--   Same row, same number, opposite meaning.
--
--   WHAT THE ENTRY ACTUALLY COSTS. market_price is a mid. You buy at the ask
--   and sell at the bid, and on the thin cities that gap reached 14-18c.
--
-- HOW THE MIRROR IS KEPT HONEST. Every threshold below is read from
-- strategies.extra - the SAME jsonb the Python engine reads - and falls back
-- to the SAME constant the Python file defines. Tune a strategy and the
-- mirror follows it. If this view and scripts/strategies/ ever disagreed
-- about what fires, the view would be a lie dressed as a preview, which is
-- worse than not having it.
--
-- WHAT IS DELIBERATELY NOT MIRRORED. s2 (combination arb), s6 (anchor +
-- insurance) and s9 (ladder basket) are BASKET strategies: they buy a set,
-- and no single-band row can state their condition without misleading. They
-- are named on the row as basket strategies rather than faked. s8 is a PAIR,
-- which is per city-day and does have a home here - v_city_day_plan.
--
-- Run order: after sql/ad4_33_control.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.v_opportunities') is null then
    raise exception 'ad4_34 needs v_opportunities - run sql/ad4_phase2.sql first';
  end if;
  if to_regclass('public.v_trade_timing') is null then
    raise exception 'ad4_34 needs v_trade_timing - run sql/ad4_33_control.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 0. Drop this file's own views before rebuilding them.
--
--    `create or replace view` refuses a change in column ORDER or NAME, which
--    is exactly what happens when a column is added in the middle of one of
--    these during development - and the failure ("cannot change name of view
--    column") reads like a permissions problem rather than a shape change.
--    Dropping first makes the file re-runnable through a redesign. Nothing
--    else in the schema depends on these; cascade is a safety net, not a plan.
-- --------------------------------------------------------------------------
do $ad4$
declare v text;
begin
  foreach v in array array['v_city_day_plan', 'v_trade_plan',
                           'v_band_ladder', 'v_strategy_params'] loop
    execute format('drop view if exists %I cascade', v);
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. The thresholds, from the same place the engine reads them.
--
--    One row. The fallbacks are the DEFAULT_ constants in the matching
--    scripts/strategies/*.py, copied deliberately rather than invented, so a
--    strategy that has never been tuned mirrors exactly as it runs.
-- --------------------------------------------------------------------------
create or replace view v_strategy_params as
with x as (select strategy_id, coalesce(extra, '{}'::jsonb) as e from strategies)
select
  -- s1_buy_low_sell_signal
  coalesce((select (e ->> 'price_threshold')::numeric   from x where strategy_id = 's1_buy_low_sell_signal'), 0.30) as s1_price_threshold,
  coalesce((select (e ->> 'min_liquidity_usd')::numeric from x where strategy_id = 's1_buy_low_sell_signal'), 200)  as s1_min_liquidity_usd,
  -- s3_concentration
  coalesce((select (e ->> 'max_mae_bands')::numeric     from x where strategy_id = 's3_concentration'), 1.0)        as s3_max_mae_bands,
  coalesce((select (e ->> 'width_bands')::int           from x where strategy_id = 's3_concentration'), 2)          as s3_width_bands,
  -- s4_tail_fade
  coalesce((select (e ->> 'tail_bands')::int            from x where strategy_id = 's4_tail_fade'), 2)              as s4_tail_bands,
  -- s5_running_max_lock
  coalesce((select (e ->> 'max_entry_price')::numeric   from x where strategy_id = 's5_running_max_lock'), 0.90)    as s5_max_entry_price,
  -- s7_pre_peak_gradient
  coalesce((select (e ->> 'entry_window_min')::int      from x where strategy_id = 's7_pre_peak_gradient'), 60)     as s7_entry_window_min,
  coalesce((select (e ->> 'max_reading_age_min')::int   from x where strategy_id = 's7_pre_peak_gradient'), 90)     as s7_max_reading_age_min,
  coalesce((select (e ->> 'min_slope_c_per_h')::numeric from x where strategy_id = 's7_pre_peak_gradient'), 0.10)   as s7_min_slope_c_per_h,
  coalesce((select (e ->> 'max_entry_price')::numeric   from x where strategy_id = 's7_pre_peak_gradient'), 0.85)   as s7_max_entry_price,
  -- s8_two_bucket_cover
  coalesce((select (e ->> 'max_pair_cost')::numeric     from x where strategy_id = 's8_two_bucket_cover'), 0.70)    as s8_max_pair_cost,
  coalesce((select (e ->> 'min_pair_prob')::numeric     from x where strategy_id = 's8_two_bucket_cover'), 0.72)    as s8_min_pair_prob,
  coalesce((select (e ->> 'min_liquidity_usd')::numeric from x where strategy_id = 's8_two_bucket_cover'), 100)     as s8_min_liquidity_usd;

comment on view v_strategy_params is
  'Every threshold the mirror uses, read from strategies.extra - the same jsonb the Python engine reads - with the same fallbacks the Python files define.';


-- --------------------------------------------------------------------------
-- 2. Two helpers the strategy files have in Python and the database did not.
--
--    Bands are labelled in the city's own unit; the archive is Celsius. Every
--    comparison between a band edge and a temperature has to convert, and
--    getting that wrong turns 28C into a band 50 degrees away.
-- --------------------------------------------------------------------------
create or replace function band_local_value(p_c numeric, p_unit text)
returns numeric language sql immutable as $ad4$
  select case when p_c is null then null
              when p_unit = 'F' then p_c * 9.0 / 5.0 + 32.0
              else p_c end;
$ad4$;

create or replace function band_contains(p_lo numeric, p_hi numeric,
                                         p_open_low boolean, p_open_high boolean,
                                         p_value numeric)
returns boolean language sql immutable as $ad4$
  select case
    when p_value is null                       then false
    when coalesce(p_open_low, false)           then p_hi is not null and p_value <  p_hi
    when coalesce(p_open_high, false)          then p_lo is not null and p_value >= p_lo
    when p_lo is null or p_hi is null          then false
    else p_value >= p_lo and p_value < p_hi
  end;
$ad4$;

comment on function band_contains(numeric, numeric, boolean, boolean, numeric) is
  'Half-open [lo, hi), with the open tails handled the way the strategy files handle them. The tails are unbounded on one side and comparing them like closed bands silently drops every extreme.';


-- --------------------------------------------------------------------------
-- 3. The ladder, per city and resolution day.
--
--    Position in the ladder is what three of the strategies actually key on -
--    "outer two", "within two of the centre" - and it cannot be computed from
--    a single row.
-- --------------------------------------------------------------------------
create or replace view v_band_ladder as
with y as (
  select o.band_id, o.city_key, o.resolution_date, o.band_lo, o.band_hi,
         o.open_low, o.open_high, o.band_label, o.unit,
         o.model_prob, o.market_price, o.tradeable, o.fillable_usd_5c,
         o.best_bid, o.best_ask, o.spread
    from v_opportunities o
   where o.side = 'YES'
),
closed_idx as (
  select band_id,
         row_number() over (partition by city_key, resolution_date order by band_lo) as closed_pos,
         count(*)     over (partition by city_key, resolution_date)                  as n_closed
    from y
   where not coalesce(open_low, false) and not coalesce(open_high, false)
)
select
  y.*,
  ci.closed_pos,
  ci.n_closed,
  row_number() over (partition by y.city_key, y.resolution_date order by y.band_lo) as ladder_pos,
  count(*)     over (partition by y.city_key, y.resolution_date)                    as n_bands,
  -- Where the model's peak bucket sits, so "within two of the centre" is
  -- answerable. Ties broken by band floor, which is stable across refreshes.
  row_number() over (partition by y.city_key, y.resolution_date
                     order by y.model_prob desc nulls last, y.band_lo)              as prob_rank,
  first_value(y.band_lo) over (partition by y.city_key, y.resolution_date
                               order by y.model_prob desc nulls last, y.band_lo)    as peak_band_lo
from y
left join closed_idx ci on ci.band_id = y.band_id;

comment on view v_band_ladder is
  'Each band''s position in its city-day ladder, and where the model''s most likely bucket sits. Three strategies key on position - "the outer two", "within two of the centre" - and position is not a property of a single row.';


-- --------------------------------------------------------------------------
-- 4. The plan. One row per tradeable side of a band, with WHEN and WHO.
-- --------------------------------------------------------------------------
create or replace view v_trade_plan as
with p as (select * from v_strategy_params),
live as (select array_agg(strategy_id) as on_now from strategies where enabled),
skill as (
  -- Latest measured forecast skill per city, at the lead that matters for
  -- today. Distinct on takes the newest computed_at, not an average across
  -- every recomputation.
  select distinct on (city_key) city_key, mae_bands, lead_days, n_days
    from derived_forecast_skill
   order by city_key, computed_at desc, lead_days
),
lad as (select * from v_band_ladder),
base as (
select
  o.edge_id,
  o.band_id,
  o.city_key,
  o.display_name,
  o.resolution_date,
  o.band_label,
  o.unit,
  o.side,
  o.model_prob,
  o.market_price,
  o.edge_net_pp,
  o.edge_per_dollar,
  o.confidence,
  o.regime_label,
  o.tradeable,
  o.block_reason,
  o.score,
  o.fillable_usd_5c,
  o.spread,
  o.best_bid,
  o.best_ask,
  o.volume_usd,
  o.thin_market,
  o.book_observed_at,

  -- ---- WHAT THE ENTRY COSTS -------------------------------------------
  -- The price you actually pay, not the mid. YES lifts the ask; NO is the
  -- other side of the same book, so it lifts 1 - bid.
  case when o.side = 'YES' then o.best_ask
       when o.best_bid is not null then round(1.0 - o.best_bid, 4) end   as entry_price,
  -- The round trip s1 has to clear: two taker fees at 5% of p(1-p), two
  -- spreads, and gas. Per share, the same arithmetic as cost_model.py.
  round((2 * 0.05 * coalesce(o.market_price, 0) * (1 - coalesce(o.market_price, 0))
         + 2 * coalesce(o.spread, 0) + 0.01)::numeric, 4)                as round_trip_cost,
  (o.edge_net_pp is not null
   and o.edge_net_pp > 2 * 0.05 * coalesce(o.market_price, 0) * (1 - coalesce(o.market_price, 0))
                       + 2 * coalesce(o.spread, 0) + 0.01)              as beats_round_trip,

  -- ---- WHEN ------------------------------------------------------------
  t.window_state,
  t.minutes_to_peak,
  t.in_entry_window,
  t.timing_note,
  t.peak_hour,
  t.peak_source,
  t.local_hour,
  t.slope_3_c_per_h,
  t.direction,
  t.rolling_over,
  t.day_decided,
  t.reading_age_min,
  t.latest_temp_c,
  t.running_max_c,
  t.implied_max_c,
  t.implied_max_low_c,
  t.implied_max_high_c,

  -- ---- WHERE THE DAY IS GOING, RELATIVE TO THIS BAND --------------------
  band_contains(o.band_lo, o.band_hi, o.open_low, o.open_high,
                band_local_value(t.implied_max_c, o.unit))              as holds_implied_max,
  band_contains(o.band_lo, o.band_hi, o.open_low, o.open_high,
                band_local_value(t.running_max_c, o.unit))              as holds_running_max,
  band_contains(o.band_lo, o.band_hi, o.open_low, o.open_high,
                band_local_value(t.implied_max_low_c, o.unit))          as holds_pessimistic_max,
  (o.band_lo is not null
   and band_local_value(t.implied_max_high_c, o.unit) is not null
   and o.band_lo > band_local_value(t.implied_max_high_c, o.unit))      as out_of_reach,

  -- ---- WHO WOULD TAKE IT ----------------------------------------------
  -- Every strategy whose single-band entry test this row passes RIGHT NOW,
  -- whether or not the strategy is switched on. That is the point: it is the
  -- answer to "would turning s7 on have changed anything today".
  (
    array_remove(array[
      -- s1: cheap enough, and the edge clears a full round trip.
      case when o.side in ('YES','NO') and o.tradeable
            and o.market_price is not null and o.market_price <= p.s1_price_threshold
            and coalesce(o.fillable_usd_5c, 0) >= p.s1_min_liquidity_usd
            and o.edge_net_pp is not null
            and o.edge_net_pp > 2 * 0.05 * o.market_price * (1 - o.market_price)
                                + 2 * coalesce(o.spread, 0) + 0.01
           then 's1_buy_low_sell_signal' end,

      -- s3: a closed band within width_bands of the model's peak bucket, on a
      -- city whose forecast is measurably accurate to under a band.
      case when o.side = 'YES' and o.tradeable and coalesce(o.edge_net_pp, 0) > 0
            and not coalesce(o.open_low, false) and not coalesce(o.open_high, false)
            and sk.mae_bands is not null and sk.mae_bands < p.s3_max_mae_bands
            and l.prob_rank is not null
            and abs(l.ladder_pos - (select l2.ladder_pos from lad l2
                                     where l2.city_key = o.city_key
                                       and l2.resolution_date = o.resolution_date
                                       and l2.prob_rank = 1)) <= p.s3_width_bands
           then 's3_concentration' end,

      -- s4: the outer closed bands, sold, away from an uncertain regime.
      case when o.side = 'NO' and o.tradeable and coalesce(o.edge_net_pp, 0) > 0
            and coalesce(o.regime_label, '') not in ('UNCERTAIN', 'BLOCKED')
            and l.closed_pos is not null
            and (l.closed_pos <= p.s4_tail_bands
                 or l.closed_pos > l.n_closed - p.s4_tail_bands)
           then 's4_tail_fade' end,

      -- s5: the day is over, this band holds the locked maximum, still cheap.
      case when o.side = 'YES' and o.tradeable and coalesce(t.day_decided, false)
            and band_contains(o.band_lo, o.band_hi, o.open_low, o.open_high,
                              band_local_value(t.running_max_c, o.unit))
            and o.market_price is not null and o.market_price < p.s5_max_entry_price
           then 's5_running_max_lock' end,

      -- s7 YES: inside the window, still climbing, this band holds where the
      -- day is heading, and the pessimistic case reaches it too.
      case when o.side = 'YES' and o.tradeable
            and coalesce(t.minutes_to_peak, -1) between 0 and p.s7_entry_window_min
            and coalesce(t.reading_age_min, 999) <= p.s7_max_reading_age_min
            and coalesce(t.slope_3_c_per_h, 0) > p.s7_min_slope_c_per_h
            and not coalesce(t.day_decided, false) and not coalesce(t.rolling_over, false)
            and band_contains(o.band_lo, o.band_hi, o.open_low, o.open_high,
                              band_local_value(t.implied_max_c, o.unit))
            and o.market_price is not null and o.market_price < p.s7_max_entry_price
           then 's7_pre_peak_gradient' end,

      -- s7 NO: the mirror. Rolled over inside the window, so sell the bands
      -- the day can no longer reach.
      case when o.side = 'NO' and o.tradeable
            and coalesce(t.minutes_to_peak, -1) between 0 and p.s7_entry_window_min
            and coalesce(t.reading_age_min, 999) <= p.s7_max_reading_age_min
            and coalesce(t.rolling_over, false)
            and o.band_lo is not null
            and band_local_value(t.latest_temp_c, o.unit) is not null
            and o.band_lo > band_local_value(t.latest_temp_c, o.unit)
            and o.market_price is not null and o.market_price < p.s7_max_entry_price
           then 's7_pre_peak_gradient' end
    ], null)
  )                                                                      as would_fire,

  -- How old is the price this is all built on. An edge computed against a
  -- three-hour-old book is a story about three hours ago.
  case when o.book_observed_at is null then null
       else round(extract(epoch from (now() - o.book_observed_at)) / 60.0)::int
  end                                                                    as book_age_min
from v_opportunities o
left join v_trade_timing t on t.city_key = o.city_key
left join lad l            on l.band_id  = o.band_id
left join skill sk         on sk.city_key = o.city_key
cross join p
)
select
  b.*,
  -- The subset that is switched ON. This is the number that makes the cost of
  -- a disabled strategy visible: "three strategies would take this and none of
  -- them is running" is a decision, where an empty signals list is a shrug.
  (select array_agg(x) from unnest(b.would_fire) x
    where x = any(coalesce((select on_now from live), array[]::text[])))  as would_fire_enabled,
  -- ---- THE SENTENCE ----------------------------------------------------
  case
    when not b.tradeable                     then coalesce(b.block_reason, 'not tradeable')
    when coalesce(b.day_decided, false)
     and b.holds_running_max and b.side = 'YES'
                                             then 'The maximum is banked and this band holds it - s5 territory'
    when coalesce(b.day_decided, false)      then 'Day decided - this band is settled, not traded'
    when cardinality(b.would_fire) > 0
                                             then format('%s would take %s at %s%s',
                                                    array_to_string(b.would_fire, ' + '), b.side,
                                                    coalesce(b.entry_price::text, 'the quote'),
                                                    -- Only s7 keys on the peak. Appending the
                                                    -- countdown to an s4 row would imply s4 cares
                                                    -- about timing, and it does not.
                                                    case when 's7_pre_peak_gradient' = any(b.would_fire)
                                                         then format(' - peak in %s min', b.minutes_to_peak)
                                                         else '' end)
    when b.side = 'YES' and b.holds_implied_max and coalesce(b.in_entry_window, false)
                                             then format('The day is heading into this band and the peak is %s min out, but no strategy''s entry test passes', b.minutes_to_peak)
    when b.side = 'NO' and b.out_of_reach    then 'Even this city''s best day does not reach this band'
    when coalesce(b.window_state, '') = 'BEFORE'
                                             then format('Watch - the peak window opens in %s min', greatest(b.minutes_to_peak, 0))
    when coalesce(b.edge_net_pp, 0) > 0      then 'An edge, but nothing about the day says act on it now'
    else                                          'No edge on this side'
  end                                                                    as action
from base b;

comment on view v_trade_plan is
  'Every edge with WHEN and WHO attached: which strategies would take it right now, what the entry actually costs, and where the day is heading relative to this band. Thresholds come from strategies.extra, the same jsonb the Python engine reads.';


-- --------------------------------------------------------------------------
-- 5. The pair trade, which is per city-day and cannot live on a band row.
--
--    s8 buys the two most likely ADJACENT buckets when the pair costs less
--    than max_pair_cost including the fee, the pair's probability beats that
--    cost, and one of the two contains where the day is heading.
-- --------------------------------------------------------------------------
create or replace view v_city_day_plan as
with p as (select * from v_strategy_params),
top2 as (
  select l.*,
         row_number() over (partition by l.city_key, l.resolution_date
                            order by l.model_prob desc nulls last, l.band_lo) as rk
    from v_band_ladder l
   where l.model_prob is not null and l.market_price is not null and l.tradeable
),
pair as (
  select
    a.city_key, a.resolution_date,
    a.band_id as band_a, a.band_label as label_a, a.market_price as price_a,
    a.model_prob as prob_a, a.fillable_usd_5c as liq_a, a.band_lo as lo_a, a.band_hi as hi_a,
    a.open_low as open_low_a, a.open_high as open_high_a, a.unit,
    b.band_id as band_b, b.band_label as label_b, b.market_price as price_b,
    b.model_prob as prob_b, b.fillable_usd_5c as liq_b, b.band_lo as lo_b, b.band_hi as hi_b,
    b.open_low as open_low_b, b.open_high as open_high_b,
    (a.ladder_pos - b.ladder_pos)                                as pos_gap
  from top2 a
  join top2 b on b.city_key = a.city_key and b.resolution_date = a.resolution_date
             and b.rk = 2
  where a.rk = 1
)
select
  pr.city_key,
  pr.resolution_date,
  pr.label_a,
  pr.label_b,
  pr.band_a,
  pr.band_b,
  pr.price_a,
  pr.price_b,
  round(pr.prob_a + pr.prob_b, 4)                                as pair_prob,
  -- The pair fee is charged on each leg at 5% of p(1-p), same as cost_model.
  round((pr.price_a + pr.price_b
         + 0.05 * pr.price_a * (1 - pr.price_a)
         + 0.05 * pr.price_b * (1 - pr.price_b))::numeric, 4)    as pair_cost_with_fee,
  (abs(pr.pos_gap) = 1)                                          as adjacent,
  least(pr.liq_a, pr.liq_b)                                      as thinner_leg_usd,
  t.implied_max_c,
  (band_contains(pr.lo_a, pr.hi_a, pr.open_low_a, pr.open_high_a,
                 band_local_value(t.implied_max_c, pr.unit))
   or band_contains(pr.lo_b, pr.hi_b, pr.open_low_b, pr.open_high_b,
                 band_local_value(t.implied_max_c, pr.unit)))    as covers_implied_max,
  t.window_state,
  t.minutes_to_peak,
  -- s8's full condition, stated once.
  (
        abs(pr.pos_gap) = 1
    and (pr.price_a + pr.price_b
         + 0.05 * pr.price_a * (1 - pr.price_a)
         + 0.05 * pr.price_b * (1 - pr.price_b)) < p.s8_max_pair_cost
    and (pr.prob_a + pr.prob_b) >= p.s8_min_pair_prob
    and (pr.prob_a + pr.prob_b) > (pr.price_a + pr.price_b
         + 0.05 * pr.price_a * (1 - pr.price_a)
         + 0.05 * pr.price_b * (1 - pr.price_b))
    and least(pr.liq_a, pr.liq_b) >= p.s8_min_liquidity_usd
    and (band_contains(pr.lo_a, pr.hi_a, pr.open_low_a, pr.open_high_a,
                       band_local_value(t.implied_max_c, pr.unit))
      or band_contains(pr.lo_b, pr.hi_b, pr.open_low_b, pr.open_high_b,
                       band_local_value(t.implied_max_c, pr.unit)))
  )                                                              as s8_would_fire,
  case
    when abs(pr.pos_gap) <> 1                    then 'the two most likely buckets are not neighbours - that is two bets, not a cover'
    when (pr.price_a + pr.price_b
          + 0.05 * pr.price_a * (1 - pr.price_a)
          + 0.05 * pr.price_b * (1 - pr.price_b)) >= p.s8_max_pair_cost
                                                 then 'the pair costs too much for the upside it leaves'
    when (pr.prob_a + pr.prob_b) < p.s8_min_pair_prob
                                                 then format('the pair only covers %s of the outcome', round(100 * (pr.prob_a + pr.prob_b)) || '%')
    when least(pr.liq_a, pr.liq_b) < p.s8_min_liquidity_usd
                                                 then 'one leg is too thin to fill'
    when not (band_contains(pr.lo_a, pr.hi_a, pr.open_low_a, pr.open_high_a,
                            band_local_value(t.implied_max_c, pr.unit))
           or band_contains(pr.lo_b, pr.hi_b, pr.open_low_b, pr.open_high_b,
                            band_local_value(t.implied_max_c, pr.unit)))
                                                 then 'neither bucket holds where the day is actually heading'
    else format('COVER both - %s and %s for %s, covering %s of the outcome',
                pr.label_a, pr.label_b,
                round(pr.price_a + pr.price_b
                      + 0.05 * pr.price_a * (1 - pr.price_a)
                      + 0.05 * pr.price_b * (1 - pr.price_b), 2),
                round(100 * (pr.prob_a + pr.prob_b)) || '%')
  end                                                            as pair_note
from pair pr
left join v_trade_timing t on t.city_key = pr.city_key
cross join p;

comment on view v_city_day_plan is
  's8''s two-bucket cover, per city and day: the two most likely adjacent buckets, what the pair costs with fees, and the one reason it does not qualify when it does not.';


-- --------------------------------------------------------------------------
-- 6. Grants. All reads.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_strategy_params', 'v_band_ladder',
                           'v_trade_plan', 'v_city_day_plan'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function band_local_value(numeric, text) to %I', r);
      execute format('grant execute on function band_contains(numeric, numeric, boolean, boolean, numeric) to %I', r);
    end if;
  end loop;
end
$ad4$;


do $ad4$
declare v_n int; v_fire int; v_ms numeric; t0 timestamptz;
begin
  t0 := clock_timestamp();
  select count(*), count(*) filter (where cardinality(would_fire) > 0)
    into v_n, v_fire from v_trade_plan;
  v_ms := round(extract(epoch from (clock_timestamp() - t0)) * 1000);
  raise notice 'ad4_34: v_trade_plan - % row(s), % with a strategy that would fire, % ms', v_n, v_fire, v_ms;
  if v_ms > 400 then
    raise warning 'ad4_34: v_trade_plan took % ms - Supabase will feel that on a page load', v_ms;
  end if;
  if v_n = 0 then
    raise notice 'ad4_34: no edges yet - run the edge engine (Actions -> Edge Engine) after a book snapshot.';
  end if;
end
$ad4$;
