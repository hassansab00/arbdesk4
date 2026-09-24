-- ===========================================================================
-- POSITIONS CARRY THEIR ENTRY (plan v2 P5.0 item 4)
--
-- A position said how many shares and what they cost, and nothing about why
-- the desk holds them. Judging a strategy on settled outcomes needs the entry
-- on the position itself: which strategy, which ledger, which order and plan
-- (the plan is the group linking the legs of one basket), and the probability
-- the desk believed when it bought.
--
-- Filled here, from what exists today:
--   strategy_id, ledger_id (the account: P5.1 makes each account a ledger),
--   entry_order_id, group_id (the plan), p_at_entry (the signal's
--   prob_at_fire), entered_at
-- Left NULL until the step that creates them, rather than guessed:
--   entry_decision_id  P5.11 (the decisions table)
--   p_cons_at_entry    P5.3  (the belief layer)
--   params_version     P5.5
--
-- Written by a trigger on the BUY order reaching filled/partial.
-- complete_paper_order writes the position before it updates the order's
-- status (checked in the live function on 24 Sep), so the trigger sees the
-- finished position. A position that is EXACTLY this fill is a new entry -
-- new, or bought again from flat - and takes this order's lineage; a fill that
-- adds to shares already held keeps the first entry's. Positions opened
-- before this migration keep NULLs: which order opened them is not recorded
-- unambiguously (14 keys were bought more than once), and a guess is worse.
-- ===========================================================================

alter table public.paper_positions
  add column if not exists strategy_id text,
  add column if not exists ledger_id uuid,
  add column if not exists entry_order_id uuid,
  add column if not exists entry_decision_id uuid,
  add column if not exists p_at_entry numeric,
  add column if not exists p_cons_at_entry numeric,
  add column if not exists group_id uuid,
  add column if not exists params_version text,
  add column if not exists entered_at timestamptz;

comment on column public.paper_positions.entry_decision_id is
  'The decision that opened this position. NULL until plan v2 P5.11 creates the decisions table.';
comment on column public.paper_positions.p_cons_at_entry is
  'The conservative probability at entry. NULL until plan v2 P5.3 (belief layer).';
comment on column public.paper_positions.params_version is
  'The strategy parameter version at entry. NULL until plan v2 P5.5.';

create or replace function arbdesk_private.position_records_its_entry()
returns trigger language plpgsql security definer set search_path = '' as $$
declare filled numeric;
begin
  if new.action <> 'BUY' or new.status not in ('filled', 'partial')
     or old.status is not distinct from new.status then
    return new;
  end if;
  filled := coalesce((new.result->>'shares')::numeric, 0);
  if filled <= 0 then
    return new;
  end if;
  update public.paper_positions p
     set strategy_id = new.strategy_id,
         ledger_id = new.account_id,
         entry_order_id = new.order_id,
         group_id = new.plan_id,
         p_at_entry = (select s.prob_at_fire from public.signals s where s.signal_id = new.signal_id),
         entered_at = now(),
         entry_decision_id = null, p_cons_at_entry = null, params_version = null
   where p.account_id = new.account_id and p.band_id = new.band_id and p.side = new.side
     and p.shares = filled;          -- the position is exactly this fill: a new entry
  return new;
end $$;

revoke all on function arbdesk_private.position_records_its_entry() from public;

drop trigger if exists paper_position_records_entry on public.paper_orders;
create trigger paper_position_records_entry
  after update of status on public.paper_orders
  for each row execute function arbdesk_private.position_records_its_entry();
