-- Bounded headroom while the private, readable cold tier is completed.
--
-- Live measurement on 14 Nov 2026 found the original-side relations at
-- 15,925,248 / 16,777,216 bytes. Stopping at the next allocation would lose
-- new paper-research receipts. Deleting rows before verified read-through
-- would lose application access to retained evidence. Neither is acceptable.
--
-- This bridge adds 8 MiB only. It deliberately reports cold_tier_ready=false
-- so the worker stays at attention even below the ordinary 80% warning line.
-- It is not evidence offload and must not be presented as such.
create or replace function arbdesk_private.original_side_capacity() returns jsonb
language sql stable set search_path='' as $$
 with measured as (
  select coalesce(sum(pg_total_relation_size(c.oid)),0) bytes
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='arbdesk_private' and c.relkind='r' and c.relname like 'original_side_%'
 )
 select jsonb_build_object(
  'bytes',bytes,
  'limit_bytes',25165824,
  'capture_allowed',bytes<25165824,
  'temporary_bridge',true,
  'cold_tier_ready',false)
 from measured
$$;

revoke all on function arbdesk_private.original_side_capacity() from public,anon,authenticated;
grant execute on function arbdesk_private.original_side_capacity() to service_role;
comment on function arbdesk_private.original_side_capacity() is
 'Bounded 24 MiB continuity bridge. cold_tier_ready remains false until verified private read-through and recovery are deployed.';
