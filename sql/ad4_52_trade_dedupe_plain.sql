-- AD4 52 - make the trades_observed dedupe key reachable from PostgREST.
--
-- WHY THIS EXISTS
--
-- P0.4 inserts a trade tape that overlaps heavily with what it already wrote:
-- the endpoint serves the most recent N trades per market, so consecutive runs
-- re-send the same rows. That is fine and expected - the write is supposed to
-- be idempotent. It dedupes on
--
--     uq_trade_dedupe (condition_id, traded_at, price, size,
--                      coalesce(proxy_wallet, ''::text))
--
-- and the last column is an EXPRESSION. PostgREST's `on_conflict=` query
-- parameter can only name plain columns, so the workflow's
--
--     ?on_conflict=condition_id,traded_at,price,size,proxy_wallet
--
-- matched no constraint at all and every single insert failed with
--
--     42P10  there is no unique or exclusion constraint matching the
--            ON CONFLICT specification
--
-- Dropping the target does not help either: `Prefer: resolution=ignore-
-- duplicates` is only honoured when a target is named, so without it
-- PostgREST emits a plain INSERT and the overlap comes back as
--
--     23505  duplicate key value violates unique constraint "uq_trade_dedupe"
--
-- Both failure modes are the same root cause: the dedupe key cannot be named.
--
-- THE FIX
--
-- coalesce() was there to stop two rows with a NULL wallet counting as
-- distinct - NULLs are never equal to each other, so a plain unique index
-- would let duplicates through. The cheaper way to get that guarantee is to
-- not allow the NULL in the first place. Every one of the 127,585 existing
-- rows already has a proxy_wallet, so the column can simply be made NOT NULL
-- with an empty-string default, and the index rebuilt on plain columns. The
-- two indexes are then exactly equivalent, and the new one is targetable.
--
-- Idempotent: safe to run more than once.

begin;

-- 1. No NULLs today (verified: 0 of 127,585). Normalise any that appear
--    between now and the alter, so the NOT NULL cannot fail.
update public.trades_observed
   set proxy_wallet = ''
 where proxy_wallet is null;

alter table public.trades_observed
  alter column proxy_wallet set default '';

alter table public.trades_observed
  alter column proxy_wallet set not null;

-- 2. The same key, on plain columns, so `on_conflict=` can name it.
create unique index if not exists ad4_uq_trade_dedupe
  on public.trades_observed (condition_id, traded_at, price, size, proxy_wallet);

-- 3. The expression index is now redundant. Dropping it also reclaims the
--    space it held, which matters on a Nano instance (ad4_50 measured
--    trades_observed at 53 MB of heap against 121 MB of indexes).
drop index if exists public.uq_trade_dedupe;

commit;

-- What the workflow must send, now that the key is nameable:
--
--   POST /rest/v1/trades_observed
--        ?on_conflict=condition_id,traded_at,price,size,proxy_wallet
--   Prefer: resolution=ignore-duplicates,return=minimal
--
-- and it must never send a null proxy_wallet - P0.4's "Map trades to bands"
-- coalesces it to '' for exactly this reason.

select indexname, indexdef
  from pg_indexes
 where tablename = 'trades_observed'
   and indexname in ('ad4_uq_trade_dedupe', 'uq_trade_dedupe');
