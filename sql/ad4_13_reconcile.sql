-- ===========================================================================
-- ad4_13_reconcile.sql - reconcile AD4 with the REAL Phase 0 schema.
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Files 00-12 were written against an assumed Phase 0 schema. Running
-- sql/ad4_99_verify.sql against the live database returned the actual
-- shape, and three of those assumptions were wrong:
--
--   1. book_snapshots.bid_levels / ask_levels are `integer` LEVEL COUNTS,
--      not jsonb ladders. The real book lives in `raw_book jsonb`, with
--      pre-aggregated cumulative depth in ask_usd_1c/2c/5c/10c/25c,
--      bid_usd_*, ask_total_usd, bid_total_usd. Every consumer that
--      assumed a jsonb ladder (recompute_capacity, calc_recommendation,
--      the Goals engine) was reading an integer.
--
--   2. trades_observed timestamps live in `traded_at`. The preflight added
--      an `observed_at` column that is NULL on all 122k rows, and the
--      volume views filtered on it - so every band reported $0 traded
--      volume while the data sat right there.
--
--   3. anon still held INSERT/UPDATE/DELETE/TRUNCATE on ~40 tables from an
--      earlier `grant all`. ad4_rls.sql only ever ADDED select; it never
--      took the writes away, so the write boundary was open.
--
-- Plus a set of type mismatches: paper_trades.trade_id is uuid (not
-- bigint), the real close columns are close_price/close_reason, and
-- signals carries approved/acted_on alongside status.
--
-- HOW IT SOLVES THEM
-- ------------------
-- Structurally, not case by case. Nothing below hard-codes "the column is
-- called traded_at" or "the ladder is in raw_book". Every shape-dependent
-- object is built by inspecting information_schema at run time and
-- emitting the right SQL, and every reader goes through one adapter
-- (v_band_book) that normalises whatever the book physically is into
-- [{"price":..,"size":..}]. Point it at a different Phase 0 and it adapts
-- instead of erroring.
--
-- RUN ORDER: last, after sql/ad4_rls.sql. Re-runnable. Every statement is
-- independent - no temp tables, no cross-statement state, so it cannot
-- half-apply if the SQL editor moves between connections.
--
-- WHAT IT OVERRIDES: v_band_volume, v_city_volume, recompute_capacity,
-- refresh_derived, calc_recommendation, log_paper_trade, approve_signal,
-- close_position, and the anon grants. Those definitions also exist in
-- sql/ad4_phase2.sql, ad4_capacity_correlation.sql, ad4_rpc.sql and
-- ad4_rls.sql; this file runs after all of them and wins.
-- ===========================================================================


-- ===========================================================================
-- 1. LADDER NORMALISATION - accept any book shape, emit one canonical one
-- ===========================================================================

