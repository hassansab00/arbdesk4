-- ===========================================================================
-- A TRADE PRINT NEEDS NO CONFLICT TARGET (WXPredict build 2.A group C, fixes
-- 20261008170000).
--
-- THE FAULT, 8 Oct 16:36Z. The hourly trade ingest's first run through
-- insert_trade_prints() lost 4 of its 9 batches to 42P10 "there is no unique
-- or exclusion constraint matching the ON CONFLICT specification" (Postgres
-- log 16:36:18-23Z; ingest_log P0.4_trade_history, attention); the other 5
-- inserted 1,614 prints. The function ran in a fresh PostgREST connection
-- each time ("SQL function insert_trade_prints during startup").
--
-- THE CAUSE. ON CONFLICT (trade_dedupe_key(...)) is matched to the unique
-- index by comparing expressions after the planner has simplified both, and
-- the planner inlines a SQL function only for a role that may EXECUTE it.
-- Only postgres and service_role may execute trade_dedupe_key (live proacl).
-- A backend keeps an index's simplified expression from the first time it
-- plans the table: when that first plan was a browser read as anon, the
-- cached index expression is the un-inlined call, the service role's target
-- is inlined, they differ, and the insert is refused. Reproduced in
-- tests/database/trade-dedupe-hash.cjs: first plan as service_role, the
-- insert works; first plan as anon, 42P10.
--
-- THE FIX. No target: ON CONFLICT DO NOTHING checks every unique index of
-- the table, so nothing is inferred. A print is turned away by
-- ad4_uq_trade_dedupe_hash exactly as before (and by ad4_uq_trade_dedupe
-- while it stands, which turns away the same prints); trade_id comes from its
-- sequence and never conflicts. Same columns, same return. Re-runnable.
-- ===========================================================================

create or replace function public.insert_trade_prints(p_rows jsonb)
returns table (trade_id bigint)
language sql
set search_path = public
as $$
  insert into public.trades_observed as t
         (band_id, condition_id, city_key, token_id, side, price, size, traded_at, proxy_wallet, ingested_at)
  select r.band_id, r.condition_id, r.city_key, r.token_id, r.side, r.price, r.size, r.traded_at,
         coalesce(r.proxy_wallet, ''), coalesce(r.ingested_at, now())
    from jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb))
         as r(band_id uuid, condition_id text, city_key text, token_id text, side text,
              price numeric, size numeric, traded_at timestamptz, proxy_wallet text, ingested_at timestamptz)
  on conflict do nothing
  returning t.trade_id
$$;

comment on function public.insert_trade_prints(jsonb) is
  'The trade ingest''s insert: every print not already held, turned away by the unique dedupe key (ad4_uq_trade_dedupe_hash, trade_dedupe_key) with no conflict target, so no role''s inlining can break it; returns the trade_id of each one inserted (WXPredict build 2.A, 8 Oct).';

revoke all on function public.insert_trade_prints(jsonb) from public, anon, authenticated;
grant execute on function public.insert_trade_prints(jsonb) to service_role;
