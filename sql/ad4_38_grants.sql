-- ===========================================================================
-- ad4_38_grants.sql - make the WRITE path work, and prove it did.
--
-- THE SYMPTOM THIS FILE EXISTS FOR
-- --------------------------------
--   n8n reads everything fine, then every upsert comes back
--       permission denied for table markets (42501)
--   and the run reports success having written nothing.
--
-- 42501 on a write has exactly three causes, and this file settles all three
-- rather than guessing between them:
--
--   1. THE KEY IS THE ANON KEY. Nothing SQL can do about that - the fix is in
--      the n8n Config node. Section 5 prints what anon can write so you can
--      tell this case apart from the other two in one look, and the "Run now?"
--      node in every workflow now reads the role out of the key and stops
--      before the first request if it is not service_role.
--
--   2. service_role WAS NEVER GRANTED THE TABLE. This is the common one on a
--      database built by running a SUBSET of the sql/ files: the grants live
--      in ad4_13_reconcile.sql, so a database that jumped from ad4_00 to
--      ad4_30 has tables no role can write. Section 2 fixes it, and section 3
--      makes it stick for tables created later.
--
--   3. ROW LEVEL SECURITY. sql/ad4_rls.sql turns RLS on for markets, bands and
--      28 other tables with a SELECT-only policy. On Supabase service_role is
--      BYPASSRLS so it never notices; on any other Postgres a service_role
--      created without that attribute is refused every INSERT with the same
--      SQLSTATE. Section 4 asserts the attribute.
--
-- SAFE TO RUN AT ANY TIME, as many times as you like. It grants to
-- service_role, re-asserts SELECT-only for anon/authenticated, and changes no
-- data. It does not widen the browser's access by one privilege.
--
-- RUN ORDER: after ad4_00_preflight.sql. Anywhere after that is fine - run it
-- last, and run it again any time a write starts failing.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. The roles must exist. On Supabase they already do; outside it they may
--    not, and every grant below would fail on the first missing one.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
    raise notice 'ad4_38: created role anon (normal outside Supabase)';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
    raise notice 'ad4_38: created role service_role (normal outside Supabase)';
  end if;
exception when insufficient_privilege then
  raise notice 'ad4_38: cannot create roles (needs superuser). Skipping - on Supabase they already exist.';
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. service_role gets everything that exists right now.
--
--    This is the whole fix for cause 2. It is deliberately blanket: naming
--    tables here is how the last version of this got out of date, because
--    every new sql/ file adds tables and none of them remembered to grant.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    raise notice 'ad4_38: no service_role - nothing to grant';
    return;
  end if;
  execute 'grant usage on schema public to service_role';
  execute 'grant all on all tables in schema public to service_role';
  execute 'grant all on all sequences in schema public to service_role';
  execute 'grant execute on all functions in schema public to service_role';
  raise notice 'ad4_38: service_role granted on every table, sequence and function in public';
exception when insufficient_privilege then
  raise notice 'ad4_38: GRANT refused - you are connected as a role that does not own these tables. Run this in the Supabase SQL editor, which connects as postgres.';
end
$ad4$;


-- --------------------------------------------------------------------------
-- 3. And everything created from now on.
--
--    ALTER DEFAULT PRIVILEGES only applies to objects created by the role
--    that runs it, which is why this has to run as the same role that runs
--    the rest of sql/ - in the Supabase SQL editor, that is postgres.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    return;
  end if;
  execute 'alter default privileges in schema public grant all on tables to service_role';
  execute 'alter default privileges in schema public grant all on sequences to service_role';
  execute 'alter default privileges in schema public grant execute on functions to service_role';
  -- The browser side is SELECT-only by default too, so a table added by a
  -- future file is readable by the UI without being writable by it.
  execute 'alter default privileges in schema public grant select on tables to anon, authenticated';
  raise notice 'ad4_38: default privileges set - tables created later are writable by service_role and read-only to the browser';
exception when others then
  raise notice 'ad4_38: could not set default privileges (%). Not fatal: section 2 covers everything that exists today, and re-running this file covers anything added later.', sqlerrm;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. Row level security (cause 3).
--
--    sql/ad4_rls.sql enables RLS with a SELECT-only policy on the ingest
--    tables. A role without BYPASSRLS is then refused every INSERT with 42501
--    even holding a full GRANT - the grant and the policy are two different
--    gates and both have to open.
-- --------------------------------------------------------------------------
do $ad4$
declare v_bypass boolean;
begin
  select rolbypassrls into v_bypass from pg_roles where rolname = 'service_role';
  if v_bypass is null then
    return;
  elsif v_bypass then
    raise notice 'ad4_38: service_role is BYPASSRLS - row level security cannot refuse its writes';
    return;
  end if;
  begin
    execute 'alter role service_role bypassrls';
    raise notice 'ad4_38: gave service_role BYPASSRLS (it was missing - every RLS table would have refused its writes)';
  exception when insufficient_privilege then
    -- Cannot set the attribute, so open the gate the other way: a policy per
    -- RLS-enabled table that lets service_role do anything. Same effect,
    -- reachable without superuser.
    declare t record;
    begin
      for t in
        select c.relname
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relkind = 'r' and c.relrowsecurity
      loop
        execute format('drop policy if exists service_role_all on %I', t.relname);
        execute format(
          'create policy service_role_all on %I for all to service_role using (true) with check (true)',
          t.relname);
      end loop;
      raise notice 'ad4_38: no superuser, so added a service_role_all policy to every RLS table instead';
    end;
  end;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Re-assert the boundary. Section 2 granted service_role everything; this
