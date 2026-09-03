-- ===========================================================================
-- ad4_15_pipeline_fixes.sql - unblock the failing GitHub Actions.
--
-- Four scheduled Actions had been failing for days. Three were bugs in the
-- Python (fixed in scripts/); this file is the one that needs a database
-- change.
--
--   Signal Engine:  insert or update on table "signals" violates foreign key
--                   constraint "signals_strategy_id_fkey"
--                   Key (strategy_id)=(system) is not present in "strategies".
--
-- scripts/signals.py raises system-wide alerts - a job failed, a job went
-- stale, an anomaly fired - that are deliberately NOT tied to a trading
-- strategy, and stamps them strategy_id = 'system'. There is no such row in
-- strategies, so the foreign key rejects the whole batch and the run dies
-- before any signal is written, including the real trading ones.
--
-- The fix is to make 'system' a real row rather than to weaken the key. It
-- IS the source of those signals, so naming it is honest; and a row works
-- whether or not signals.strategy_id happens to be nullable, which a NULL
-- would not.
--
-- WHY THIS FILE LOOKS DEFENSIVE
-- The first version of it hard-coded side = 'NONE' and was rejected:
--     new row for relation "strategies" violates check constraint
--     "strategies_side_check"
-- Phase 0 constrains that column and this repo never had its definition.
-- So nothing below asserts a value it has not proven the table will accept:
-- side and conflict_class are resolved at run time, preferring NULL (which
-- satisfies any `in (...)` check), then a value an existing row already
-- uses, then one read out of the constraint itself.
--
-- Run order: any time after sql/ad4_strategies_seed.sql. Re-runnable.
-- ===========================================================================

do $ad4$
declare
  v_side     text;
  v_class    text;
  v_nullable boolean;
  v_cols     text := '';
  v_vals     text := '';
  v_sets     text := '';
  v_sql      text;

  -- Constrained text columns are resolved below rather than asserted: NULL if
  -- the column allows it, else a value some existing row already carries,
  -- else the first literal in the check constraint's own definition.
begin
  if to_regclass('public.strategies') is null then
    raise exception 'strategies table does not exist - run sql/ad4_00_preflight.sql first';
  end if;

  -- ---- side ---------------------------------------------------------------
  select is_nullable = 'YES' into v_nullable
  from information_schema.columns
  where table_schema = 'public' and table_name = 'strategies' and column_name = 'side';

  if v_nullable then
    v_side := null;                       -- "not applicable" - the honest value
  else
    select side into v_side from strategies
     where side is not null group by side order by count(*) desc limit 1;

    if v_side is null then
      select (regexp_match(pg_get_constraintdef(c.oid), '''([A-Za-z_]+)'''))[1]
        into v_side
      from pg_constraint c
      where c.conrelid = 'public.strategies'::regclass and c.contype = 'c'
        and pg_get_constraintdef(c.oid) like '%side%'
      limit 1;
    end if;
  end if;

  -- ---- conflict_class -----------------------------------------------------
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'strategies'
                and column_name = 'conflict_class') then
    select is_nullable = 'YES' into v_nullable
    from information_schema.columns
    where table_schema = 'public' and table_name = 'strategies'
      and column_name = 'conflict_class';

    if v_nullable then
      v_class := null;
    else
      select conflict_class into v_class from strategies
       where conflict_class is not null
       group by conflict_class order by count(*) desc limit 1;
    end if;
  end if;

  raise notice 'resolved: side=%, conflict_class=%',
               coalesce(v_side, 'NULL'), coalesce(v_class, 'NULL');

  -- ---- build the insert from columns this table actually has ---------------
  -- Every column below is optional; only the ones present are written, so a
  -- database a few migrations behind still gets a usable row.
  declare
    r record;
    v_extra jsonb := jsonb_build_object(
      'note', 'Owner of the system-wide signals scripts/signals.py raises - '
              'job_failed, job_stale, anomaly. Not tradeable. Exists so '
              'signals.strategy_id has something to point at.',
      'origin', 'ad4_15_pipeline_fixes.sql');
  begin
    for r in
      select * from (values
        ('strategy_id',     quote_literal('system')),
        ('name',            quote_literal('System alerts (not a trading strategy)')),
        ('side',            coalesce(quote_literal(v_side), 'null')),
        ('origin',          quote_literal('system')),
        ('conflict_class',  coalesce(quote_literal(v_class), 'null')),
        ('enabled',         'false'),
        ('universe',        quote_literal('[]') || '::jsonb'),
        ('regime_filter',   quote_literal('[]') || '::jsonb'),
        ('capital_cap_pct', '0'),
        ('max_concurrent',  '0'),
        ('config',          quote_literal('{}') || '::jsonb'),
        ('extra',           quote_literal(v_extra::text) || '::jsonb')
      ) as t(col, val)
    loop
      if exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'strategies'
                    and column_name = r.col) then
        v_cols := v_cols || case when v_cols = '' then '' else ', ' end || quote_ident(r.col);
        v_vals := v_vals || case when v_vals = '' then '' else ', ' end || r.val;
        -- on re-run, re-assert the three that keep it untradeable
        if r.col in ('enabled', 'capital_cap_pct', 'max_concurrent', 'name', 'extra') then
          v_sets := v_sets || case when v_sets = '' then '' else ', ' end
                    || quote_ident(r.col) || ' = ' || r.val;
        end if;
      end if;
    end loop;

    v_sql := 'insert into strategies (' || v_cols || ') values (' || v_vals || ')'
             || ' on conflict (strategy_id) do update set ' || v_sets;
    execute v_sql;
  end;

  raise notice 'system strategy written';

exception
  when check_violation then
    raise exception E'could not write the system strategy: % \n'
      'The value chosen for a constrained column was still rejected. Send me:\n'
      '  select conname, pg_get_constraintdef(oid) from pg_constraint\n'
      '   where conrelid = ''public.strategies''::regclass and contype = ''c'';',
      sqlerrm;
end
$ad4$;


-- --------------------------------------------------------------------------
-- Confirm it landed, and that it cannot trade.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_total int;
  v_trade int;
  v_on    int;
  v_row   record;
begin
  select count(*) into v_total from strategies;
  select count(*) into v_trade from strategies where coalesce(origin, '') <> 'system';
  select count(*) into v_on    from strategies where enabled = true;

  raise notice 'strategies: % total (% tradeable, % system), % enabled',
               v_total, v_trade, v_total - v_trade, v_on;

  select * into v_row from strategies where strategy_id = 'system';
  if not found then
    raise warning 'the system strategy did not land - the Signal Engine will keep failing';
  else
    raise notice 'system row: side=%, enabled=%, capital_cap_pct=%, max_concurrent=%',
                 coalesce(v_row.side, 'NULL'), v_row.enabled,
                 v_row.capital_cap_pct, v_row.max_concurrent;
    if v_on > 0 then
      raise warning '% strategy(ies) are ENABLED. Deliberate?', v_on;
    else
      raise notice 'OK - the Signal Engine can now write its system alerts';
    end if;
  end if;
end
$ad4$;
