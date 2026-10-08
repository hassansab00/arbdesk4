-- ===========================================================================
-- A TRADE IS KNOWN BY ITS HASH (WXPredict build 2.A group C; Hassan, 8 Oct:
-- "proceed to all in that order", storage first).
--
-- trades_observed keeps one day of prints (archive dataset `trades`), but the
-- prune runs nightly and only takes what the mirror already has, so the table
-- holds 27-51 hours and refills by about 55,000 prints a day (54,845 ingested
-- in the 24 h to 8 Oct 14:52Z). Measured then: 88,746 rows, 63.0 MB, of
-- which 26 MB heap and 34 MB indexes; the dedupe index alone 19 MB, about 220
-- bytes a print, because it holds the five columns that make a print unique:
--
--     ad4_uq_trade_dedupe (condition_id, traded_at, price, size, proxy_wallet)
--
-- It cannot simply go: trade_id is a serial, and the trade ingest re-sends
-- the overlap of every run (sql/ad4_53), which this index turns away.
--
-- THE SAME KEY, SIXTEEN BYTES. trade_dedupe_key() is the md5 of the five
-- columns in a form where equal keys are exactly the index's equal keys:
-- each text column length-prefixed (so no two different pairs of texts make
-- one string), the instant as its 8 binary bytes (equal instants, equal
-- bytes, whatever the session's time zone), and each numeric through
-- trim_scale (0.5 and 0.50 are equal to the old index, and so here). A NULL
-- in any of condition_id, traded_at, price or size makes the key NULL, which
-- never conflicts, as a NULL never did in the five-column index;
-- proxy_wallet is NOT NULL (ad4_53). Every function in it is IMMUTABLE
-- (pg_proc, 8 Oct), as an index needs.
--
-- Proved on live data first (8 Oct ~15:10Z): 88,746 rows, 88,746 distinct
-- five-column keys, 88,746 distinct hashes, no hash shared by two keys and no
-- key with two hashes.
--
-- PostgREST cannot name an expression in on_conflict (ad4_53), so the
-- ingest inserts through insert_trade_prints(), which names it. It returns the
-- trade_id of each print it inserted, as the REST insert's
-- return=representation did, so the ingest still counts what was new. The
-- name is not insert_trades: production holds an insert_trades(jsonb)
-- returning jsonb (security definer, in no file of this repository, seen 8 Oct
-- when the first apply of this migration was refused for it), left as it is.
--
-- This migration only adds: the five-column index stays, so the ingest on
-- main keeps working while this is applied. 20261008170100 drops it once the
-- ingest that calls insert_trade_prints() is live. Re-runnable.
-- ===========================================================================

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
  on conflict (public.trade_dedupe_key(condition_id, traded_at, price, size, proxy_wallet)) do nothing
  returning t.trade_id
$$;

comment on function public.insert_trade_prints(jsonb) is
  'The trade ingest''s insert: every print not already held, turned away by its dedupe key (trade_dedupe_key); returns the trade_id of each one inserted (WXPredict build 2.A, 8 Oct).';

revoke all on function public.insert_trade_prints(jsonb) from public, anon, authenticated;
grant execute on function public.insert_trade_prints(jsonb) to service_role;
