-- ===========================================================================
-- THE AUTOMATIC DESK'S FILLS WERE INVISIBLE ON EVERY PAGE THAT SHOWS TRADES.
--
-- paper_trades is what web/app/page.tsx, analytics/page.tsx and goals/page.tsx
-- read. It is written by exactly one thing: log_paper_trade(), the
-- approve-by-hand RPC, whose every row is stamped approved_by_user = true.
--
-- The automatic desk does not go through that path. complete_paper_order()
-- writes paper_positions and paper_activity and stops. So on 16 Sep three real
-- fills - 64.9 shares at $0.21, 69.9 at $0.21, 159.47 at $0.05 - produced
-- three positions, zero trades, and a dashboard that said the desk had never
-- traded. A record nothing displays is not a record.
--
-- paper_trades also predates the multi-desk engine: it has no account_id and
-- no order_id, so there was no way to attribute a row to a desk or to tie it
-- back to the order that made it. Both are added here.
--
--
-- A TRADE IS OPENED ONCE AND CLOSED BY ONE OF TWO THINGS, and both now write:
--
--   complete_paper_order   a BUY fill opens a row; a SELL fill closes it
--   settle_paper_inventory a venue resolution closes it at the binary payout
--
-- Without the second, a position held to resolution - which is the whole
-- strategy on a daily temperature band - would never book a P&L, net_pnl
-- would stay null for ever, and the analytics page would stay empty even
-- after this migration. That is the case that matters most here.
--
--
-- CLOSING IS FIFO AND SPLITS. One position can be the sum of several entries
-- at different prices, and an exit can be smaller than the position. Closing
-- oldest-first, and splitting the row that straddles the boundary, is the only
-- way the per-trade prices stay true. arbdesk_private.close_paper_trades does
-- that in one place so the exit path and the settlement path cannot disagree.
--
-- COST CONVENTION, stated because gross and net are easy to get backwards:
--   shares * avg_fill_price   entry notional, fee excluded
--   fee_paid                  entry fee
--   gross_pnl                 proceeds - entry notional      (no fees at all)
--   net_pnl                   proceeds - exit fee - entry notional - entry fee
-- paper_positions.cost_basis includes the entry fee, so the split arithmetic
-- below divides notional and fee separately rather than scaling one number.
-- ===========================================================================
begin;

alter table public.paper_trades
  add column if not exists account_id uuid references public.paper_accounts(account_id),
  add column if not exists order_id uuid,
  add column if not exists city_key text,
  add column if not exists resolution_date date;

create index if not exists paper_trades_account_open
  on public.paper_trades(account_id, band_id, side) where closed_at is null;
create index if not exists paper_trades_opened
  on public.paper_trades(opened_at desc);
create unique index if not exists paper_trades_order_unique
  on public.paper_trades(order_id) where order_id is not null;

comment on column public.paper_trades.account_id is
  'Which paper desk. Null on rows written by log_paper_trade before the multi-desk engine existed.';
comment on column public.paper_trades.order_id is
  'The paper_orders row that opened this trade. Unique, so a replayed fill cannot double-record.';
comment on column public.paper_trades.city_key is
  'Denormalised from the band''s market so a closed trade stays readable after the band row is pruned.';


-- --------------------------------------------------------------------------
-- FIFO close, shared by the exit path and the settlement path.
--
-- Returns the number of rows it closed. p_proceeds is the total cash received
-- for p_shares, BEFORE p_fee. A settlement passes the binary payout and a zero
-- fee; an exit passes the sale notional and the venue fee.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.close_paper_trades(
  p_account uuid, p_band uuid, p_side text, p_shares numeric,
  p_proceeds numeric, p_fee numeric, p_reason text, p_when timestamptz
) returns integer
language plpgsql security definer set search_path = '' as $$
declare
  t public.paper_trades;
  remaining numeric := p_shares;
  price numeric;
  fee_rate numeric;
  take numeric;
  part_notional numeric;
  part_fee numeric;
  part_entry_fee numeric;
  closed integer := 0;
