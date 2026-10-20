-- Read-only physical-storage diagnostics. No application rows or grants change.
-- PGlite's contract fixture does not provide this optional server extension.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pgstattuple') then
    create extension if not exists pgstattuple with schema extensions;
  end if;
end $$;
