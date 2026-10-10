-- AD4 trade dedupe by hash (WXPredict build 2.A group C, 8 Oct; R46 part 2,
-- 10 Oct). The install form of supabase/migrations/20261008170000,
-- 20261008180000, 20261008170100 and 20261010190000: a trade print's dedupe
-- key as 16 bytes (trade_dedupe_key), the ingest's insert (insert_trade_prints,
-- with no conflict target: 20261008180000 says why), and the five-column index
-- ad4_53 built dropped; then the transaction in the key (transaction_hash,
-- trade_tx_key), the old key kept for prints without one, the changeover
-- guard, and the full hash index dropped. The migrations say why. Re-runnable.

create or replace function public.trade_dedupe_key(p_condition_id text, p_traded_at timestamptz,
                                                   p_price numeric, p_size numeric, p_proxy_wallet text)
returns bytea
language sql
immutable
parallel safe
as $$
  select decode(md5(octet_length(p_condition_id)::text || ':' || p_condition_id || '|'
                    || encode(timestamptz_send(p_traded_at), 'hex') || '|'
                    || trim_scale(p_price)::text || '|' || trim_scale(p_size)::text || '|'
                    || octet_length(p_proxy_wallet)::text || ':' || p_proxy_wallet), 'hex')
$$;

comment on function public.trade_dedupe_key(text, timestamptz, numeric, numeric, text) is
  'The dedupe key of one trade print as 16 bytes: equal exactly when (condition_id, traded_at, price, size, proxy_wallet) are equal; NULL when any of the first four is (WXPredict build 2.A, 8 Oct).';

-- Built only before the transaction is part of the key (R46 part 2, below):
-- once separate transactions sharing these five columns are held, this full
-- index cannot be built, and a re-run of this file must not try (Codex on #366).
do $$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'trades_observed'
                    and column_name = 'transaction_hash') then
    create unique index if not exists ad4_uq_trade_dedupe_hash
      on public.trades_observed (public.trade_dedupe_key(condition_id, traded_at, price, size, proxy_wallet));
  end if;
end $$;

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

drop index if exists public.ad4_uq_trade_dedupe;

-- R46 part 2 (10 Oct): the transaction is part of the key; 20261010190000 says why.
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