-- Scalar jsonb -> numeric, without throwing on garbage. Used everywhere
-- below so a single malformed level can never abort a whole view.
create or replace function ad4_num(p jsonb) returns numeric
language plpgsql immutable as $ad4$
declare v numeric;
begin
  if p is null or jsonb_typeof(p) = 'null' then return null; end if;
  begin
    v := (p #>> '{}')::numeric;
  exception when others then
    return null;
  end;
  return v;
end;
$ad4$;

-- Normalise a ladder into [{"price":n,"size":n}, ...], best-first.
-- Accepts: array of objects ({price,size} / {p,s} / {px,sz} / {price,shares}
-- / {price,amount}), array of [price,size] pairs, or a price-keyed object
-- {"0.42": 1200}. Anything unparseable is dropped, not fatal.
create or replace function ad4_norm_levels(p_levels jsonb, p_is_ask boolean)
returns jsonb
language plpgsql immutable as $ad4$
declare
  v_arr jsonb := '[]'::jsonb;
  v_el  jsonb;
  v_p   numeric;
  v_s   numeric;
  k     text;
begin
  if p_levels is null then return '[]'::jsonb; end if;

  if jsonb_typeof(p_levels) = 'array' then
    for v_el in select value from jsonb_array_elements(p_levels) loop
      v_p := null; v_s := null;
      if jsonb_typeof(v_el) = 'object' then
        v_p := ad4_num(coalesce(v_el->'price', v_el->'p', v_el->'px',
                                v_el->'limit_price', v_el->'rate'));
        v_s := ad4_num(coalesce(v_el->'size', v_el->'s', v_el->'sz',
                                v_el->'shares', v_el->'quantity',
                                v_el->'qty', v_el->'amount'));
      elsif jsonb_typeof(v_el) = 'array' and jsonb_array_length(v_el) >= 2 then
        v_p := ad4_num(v_el->0);
        v_s := ad4_num(v_el->1);
      end if;
      if v_p is not null and v_s is not null and v_p > 0 and v_s > 0 then
        v_arr := v_arr || jsonb_build_object('price', v_p, 'size', v_s);
      end if;
    end loop;

  elsif jsonb_typeof(p_levels) = 'object' then
    for k in select jsonb_object_keys(p_levels) loop
      begin
        v_p := k::numeric;
      exception when others then
        continue;
      end;
      v_s := ad4_num(p_levels->k);
      if v_p is not null and v_s is not null and v_p > 0 and v_s > 0 then
        v_arr := v_arr || jsonb_build_object('price', v_p, 'size', v_s);
      end if;
    end loop;
  end if;

  -- best-first: asks ascending, bids descending
  select coalesce(
           jsonb_agg(e order by (e->>'price')::numeric *
                                (case when p_is_ask then 1 else -1 end)),
           '[]'::jsonb)
    into v_arr
  from jsonb_array_elements(v_arr) e;

  return v_arr;
end;
$ad4$;

-- Pull one side out of raw_book, whatever it calls its keys and however
-- deeply it nests them. Returns [] if this blob has no such side.
create or replace function ad4_raw_book_side(p_raw jsonb, p_is_ask boolean)
returns jsonb
language plpgsql immutable as $ad4$
declare
  v    jsonb;
  keys text[];
  k    text;
begin
  if p_raw is null then return '[]'::jsonb; end if;

  -- raw_book may itself already BE the ladder for this side
  if jsonb_typeof(p_raw) = 'array' then
    return ad4_norm_levels(p_raw, p_is_ask);
  end if;
  if jsonb_typeof(p_raw) <> 'object' then return '[]'::jsonb; end if;

  keys := case when p_is_ask
            then array['asks','ask','ask_levels','askLevels','sells','sell','offers','a']
            else array['bids','bid','bid_levels','bidLevels','buys','buy','b'] end;

  foreach k in array keys loop
    if p_raw ? k then
      v := ad4_norm_levels(p_raw->k, p_is_ask);
      if jsonb_array_length(v) > 0 then return v; end if;
    end if;
  end loop;

  -- one wrapper down: {"book":{...}}, {"data":{...}}, {"yes":{...}}, ...
  foreach k in array array['book','orderbook','order_book','data','result','yes','no'] loop
    if p_raw ? k and jsonb_typeof(p_raw->k) in ('object','array') then
      v := ad4_raw_book_side(p_raw->k, p_is_ask);
      if jsonb_array_length(v) > 0 then return v; end if;
    end if;
  end loop;

  return '[]'::jsonb;
end;
$ad4$;

-- LAST RESORT, and deliberately conservative: rebuild an approximate
-- ladder from the cumulative USD-depth tiers when there is no ladder at
-- all. ask_usd_2c is "USD available within 2c of the touch", so the
-- incremental USD in each tier is the difference between successive
-- tiers, and that increment is priced at the tier's OUTER edge - the
-- worst price inside it. The first tier sits at the touch itself, since
-- the touch level is most of it.
--
-- The last bucket - ask_total_usd minus ask_usd_25c - is everything past
-- the furthest tier anyone measured, and we have NO idea how far past. It
-- is priced at touch +/- 0.26, one cent OUTSIDE the widest tier, and that
-- is deliberate: a slippage-capped ladder walk must refuse it. Pricing it
-- at exactly +/- 0.25 made it pass a 25c slippage test and the calculator
-- reported fills that do not exist - on a test ladder whose real depth ran
-- out at $1,146, the 0.25-capped walk claimed $1,500 filled at avg 0.4085
-- instead of stopping at $1,146 / 0.3760. Total depth is unaffected either
-- way (the USD is the same wherever it is priced); what changes is whether
-- an unreachable bucket can masquerade as reachable.
--
-- Known residual bias, in the honest direction as far as it can be: the
-- first tier is priced at the touch, so a small order's average fill comes
-- out marginally cheaper than reality (0.3409 vs 0.3415 on the same test
-- ladder - about 0.2%). Splitting it would mean inventing how much sits
-- exactly at the touch, which is a worse lie than a measured 0.2%.
--
-- This is an approximation and every consumer labels it as one
-- (v_band_book.ask_levels_source = 'synthetic_tiers'). It is never
-- preferred over a real ladder; it exists so capacity, the calculator and
-- Goals degrade to "coarse but directionally right" instead of "zero".
-- The cure is to populate raw_book - see n8n/P0.3_book_volume_snapshot.
create or replace function ad4_synth_levels(
  p_touch numeric, p_u1 numeric, p_u2 numeric, p_u5 numeric,
  p_u10 numeric, p_u25 numeric, p_total numeric, p_is_ask boolean)
returns jsonb
language plpgsql immutable as $ad4$
declare
  v_arr   jsonb := '[]'::jsonb;
  v_prev  numeric := 0;
  v_dir   numeric;
  v_inc   numeric;
  v_price numeric;
  r       record;
begin
  if p_touch is null or p_touch <= 0 then return '[]'::jsonb; end if;
  v_dir := case when p_is_ask then 1 else -1 end;

  for r in
    select * from (values
      (0.00::numeric, p_u1),
      (0.02::numeric, p_u2),
      (0.05::numeric, p_u5),
      (0.10::numeric, p_u10),
      (0.25::numeric, p_u25),
      -- one cent OUTSIDE the widest measured tier: unreachable under a 25c
      -- slippage cap, which is exactly what an unmeasured residual is.
      (0.26::numeric, p_total)
    ) as t(off, cum)
  loop
    continue when r.cum is null;
    v_inc  := r.cum - v_prev;
    v_prev := greatest(v_prev, r.cum);
    continue when v_inc <= 0;
    v_price := round(least(0.999, greatest(0.001, p_touch + v_dir * r.off)), 4);
    v_arr := v_arr || jsonb_build_object('price', v_price,
                                          'size', round(v_inc / v_price, 4));
  end loop;

  return v_arr;
end;
$ad4$;

-- Depth helpers. Unchanged in behaviour - they were always correct, they
-- were just being handed an integer. Redefined here so this file stands
-- alone if ad4_capacity_correlation.sql has not been run.
create or replace function depth_usd(levels jsonb) returns numeric
language sql immutable as $ad4$
  select coalesce(sum((l->>'price')::numeric * (l->>'size')::numeric), 0)
  from jsonb_array_elements(coalesce(levels, '[]'::jsonb)) l;
$ad4$;

create or replace function capacity_side(levels jsonb, touch numeric, cap numeric, is_ask boolean)
returns numeric language sql immutable as $ad4$
  select coalesce(sum((l->>'price')::numeric * (l->>'size')::numeric), 0)
  from jsonb_array_elements(coalesce(levels, '[]'::jsonb)) l
  where touch is not null
    and case when is_ask then (l->>'price')::numeric <= touch + cap
             else (l->>'price')::numeric >= touch - cap end;
$ad4$;


-- ===========================================================================
-- 2. v_band_book - THE book adapter. One row per band, latest snapshot,
--    with both ladders already normalised and the provenance of each.
--
--    Every downstream reader (capacity, calc_recommendation, the Goals
--    page) goes through this view and never touches book_snapshots'
--    physical columns again. The column list it emits is FIXED whatever
--    the underlying table looks like, so the UI's types stay valid.
--
--    Preference order per side: raw_book -> jsonb *_levels -> synthesised
--    from the cumulative USD tiers -> empty. ask_levels_source /
--    bid_levels_source say which one was used, so nothing silently passes
--    off an approximation as a real book.
-- ===========================================================================
do $ad4$
declare
  v_has_raw   boolean;
  v_lv_jsonb  boolean;
  v_has_tiers boolean;
  v_has_state boolean;
  v_has_trade boolean;
  v_has_sprd  boolean;
  v_has_vol   boolean;
  v_has_v24   boolean;
  v_ask  text := 'coalesce(';
  v_bid  text := 'coalesce(';
  v_asrc text := 'case ';
  v_bsrc text := 'case ';
  v_sql  text;
begin
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'raw_book' and data_type = 'jsonb')
    into v_has_raw;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'ask_levels' and data_type = 'jsonb')
    into v_lv_jsonb;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'ask_usd_5c')
    into v_has_tiers;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'market_state') into v_has_state;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'tradeable') into v_has_trade;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'spread') into v_has_sprd;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'band_volume') into v_has_vol;
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'book_snapshots'
                    and column_name = 'band_volume_24hr') into v_has_v24;

  if v_has_raw then
    v_ask  := v_ask  || 'nullif(ad4_raw_book_side(s.raw_book, true), ''[]''::jsonb), ';
    v_bid  := v_bid  || 'nullif(ad4_raw_book_side(s.raw_book, false), ''[]''::jsonb), ';
    v_asrc := v_asrc || 'when jsonb_array_length(ad4_raw_book_side(s.raw_book, true)) > 0 then ''raw_book'' ';
    v_bsrc := v_bsrc || 'when jsonb_array_length(ad4_raw_book_side(s.raw_book, false)) > 0 then ''raw_book'' ';
  end if;

  if v_lv_jsonb then
    v_ask  := v_ask  || 'nullif(ad4_norm_levels(s.ask_levels, true), ''[]''::jsonb), ';
    v_bid  := v_bid  || 'nullif(ad4_norm_levels(s.bid_levels, false), ''[]''::jsonb), ';
    v_asrc := v_asrc || 'when jsonb_array_length(ad4_norm_levels(s.ask_levels, true)) > 0 then ''levels_jsonb'' ';
    v_bsrc := v_bsrc || 'when jsonb_array_length(ad4_norm_levels(s.bid_levels, false)) > 0 then ''levels_jsonb'' ';
  end if;

  if v_has_tiers then
    v_ask  := v_ask  || 'nullif(ad4_synth_levels(s.best_ask, s.ask_usd_1c, s.ask_usd_2c, s.ask_usd_5c, s.ask_usd_10c, s.ask_usd_25c, s.ask_total_usd, true), ''[]''::jsonb), ';
    v_bid  := v_bid  || 'nullif(ad4_synth_levels(s.best_bid, s.bid_usd_1c, s.bid_usd_2c, s.bid_usd_5c, s.bid_usd_10c, s.bid_usd_25c, s.bid_total_usd, false), ''[]''::jsonb), ';
    v_asrc := v_asrc || 'when jsonb_array_length(ad4_synth_levels(s.best_ask, s.ask_usd_1c, s.ask_usd_2c, s.ask_usd_5c, s.ask_usd_10c, s.ask_usd_25c, s.ask_total_usd, true)) > 0 then ''synthetic_tiers'' ';
    v_bsrc := v_bsrc || 'when jsonb_array_length(ad4_synth_levels(s.best_bid, s.bid_usd_1c, s.bid_usd_2c, s.bid_usd_5c, s.bid_usd_10c, s.bid_usd_25c, s.bid_total_usd, false)) > 0 then ''synthetic_tiers'' ';
  end if;

  v_ask  := v_ask  || '''[]''::jsonb)';
  v_bid  := v_bid  || '''[]''::jsonb)';
  v_asrc := v_asrc || 'else ''none'' end';
  v_bsrc := v_bsrc || 'else ''none'' end';

  v_sql :=
    'create or replace view v_band_book as ' ||
    'with latest as (' ||
    '  select distinct on (band_id) * from book_snapshots' ||
    '  where band_id is not null order by band_id, observed_at desc' ||
    ') select ' ||
    '  s.band_id, s.observed_at, s.best_bid, s.best_ask, ' ||
    case when v_has_sprd  then 's.spread'       else 'null::numeric' end || ' as spread, ' ||
    case when v_has_state then 's.market_state' else 'null::text'    end || ' as market_state, ' ||
    case when v_has_trade then 's.tradeable'    else 'null::boolean' end || ' as tradeable, ' ||
    v_bid  || ' as bid_levels, ' ||
    v_ask  || ' as ask_levels, ' ||
    v_bsrc || ' as bid_levels_source, ' ||
    v_asrc || ' as ask_levels_source, ' ||
    'depth_usd(' || v_bid || ') as bid_depth_usd, ' ||
    'depth_usd(' || v_ask || ') as ask_depth_usd, ' ||
    case when v_has_v24 then 's.band_volume_24hr' else 'null::numeric' end || ' as band_volume_24h, ' ||
    case when v_has_vol then 's.band_volume'      else 'null::numeric' end || ' as band_volume_lifetime ' ||
    'from latest s';

  execute v_sql;

  raise notice 'v_band_book built: raw_book=%  jsonb_levels=%  usd_tiers=%',
               v_has_raw, v_lv_jsonb, v_has_tiers;
