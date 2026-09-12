-- AD4 53 - give derived_city_day_volume the same shape as its sibling.
--
-- WHY THIS EXISTS
--
-- Two tables hold the same rollup at two grains:
--
--     derived_band_day_volume (band_id, city_key, trade_date, volume_usd,
--                              n_trades, computed_at)
--       primary key (band_id, trade_date)
--
--     derived_city_day_volume (city_key, for_date,   volume_usd,
--                              n_trades, computed_at)
--       primary key (city_key, for_date, computed_at)      <-- both bugs
--
-- They disagree on the name of the date column, and only one of them has a
-- key that means anything.
--
-- BUG 1 - THE COLUMN NAME. refresh_derived() writes `trade_date` to both,
-- because every one of the seven places in this repo that reads these tables
-- calls it `trade_date`. Against the city table that is simply a missing
-- column, so the whole function aborted:
--
--     42703  column "trade_date" of relation "derived_city_day_volume"
--            does not exist
--
-- and NEITHER rollup was ever refreshed - the band-day insert is in the same
-- function, after the city-day one. Every band and every city read $0 of
-- traded volume no matter how much had been ingested.
--
-- BUG 2 - THE KEY. computed_at is part of the primary key, so
-- `on conflict (city_key, trade_date) do update` could never match it and
-- every refresh INSERTED a new row instead of updating the existing one. The
-- table held 2,252 rows for 490 distinct (city_key, date) pairs - a 4.6x
-- duplication - so anything summing it without first picking the latest
-- computed_at was over-reporting city volume by that factor.
--
-- THE FIX: rename the column, collapse the duplicates, and key the table on
-- what actually identifies a row.
--
-- Idempotent: safe to run more than once.

begin;

-- 1. Collapse the duplicates, keeping the most recently computed row for each
--    (city_key, date). Done BEFORE the key changes, or the new key cannot be
--    created.
delete from public.derived_city_day_volume a
 using public.derived_city_day_volume b
 where a.city_key = b.city_key
   and a.for_date = b.for_date
   and (a.computed_at < b.computed_at
        or (a.computed_at = b.computed_at and a.ctid < b.ctid));

-- 2. computed_at is when we last did the arithmetic, not part of what the row
--    identifies.
alter table public.derived_city_day_volume
  drop constraint if exists derived_city_day_volume_pkey;

-- 3. The name every reader already uses. v_data_freshness is the only view on
--    this table and it touches computed_at and count(*) only, so the rename
--    does not disturb it.
alter table public.derived_city_day_volume
  rename column for_date to trade_date;

alter table public.derived_city_day_volume
  add constraint derived_city_day_volume_pkey primary key (city_key, trade_date);

commit;

-- Both tables should now report the same shape.
select table_name,
       string_agg(column_name, ', ' order by ordinal_position) as columns
  from information_schema.columns
 where table_name in ('derived_city_day_volume', 'derived_band_day_volume')
 group by table_name;