--    makes sure that did not come with anything for the browser. anon and
--    authenticated read, and do not write, full stop.
--
--    This is the same rule as section 8 of ad4_13_reconcile.sql. Repeating it
--    here means a database repaired by this file alone still ends up with the
--    write boundary closed, not just the write path open.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if not exists (select 1 from pg_roles where rolname = r) then continue; end if;
    execute format(
      'revoke insert, update, delete, truncate, references, trigger on all tables in schema public from %I', r);
    execute format('revoke update on all sequences in schema public from %I', r);
    execute format('grant usage on schema public to %I', r);
    execute format('grant select on all tables in schema public to %I', r);
  end loop;
  execute 'revoke insert, update, delete, truncate on all tables in schema public from public';
end
$ad4$;


-- --------------------------------------------------------------------------
-- 6. Report. This is the part to read.
--
--    Every table an n8n workflow writes, and whether the service key can
--    actually write it. If a row says NO after running this file, the cause is
--    named in the row - not left for you to work out from a 42501.
-- --------------------------------------------------------------------------
create or replace view v_write_access as
with role_oid as (
  -- has_table_privilege(oid, ...) is strict: a missing role gives NULL rather
  -- than "role does not exist", so this view installs on a plain Postgres too.
  select (select oid from pg_roles where rolname = 'service_role') as svc,
         (select oid from pg_roles where rolname = 'anon')         as anon
),
t(tbl, written_by) as (
  values ('markets',                  'P0.2 market discovery, P0.5 rules refresh'),
         ('bands',                    'P0.2 market discovery'),
         ('book_snapshots',           'P0.3 book + volume snapshot'),
         ('trades_observed',          'P0.4 trade history'),
         ('weather_observations',     'P1.2 NWS monitor'),
         ('weather_forecasts',        'P1.3 NWS forecast, P1.5 open-meteo'),
         ('weather_forecast_features','P1.4 NWS gridpoint, P1.5 open-meteo'),
         ('live_weather',             'P1.2 NWS monitor, P1.5 open-meteo'),
         ('weather_events',           'P1.1 alerts, P1.2 NWS monitor'),
         ('cities',                   'P1.2/P1.3 save NWS grid ids'),
         ('ingest_log',               'every workflow, via log_ingest()')
)
select
  t.tbl                                                    as table_name,
  t.written_by,
  coalesce(has_table_privilege(o.svc,  c.oid, 'INSERT'), false) as service_can_insert,
  coalesce(has_table_privilege(o.svc,  c.oid, 'UPDATE'), false) as service_can_update,
  coalesce(has_table_privilege(o.anon, c.oid, 'INSERT'), false) as anon_can_insert,
  c.relrowsecurity                                         as rls_on,
  coalesce((select rolbypassrls from pg_roles where rolname = 'service_role'), false)
                                                           as service_bypasses_rls,
  case
    when o.svc is null
      then 'NO service_role ROLE at all. On Supabase it always exists; outside it, re-run section 1.'
    when not coalesce(has_table_privilege(o.svc, c.oid, 'INSERT'), false)
      then 'BROKEN: service_role has no INSERT. Re-run this file as the table owner (Supabase SQL editor).'
    when c.relrowsecurity
     and not coalesce((select rolbypassrls from pg_roles where rolname = 'service_role'), false)
     and not exists (select 1 from pg_policy p
                      where p.polrelid = c.oid and p.polname = 'service_role_all')
      then 'BROKEN: RLS is on, service_role is not BYPASSRLS and has no policy. Re-run section 4.'
    when coalesce(has_table_privilege(o.anon, c.oid, 'INSERT'), false)
      then 'OPEN: the browser key can write this table. Re-run section 5.'
    else 'ok - the service key can write this, the anon key cannot'
  end                                                      as verdict
  from t
  cross join role_oid o
  join pg_class c     on c.relname = t.tbl and c.relkind = 'r'
  join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public';

comment on view v_write_access is
  'One row per table an n8n workflow writes: can the service key insert it, can the browser key, is RLS in the way. Read this the moment a workflow reports permission denied (42501).';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_write_access to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare
  v_bad int;
  v_row record;
begin
  select count(*) into v_bad from v_write_access where verdict not like 'ok%';
  if v_bad = 0 then
    raise notice 'ad4_38: OK - the service key can write all % ingest table(s), and the browser key can write none of them.',
      (select count(*) from v_write_access);
  else
    raise warning 'ad4_38: % table(s) still wrong:', v_bad;
    for v_row in select table_name, verdict from v_write_access where verdict not like 'ok%' loop
      raise warning '  % -> %', v_row.table_name, v_row.verdict;
    end loop;
  end if;
end
$ad4$;

select table_name, written_by, service_can_insert, anon_can_insert, rls_on, verdict
  from v_write_access
 order by (verdict not like 'ok%') desc, table_name;
