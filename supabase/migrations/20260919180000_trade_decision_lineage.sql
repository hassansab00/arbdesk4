-- ===========================================================================
-- EVERY TRADE CARRIES THE DECISION THAT PRODUCED IT
--
-- WHAT WAS WRONG
-- --------------
-- Measured on the live desk, 2026-09-19:
--
--   65 fills
--    0 with forecast_version
--    0 with calibration_version
--    0 with cost_version
--    0 with fill_quality
--    0 rows in ledger
--
-- The columns have existed since the first paper migration. Nothing ever
-- wrote them, because record_paper_trade() builds a trade from the ORDER and
-- an order does not know what the desk believed when it decided.
--
-- So no trade can be traced to the model, calibration or cost assumptions
-- behind it, and every one of those is a moving target: band_probabilities is
-- rewritten by every pricing run, calibration is refitted from settled
-- outcomes, cost parameters change when the fee model is edited, and the book
-- moves by the second. By the time a trade settles, none of the rows that
-- produced it still say what they said. A post-mortem is then guesswork, and
-- measuring which forecast or calibration version made money is impossible.
--
-- HOW IT IS FIXED, AND WHY HERE RATHER THAN IN PYTHON
-- ---------------------------------------------------
-- scripts/signal_engine.py now freezes a decision snapshot at signal time -
-- raw probability, calibrated probability, the executable price per side, the
-- model centre, sigma, confidence and all four versions - at the shallow path
-- signals.payload->'decision_snapshot'->band_id.
--
-- Carrying it onward through plan -> order -> fill in Python would mean three
-- hops, each of which can silently drop a field, and the failure is invisible
-- until someone asks a question months later. The trade is created BY A
-- TRIGGER, from the order, in one statement. Reading the snapshot there makes
-- the stamp atomic with the insert and impossible to skip.
--
-- AND IT IS WRITE-ONCE. Rebanking, re-marking and the partial-close split all
-- UPDATE paper_trades. None of them may rewrite what was believed at entry: a
-- snapshot that can be edited afterwards is not evidence. The guard refuses a
-- change to a lineage column that already holds a value, and says which one.
-- ===========================================================================
begin;

-- --------------------------------------------------------------------------
-- 1. THE SNAPSHOT, WHEREVER IT IS.
--
-- Signals written before this migration carry no decision_snapshot, but they
-- do carry decision_inputs, and the forecast identity is inside it. Reading
-- both means the 65 trades already on the books recover what is recoverable
-- instead of staying blank forever.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.decision_snapshot(
  p_signal_id bigint, p_band_id uuid
) returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    -- the shallow contract: a fixed set of fields, written for this purpose
    s.payload -> 'decision_snapshot' -> p_band_id::text,
    -- ...and the older shape, where only the forecast identity survives
    jsonb_build_object(
      'forecast_version',
      s.payload -> 'decision_inputs' -> p_band_id::text
                -> 'decision_evidence' -> 'forecast' ->> 'forecast_version',
      'recovered_from', 'decision_inputs'),
    '{}'::jsonb)
  from public.signals s
  where s.signal_id = p_signal_id
$$;

comment on function arbdesk_private.decision_snapshot(bigint, uuid) is
  'What the desk believed about one band at the moment a signal could fire. Reads the shallow decision_snapshot contract, falling back to the forecast identity buried in decision_inputs for signals written before that contract existed.';