end
$ad4$;

-- Long form of the same thing, one row per (band, side). Handy for the
-- calculator and for anything that wants to iterate sides generically.
create or replace view v_book_ladder as
select band_id, observed_at, 'ask'::text as side, best_ask as touch,
       ask_levels as levels, ask_levels_source as levels_source,
       ask_depth_usd as depth_usd
from v_band_book
union all
select band_id, observed_at, 'bid'::text, best_bid,
       bid_levels, bid_levels_source, bid_depth_usd
from v_band_book;


-- ===========================================================================
-- 3. TRADED VOLUME - read the column that actually holds the timestamp.
--
--    The views keep their original first four columns (band_id/city_key,
--    volume_usd, n_trades, last_trade_at) in the same order and types, so
--    `create or replace` works and v_opportunities is never dropped.
--
--    Two independent sources, never silently merged:
--      volume_usd_book   - the exchange's own 24h figure carried on the
--                          latest book snapshot (band_volume_24hr)
--      volume_usd_trades - what our own trade capture actually saw
--    Both are LOWER BOUNDS on true traded volume: our capture is a sample
--    and so can only undercount, and the exchange figure can lag. So
--    volume_usd is the greater of the two - the tighter lower bound - and
--    volume_source names which one won. Both raw figures stay visible, and
--    volume_stale flags a book figure older than three lookback windows.
-- ===========================================================================
-- Which column on trades_observed actually holds the trade time? Resolved
-- at run time, every time, so nothing downstream hard-codes a name. Returns
-- a coalesce() over every candidate present, in preference order - so it is
-- right whichever one the ingest populates, and stays right if a migration
-- adds another.
create or replace function ad4_trades_ts_expr(p_alias text default 't')
returns text
language plpgsql stable as $ad4$
declare
  c     text;
  parts text[] := '{}';
begin
  foreach c in array array['traded_at','observed_at','ts','trade_time','ingested_at'] loop
    if exists (select 1 from information_schema.columns
                where table_schema = 'public' and table_name = 'trades_observed'
                  and column_name = c) then
      parts := parts || (p_alias || '.' || quote_ident(c));
    end if;
  end loop;
  if array_length(parts, 1) is null then return null; end if;
  if array_length(parts, 1) = 1 then return parts[1]; end if;
  return 'coalesce(' || array_to_string(parts, ', ') || ')';
end;
$ad4$;

do $ad4$
declare
  v_ts     text;
  v_lb     text := 'coalesce(((select value from settings where key = ''volume_thresholds'')->>''lookback_hours'')::int, 24)';
  v_city   boolean;
  v_from   text;
  v_cityex text;
  v_sql    text;
