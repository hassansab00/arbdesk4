-- ===========================================================================
-- paper_trades.regime_label: a column, an index of it in the ledger, a
-- write-once guard that names it - and nothing, anywhere, that ever wrote it.
--
--     69 trades          69 with forecast_version
--                         3 with calibration_version
--                         3 with cost_version
--                         0 with regime_label
--
-- The three versions are right. 20260919180000 added them, and the split at
-- three is correct: only trades written after signal_engine began freezing a
-- decision_snapshot carry calibration and cost, and the older ones recover the
-- forecast identity from decision_inputs, which is all that is recoverable.
-- Measured by day, every trade opened today has all three.
--
-- regime_label is a different thing: it was never in that migration's scope.
-- Its header says "the four fields that were always null" and the four are
-- forecast_version, calibration_version, cost_version and fill_quality. So the
-- column sat there, on a table with a guard listing it and a ledger writing it
-- at close, filled by nobody at entry - which means "which regime made money"
-- cannot be asked of paper_trades at all, and the close-link in the ledger
-- records a null it read from the trade.
--
-- IT WAS NEVER MISSING, ONLY UNCOPIED. signals.regime_label is populated -
-- 3,377 of 5,271 rows across BLOCKED, NORMAL, SHARP and UNCERTAIN - and every
-- one of the 69 trades has a signal_id, so all 69 are recoverable by a join
-- today.
--
-- WHICH IS EXACTLY WHY IT HAS TO BE STAMPED RATHER THAN JOINED. The value is
-- reachable now because signals still holds every row it ever wrote. signals
-- carries 14 MB of payload and is a candidate for the same archive that covers
-- observations, books and edges; the moment it is pruned, the regime behind a
-- settled trade becomes unrecoverable. Denormalising it at entry is what makes
-- pruning safe later, and it is the same argument the three versions already
-- won: the trade carries its own decision.
--
-- The snapshot is read through decision_snapshot() rather than a second join,
-- so the trigger keeps ONE source for what was believed. regime_label is
-- merged from the signals row beneath the snapshot, so if signal_engine ever
-- starts freezing it per band the snapshot wins and this needs no change.
-- ===========================================================================
begin;

-- --------------------------------------------------------------------------
-- 1. THE SNAPSHOT CARRIES THE REGIME.
--
-- `||` lets the right side win, and the per-band snapshot is authoritative -
-- it just has no regime_label key today, so the signal's own column survives.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.decision_snapshot(
  p_signal_id bigint, p_band_id uuid
) returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_strip_nulls(jsonb_build_object('regime_label', s.regime_label))
      || coalesce(
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
  'What the desk believed about one band at the moment a signal could fire: the shallow decision_snapshot contract, the forecast identity recovered from decision_inputs for signals written before it existed, and the signal''s own regime_label beneath both.';


-- --------------------------------------------------------------------------
-- 2. THE TRIGGER STAMPS IT, on the trade and on the fill link.
--
-- Unchanged from 20260919180000 except for the two regime_label additions.
-- The close link already wrote new.regime_label; it was reading a null.
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
    fill_quality, forecast_version, calibration_version, cost_version,
    regime_label)
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
    nullif(snap->>'cost_version', '')::uuid,
    nullif(snap->>'regime_label', ''))
  returning trade_id into new_trade;

  insert into public.ledger(
    trade_id, signal_id, event_type, stage, strategy_id, band_id, regime_label,
    forecast_version, calibration_version, cost_version, payload, detail)
  values (
    new_trade, new.signal_id, 'fill', 'fill', new.strategy_id, new.band_id,
    nullif(snap->>'regime_label', ''),
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
-- 3. AND IT IS WRITE-ONCE LIKE THE REST.
--
-- The guard already refuses a change to a lineage column that holds a value.
-- regime_label was missing from its list, so the one field nothing wrote was
-- also the one field anything could have rewritten.
-- --------------------------------------------------------------------------
create or replace function arbdesk_private.lineage_is_write_once()
returns trigger language plpgsql set search_path = '' as $$
declare
  col text;
begin
  foreach col in array array['forecast_version', 'calibration_version', 'cost_version',
                             'regime_label',
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


-- --------------------------------------------------------------------------
-- 4. THE 69 ALREADY ON THE BOOKS.
--
-- Recoverable today and only today, while signals still holds every row. The
-- guard permits this: it refuses a CHANGE to a value, not a first write.
-- --------------------------------------------------------------------------
update public.paper_trades t
   set regime_label = s.regime_label
  from public.signals s
 where s.signal_id = t.signal_id
   and t.regime_label is null
   and s.regime_label is not null;

update public.ledger l
   set regime_label = t.regime_label
  from public.paper_trades t
 where t.trade_id = l.trade_id
   and l.regime_label is null
   and t.regime_label is not null;

commit;