-- --------------------------------------------------------------------------
-- 2. THE TRIGGER STAMPS IT.
--
-- Everything else about this function is unchanged: the SELL branch still
-- delegates to close_paper_trades, the order_id uniqueness check still makes
-- a replayed completion a no-op, and the arithmetic is still the venue's.
-- What is added is the four fields that were always null.
--
-- fill_quality is the share of the request that filled - 1.0 for a complete
-- fill, 0.4 for a request that got two fifths of the way. It was computable
-- from the two numbers on the same row the whole time.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.record_paper_trade()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  q numeric; cost numeric; fee numeric;
  m record;
  automatic boolean;
  legs integer;
  snap jsonb;
  new_trade uuid;
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

  snap := arbdesk_private.decision_snapshot(new.signal_id, new.band_id);

  insert into public.paper_trades(
    account_id, order_id, signal_id, strategy_id, band_id, city_key,
    resolution_date, side, action, opened_at, shares, avg_fill_price,
    quoted_price, slippage_paid, fee_paid, partial_fill, requested_shares,
    legs_requested, approved_by_user,
    fill_quality, forecast_version, calibration_version, cost_version)
  values (
    new.account_id, new.order_id, new.signal_id, new.strategy_id, new.band_id,
    m.city_key, m.resolution_date, new.side, 'BUY', now(), q, cost / q,
    new.limit_price, cost - new.limit_price * q, fee, q < new.shares, new.shares,
    legs, not coalesce(automatic, false),
    case when new.shares > 0 then q / new.shares end,
    -- A malformed uuid in the payload must not take the fill down with it:
    -- the trade is the money and the version is the paperwork.
    nullif(snap->>'forecast_version', '')::uuid,
    nullif(snap->>'calibration_version', '')::uuid,
    nullif(snap->>'cost_version', '')::uuid)
  returning trade_id into new_trade;

  -- --------------------------------------------------------------------
  -- 3. AND THE LEDGER GETS ITS FIRST LINK.
  --
  -- ledger has existed since the first paper migration with a column for
  -- every part of the chain, and zero rows in it. An empty table that the
  -- schema advertises is worse than no table: it implies a record that does
  -- not exist. Each trade now writes 'fill' here and 'close' below, so the
  -- chain is signal -> order -> fill -> close with the decision attached to
  -- each link.
  -- --------------------------------------------------------------------
  insert into public.ledger(
    trade_id, signal_id, event_type, stage, strategy_id, band_id,
    forecast_version, calibration_version, cost_version, payload, detail)
  values (
    new_trade, new.signal_id, 'fill', 'fill', new.strategy_id, new.band_id,
    snap->>'forecast_version', snap->>'calibration_version', snap->>'cost_version',
    jsonb_build_object(
      'order_id', new.order_id, 'side', new.side,
      'requested_shares', new.shares, 'filled_shares', q,
      'limit_price', new.limit_price, 'avg_fill_price', cost / q,
      'notional', cost, 'fee', fee,
      'fill_quality', case when new.shares > 0 then q / new.shares end),
    snap);
  return null;
end $$;


-- --------------------------------------------------------------------------
-- 4. THE CLOSE IS A LINK TOO.
--
-- close_paper_trades is called by an auto-exit and by venue settlement, so
-- one insert here covers both ways a position can end.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.record_ledger_close()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.closed_at is null or old.closed_at is not null then
    return null;
  end if;
  insert into public.ledger(
    trade_id, signal_id, event_type, stage, strategy_id, band_id, regime_label,
    forecast_version, calibration_version, cost_version, payload)
  values (
    new.trade_id, new.signal_id, 'close', 'close', new.strategy_id, new.band_id,
    new.regime_label,
    new.forecast_version::text, new.calibration_version::text, new.cost_version::text,
    jsonb_build_object(
      'closed_at', new.closed_at, 'close_reason', new.close_reason,
      'shares', new.shares, 'entry_price', new.avg_fill_price,
      'close_price', new.close_price,
      'gross_pnl', new.gross_pnl, 'net_pnl', new.net_pnl));
  return null;
end $$;

drop trigger if exists paper_trade_records_close on public.paper_trades;
create trigger paper_trade_records_close
  after update of closed_at on public.paper_trades
  for each row execute function arbdesk_private.record_ledger_close();


-- --------------------------------------------------------------------------
-- 5. WRITE-ONCE.
--
-- A snapshot that can be edited afterwards is not evidence. Rebanking,
-- re-marking and the partial-close split all UPDATE paper_trades; none of
-- them may change what was believed at entry. Setting a null field is
-- allowed - that is the backfill below filling in what was never written -
-- and changing a value that is already there is refused, by name.
--
-- The close path is untouched: shares, fee_paid, slippage_paid, closed_at,
-- close_price and the P&L columns are not lineage and move freely.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.lineage_is_write_once()
returns trigger language plpgsql set search_path = '' as $$
declare
  col text;