begin
  if p_shares is null or p_shares <= 0 then
    return 0;
  end if;
  price := p_proceeds / p_shares;          -- proceeds per share
  fee_rate := p_fee / p_shares;            -- exit fee per share

  for t in
    select * from public.paper_trades
     where account_id = p_account and band_id = p_band and side = p_side
       and closed_at is null and shares > 0
     order by opened_at, trade_id
     for update
  loop
    exit when remaining <= 0;
    take := least(remaining, t.shares);

    -- The row straddles the boundary: keep the untaken part open as its own
    -- row at the same entry price, and let the loop close `take` below.
    if take < t.shares then
      insert into public.paper_trades(
        account_id, order_id, signal_id, strategy_id, deployment_id, band_id,
        city_key, resolution_date, side, action, opened_at, shares,
        avg_fill_price, quoted_price, slippage_paid, fee_paid, gas_paid,
        partial_fill, requested_shares, cost_version, forecast_version,
        calibration_version, regime_label, max_slippage_setting, fill_quality,
        legs_requested, legs_filled, approved_by_user)
      select t.account_id, null, t.signal_id, t.strategy_id, t.deployment_id, t.band_id,
        t.city_key, t.resolution_date, t.side, t.action, t.opened_at, t.shares - take,
        t.avg_fill_price, t.quoted_price,
        t.slippage_paid * (t.shares - take) / t.shares,
        coalesce(t.fee_paid,0) * (t.shares - take) / t.shares, t.gas_paid,
        t.partial_fill, t.requested_shares, t.cost_version, t.forecast_version,
        t.calibration_version, t.regime_label, t.max_slippage_setting, t.fill_quality,
        t.legs_requested, t.legs_filled, t.approved_by_user;
      part_entry_fee := coalesce(t.fee_paid,0) * take / t.shares;
    else
      part_entry_fee := coalesce(t.fee_paid,0);
    end if;

    part_notional := price * take;
    part_fee := fee_rate * take;

    update public.paper_trades set
      shares = take,
      fee_paid = part_entry_fee,
      slippage_paid = case when t.shares = 0 then t.slippage_paid
                           else t.slippage_paid * take / t.shares end,
      closed_at = p_when,
      close_price = price,
      exit_price = price,
      close_reason = p_reason,
      gross_pnl = part_notional - t.avg_fill_price * take,
      net_pnl = part_notional - part_fee - t.avg_fill_price * take - part_entry_fee
    where trade_id = t.trade_id;

    remaining := remaining - take;
    closed := closed + 1;
  end loop;

  return closed;
end $$;
revoke all on function arbdesk_private.close_paper_trades(uuid,uuid,text,numeric,numeric,numeric,text,timestamptz)
  from public, anon, authenticated;

commit;


-- ===========================================================================
-- WIRED BY TRIGGERS, NOT BY EDITING THE TWO RPCs.
--
-- complete_paper_order() and settle_paper_inventory() carry the cash ceiling,
-- the lease check, the book-evidence freshness test and the fill arithmetic
-- validation. Rewriting either of them to add a bookkeeping insert means
-- re-deriving all of that correctly from memory, and the cost of getting it
-- subtly wrong is a desk that spends money it does not have. Neither is
-- touched. The triggers hang off what those functions already write:
--
--   paper_orders reaching a terminal filled/partial status
--   paper_position_settlements gaining a row
--
-- so the record follows the money instead of being maintained beside it.
-- ===========================================================================
begin;

create or replace function arbdesk_private.record_paper_trade()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  q numeric; cost numeric; fee numeric;
  m record;
  automatic boolean;
  legs integer;
