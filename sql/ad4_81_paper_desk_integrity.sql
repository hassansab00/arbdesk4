-- ===========================================================================
-- ad4_81_paper_desk_integrity.sql - DO THE DESK'S BOOKS BALANCE, RIGHT NOW?
--
-- Read-only detector plus one RPC. Safe to run any time. Re-runnable.
--
-- WHY THIS EXISTS
-- ---------------
-- tests/database/paper-contracts.cjs proves four money identities hold after
-- every operation the paper engine performs - submit, claim, complete, exit,
-- settle, reset, archive. It proves them in PGlite, against a database built
-- from scratch thirty seconds earlier, on desks the test itself created.
--
-- Production is not that database. It has desks nobody wrote a contract for,
-- created by a person through the UI, running strategies that did not exist
-- when the contract was written. The identities are properties of the ENGINE,
-- so they must hold for a desk made tomorrow by somebody who never read the
-- test - and nothing was checking that.
--
-- THE IDENTITIES
-- --------------
--   1. cash = sum of the activity ledger's cash_delta
--      Money moved with no event behind it means the ledger stopped being the
--      record of what happened and became a commentary on it.
--
--   2. reserved_cash = sum of the cash ceilings of orders still live
--      Reserved cash that no live order claims is buying power the desk
--      cannot spend and will never get back.
--
--   3. cash = starting_cash - open cost basis + realised P&L + re-basings
--             - cost basis a reset wrote off
--      The cross-store one, and the only one that can catch a trade recorded
--      without the money moving: it makes paper_accounts, paper_positions,
--      paper_trades and paper_activity agree, and they are four separate
--      tables written by four different code paths.
--
--   4. Nothing is negative, and the desk has not committed more than it holds.
--
--   5. (When it can be checked) the realised P&L on the positions equals the
--      realised P&L on the trades. Two independent stores; the bridge between
--      them is code that can drop a row.
--
-- WHAT WOULD HAVE MADE THIS VIEW LIE, AND WHAT WAS DONE ABOUT IT
-- --------------------------------------------------------------
-- Identity 3 as the harness states it - cash = starting - basis + realised -
-- is only true of a database that never loses a row. Production loses rows on
-- purpose, twice:
--
--   A RESET clears paper_positions. The open cost basis it deletes vanishes
--   from the left of the identity and from nowhere on the right, so from the
--   moment a desk with open positions is reset, identity 3 is off by exactly
--   that basis - permanently. paper_desk_reset now records the number it
--   wrote off, and this view subtracts it. A reset recorded BEFORE that change
--   that actually cleared positions cannot be reconstructed, so the view says
--   so - see `unverifiable` - rather than reporting a breach it cannot prove.
--
--   THE DAILY EXPORT deletes closed trades thirty days after they close, once
--   they are committed to web/public/paper-trades in the repository. Their
--   net_pnl is realised P&L that has already been spent into cash, so deleting
--   them moves the right of identity 3 and not the left.
--   prune_exported_paper_trades now writes a `trades_archived` activity row
--   carrying the realised total it removed, and this view adds it back.
--
-- Both fixes put the number in paper_activity, which is append-only - the
-- immutable_record() trigger refuses UPDATE, DELETE and TRUNCATE - so the
-- correction cannot be lost the way the rows it accounts for were.
--
-- A view that is arithmetically guaranteed to go red thirty days from now is
-- worse than no view: it teaches the person reading it to ignore it.
--
-- RUN ORDER: after ad4_00_preflight.sql (paper_trades, anomalies) and the
-- supabase/ migrations that create the paper engine. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- ONE ROW PER DESK. Every number the identities need, then the identities.
--
-- TOLERANCE IS 0.000001 - one ten-thousandth of a cent. Every column here is
-- numeric, so the arithmetic is exact and equality would almost always work;
-- the tolerance is for the last digit of a fee split, not for slop. It is the
-- same figure the contract harness uses, on purpose: the two must not be able
-- to disagree about what "balanced" means.
-- --------------------------------------------------------------------------
drop view if exists v_paper_desk_integrity cascade;

