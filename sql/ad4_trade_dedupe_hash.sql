-- AD4 trade dedupe by hash (WXPredict build 2.A group C, 8 Oct). The
-- install form of supabase/migrations/20261008170000, 20261008180000 and
-- 20261008170100: a trade print's dedupe key as 16 bytes (trade_dedupe_key),
-- its unique index, the ingest's insert (insert_trade_prints, with no conflict
-- target: 20261008180000 says why), and the five-column index ad4_53 built
-- dropped. The migrations say why. Re-runnable.

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

create unique index if not exists ad4_uq_trade_dedupe_hash
  on public.trades_observed (public.trade_dedupe_key(condition_id, traded_at, price, size, proxy_wallet));

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
