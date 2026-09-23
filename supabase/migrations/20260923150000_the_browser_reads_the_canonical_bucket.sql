-- ===========================================================================
-- THE BROWSER READS THE CANONICAL BUCKET (plan v2 P2.5, Hassan's decision
-- of 23 Sep: option (a)).
--
-- v_canonical_bands and v_canonical_markets apply the append-only corrections
-- in proprietary_data_corrections to the raw bands and markets. They were
-- security_invoker, and security_invoker checks the base tables as the role
-- running the query even when the view is reached through an owner-rights
-- view. So no view the browser reads (anon) could be built on them: on 23 Sep,
-- 16:23:22-16:26:05Z, three such views were, and every anon read of them
-- failed with "permission denied for table proprietary_data_corrections".
--
-- Now they run with their owner's rights, like the views the browser reads.
-- WHAT DOES NOT CHANGE: anon and authenticated still have no grant on either
-- view, nor on proprietary_data_corrections, so neither can select them
-- directly. What anon gains is exactly the corrected band_lo / band_hi /
-- open_low / open_high / unit that the owner-rights views it already reads
-- expose - the same columns it reads from raw bands and markets today.
-- tests/database/paper-contracts.cjs asserts both halves.
-- ===========================================================================

alter view public.v_canonical_bands set (security_invoker = false);
alter view public.v_canonical_markets set (security_invoker = false);

revoke all on public.v_canonical_bands, public.v_canonical_markets from public, anon, authenticated;
grant select on public.v_canonical_bands, public.v_canonical_markets to service_role;

comment on view public.v_canonical_bands is
  'bands with the append-only corrections applied: the one bucket convention (half-open, corrected bounds). Owner rights since 23 Sep 2026 so browser-read views can be built on it; not granted to anon or authenticated.';
comment on view public.v_canonical_markets is
  'markets with the append-only corrections applied (the corrected unit). Owner rights since 23 Sep 2026 so browser-read views can be built on it; not granted to anon or authenticated.';