create view v_paper_desk_integrity as
with per_desk as (
  select
    a.account_id,
    a.name,
    a.archived_at is not null                                     as archived,
    a.created_at,
    a.starting_cash,
    a.cash,
    a.reserved_cash,

    -- 1. what the ledger says the cash should be
    coalesce((select sum(v.cash_delta) from paper_activity v
               where v.account_id = a.account_id), 0)             as ledger_cash,

    -- 2. what live orders have claimed. 'queued' and 'working' are the only
    --    two non-terminal statuses paper_orders_status_check allows; filled,
    --    partial, rejected, expired and canceled have all released.
    coalesce((select sum(o.cash_ceiling) from paper_orders o
               where o.account_id = a.account_id
                 and o.status in ('queued', 'working')), 0)        as live_ceilings,

    -- 3. the three stores identity 3 makes agree
    coalesce((select sum(p.cost_basis) from paper_positions p
               where p.account_id = a.account_id), 0)             as open_basis,
    coalesce((select sum(t.net_pnl) from paper_trades t
               where t.account_id = a.account_id
                 and t.closed_at is not null), 0)                 as realized_on_hand,
    coalesce((select sum((v.payload->>'realized_pnl')::numeric)
                from paper_activity v
               where v.account_id = a.account_id
                 and v.event_type = 'trades_archived'
                 and v.payload ? 'realized_pnl'), 0)              as realized_archived,
    coalesce((select sum(v.cash_delta) from paper_activity v
               where v.account_id = a.account_id
                 and v.event_type = 'account_reset'), 0)          as rebased,
    coalesce((select sum((v.payload->>'basis_written_off')::numeric)
                from paper_activity v
               where v.account_id = a.account_id
                 and v.event_type = 'account_reset'
                 and v.payload ? 'basis_written_off'), 0)         as basis_written_off,

    -- 5. the same realised P&L from the other side
    coalesce((select sum(p.realized_pnl) from paper_positions p
               where p.account_id = a.account_id), 0)             as positions_realized,

    -- 4. the thinnest position. paper_positions_shares_check already forbids
    --    a negative, so this is a second pair of eyes on a constraint rather
    --    than the only thing standing there - which is the point of a
    --    cross-check.
    coalesce((select min(p.shares) from paper_positions p
               where p.account_id = a.account_id), 0)             as thinnest_position,
    (select count(*) from paper_positions p
      where p.account_id = a.account_id)                          as open_position_rows,

    -- What this desk's history stops us from checking.
    (select count(*) from paper_activity v
      where v.account_id = a.account_id
        and v.event_type = 'account_reset'
        and not (v.payload ? 'basis_written_off')
        and coalesce((v.payload->>'positions_cleared')::numeric, 1) > 0)
                                                                  as blind_resets,
    (select count(*) from paper_activity v
      where v.account_id = a.account_id
        and v.event_type = 'account_reset'
        and coalesce((v.payload->>'positions_cleared')::numeric, 1) > 0)
                                                                  as clearing_resets,
    (select count(*) from paper_activity v
      where v.account_id = a.account_id
        and v.event_type = 'trades_archived')                     as archive_events
  from paper_accounts a
),
derived as (
  select d.*,
         d.realized_on_hand + d.realized_archived                 as realized,
         d.cash - d.reserved_cash                                 as available_cash,
         d.starting_cash - d.open_basis
           + (d.realized_on_hand + d.realized_archived)
           + d.rebased - d.basis_written_off                      as expected_cash
  from per_desk d
),
judged as (
  select d.*,
    abs(d.cash - d.ledger_cash)            <= 0.000001            as cash_matches_ledger,
    abs(d.reserved_cash - d.live_ceilings) <= 0.000001            as reserved_matches_live_orders,
    -- NULL, NOT FALSE, WHEN IT CANNOT BE ANSWERED. A desk reset before
    -- paper_desk_reset recorded the basis it wrote off is missing a term of
    -- this identity that is not recoverable from anything. Calling that a
    -- breach would put the desk permanently in the red for a gap in the
    -- record, and a detector that is always red is a detector nobody reads.
    case when d.blind_resets > 0 then null
         else abs(d.cash - d.expected_cash) <= 0.000001 end          as cash_matches_expected,
    d.cash          >= 0                                          as cash_not_negative,
    d.reserved_cash >= 0                                          as reserved_not_negative,
    d.available_cash >= -0.000001                                 as within_its_means,
    d.thinnest_position >= 0                                      as no_negative_position,
    -- Only answerable on a desk that still holds every position and every
    -- trade it ever had. A reset deletes the positions; the export deletes
    -- the trades. Either one makes the two sides count different things, and
    -- reporting that as a breach would be a lie.
    case when d.clearing_resets = 0 and d.archive_events = 0
         then abs(d.realized - d.positions_realized) <= 0.000001
    end                                                           as realized_matches_positions
  from derived d
)
select
  j.account_id, j.name, j.archived, j.created_at,

  -- the money, as four tables hold it
  j.starting_cash, j.cash, j.reserved_cash, j.available_cash,
  j.ledger_cash, j.live_ceilings, j.open_basis,
  j.realized, j.realized_on_hand, j.realized_archived,
  j.rebased, j.basis_written_off, j.positions_realized,
  j.expected_cash,
  round(j.cash - j.expected_cash, 8)                              as cash_gap,
  j.open_position_rows, j.thinnest_position,

  -- the verdicts, one column each, so a consumer can ask about one
  j.cash_matches_ledger, j.reserved_matches_live_orders,
  j.cash_matches_expected, j.cash_not_negative, j.reserved_not_negative,
  j.within_its_means, j.no_negative_position, j.realized_matches_positions,

  -- ...and all of them at once, named, for anything that just wants to alarm
  array_remove(array[
    case when not j.cash_matches_ledger          then 'cash_vs_ledger'          end,
    case when not j.reserved_matches_live_orders then 'reserved_vs_live_orders' end,
    case when j.cash_matches_expected is false   then 'cash_vs_expected'        end,
    case when not j.cash_not_negative            then 'negative_cash'           end,
    case when not j.reserved_not_negative        then 'negative_reserved'       end,
    case when not j.within_its_means             then 'overcommitted'           end,
    case when not j.no_negative_position         then 'negative_position'       end,
    case when j.realized_matches_positions is false then 'realized_vs_positions' end
  ], null)                                                        as breaches,

  array_remove(array[
    case when j.cash_matches_expected      is null then 'cash_vs_expected'      end,
    case when j.realized_matches_positions is null then 'realized_vs_positions' end
  ], null)                                                        as unverifiable,

  (array_length(array_remove(array[
    case when not j.cash_matches_ledger          then 'x' end,
    case when not j.reserved_matches_live_orders then 'x' end,
    case when j.cash_matches_expected is false   then 'x' end,
    case when not j.cash_not_negative            then 'x' end,
    case when not j.reserved_not_negative        then 'x' end,
    case when not j.within_its_means             then 'x' end,
    case when not j.no_negative_position         then 'x' end,
    case when j.realized_matches_positions is false then 'x' end
  ], null), 1) is null)                                           as ok,

  case
    when not j.cash_matches_ledger then
      format('Cash is %s but the activity log adds up to %s. Money moved without an event behind it - '
             'find the write that skipped paper_activity.', j.cash, j.ledger_cash)
    when j.cash_matches_expected is null then
      format('Cash is %s. Whether that is right cannot be answered: this desk was reset %s time(s) before '
             'resets recorded the cost basis they wrote off, and that number is not recoverable from '
             'anything. Not a breach - a gap in the record. Every other identity holds.',
             j.cash, j.blind_resets)
    when j.cash_matches_expected is false then
      format('Cash is %s but starting cash (%s) less open basis (%s) plus realised (%s) plus re-basings (%s) '
             'less written-off basis (%s) comes to %s. Four tables disagree by %s.',
             j.cash, j.starting_cash, j.open_basis, j.realized, j.rebased, j.basis_written_off,
             j.expected_cash, round(j.cash - j.expected_cash, 6))
    when not j.reserved_matches_live_orders then
      format('Reserved cash is %s but live orders only claim %s. The difference is buying power the desk '
             'cannot spend and no order will release.', j.reserved_cash, j.live_ceilings)
    when not j.within_its_means then
      format('Reserved %s against %s of cash - the desk has committed more than it holds.',
             j.reserved_cash, j.cash)
    when not j.cash_not_negative      then format('Cash is negative: %s.', j.cash)
    when not j.reserved_not_negative  then format('Reserved cash is negative: %s.', j.reserved_cash)
    when not j.no_negative_position   then format('A position holds %s shares.', j.thinnest_position)
    when j.realized_matches_positions is false then
      format('The trades realised %s and the positions realised %s. The bridge between them has dropped '
             'or double-counted a close.', j.realized, j.positions_realized)
    when j.realized_matches_positions is null then
      'Balanced. Trades against positions cannot be compared on this desk - a reset cleared its positions '
      'or the export has archived some of its trades, so the two sides no longer count the same things.'
    else
      format('Balanced. %s cash, %s reserved against live orders, %s of open cost basis, %s realised.',
             j.cash, j.reserved_cash, j.open_basis, j.realized)
  end                                                             as note
