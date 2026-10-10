-- ===========================================================================
-- A TRADE IS KNOWN BY ITS TRANSACTION (R46 part 2; Hassan, 10 Oct: "yes fix
-- dedupe too").
--
-- THE FAULT, measured 10 Oct (tools/wxpredict/trade_history.py archive): the
-- dedupe key (trade_dedupe_key: condition_id, traded_at to the second, price,
-- size, proxy_wallet) merged trades that are not the same trade. For the 10
-- sampled events of 1-4 Oct, 119 of the 15,726 trades the API serves share
-- that key with another, in 53 groups, and in every group each trade has its
-- own transactionHash: a wallet repeating an identical order within one
-- second. 107 of them sat on keys we stored, so the table held one row where
-- the venue had two or three.
--
-- THE FIX. The API's transactionHash is stored (transaction_hash) and is part
-- of the key: ad4_uq_trade_tx_key is unique on trade_tx_key(), the same five
-- columns and the hash, for every row that has one. A row without one (the
-- rows stored before this, and any print the API sends without a hash) keeps
-- the old key, as a partial index (ad4_uq_trade_dedupe_untx), so nothing that
-- was turned away before goes in now. The full five-column hash index
-- (ad4_uq_trade_dedupe_hash) goes, because it would still merge them.
--
-- THE CHANGEOVER. The rows already held have no hash. The ingest re-sends the
-- overlap of every run (1 h) and the backfill re-reads each new market's
-- first day, so a print held without a hash would come back with one and,
-- under the new key alone, go in twice. insert_trade_prints() therefore skips
-- a print with a hash when a row WITHOUT a hash has its five columns; the
-- lookup reads ad4_ix_trades_untx_print, a partial index on plain columns
-- (condition_id, traded_at) over those rows only, so it does not depend on the
-- planner inlining a function (the fault 20261008180000 met). The cost: while
-- un-hashed rows remain, a second transaction sharing one of their five-column
-- keys is still merged, as before. trades_observed keeps about a day
-- (archive dataset `trades`), so within two nights every row held has a hash
-- and the guard reads an empty index.
--
-- ORDER. This can be applied before or after the ingest that sends the hash:
-- the old ingest sends no hash and is served by the partial old key exactly as
-- today. The archive's trades export names its columns, so its new
-- `transaction_hash` column waits until this is live. Re-runnable.
--
-- The DROP INDEX means the Supabase tool cannot apply this (it holds DROP for
-- a confirmation); it is run in the SQL editor.
-- ===========================================================================

alter table public.trades_observed add column if not exists transaction_hash text;

comment on column public.trades_observed.transaction_hash is
  'The venue''s transactionHash for this print (data API /trades), NULL for prints stored before 10 Oct (R46 part 2). Part of the dedupe key where present (trade_tx_key).';

create or replace function public.trade_tx_key(p_condition_id text, p_traded_at timestamptz,
                                               p_price numeric, p_size numeric, p_proxy_wallet text,
                                               p_transaction_hash text)
returns bytea
language sql
immutable
parallel safe
as $$
  select decode(md5(encode(public.trade_dedupe_key(p_condition_id, p_traded_at, p_price, p_size, p_proxy_wallet), 'hex')
                    || '|' || octet_length(p_transaction_hash)::text || ':' || p_transaction_hash), 'hex')
$$;

comment on function public.trade_tx_key(text, timestamptz, numeric, numeric, text, text) is
  'The dedupe key of one trade print with its transaction as 16 bytes: equal exactly when trade_dedupe_key and transaction_hash are equal; NULL when either is (R46 part 2, 10 Oct).';

create unique index if not exists ad4_uq_trade_tx_key
  on public.trades_observed
     (public.trade_tx_key(condition_id, traded_at, price, size, proxy_wallet, transaction_hash))
  where transaction_hash is not null;

create unique index if not exists ad4_uq_trade_dedupe_untx
  on public.trades_observed (public.trade_dedupe_key(condition_id, traded_at, price, size, proxy_wallet))
  where transaction_hash is null;

create index if not exists ad4_ix_trades_untx_print
  on public.trades_observed (condition_id, traded_at)
  where transaction_hash is null;

drop index if exists public.ad4_uq_trade_dedupe_hash;

create or replace function public.insert_trade_prints(p_rows jsonb)
returns table (trade_id bigint)
language sql
set search_path = public
as $$
  insert into public.trades_observed as t
         (band_id, condition_id, city_key, token_id, side, price, size, traded_at, proxy_wallet, ingested_at,
          transaction_hash)
  select r.band_id, r.condition_id, r.city_key, r.token_id, r.side, r.price, r.size, r.traded_at,
         coalesce(r.proxy_wallet, ''), coalesce(r.ingested_at, now()), nullif(r.transaction_hash, '')
    from jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb))
         as r(band_id uuid, condition_id text, city_key text, token_id text, side text,
              price numeric, size numeric, traded_at timestamptz, proxy_wallet text, ingested_at timestamptz,
              transaction_hash text)
   where coalesce(r.transaction_hash, '') = ''
      or not exists (select 1 from public.trades_observed o
                      where o.transaction_hash is null
                        and o.condition_id = r.condition_id and o.traded_at = r.traded_at
                        and o.price = r.price and o.size = r.size
                        and o.proxy_wallet = coalesce(r.proxy_wallet, ''))
  on conflict do nothing
  returning t.trade_id
$$;

comment on function public.insert_trade_prints(jsonb) is
  'The trade ingest''s insert: every print not already held. A print with a transaction is known by trade_tx_key (ad4_uq_trade_tx_key), one without by trade_dedupe_key (ad4_uq_trade_dedupe_untx), and a print with a transaction whose five columns a row without one already holds is skipped (the changeover, R46 part 2, 10 Oct). No conflict target (20261008180000); returns the trade_id of each one inserted.';

revoke all on function public.insert_trade_prints(jsonb) from public, anon, authenticated;
grant execute on function public.insert_trade_prints(jsonb) to service_role;