begin
  if to_regclass('public.trades_observed') is null then
    raise notice 'reconcile: trades_observed missing - volume views left alone';
    return;
  end if;

  v_ts := ad4_trades_ts_expr('t');
  if v_ts is null then
    raise notice 'reconcile: trades_observed has no recognised timestamp column - volume views left alone';
    return;
  end if;

  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'trades_observed'
                    and column_name = 'city_key') into v_city;

  raise notice 'reconcile: trades_observed timestamp = %s, city_key column = %', v_ts, v_city;

  -- ---- v_band_volume ------------------------------------------------------
  v_sql :=
   'create or replace view v_band_volume as
    with tv as (
      select t.band_id,
             sum(t.price * t.size)::numeric as volume_usd,
             count(*)::int                  as n_trades,
             max(' || v_ts || ')            as last_trade_at
      from trades_observed t
      where t.band_id is not null
        and ' || v_ts || ' >= now() - make_interval(hours => ' || v_lb || ')
      group by t.band_id
    ), bk as (
      select band_id, band_volume_24h, band_volume_lifetime, observed_at
      from v_band_book
    )
    select
      coalesce(tv.band_id, bk.band_id) as band_id,
      greatest(coalesce(bk.band_volume_24h, 0),
               coalesce(tv.volume_usd, 0))::numeric as volume_usd,
      coalesce(tv.n_trades, 0) as n_trades,
      tv.last_trade_at,
      coalesce(tv.volume_usd, 0)::numeric          as volume_usd_trades,
      coalesce(bk.band_volume_24h, 0)::numeric     as volume_usd_book,
      coalesce(bk.band_volume_lifetime, 0)::numeric as volume_usd_lifetime,
      (case when greatest(coalesce(bk.band_volume_24h, 0), coalesce(tv.volume_usd, 0)) = 0 then ''none''
            when coalesce(bk.band_volume_24h, 0) >= coalesce(tv.volume_usd, 0) then ''book_24h''
            else ''trades_observed'' end) as volume_source,
      bk.observed_at as book_volume_as_of,
      (bk.observed_at is not null
        and bk.observed_at < now() - make_interval(hours => (' || v_lb || ') * 3)) as volume_stale
    from tv full join bk on bk.band_id = tv.band_id';
  execute v_sql;

  -- ---- v_city_volume ------------------------------------------------------
  if v_city then
    v_cityex := 't.city_key';
    v_from   := 'from trades_observed t';
  else
    v_cityex := 'm.city_key';
    v_from   := 'from trades_observed t
                 join bands b2   on b2.band_id = t.band_id
                 join markets m  on m.market_id = b2.market_id';
  end if;

  v_sql :=
   'create or replace view v_city_volume as
    with tv as (
      select ' || v_cityex || ' as city_key,
             sum(t.price * t.size)::numeric as volume_usd,
             count(*)::int                  as n_trades,
             max(' || v_ts || ')            as last_trade_at
      ' || v_from || '
      where ' || v_cityex || ' is not null
        and ' || v_ts || ' >= now() - make_interval(hours => ' || v_lb || ')
      group by 1
    ), bv as (
      select mk.city_key,
             sum(v.volume_usd_book)::numeric as volume_usd_book,
             max(v.book_volume_as_of)        as book_volume_as_of
      from v_band_volume v
      join bands bb  on bb.band_id = v.band_id
      join markets mk on mk.market_id = bb.market_id
      where mk.resolution_date is null or mk.resolution_date >= current_date - 1
      group by mk.city_key
    )
    select
      coalesce(bv.city_key, tv.city_key) as city_key,
      greatest(coalesce(bv.volume_usd_book, 0),
               coalesce(tv.volume_usd, 0))::numeric as volume_usd,
      coalesce(tv.n_trades, 0) as n_trades,
      tv.last_trade_at,
      coalesce(tv.volume_usd, 0)::numeric      as volume_usd_trades,
      coalesce(bv.volume_usd_book, 0)::numeric as volume_usd_book,
      (case when greatest(coalesce(bv.volume_usd_book, 0), coalesce(tv.volume_usd, 0)) = 0 then ''none''
            when coalesce(bv.volume_usd_book, 0) >= coalesce(tv.volume_usd, 0) then ''book_24h''
            else ''trades_observed'' end) as volume_source,
      bv.book_volume_as_of
    from bv full join tv on tv.city_key = bv.city_key';
  execute v_sql;

  raise notice 'reconcile: v_band_volume / v_city_volume rebuilt';
end
$ad4$;


-- ===========================================================================
-- 4. v_latest_book - keeps every real column of book_snapshots, but its
--    bid_levels / ask_levels are now the NORMALISED jsonb ladders rather
--    than the integer level counts.
--
--    This is the single change that repairs every remaining consumer at
--    once - calc_recommendation's ladder walk and the Goals page both read
--    v_latest_book.ask_levels and neither needs to know the physical book
--    changed shape. ask_levels_source / bid_levels_source ride along so a
--    synthesised ladder is never mistaken for a real one.
--
--    v_latest_book's column TYPES change (integer -> jsonb), so this has to
--    drop and recreate rather than `create or replace`, which takes
--    v_opportunities with it. Both are rebuilt inside ONE statement so the
--    pair can never be left half-applied.
-- ===========================================================================
do $ad4$
declare
  r      record;
  v_cols text := '';
  v_seen boolean := false;
begin
  for r in select column_name from information_schema.columns
            where table_schema = 'public' and table_name = 'book_snapshots'
            order by ordinal_position
  loop
    if r.column_name in ('ask_levels', 'bid_levels') then
      v_cols := v_cols || 'bb.' || quote_ident(r.column_name) || ' as ' || quote_ident(r.column_name) || ', ';
      v_seen := true;
    else
      v_cols := v_cols || 's.' || quote_ident(r.column_name) || ', ';
    end if;
  end loop;

  if not v_seen then
    -- a book_snapshots with no *_levels columns at all still gets ladders
    v_cols := v_cols || 'bb.bid_levels, bb.ask_levels, ';
  end if;

  v_cols := v_cols ||
    'bb.bid_levels_source, bb.ask_levels_source, ' ||
    'bb.bid_depth_usd, bb.ask_depth_usd';

  execute 'drop view if exists v_opportunities cascade';
  execute 'drop view if exists v_latest_book cascade';

  execute
    'create view v_latest_book as
     with latest as (
       select distinct on (band_id) * from book_snapshots
       where band_id is not null order by band_id, observed_at desc
     )
     select ' || v_cols || '
     from latest s
     join v_band_book bb on bb.band_id = s.band_id';

  -- v_opportunities, rebuilt in the same statement. Same leading columns as
  -- before, with the provenance of both the depth and the volume appended -
  -- never show a number without saying where it came from.
  execute $v$
