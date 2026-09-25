-- THE DECISION LOG (plan v2, step P5.11, part 1).
--
-- One row per (run, strategy, city-day), INCLUDING the runs where the answer
-- was to do nothing. signals holds only what fired; a strategy that looked at
-- a city-day and passed left no trace, so "why did s3 not buy Dallas today"
-- had no answer, and a strategy's record could not be told apart from its
-- coverage. Every row here is one strategy's verdict on one city-day at one
-- decision time:
--
--   action       BUY / SELL / HOLD / WAIT / NONE
--   reason_code  a short code (enter, exit, holding, already_decided,
--                no_cost_version, conflict, sized_to_zero, no_signal)
--   g_now/g_wait expected log-growth of acting now and of waiting. NULL
--                until the timing rule (P5.6) computes them; never guessed
--   binding      the constraints that bound the size (from the solver, P5.5,
--                once the live engine runs on it - P5.12)
--   target_usd   what the decision wants held on the city-day, at cost
--   held_usd     what the strategy's ledger holds there now, at cost
--   params_version the engine version the decision was made with
--   run_id       the signal_engine cycle; signals of the same cycle carry it
--                as payload->>'cycle_id', which is the join
--   tick_id / checkpoint_id  set when the hourly tick decides (P5.6)
--
-- COMPACT: numbers and short codes, no text blobs. Measured 25 Sep: 48
-- city-days on the board and 9 strategies, six intraday runs a day - about
-- 2,600 rows a day. 30 days stay live; archive_observations.py exports older
-- rows to a Release (dataset 'decisions') and only then prunes them, through
-- prune_decisions(), which requires the verified row count.
--
-- NOT YET: plan P5.11 also turns `signals` into a view over this table. signals
-- is a live table with writers, foreign keys (paper_trades, paper_orders) and
-- a board reading it; replacing it before the engine writes its decisions
-- natively (P5.12) would break what works. Part 2, with an EXCEPT ALL proof.
--
-- Append-only: an UPDATE is refused always; a DELETE only inside
-- prune_decisions(), which names this table in arbdesk.archiving for its own
-- transaction. Idempotent.

create table if not exists public.decisions (
  decision_id     bigint generated always as identity primary key,
  run_id          uuid        not null,
  decided_at      timestamptz not null,
  tick_id         uuid,
  checkpoint_id   uuid,
  strategy_id     text        not null references public.strategies(strategy_id),
  city_key        text        not null,
  resolution_date date        not null,
  action          text        not null check (action in ('BUY', 'SELL', 'HOLD', 'WAIT', 'NONE')),
  reason_code     text        not null check (reason_code ~ '^[a-z_]{1,40}$'),
  g_now           real,
  g_wait          real,
  binding         text[]      not null default '{}',
  target_usd      real,
  held_usd        real,
  n_signals       smallint    not null default 0 check (n_signals >= 0),
  params_version  text,
  constraint decisions_one_per_run unique (run_id, strategy_id, city_key, resolution_date)
);
create index if not exists decisions_strategy_time on public.decisions (strategy_id, decided_at desc);
create index if not exists decisions_decided_at on public.decisions (decided_at);

create or replace function arbdesk_private.decisions_are_append_only()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' and current_setting('arbdesk.archiving', true) = 'decisions' then
    return old;
  end if;
  raise exception 'decisions is append-only; rows leave only through prune_decisions() after the archive';
end $$;
drop trigger if exists decisions_append_only on public.decisions;
create trigger decisions_append_only before update or delete on public.decisions
  for each row execute function arbdesk_private.decisions_are_append_only();
drop trigger if exists decisions_no_truncate on public.decisions;
create trigger decisions_no_truncate before truncate on public.decisions
  for each statement execute function arbdesk_private.decisions_are_append_only();

alter table public.decisions enable row level security;
revoke all on public.decisions from public, anon, authenticated, service_role;
grant select, insert on public.decisions to service_role;

create or replace function public.prune_decisions(
  p_keep_days     integer     default 30,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_doomed bigint;
begin
  -- 14 DAYS IS THE FLOOR. The plan keeps 30 live; the floor is how far storage
  -- pressure may shorten that (archive_observations min_keep_days), so a
  -- mistyped window cannot strip the decisions a live page is still reading.
  if p_keep_days < 14 then
    return jsonb_build_object('ok', false, 'error', 'keep_days must be at least 14');
  end if;
  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count verified by re-downloading the uploaded archive');
  end if;
  if not p_dry_run then
    lock table public.decisions in share row exclusive mode;
  end if;
  -- A plain age predicate, which the export's REST filter says identically.
  select count(*) into v_doomed from public.decisions where decided_at < v_before;
  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s', p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;
  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0, 'note', format('no decisions older than %s', v_before));
  end if;
  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true, 'would_delete', v_doomed,
      'rows_now', (select count(*) from public.decisions), 'older_than', v_before,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;
  perform set_config('arbdesk.archiving', 'decisions', true);
  delete from public.decisions where decided_at < v_before;
  perform set_config('arbdesk.archiving', '', true);
  return jsonb_build_object('ok', true, 'deleted', v_doomed, 'older_than', v_before);
end $$;
revoke all on function public.prune_decisions(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_decisions(integer, boolean, timestamptz, bigint) to service_role;
