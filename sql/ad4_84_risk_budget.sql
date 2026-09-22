-- ===========================================================================
-- ad4_84_risk_budget.sql - THE STATE THE THREE RISK LIMITS RUN ON.
--
-- scripts/risk_budget.py holds the arithmetic and reads no database. This file
-- is the other half: the numbers it needs, computed where the rows live.
--
-- WHY EQUITY HERE IS REALISED ONLY.
--
-- The drawdown control is derived for WEALTH - money the desk actually has,
-- not money it has marked. Marking open positions at the current book would
-- make the control jump every time a quote moves, and marking them at cost
-- would make it blind to a position that is already lost. Realised equity is
-- the one series that is unambiguous, and the blind window it leaves - money
-- committed since the last settlement - is exactly what the daily entry budget
-- in risk_budget.py exists to bound. The two are designed together; using
-- either alone would leave a hole.
--
-- WHY THE HIGH-WATER MARK STARTS AT starting_cash. A desk that has only ever
-- lost is at its high-water mark on day one, and its drawdown is measured from
-- the money it was given. Seeding the running max with the first closed trade
-- instead would call a desk that lost on its opening trade "at its peak", and
-- the control would do nothing exactly when it should start.
--
-- RUN ORDER: after ad4_81_paper_desk_integrity.sql. Views only, no table, no
-- writes: everything here is derived from paper_trades and paper_accounts on
-- read, so there is nothing to keep fresh and nothing to go stale.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- The realised equity path, one row per closed trade, with its running max.
--
-- `order by closed_at, trade_id` - closed_at alone ties whenever settlement
-- closes a market's positions in one pass, and a window function over a tie
-- is free to order it either way, which makes the running max depend on the
-- plan rather than on the data.
-- --------------------------------------------------------------------------
create or replace view v_desk_equity_curve as
select
  q.account_id,
  q.trade_id,
  q.closed_at,
  q.starting_cash,
  q.net_pnl,
  q.equity_after,
  greatest(q.starting_cash,
           max(q.equity_after) over (partition by q.account_id
                                     order by q.closed_at, q.trade_id
                                     rows between unbounded preceding and current row))
                                                               as high_water_after
from (
  select t.account_id, t.trade_id, t.closed_at, a.starting_cash, t.net_pnl,
         a.starting_cash
           + sum(coalesce(t.net_pnl, 0)) over (partition by t.account_id
                                               order by t.closed_at, t.trade_id
                                               rows between unbounded preceding and current row)
                                                               as equity_after
    from paper_trades t
    join paper_accounts a on a.account_id = t.account_id
   where t.closed_at is not null
) q;

comment on view v_desk_equity_curve is
  'Realised equity after every closed trade, per desk, with the running high-water mark. Realised only: see the header of sql/ad4_84_risk_budget.sql for why marking open positions would break the drawdown control it feeds.';


-- --------------------------------------------------------------------------
-- One row per desk: everything scripts/risk_budget.py asks for.
-- --------------------------------------------------------------------------
create or replace view v_desk_risk_state as
with realised as (
  select account_id,
         sum(coalesce(net_pnl, 0)) as realised_pnl,
         count(*)                  as closed_trades
    from paper_trades
   where closed_at is not null
   group by account_id
),
peak as (
  select account_id, max(high_water_after) as peak_equity
    from v_desk_equity_curve
   group by account_id
),
today as (
  -- What has been COMMITTED since midnight UTC, whether or not it has
  -- settled. This is the money the drawdown control cannot see.
  select account_id,
         sum(coalesce(shares, 0) * coalesce(avg_fill_price, 0)) as spent_today_usd,
         count(*)                                                as entries_today
    from paper_trades
   where action = 'ENTER'
     and opened_at >= date_trunc('day', now() at time zone 'utc')
   group by account_id
),
still_open as (
  select account_id,
         sum(coalesce(shares, 0) * coalesce(avg_fill_price, 0)) as open_gross_usd,
         count(*)                                                as open_trades
    from paper_trades
   where closed_at is null
   group by account_id
)
select
  a.account_id,
  a.name,
  a.mode,
  a.entries_paused,
  a.starting_cash,
  a.cash,
  a.reserved_cash,
  coalesce(r.realised_pnl, 0)                                   as realised_pnl,
  coalesce(r.closed_trades, 0)                                  as closed_trades,
  a.starting_cash + coalesce(r.realised_pnl, 0)                 as equity,
  greatest(a.starting_cash, coalesce(p.peak_equity, a.starting_cash))
                                                                 as high_water,
  case
    when greatest(a.starting_cash, coalesce(p.peak_equity, a.starting_cash)) > 0
    then round((1 - (a.starting_cash + coalesce(r.realised_pnl, 0))
                  / greatest(a.starting_cash,
                             coalesce(p.peak_equity, a.starting_cash)))::numeric, 4)
    else 0
  end                                                            as drawdown_fraction,
  coalesce(t.spent_today_usd, 0)                                as spent_today_usd,
  coalesce(t.entries_today, 0)                                  as entries_today,
  coalesce(o.open_gross_usd, 0)                                 as open_gross_usd,
  coalesce(o.open_trades, 0)                                    as open_trades
from paper_accounts a
left join realised   r on r.account_id = a.account_id
left join peak       p on p.account_id = a.account_id
left join today      t on t.account_id = a.account_id
left join still_open o on o.account_id = a.account_id
where a.archived_at is null;

comment on view v_desk_risk_state is
  'Per desk: realised equity, its high-water mark, the drawdown between them, what has been committed since midnight UTC, and what is still open. The four inputs scripts/risk_budget.py needs to size a day.';


-- --------------------------------------------------------------------------
-- Where the money already is, by weather event.
--
-- A city-day, not a city: two ladders on the same city for different dates
-- are two events. The correlation budget groups by CITY because a station and
-- a season are shared; this cap is per EVENT because a single day's maximum
-- is one outcome. They are different questions and both are asked.
-- --------------------------------------------------------------------------
create or replace view v_city_day_exposure as
select
  t.account_id,
  t.city_key,
  t.resolution_date,
  sum(coalesce(t.shares, 0) * coalesce(t.avg_fill_price, 0)) as open_gross_usd,
  count(*)                                                    as open_trades,
  min(t.opened_at)                                            as first_opened_at
from paper_trades t
where t.closed_at is null
  and t.city_key is not null
group by t.account_id, t.city_key, t.resolution_date;

comment on view v_city_day_exposure is
  'Open gross per desk, city and resolution date - one weather event per row, which is the grain the per-city-day cap is about.';


-- --------------------------------------------------------------------------
-- What a human reads when a position was cut.
-- --------------------------------------------------------------------------
create or replace view v_risk_budget_health as
select
  s.account_id,
  s.name,
  s.equity,
  s.high_water,
  s.drawdown_fraction,
  s.spent_today_usd,
  s.open_gross_usd,
  (select count(*) from v_city_day_exposure e where e.account_id = s.account_id)
                                                              as city_days_open,
  case
    when s.entries_paused                       then 'paused: the desk is not entering'
    when s.drawdown_fraction >= 0.30            then 'AT THE FLOOR: no new risk until equity recovers'
    when s.drawdown_fraction >= 0.20            then 'deep drawdown: risk scaled well under half'
    when s.drawdown_fraction >= 0.05            then 'drawdown: risk scaled down'
    when s.closed_trades = 0                    then 'no settled trades yet: base sizing'
    else                                             'at or near the high-water mark: base sizing'
  end                                                          as verdict
from v_desk_risk_state s;

comment on view v_risk_budget_health is
  'One line per desk saying which of the three limits is doing the work today.';
