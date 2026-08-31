-- ---------------------------------------------------------------------------
-- SELF-SUFFICIENCY GUARD (added by the final completion pass).
--
-- This file no longer assumes any prior schema state. Everything it reads
-- or writes below is created here if absent, so it runs standalone against
-- the live Supabase database, a fresh Postgres, or a half-migrated one.
-- sql/ad4_00_preflight.sql does the same job for the whole system at once
-- and should still be run first - this block is the belt to its braces.
-- Idempotent: only ever ADDS, never drops, renames or retypes.
-- ---------------------------------------------------------------------------
create table if not exists cities (
  city_key          text primary key,
  display_name      text,
  icao              text,
  station_name      text,
  timezone          text,
  unit              text default 'C',
  band_width        numeric,
  latitude          numeric,
  longitude         numeric,
  resolution_source text,
  status            text default 'active'
);
create table if not exists markets (
  market_id       uuid primary key default gen_random_uuid(),
  city_key        text,
  resolution_date date,
  unit            text,
  closed          boolean default false,
  event_slug      text,
  condition_id    text
);
create table if not exists bands (
  band_id    uuid primary key default gen_random_uuid(),
  market_id  uuid,
  band_lo    numeric,
  band_hi    numeric,
  open_low   boolean default false,
  open_high  boolean default false,
  band_label text,
  token_yes  text,
  token_no   text
);

do $$
declare r record;
begin
--    every column v_opportunities selects from a table it does not create
  for r in
    select * from (values
      ('cities','display_name','text'),
      ('cities','icao','text'),
      ('cities','station_name','text'),
      ('cities','timezone','text'),
      ('cities','band_width','numeric'),
      ('markets','city_key','text'),
      ('markets','resolution_date','date'),
      ('markets','unit','text'),
      ('bands','market_id','uuid'),
      ('bands','band_label','text'),
      ('bands','band_lo','numeric'),
      ('bands','band_hi','numeric'),
      ('bands','open_low','boolean default false'),
      ('bands','open_high','boolean default false'),
      ('bands','token_yes','text'),
      ('bands','token_no','text')
    ) as t(tbl, col, def)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if not exists (select 1 from information_schema.columns
                   where table_schema='public' and table_name=r.tbl and column_name=r.col) then
      execute format('alter table public.%I add column %I %s', r.tbl, r.col, r.def);
      raise notice 'guard: added %.%', r.tbl, r.col;
    end if;
  end loop;
end $$;

-- ===========================================================================
-- Task 6 addendum - opportunity ranking.
-- Run after sql/ad4_phase2.sql. Idempotent (create or replace view).
--
--   score_depth_only = edge_net_pp x confidence x ln(1 + fillable_usd_5c)
--   score            = score_depth_only x liquidity_factor
--   liquidity_factor = volume_usd / (volume_usd + k)
--
-- A large edge on an unfillable book must rank below a modest edge with
-- real depth - that is what fillable_usd is for, and it is the whole
-- score_depth_only term. Ranking is NOT by raw edge.
--
-- The liquidity_factor is the second half of the same idea, and the
-- reason it exists: book depth and TRADED VOLUME are different facts. A
-- band can show a fat resting ask that has never once been hit. Depth says
-- what you could fill against the current quote; volume says whether this
-- market trades at all. A saturating factor in [0,1) - vol/(vol+k), from
-- settings.volume_thresholds.liquidity_half_saturation_usd - discounts a
-- market nobody trades and leaves a liquid one essentially untouched. It
-- can only ever pull a score DOWN, never inflate one.
--
-- k is a PROVISIONAL Claude placeholder with NO evidential basis, seeded
-- in sql/ad4_phase2.sql and UI-settable. score_depth_only is kept beside
-- score so the effect of the volume adjustment is always visible rather
-- than baked in invisibly.
-- ===========================================================================

-- drop-then-create, not `create or replace`: safe to re-run regardless of
-- which version of this view (ad4_phase2.sql's narrower one, or this
-- file's own previous run) is currently in place - see the comment above
-- ad4_phase2.sql's view section for why `create or replace` can fail here.
drop view if exists v_opportunities cascade;

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
    as score
from v_latest_edge e
join bands b   on b.band_id = e.band_id
join markets m on m.market_id = b.market_id
join cities c  on c.city_key = m.city_key
cross join (
  select
    coalesce(((select value from settings where key = 'volume_thresholds')->>'liquidity_half_saturation_usd')::numeric, 1) as k,
    coalesce(((select value from settings where key = 'volume_thresholds')->>'thin_band_usd_24h')::numeric, 0) as thin_band_usd
) vt
left join v_latest_book bk on bk.band_id = b.band_id
left join v_band_volume bv on bv.band_id = b.band_id
left join v_city_volume cv on cv.city_key = m.city_key
where m.resolution_date >= current_date
order by score desc nulls last;