begin
  if new.status not in ('filled','partial') or coalesce(old.status,'') = new.status then
    return null;
  end if;
  q    := (new.result->>'shares')::numeric;
  cost := (new.result->>'notional')::numeric;
  fee  := coalesce((new.result->>'fee')::numeric, 0);
  if q is null or q <= 0 then
    return null;                       -- nothing economic happened
  end if;

  if new.action = 'SELL' then
    perform arbdesk_private.close_paper_trades(
      new.account_id, new.band_id, new.side, q, cost, fee, 'auto_exit', now());
    return null;
  end if;

  -- A BUY opens a row. The unique index on order_id makes a replayed
  -- completion a no-op rather than a second trade.
  if exists (select 1 from public.paper_trades where order_id = new.order_id) then
    return null;
  end if;

  select mk.city_key, mk.resolution_date into m
    from public.bands b join public.markets mk on mk.market_id = b.market_id
   where b.band_id = new.band_id;

  select a.mode = 'automatic' into automatic
    from public.paper_accounts a where a.account_id = new.account_id;

  select jsonb_array_length(p.legs) into legs
    from public.paper_trade_plans p where p.plan_id = new.plan_id;

  insert into public.paper_trades(
    account_id, order_id, signal_id, strategy_id, band_id, city_key,
    resolution_date, side, action, opened_at, shares, avg_fill_price,
    quoted_price, slippage_paid, fee_paid, partial_fill, requested_shares,
    legs_requested, approved_by_user)
  values (
    new.account_id, new.order_id, new.signal_id, new.strategy_id, new.band_id,
    m.city_key, m.resolution_date, new.side, 'BUY', now(), q, cost / q,
    new.limit_price, cost - new.limit_price * q, fee, q < new.shares, new.shares,
    legs, not coalesce(automatic, false));
  return null;
end $$;

drop trigger if exists paper_order_records_trade on public.paper_orders;
create trigger paper_order_records_trade
  after update of status on public.paper_orders
  for each row execute function arbdesk_private.record_paper_trade();


-- A held position pays out at resolution and never sees a SELL order, so this
-- is the close that matters for a daily temperature band.
create or replace function arbdesk_private.record_paper_settlement()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  perform arbdesk_private.close_paper_trades(
    new.account_id, new.band_id, new.side, new.shares, new.payout, 0,
    case when new.payout > 0 then 'venue_resolution_won' else 'venue_resolution_lost' end,
    new.settled_at);
  return null;
end $$;

drop trigger if exists paper_settlement_records_trade on public.paper_position_settlements;
create trigger paper_settlement_records_trade
  after insert on public.paper_position_settlements
  for each row execute function arbdesk_private.record_paper_settlement();


-- --------------------------------------------------------------------------
-- BACKFILL. The fills that already happened predate the trigger, so they would
-- stay invisible - which is the whole complaint this migration answers.
-- Reconstructed from the orders themselves, so the numbers are the venue's.
-- --------------------------------------------------------------------------
insert into public.paper_trades(
  account_id, order_id, signal_id, strategy_id, band_id, city_key,
  resolution_date, side, action, opened_at, shares, avg_fill_price,
  quoted_price, slippage_paid, fee_paid, partial_fill, requested_shares,
  approved_by_user)
select o.account_id, o.order_id, o.signal_id, o.strategy_id, o.band_id,
       mk.city_key, mk.resolution_date, o.side, 'BUY', o.requested_at,
       (o.result->>'shares')::numeric,
       (o.result->>'notional')::numeric / (o.result->>'shares')::numeric,
       o.limit_price,
       (o.result->>'notional')::numeric - o.limit_price * (o.result->>'shares')::numeric,
       coalesce((o.result->>'fee')::numeric, 0),
       (o.result->>'shares')::numeric < o.shares, o.shares,
       coalesce(a.mode, 'automatic') <> 'automatic'
  from public.paper_orders o
  left join public.bands b on b.band_id = o.band_id
  left join public.markets mk on mk.market_id = b.market_id
  left join public.paper_accounts a on a.account_id = o.account_id
 where o.action = 'BUY'
   and o.status in ('filled','partial')
   and (o.result->>'shares')::numeric > 0
   and not exists (select 1 from public.paper_trades t where t.order_id = o.order_id)
on conflict do nothing;

commit;