create view v_opportunities as
select
  e.edge_id, e.side, e.model_prob, e.market_price, e.edge_net_pp,
  e.edge_per_dollar, e.fillable_usd_5c, e.confidence, e.regime_label,
  e.tradeable, e.block_reason,
  b.band_id, b.band_label, b.band_lo, b.band_hi, b.open_low, b.open_high,
  b.token_yes, b.token_no,
  m.city_key, m.resolution_date, m.unit,
  c.display_name, c.icao, c.station_name, c.timezone, c.band_width,
  bk.best_bid, bk.best_ask, bk.spread, bk.market_state,
  coalesce(bv.volume_usd, 0) as volume_usd,
  coalesce(bv.n_trades, 0)   as n_trades,
  bv.last_trade_at,
  coalesce(cv.volume_usd, 0) as city_volume_usd,
  (coalesce(bv.volume_usd, 0) < coalesce(vt.thin_band_usd, 0)) as thin_market,
  (coalesce(bv.volume_usd, 0) / nullif(coalesce(bv.volume_usd, 0) + vt.k, 0)) as liquidity_factor,
  (e.edge_net_pp * coalesce(e.confidence, 0) * ln(1 + greatest(coalesce(e.fillable_usd_5c, 0), 0)))
    as score_depth_only,
  (e.edge_net_pp * coalesce(e.confidence, 0) * ln(1 + greatest(coalesce(e.fillable_usd_5c, 0), 0))
     * (coalesce(bv.volume_usd, 0) / nullif(coalesce(bv.volume_usd, 0) + vt.k, 0)))
    as score,
  -- appended by sql/ad4_13_reconcile.sql: never show a number without
  -- saying where it came from.
  coalesce(bv.volume_source, 'none')      as volume_source,
  coalesce(bv.volume_stale, false)        as volume_stale,
  coalesce(cv.volume_source, 'none')      as city_volume_source,
  coalesce(bk.ask_levels_source, 'none')  as ask_levels_source,
  coalesce(bk.bid_levels_source, 'none')  as bid_levels_source,
  coalesce(bk.ask_depth_usd, 0)           as ask_depth_usd,
  coalesce(bk.bid_depth_usd, 0)           as bid_depth_usd,
  bk.observed_at                          as book_observed_at
from v_latest_edge e
join bands b   on b.band_id = e.band_id
join markets m on m.market_id = b.market_id
join cities c  on c.city_key = m.city_key
cross join (
  select
    coalesce(((select value from settings where key = 'volume_thresholds')->>'liquidity_half_saturation_usd')::numeric, 1) as k,
    coalesce(((select value from settings where key = 'volume_thresholds')->>'thin_band_usd_24h')::numeric, 0) as thin_band_usd
) vt
left join v_band_book   bk on bk.band_id = b.band_id
left join v_band_volume bv on bv.band_id = b.band_id
left join v_city_volume cv on cv.city_key = m.city_key
where m.resolution_date >= current_date
order by score desc nulls last
  $v$;

  raise notice 'reconcile: v_latest_book + v_opportunities rebuilt';
end
$ad4$;


-- ===========================================================================
-- 5. CAPACITY - reads normalised ladders instead of an integer.
--    Still a curve (2c / 5c / 10c / full), never one number.
-- ===========================================================================
create or replace function recompute_capacity() returns integer
language plpgsql security definer as $ad4$
declare
  v_rows integer;
begin
  insert into derived_capacity (city_key, computed_at, hour_utc,
                                usd_at_2c, usd_at_5c, usd_at_10c, usd_full, live_bands)
  select
    m.city_key,
    now(),
    extract(hour from bb.observed_at)::int as hour_utc,
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.02, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.02, false)),
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.05, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.05, false)),
    sum(capacity_side(bb.ask_levels, bb.best_ask, 0.10, true)
      + capacity_side(bb.bid_levels, bb.best_bid, 0.10, false)),
    sum(coalesce(bb.ask_depth_usd, 0) + coalesce(bb.bid_depth_usd, 0)),
    count(*) filter (where bb.market_state = 'LIVE' or bb.tradeable is true)
  from v_band_book bb
  join bands b   on b.band_id = bb.band_id
  join markets m on m.market_id = b.market_id
  where bb.observed_at is not null
  group by m.city_key, extract(hour from bb.observed_at)::int;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$ad4$;


-- ===========================================================================
-- 6. refresh_derived - same two grains, reading the real timestamp column.
--    Resolves the column on every call rather than at definition time, so
--    it keeps working if the ingest starts populating a different one.
-- ===========================================================================
create or replace function refresh_derived() returns jsonb
language plpgsql security definer as $ad4$
declare
  v_ts        text;
  v_city      boolean;
  v_city_rows int := 0;
  v_band_rows int := 0;
begin
  v_ts := ad4_trades_ts_expr('t');
  if v_ts is null then
    return jsonb_build_object('ok', false,
      'error', 'trades_observed has no recognised timestamp column');
  end if;

  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'trades_observed'
                    and column_name = 'city_key') into v_city;

  -- ---- city-day ----------------------------------------------------------
  if v_city then
    execute format($f$
      insert into derived_city_day_volume (city_key, trade_date, volume_usd, n_trades, computed_at)
      select t.city_key, (%1$s)::date, sum(t.price * t.size), count(*), now()
      from trades_observed t
      where t.city_key is not null and (%1$s) is not null
      group by t.city_key, (%1$s)::date
      on conflict (city_key, trade_date) do update
        set volume_usd = excluded.volume_usd,
            n_trades   = excluded.n_trades,
            computed_at = excluded.computed_at
    $f$, v_ts);
  else
    execute format($f$
      insert into derived_city_day_volume (city_key, trade_date, volume_usd, n_trades, computed_at)
      select mk.city_key, (%1$s)::date, sum(t.price * t.size), count(*), now()
      from trades_observed t
      join bands bb   on bb.band_id = t.band_id
      join markets mk on mk.market_id = bb.market_id
      where mk.city_key is not null and (%1$s) is not null
      group by mk.city_key, (%1$s)::date
      on conflict (city_key, trade_date) do update
        set volume_usd = excluded.volume_usd,
            n_trades   = excluded.n_trades,
            computed_at = excluded.computed_at
    $f$, v_ts);
  end if;
  get diagnostics v_city_rows = row_count;

  -- ---- band-day ----------------------------------------------------------
  execute format($f$
    insert into derived_band_day_volume (band_id, city_key, trade_date, volume_usd, n_trades, computed_at)
    select t.band_id,
           %2$s,
           (%1$s)::date,
           sum(t.price * t.size), count(*), now()
    from trades_observed t
    where t.band_id is not null and (%1$s) is not null
    group by t.band_id, (%1$s)::date
    on conflict (band_id, trade_date) do update
      set city_key   = excluded.city_key,
          volume_usd = excluded.volume_usd,
          n_trades   = excluded.n_trades,
          computed_at = excluded.computed_at
  $f$,
    v_ts,
    case when v_city then 'max(t.city_key)'
         else '(select max(mk.city_key) from bands bb join markets mk on mk.market_id = bb.market_id where bb.band_id = t.band_id)'
    end);
  get diagnostics v_band_rows = row_count;

  return jsonb_build_object('ok', true,
                            'timestamp_column', v_ts,
                            'city_day_volume_rows', v_city_rows,
                            'band_day_volume_rows', v_band_rows);