from judged j
order by j.created_at;

comment on view v_paper_desk_integrity is
  'One row per paper desk answering whether its books balance: cash against the activity ledger, reserved cash against live order ceilings, and cash against starting cash less open basis plus realised P&L - across paper_accounts, paper_activity, paper_orders, paper_positions and paper_trades. breaches names what failed, unverifiable names what this desk''s history makes uncheckable, note says it in a sentence.';


-- --------------------------------------------------------------------------
-- THE THING THAT RUNS IT. A view nobody selects from enforces nothing.
--
-- Returns a verdict and records one anomaly per desk per distinct breach set
-- per day. A breach that persists is one alarm, not one an hour - and the run
-- that finds it reports 'error' to ingest_log, which is what the health
-- watchdog actually reads. (It also reads `anomalies`, but implausible_edge
-- writes thousands of rows there; a books breach would be invisible in that
-- stream, so the anomaly is the durable record and ingest_log is the alarm.)
-- --------------------------------------------------------------------------
create or replace function public.check_paper_desk_integrity(p_record boolean default true)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $ad4$
declare
  v_desks int; v_breached int; v_unverifiable int; v_recorded int := 0;
  r record; v_key text;
begin
  select count(*),
         count(*) filter (where not ok),
         count(*) filter (where coalesce(array_length(unverifiable, 1), 0) > 0)
    into v_desks, v_breached, v_unverifiable
    from v_paper_desk_integrity;

  if p_record then
    for r in select * from v_paper_desk_integrity where not ok loop
      v_key := array_to_string(r.breaches, ',');
      if not exists (select 1 from anomalies an
                      where an.kind = 'paper_books_unbalanced'
                        and an.detail->>'account_id' = r.account_id::text
                        and an.detail->>'breaches'   = v_key
                        and an.detected_at > now() - interval '24 hours') then
        insert into anomalies (kind, severity, detail, value)
        values ('paper_books_unbalanced', 'high',
                jsonb_build_object('account_id', r.account_id, 'desk', r.name,
                                   'breaches', v_key, 'note', r.note,
                                   'cash', r.cash, 'expected_cash', r.expected_cash),
                r.cash_gap);
        v_recorded := v_recorded + 1;
      end if;
    end loop;
  end if;

  return jsonb_build_object(
    'ok', v_breached = 0,
    'desks', v_desks,
    'breached', v_breached,
    'unverifiable_desks', v_unverifiable,
    'anomalies_recorded', v_recorded,
    'detail', coalesce((select jsonb_agg(jsonb_build_object(
                          'desk', name, 'account_id', account_id,
                          'breaches', breaches, 'cash_gap', cash_gap, 'note', note))
                          from v_paper_desk_integrity where not ok), '[]'::jsonb));