begin
  foreach col in array array['forecast_version', 'calibration_version', 'cost_version',
                             'fill_quality', 'signal_id', 'avg_fill_price', 'quoted_price']
  loop
    if to_jsonb(old) -> col is not null
       and to_jsonb(old) -> col <> 'null'::jsonb
       and to_jsonb(new) -> col is distinct from to_jsonb(old) -> col then
      raise exception
        'paper_trades.% is the decision that produced this trade and cannot be rewritten (trade %, % -> %)',
        col, old.trade_id, to_jsonb(old) -> col, to_jsonb(new) -> col;
    end if;
  end loop;
  return new;
end $$;

drop trigger if exists paper_trade_lineage_write_once on public.paper_trades;
create trigger paper_trade_lineage_write_once
  before update on public.paper_trades
  for each row execute function arbdesk_private.lineage_is_write_once();


-- --------------------------------------------------------------------------
-- 6. WHAT THE 65 EXISTING FILLS CAN STILL RECOVER.
--
-- fill_quality is fully recoverable: both numbers are on the row. The
-- forecast identity is recoverable from the signal's decision_inputs. The
-- calibration and cost versions are NOT - nothing ever recorded them and
-- inventing today's values for a trade made a week ago would be a lie with a
-- uuid on it. They stay null, and the write-once guard lets a future
-- correction fill them if real evidence ever turns up.
-- --------------------------------------------------------------------------
update public.paper_trades t
   set fill_quality = case when t.requested_shares > 0
                           then t.shares / t.requested_shares end
 where t.fill_quality is null and t.requested_shares is not null;

update public.paper_trades t
   set forecast_version = v.fv
  from (
    select p.trade_id,
           nullif(arbdesk_private.decision_snapshot(p.signal_id, p.band_id)
                  ->> 'forecast_version', '')::uuid as fv
      from public.paper_trades p
     where p.forecast_version is null and p.signal_id is not null
  ) v
 where v.trade_id = t.trade_id and v.fv is not null;


-- --------------------------------------------------------------------------
-- 7. AND THE LEDGER GETS THE CHAIN IT MISSED - MARKED AS RECONSTRUCTED.
--
-- The triggers above only fire on new fills and new closes, so the table
-- would stay empty until the next trade, which on a desk that fills a few
-- times a week means days of a table that still implies a record it does not
-- have. These links are rebuilt from the trades themselves.
--
-- They are NOT the same thing as a link written as the money moved, and
-- saying so is the whole point: reconstructed = true, and the timestamp is
-- the trade's, not now(). Anything that treats them as contemporaneous
-- evidence can filter them out by that flag.
-- --------------------------------------------------------------------------
insert into public.ledger(
  recorded_at, trade_id, signal_id, event_type, stage, strategy_id, band_id,
  regime_label, forecast_version, calibration_version, cost_version, payload, detail)
select t.opened_at, t.trade_id, t.signal_id, 'fill', 'fill', t.strategy_id, t.band_id,
       t.regime_label, t.forecast_version::text, t.calibration_version::text,
       t.cost_version::text,
       jsonb_build_object(
         'order_id', t.order_id, 'side', t.side,
         'requested_shares', t.requested_shares, 'filled_shares', t.shares,
         'limit_price', t.quoted_price, 'avg_fill_price', t.avg_fill_price,
         'fee', t.fee_paid, 'fill_quality', t.fill_quality),
       jsonb_build_object('reconstructed', true,
                          'from', 'paper_trades after 20260919180000')
  from public.paper_trades t
 where not exists (select 1 from public.ledger l
                    where l.trade_id = t.trade_id and l.event_type = 'fill');

insert into public.ledger(
  recorded_at, trade_id, signal_id, event_type, stage, strategy_id, band_id,
  regime_label, forecast_version, calibration_version, cost_version, payload, detail)
select t.closed_at, t.trade_id, t.signal_id, 'close', 'close', t.strategy_id, t.band_id,
       t.regime_label, t.forecast_version::text, t.calibration_version::text,
       t.cost_version::text,
       jsonb_build_object(
         'closed_at', t.closed_at, 'close_reason', t.close_reason,
         'shares', t.shares, 'entry_price', t.avg_fill_price,
         'close_price', t.close_price, 'gross_pnl', t.gross_pnl, 'net_pnl', t.net_pnl),
       jsonb_build_object('reconstructed', true,
                          'from', 'paper_trades after 20260919180000')
  from public.paper_trades t
 where t.closed_at is not null
   and not exists (select 1 from public.ledger l
                    where l.trade_id = t.trade_id and l.event_type = 'close');

commit;