end;
$ad4$;


-- ===========================================================================
-- 7. RPC TYPE RECONCILIATION
--
--    paper_trades.trade_id is uuid, not bigint; the real close columns are
--    close_price / close_reason; and cost_version / forecast_version /
--    calibration_version are uuid on paper_trades but text on ledger. Every
--    one of those was a runtime error waiting for the first trade.
--
--    None of the types below are hard-coded. Each function is generated
--    against whatever the columns actually are, so the same file is correct
--    on this database and on a fresh one built by ad4_00_preflight.sql.
-- ===========================================================================

-- Declared type of a column, or 'text' if the column is not there.
create or replace function ad4_coltype(p_table text, p_col text) returns text
language sql stable as $ad4$
  select coalesce(
    (select data_type from information_schema.columns
      where table_schema = 'public' and table_name = p_table and column_name = p_col),
    'text');
$ad4$;

create or replace function ad4_hascol(p_table text, p_col text) returns boolean
language sql stable as $ad4$
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = p_table
                    and column_name = p_col);
$ad4$;


-- ---- close_position -------------------------------------------------------
do $ad4$
declare
  v_id_type text;
  v_sets    text := '';
  v_ledger  text;
begin
  if to_regclass('public.paper_trades') is null then
    raise notice 'reconcile: paper_trades missing - close_position left alone';
    return;
  end if;

  v_id_type := ad4_coltype('paper_trades', 'trade_id');
  if v_id_type not in ('uuid', 'bigint', 'integer') then v_id_type := 'bigint'; end if;

  -- write every close column the table actually has, so the rest of the
  -- platform sees the close whichever name it reads.
  if ad4_hascol('paper_trades', 'close_price')  then v_sets := v_sets || ', close_price = p_exit_price'; end if;
  if ad4_hascol('paper_trades', 'exit_price')   then v_sets := v_sets || ', exit_price = p_exit_price'; end if;
  if ad4_hascol('paper_trades', 'close_reason') then v_sets := v_sets || ', close_reason = p_reason'; end if;

  -- ledger's version columns may be text while paper_trades' are uuid
  v_ledger := format(
    'nullif(v_trade.forecast_version::text, '''')::%s, '     ||
    'nullif(v_trade.calibration_version::text, '''')::%s, '  ||
    'nullif(v_trade.cost_version::text, '''')::%s',
    ad4_coltype('ledger', 'forecast_version'),
    ad4_coltype('ledger', 'calibration_version'),
    ad4_coltype('ledger', 'cost_version'));

  execute 'drop function if exists close_position(bigint, numeric, text)';
  execute 'drop function if exists close_position(uuid, numeric, text)';

  execute format($f$
    create function close_position(p_trade_id %1$s, p_exit_price numeric, p_reason text)
    returns jsonb language plpgsql security definer as $body$
    declare
      v_trade record;
      v_gross numeric;
      v_net   numeric;
    begin
      select * into v_trade from paper_trades
       where paper_trades.trade_id = p_trade_id and paper_trades.closed_at is null;
      if not found then
        return jsonb_build_object('ok', false, 'error', 'open trade not found');
      end if;

      v_gross := v_trade.shares * (p_exit_price - v_trade.avg_fill_price);
      v_net   := v_gross - coalesce(v_trade.fee_paid, 0) - coalesce(v_trade.gas_paid, 0);

      update paper_trades
         set closed_at = now(), gross_pnl = v_gross, net_pnl = v_net %2$s
       where paper_trades.trade_id = p_trade_id;

      insert into ledger (stage, strategy_id, band_id, regime_label,
                          forecast_version, calibration_version, cost_version,
                          detail, recorded_at)
      values ('exit', v_trade.strategy_id, v_trade.band_id, v_trade.regime_label,
              %3$s,
              jsonb_build_object('reason', p_reason, 'exit_price', p_exit_price,
                                 'gross_pnl', v_gross, 'net_pnl', v_net),
              now());

      return jsonb_build_object('ok', true, 'gross_pnl', v_gross, 'net_pnl', v_net);
    end;
    $body$
  $f$, v_id_type, v_sets, v_ledger);

  raise notice 'reconcile: close_position(%s, numeric, text) rebuilt', v_id_type;
end
$ad4$;


-- ---- log_paper_trade ------------------------------------------------------
do $ad4$
declare
  v_ledger text;
begin
  if to_regclass('public.paper_trades') is null then
    raise notice 'reconcile: paper_trades missing - log_paper_trade left alone';
    return;
  end if;

  v_ledger := format(
    'nullif(p_trade->>''forecast_version'', '''')::%s, '    ||
    'nullif(p_trade->>''calibration_version'', '''')::%s, ' ||
    'nullif(p_trade->>''cost_version'', '''')::%s',
    ad4_coltype('ledger', 'forecast_version'),
    ad4_coltype('ledger', 'calibration_version'),
    ad4_coltype('ledger', 'cost_version'));

  execute format($f$
    create or replace function log_paper_trade(p_trade jsonb) returns jsonb
    language plpgsql security definer as $body$
    declare
      v_id text;
    begin
      insert into paper_trades (
        strategy_id, band_id, side, action, shares, avg_fill_price, quoted_price,
        slippage_paid, fee_paid, gas_paid, partial_fill, requested_shares,
        legs_requested, legs_filled, fill_quality, max_slippage_setting,
        cost_version, forecast_version, calibration_version, regime_label,
        approved_by_user, opened_at
      )
      select
        p_trade->>'strategy_id', nullif(p_trade->>'band_id','')::uuid,
        p_trade->>'side', p_trade->>'action',
        (p_trade->>'shares')::numeric, (p_trade->>'avg_fill_price')::numeric,
        (p_trade->>'quoted_price')::numeric,
        (p_trade->>'slippage_paid')::numeric, (p_trade->>'fee_paid')::numeric,
        (p_trade->>'gas_paid')::numeric,
        coalesce((p_trade->>'partial_fill')::boolean, false),
        (p_trade->>'requested_shares')::numeric,
        coalesce((p_trade->>'legs_requested')::int, 1),
        coalesce((p_trade->>'legs_filled')::int, 1),
        (p_trade->>'fill_quality')::numeric, (p_trade->>'max_slippage_setting')::numeric,
        nullif(p_trade->>'cost_version','')::%1$s,
        nullif(p_trade->>'forecast_version','')::%2$s,
        nullif(p_trade->>'calibration_version','')::%3$s,
        p_trade->>'regime_label', true, now()
      returning trade_id::text into v_id;

      insert into ledger (stage, strategy_id, band_id, regime_label,
                          forecast_version, calibration_version, cost_version,
                          detail, recorded_at)
      values ('fill', p_trade->>'strategy_id', nullif(p_trade->>'band_id','')::uuid,
              p_trade->>'regime_label',
              %4$s,
              jsonb_build_object('source', 'log_paper_trade_rpc'), now());

      return jsonb_build_object('ok', true, 'trade_id', v_id);
    end;
    $body$
  $f$,
    ad4_coltype('paper_trades', 'cost_version'),
    ad4_coltype('paper_trades', 'forecast_version'),
    ad4_coltype('paper_trades', 'calibration_version'),
    v_ledger);

  raise notice 'reconcile: log_paper_trade rebuilt (trade_id returned as text)';