end
$ad4$;

comment on function public.check_paper_desk_integrity(boolean) is
  'Checks every paper desk''s books and returns {ok, desks, breached, detail}. With p_record (the default) it also writes one paper_books_unbalanced anomaly per desk per distinct breach set per day. Called hourly by n8n P2.2 Paper Maintenance, which reports the run as an error when ok is false.';


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_paper_desk_integrity to %I', r);
    end if;
  end loop;
  -- Only the server side may WRITE an anomaly.
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function check_paper_desk_integrity(boolean) to service_role';
  end if;
  execute 'revoke all on function check_paper_desk_integrity(boolean) from public';
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on function check_paper_desk_integrity(boolean) from %I', r);
    end if;
  end loop;
end $ad4$;


do $ad4$
declare v_bad int; v_all int; v_blind int;
begin
  select count(*), count(*) filter (where not ok),
         count(*) filter (where 'cash_vs_expected' = any(unverifiable))
    into v_all, v_bad, v_blind from v_paper_desk_integrity;
  -- v_blind is not a fault. It is how many desks carry a reset from before the
  -- write-off was recorded, on which identity 3 is permanently unanswerable.
  raise notice 'ad4_81: % desk(s) checked, % out of balance, % with a reset predating the write-off record',
    v_all, v_bad, v_blind;
  if v_bad > 0 then
    raise warning 'ad4_81: % desk(s) do NOT balance - select breaches, note from v_paper_desk_integrity where not ok', v_bad;
  end if;
end $ad4$;
