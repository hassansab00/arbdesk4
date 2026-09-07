-- ===========================================================================
-- ad4_48_trade_city_key.sql - EVERY CITY VOLUME ON THE DESK READ $0.
--
-- Safe to run any time. Fills a column that was null; destroys nothing.
--
--
-- WHAT WAS WRONG
--
-- trades_observed.city_key was NULL on every one of 127,585 rows, and
-- v_city_volume is:
--
--     select t.city_key, sum(t.price * t.size) ...
--       from trades_observed t
--      where t.city_key is not null
--      group by t.city_key
--
-- so it returned ZERO ROWS. Not an error, not a warning - an empty result,
-- which every consumer renders as "no trading":
--
--     the Board's city volume column
--     the Globe's 24h volume, and the dot size that encodes it
--     City Clusters' volume ranking
--     the thin-market flag at city level
--     the Goals feasibility read, which sizes against city volume
--
-- All of them showed $0, and none of them could tell that apart from a quiet
-- market. On this desk the hidden figure was $238,135 across 41 cities.
--
-- WHY IT HAPPENED. n8n P0.4 loads its bands with
--
--     bands?select=band_id,token_yes,token_no,condition_id,market_id
--
-- and then writes `city_key: band.city_key ?? null`. `bands` HAS NO
-- city_key - the city is on `markets` - so that expression was undefined on
-- every row and every trade landed with a null city. The workflow is fixed to
-- embed markets(city_key), which PostgREST can do because the foreign key
-- exists.
--
-- BUT THE COLUMN MUST NOT DEPEND ON THE WRITER REMEMBERING. An ingest that
-- forgets it produces a desk that silently reports no volume, which is worse
-- than one that fails. So the trigger below fills it from band_id whatever
-- the writer sends, and P0.4's fix becomes a belt to that brace.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Fill in what is already there. band -> market -> city is deterministic,
--    so nothing here is a guess.
-- --------------------------------------------------------------------------
do $ad4$
declare v_n bigint;
begin
  if to_regclass('public.trades_observed') is null then
    raise notice 'ad4_48: no trades_observed here - nothing to do';
    return;
  end if;

  update trades_observed t
     set city_key = m.city_key
    from bands b
    join markets m on m.market_id = b.market_id
   where b.band_id = t.band_id
     and t.city_key is null;
  get diagnostics v_n = row_count;

  if v_n > 0 then
    raise notice 'ad4_48: filled city_key on % trade(s) that had none', v_n;
  else
    raise notice 'ad4_48: every trade already carries its city';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. Keep it filled, whatever writes the row.
-- --------------------------------------------------------------------------
create or replace function ad4_fill_trade_city_key()
returns trigger language plpgsql as $ad4$
begin
  if new.city_key is null and new.band_id is not null then
    select m.city_key into new.city_key
      from bands b join markets m on m.market_id = b.market_id
     where b.band_id = new.band_id;
  end if;
  return new;
end
$ad4$;

drop trigger if exists trg_fill_trade_city_key on trades_observed;
create trigger trg_fill_trade_city_key
  before insert or update of band_id, city_key on trades_observed
  for each row execute function ad4_fill_trade_city_key();

analyze trades_observed;


-- --------------------------------------------------------------------------
-- 3. Say whether it worked, in the terms the desk cares about.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_null   bigint;
  v_cities int;
  v_usd    numeric;
begin
  select count(*) into v_null from trades_observed where city_key is null;
  select count(*), coalesce(sum(volume_usd), 0) into v_cities, v_usd
    from (select city_key, sum(price * size) as volume_usd
            from trades_observed
           where city_key is not null
             and coalesce(traded_at, observed_at, ingested_at) >= now() - interval '10 days'
           group by city_key) z;

  raise notice 'ad4_48: % trade(s) still without a city', v_null;
  raise notice 'ad4_48: % city/cities carry volume over the last 10 days ($%)',
               v_cities, round(v_usd);
  if v_cities = 0 then
    raise notice 'ad4_48: no volume in 10 days means the TAPE is stale, not this fix - run n8n P0.4.';
  end if;
end
$ad4$;