end
$ad4$;


-- ---- approve_signal / dismiss_signal --------------------------------------
-- The real signals table carries approved and acted_on alongside status.
-- Setting only status left approved=NULL, so an approved signal never
-- looked approved to anything reading that column.
do $ad4$
declare
  v_app text := '';
  v_dis text := '';
begin
  if to_regclass('public.signals') is null then
    raise notice 'reconcile: signals missing - approve_signal left alone';
    return;
  end if;

  if ad4_hascol('signals', 'status') then
    v_app := v_app || 'status = ''approved'', ';
    v_dis := v_dis || 'status = ''dismissed'', ';
  end if;
  if ad4_hascol('signals', 'approved') then
    v_app := v_app || 'approved = true, ';
    v_dis := v_dis || 'approved = false, ';
  end if;
  if ad4_hascol('signals', 'acted_on') then
    v_app := v_app || 'acted_on = true, ';
    v_dis := v_dis || 'acted_on = false, ';
  end if;

  if v_app = '' then
    raise notice 'reconcile: signals has no status/approved/acted_on column - left alone';
    return;
  end if;

  execute format($f$
    create or replace function approve_signal(p_signal_id bigint) returns jsonb
    language plpgsql security definer as $body$
    begin
      update signals set %1$s signal_id = signal_id where signal_id = p_signal_id;
      if not found then
        return jsonb_build_object('ok', false, 'error', 'signal not found');
      end if;
      return jsonb_build_object('ok', true, 'signal_id', p_signal_id);
    end;
    $body$
  $f$, v_app);

  execute format($f$
    create or replace function dismiss_signal(p_signal_id bigint) returns jsonb
    language plpgsql security definer as $body$
    begin
      update signals set %1$s signal_id = signal_id where signal_id = p_signal_id;
      if not found then
        return jsonb_build_object('ok', false, 'error', 'signal not found');
      end if;
      return jsonb_build_object('ok', true, 'signal_id', p_signal_id);
    end;
    $body$
  $f$, v_dis);

  raise notice 'reconcile: approve_signal / dismiss_signal rebuilt';
end
$ad4$;


-- ===========================================================================
-- 8. SECURITY - actually close the write boundary.
--
--    ad4_rls.sql only ever ADDED privileges. It granted anon SELECT and
--    assumed anon had nothing else, but an earlier `grant all in schema
--    public to anon` had already handed out INSERT/UPDATE/DELETE/TRUNCATE
--    on ~40 tables, and a grant that is never revoked never goes away. RLS
--    policies did not save it either: a `for select` policy does not block
--    an INSERT when the role holds the INSERT privilege and any permissive
--    policy applies.
--
--    So: revoke first, then grant back exactly what the browser needs.
--    Read everything, write nothing, execute only the nine UI RPCs.
--
--    IMPORTANT: the view helpers below (ad4_norm_levels, depth_usd, ...)
--    are called from inside v_latest_book / v_band_book, and a view's
--    function calls are permission-checked against the CALLING role. If
--    anon cannot execute them, `select * from v_latest_book` fails. They
--    are pure, read no tables, and are granted deliberately.
-- ===========================================================================
do $ad4$
declare
  v_roles text[] := array['anon', 'authenticated'];
  r       text;
  sig     text;
  v_allowed text[];
  v_n     int;
begin
  foreach r in array v_roles loop
    if not exists (select 1 from pg_roles where rolname = r) then
      raise notice 'reconcile: role % does not exist here - skipped', r;
      continue;
    end if;

    -- 1. take the writes away, on tables AND views
    execute format('revoke insert, update, delete, truncate, references, trigger on all tables in schema public from %I', r);
    execute format('revoke update on all sequences in schema public from %I', r);
    execute format('revoke all on schema public from %I', r);
    -- Stop future objects arriving pre-granted. Supabase ships with
    --   alter default privileges in schema public
    --     grant all on functions to anon, authenticated, service_role;
    -- which is why every function this repo creates comes out anon-callable
    -- the moment it is created - ad4_verify() included. A `revoke ... from
    -- public` does nothing about it, because the grant is to anon
    -- EXPLICITLY, not via PUBLIC. This is the actual cause; the explicit
    -- revokes further down are the belt to this file's braces.
    begin
      execute format('alter default privileges in schema public revoke insert, update, delete, truncate on tables from %I', r);
      execute format('alter default privileges in schema public revoke execute on functions from %I', r);
    exception when others then
      raise notice 'reconcile: could not alter default privileges for % (%)', r, sqlerrm;
    end;

    -- 2. hand back read-only
    execute format('grant usage on schema public to %I', r);
    execute format('grant select on all tables in schema public to %I', r);
    execute format('grant usage, select on all sequences in schema public to %I', r);

    -- 3. no function is callable unless it is on the list below
    execute format('revoke execute on all functions in schema public from %I', r);
  end loop;

  -- PUBLIC is a role too, and it is the one everyone forgets
  execute 'revoke insert, update, delete, truncate on all tables in schema public from public';
  execute 'revoke execute on all functions in schema public from public';

  -- service_role keeps everything: it is the key n8n and GitHub Actions use
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant usage on schema public to service_role';
    execute 'grant all on all tables in schema public to service_role';
    execute 'grant all on all sequences in schema public to service_role';
    execute 'grant execute on all functions in schema public to service_role';
  end if;

  -- 4. the pure helpers the read-only views call
  foreach sig in array array[
    'ad4_num(jsonb)',
    'ad4_norm_levels(jsonb, boolean)',
    'ad4_raw_book_side(jsonb, boolean)',
    'ad4_synth_levels(numeric, numeric, numeric, numeric, numeric, numeric, numeric, boolean)',
    'depth_usd(jsonb)',
    'capacity_side(jsonb, numeric, numeric, boolean)'
  ] loop
    foreach r in array v_roles loop
      if exists (select 1 from pg_roles where rolname = r) then
        begin
          execute format('grant execute on function %s to %I', sig, r);
        exception when undefined_function then
          raise notice 'reconcile: helper % not found - grant skipped', sig;
        end;
      end if;
    end loop;
  end loop;

  -- 5. the nine RPCs the browser is allowed to call, and nothing else.
  --    close_position now takes whatever type paper_trades.trade_id is.
  v_allowed := array[
    'calc_recommendation(jsonb)',
    'log_paper_trade(jsonb)',
    'approve_signal(bigint)',
    'dismiss_signal(bigint)',
    'close_position(' || ad4_coltype('paper_trades', 'trade_id') || ', numeric, text)',
    'queue_backtest(jsonb)',
    'update_setting(text, jsonb)',
    'upsert_deployment(jsonb)',
    'set_deployment_status(uuid, text)'
  ];

  foreach sig in array v_allowed loop
    foreach r in array v_roles loop
      if exists (select 1 from pg_roles where rolname = r) then
        begin
          execute format('grant execute on function %s to %I', sig, r);
        exception when undefined_function then
          raise notice 'reconcile: RPC % does not exist - grant skipped (run sql/ad4_rpc.sql and sql/ad4_backtest.sql first)', sig;
        end;
      end if;
    end loop;
  end loop;

  -- 6. report what is left, rather than asserting it worked
  select count(*) into v_n
  from information_schema.role_table_grants
  where table_schema = 'public'
    and grantee in ('anon', 'authenticated', 'PUBLIC')
    and privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE');

  if v_n = 0 then
    raise notice 'reconcile: SECURITY OK - anon/authenticated hold zero write grants in public';
  else
    raise warning 'reconcile: % write grants SURVIVED - inspect information_schema.role_table_grants', v_n;
  end if;
