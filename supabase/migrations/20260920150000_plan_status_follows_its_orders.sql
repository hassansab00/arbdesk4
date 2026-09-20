-- ===========================================================================
-- A PLAN THAT FILLED STILL READ "queued", AND THE DESK LOOKED DEAD.
--
-- Measured 20 Sep on the live desk:
--
--     paper_orders        77 rows    61 filled, 7 partial, 8 expired, 1 rejected
--     paper_trade_plans  360 rows     0 filled, 0 partial - 284 blocked,
--                                     72 queued, 4 expired
--
-- Every one of those 72 queued plans was past its expires_at, and 61 orders
-- had filled. The desk has been trading the whole time. The Settings tab
-- reads plan status, so what Hassan saw was a list of proposals that never
-- went anywhere - on a desk holding 5 open positions and 68 recorded trades.
--
-- WHY. The plan lifecycle in 20260912083728 has exactly one forward edge:
-- approve_paper_plan sets 'queued' and inserts the order. Nothing ever reads
-- the order back. 'queued' is a terminal state that means "an order exists",
-- and the word for that in every other part of this system is "waiting".
--
-- The expiry sweep does not help either: it only touches pending_approval, so
-- a queued plan whose order expired hours ago still says queued forever.
--
-- paper_orders.plan_id has pointed back at the plan since 20260912083728. The
-- edge was always available; nobody walked it.
--
-- WHAT THIS DOES. One function recomputes a plan's status from ALL of its
-- orders, and a trigger calls it whenever an order's status moves. Recomputing
-- from the full set rather than mapping one order is what makes a multi-leg
-- plan honest: s8 and s9 place two and three legs, and "filled" has to mean
-- every leg, not the first one to come back.
--
--   every order filled                    -> filled
--   any order filled or partial           -> partial   (some legs on, some not)
--   no fills, any rejected                -> rejected
--   no fills, any expired                 -> expired
--   no fills, all canceled                -> canceled
--   any order still live                  -> queued    (unchanged)
--
-- A plan that never reached an order at all - blocked, rejected at approval -
-- is not touched: it has no orders and nothing to recompute from.
-- ===========================================================================

-- 'filled', 'partial' and 'canceled' were never allowed values, which is the
-- other half of why nobody noticed: writing the truth here would have thrown.
alter table public.paper_trade_plans
  drop constraint if exists paper_trade_plans_status_check;

alter table public.paper_trade_plans
  add constraint paper_trade_plans_status_check
  check (status = any (array[
    'pending_approval','queued','blocked','rejected','expired',
    'filled','partial','canceled']));


create or replace function public.recompute_paper_plan_status(p_plan uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $fn$
declare
  n_total    integer;
  n_filled   integer;
  n_partial  integer;
  n_rejected integer;
  n_expired  integer;
  n_canceled integer;
  n_live     integer;
  v_status   text;
begin
  if p_plan is null then
    return null;
  end if;

  select count(*),
         count(*) filter (where status = 'filled'),
         count(*) filter (where status = 'partial'),
         count(*) filter (where status = 'rejected'),
         count(*) filter (where status = 'expired'),
         count(*) filter (where status = 'canceled'),
         count(*) filter (where status in ('queued','working'))
    into n_total, n_filled, n_partial, n_rejected, n_expired, n_canceled, n_live
    from public.paper_orders
   where plan_id = p_plan;

  -- No orders means the plan never got that far. blocked stays blocked.
  if n_total = 0 then
    return null;
  end if;

  -- A leg still working is the one case where "queued" is the truth.
  if n_live > 0 then
    v_status := 'queued';
  elsif n_filled = n_total then
    v_status := 'filled';
  elsif n_filled > 0 or n_partial > 0 then
    v_status := 'partial';
  elsif n_rejected > 0 then
    v_status := 'rejected';
  elsif n_expired > 0 then
    v_status := 'expired';
  else
    v_status := 'canceled';
  end if;

  -- Only ever moves a plan that HAS reached an order. A plan recorded as
  -- blocked was stopped before the venue saw it, and that reason is the
  -- record of why - it is not ours to overwrite from an order that a later
  -- retry happened to create.
  update public.paper_trade_plans
     set status = v_status
   where plan_id = p_plan
     and status in ('queued','pending_approval','filled','partial','expired','canceled')
     and status is distinct from v_status;

  return v_status;
end;
$fn$;

comment on function public.recompute_paper_plan_status(uuid) is
  'A plan''s status derived from every order it produced, so a filled plan stops reading as queued and a multi-leg plan says "filled" only when every leg did.';


create or replace function public.paper_plan_status_from_order()
returns trigger
language plpgsql
security definer
set search_path = ''
as $fn$
begin
  if new.plan_id is not null
     and (tg_op = 'INSERT' or coalesce(old.status,'') is distinct from new.status) then
    perform public.recompute_paper_plan_status(new.plan_id);
  end if;
  return null;
end;
$fn$;

drop trigger if exists paper_orders_sync_plan_status on public.paper_orders;
create trigger paper_orders_sync_plan_status
  after insert or update of status on public.paper_orders
  for each row execute function public.paper_plan_status_from_order();


-- THE 72 ALREADY ON THE SCREEN. Without this the fix only applies to orders
-- that move from now on, and the desk keeps showing the same dead list.
do $$
declare
  r record;
begin
  for r in select distinct plan_id from public.paper_orders where plan_id is not null
  loop
    perform public.recompute_paper_plan_status(r.plan_id);
  end loop;
end $$;

revoke all on function public.recompute_paper_plan_status(uuid) from public;
grant execute on function public.recompute_paper_plan_status(uuid) to service_role;