end
$ad4$;


-- ===========================================================================
-- 9. Report. Run this on its own afterwards to see the result:
--       select * from ad4_reconcile_report();
-- ===========================================================================
create or replace function ad4_reconcile_report()
returns table (check_name text, status text, detail text)
language plpgsql as $ad4$
declare
  v_n bigint;
  v_t text;
begin
  check_name := 'trades timestamp column';
  v_t := ad4_trades_ts_expr('t');
  status := case when v_t is null then 'FAIL' else 'PASS' end;
  detail := coalesce(v_t, 'no recognised timestamp column on trades_observed');
  return next;

  check_name := 'band volume is non-zero';
  execute 'select count(*) from v_band_volume where volume_usd > 0' into v_n;
  status := case when v_n > 0 then 'PASS' else 'EMPTY' end;
  detail := v_n || ' bands report traded volume';
  return next;

  check_name := 'book ladders resolve';
  execute 'select count(*) from v_band_book where ask_levels_source <> ''none''' into v_n;
  status := case when v_n > 0 then 'PASS' else 'EMPTY' end;
  execute 'select string_agg(s || ''='' || c, '', '') from (select ask_levels_source s, count(*) c from v_band_book group by 1 order by 1) x' into v_t;
  detail := coalesce(v_t, 'no book snapshots');
  return next;

  check_name := 'raw_book coverage';
  if ad4_hascol('book_snapshots', 'raw_book') then
    execute 'select count(*) filter (where raw_book is not null) || '' of '' || count(*) || '' snapshots carry raw_book'' from book_snapshots' into v_t;
    execute 'select count(*) filter (where raw_book is not null) from book_snapshots' into v_n;
    status := case when v_n > 0 then 'PASS' else 'ATTENTION' end;
    detail := v_t || case when v_n = 0
      then '  -> every ladder is being RECONSTRUCTED from the *_usd_*c depth tiers. Total depth is exact and a capped walk matches within ~0.2%, but the shape inside a tier is approximated. Fix: n8n/P0.3_book_volume_snapshot writes raw_book.'
      else '' end;
  else
    status := 'ATTENTION';
    detail := 'book_snapshots has no raw_book column';
  end if;
  return next;

  check_name := 'bands with no usable book';
  if to_regclass('public.v_band_book') is not null then
    execute $q$
      select count(*) || ' of ' || (select count(*) from v_band_book) ||
             ' bands: ' ||
             count(*) filter (where best_ask is null) || ' have no best_ask, ' ||
             count(*) filter (where best_ask is not null) || ' have a touch but no depth behind it'
      from v_band_book where ask_levels_source = 'none'
    $q$ into v_t;
    execute 'select count(*) from v_band_book where ask_levels_source = ''none''' into v_n;
    status := case when v_n = 0 then 'PASS' else 'INFO' end;
    detail := v_t || case when v_n > 0
      then '  -> these are unfillable in the calculator by design: no ladder means no size beyond the touch.'
      else '' end;
    return next;
  end if;

  check_name := 'anon/authenticated write grants';
  select count(*) into v_n from information_schema.role_table_grants
   where table_schema = 'public' and grantee in ('anon','authenticated','PUBLIC')
     and privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE');
  status := case when v_n = 0 then 'PASS' else 'FAIL' end;
  detail := v_n || ' write grants remain (want 0)';
  return next;

  check_name := 'anon EXECUTE surface';
  select string_agg(p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', ', ' order by p.proname)
    into v_t
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public'
    and has_function_privilege('anon', p.oid, 'execute');
  detail := coalesce(v_t, 'none');
  status := 'INFO';
  return next;

  check_name := 'close_position signature';
  select pg_get_function_identity_arguments(oid) into v_t from pg_proc where proname = 'close_position' limit 1;
  status := case when v_t like 'p_trade_id ' || ad4_coltype('paper_trades','trade_id') || '%' then 'PASS' else 'FAIL' end;
  detail := coalesce(v_t, 'missing') || '  (paper_trades.trade_id is ' || ad4_coltype('paper_trades','trade_id') || ')';
  return next;
end;
$ad4$;

-- This function is created AFTER the revokes above, so Supabase's default
-- privileges hand anon EXECUTE on it the instant it exists. Take it back by
-- name: revoking from PUBLIC alone is not enough, because the grant is to
-- anon and authenticated explicitly. (The `alter default privileges` in
-- section 8 stops this happening to anything created from here on, but this
-- function was already created by the time that ran on a first pass.)
do $ad4$
declare r text;
begin
  execute 'revoke execute on function ad4_reconcile_report() from public';
  foreach r in array array['anon','authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke execute on function ad4_reconcile_report() from %I', r);
    end if;
  end loop;
  if has_function_privilege('anon', 'ad4_reconcile_report()', 'execute') then
    raise warning 'reconcile: ad4_reconcile_report is STILL anon-executable';
  end if;
exception when undefined_object then
  null;  -- no anon role here (plain Postgres); nothing to take back
end
$ad4$;
