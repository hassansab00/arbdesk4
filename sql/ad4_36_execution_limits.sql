-- ===========================================================================
-- ad4_36_execution_limits.sql - the size you can actually place.
--
-- THE BUG THIS EXISTS FOR. Every position figure on this desk was computed as
-- if any quantity could be bought at the quoted price. Neither half is true:
--
--   1. THE VENUE HAS A FLOOR. Polymarket refuses an order below its minimum
--      order size. The Goals page priced spreads whose legs came out under a
--      dollar, warned about it in small text, and then went on quoting the
--      profit of a plan that cannot be placed. A warning next to an
--      unplaceable number is not a fix; the number itself has to be the one
--      you can execute.
--
--   2. THE BOOK HAS A DEPTH. market_price is a MID. You buy at the ask, and
--      you buy DOWN THE LADDER: the second hundred dollars fills worse than
--      the first, and past the end of the book it does not fill at all. Every
--      "if right / EV" figure computed off a single price is the best case of
--      the first share, quoted as though it were the whole ticket.
--
-- WHERE THE NUMBERS COME FROM, and what is honestly still a placeholder.
-- Polymarket publishes `minimum_order_size` and `minimum_tick_size` PER MARKET
-- on its CLOB /markets response. This desk does not capture them yet - P0.2
-- would have to store them on `markets`. Until it does, these are venue-wide
-- defaults, and every one of them is marked provisional so nothing on screen
-- can present a placeholder as a measurement.
--
-- Run order: any time after sql/ad4_00_preflight.sql. Re-runnable.
-- ===========================================================================

insert into settings (key, value)
values ('execution_limits', jsonb_build_object(
  '_doc', 'What the venue will actually accept. Replace the provisional values with the per-market minimum_order_size / minimum_tick_size from the Polymarket CLOB once P0.2 stores them on markets.',
  'min_order_usd',      1.0,
  'share_step',         0.01,
  'price_tick',         0.01,
  'max_book_fraction',  0.5,
  'provisional',        true,
  '_notes', jsonb_build_object(
    'min_order_usd',     'Polymarket rejects an order below this notional. A leg under it is not a small trade, it is no trade.',
    'share_step',        'Order quantities are rounded to this many shares.',
    'price_tick',        'Limit prices snap to this. 0.01 is the common case; some markets quote finer.',
    'max_book_fraction', 'The most of a level the desk will assume it can take without moving the price against itself. Provisional and deliberately conservative: taking the whole visible ladder is a modelling assumption, not an execution.'
  )
))
-- Re-running must never reset a limit the operator has tuned. Start from the
-- shipped defaults and documentation, then overlay whatever is already stored
-- (minus the docs, which should follow this file): new keys appear, existing
-- values survive.
on conflict (key) do update
set value = excluded.value || (settings.value - '_doc' - '_notes');


-- --------------------------------------------------------------------------
-- Read it in one place, with the same fallbacks the browser uses.
-- --------------------------------------------------------------------------
create or replace view v_execution_limits as
select
  coalesce((value ->> 'min_order_usd')::numeric,     1.0)  as min_order_usd,
  coalesce((value ->> 'share_step')::numeric,        0.01) as share_step,
  coalesce((value ->> 'price_tick')::numeric,        0.01) as price_tick,
  coalesce((value ->> 'max_book_fraction')::numeric, 0.5)  as max_book_fraction,
  coalesce((value ->> 'provisional')::boolean,       true) as provisional
from settings where key = 'execution_limits';

comment on view v_execution_limits is
  'What the venue will accept. Provisional venue-wide defaults until P0.2 stores Polymarket''s per-market minimum_order_size and minimum_tick_size on markets.';


-- --------------------------------------------------------------------------
-- Somewhere to put them when P0.2 does start capturing them. Added now so the
-- UI can prefer a per-market value the moment one exists, without a schema
-- change and a second round of UI edits.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.markets') is not null then
    alter table markets add column if not exists min_order_size numeric;
    alter table markets add column if not exists min_tick_size  numeric;
  end if;
end
$ad4$;

comment on column markets.min_order_size is
  'Polymarket CLOB minimum_order_size for this market. Null means fall back to settings.execution_limits.';
comment on column markets.min_tick_size is
  'Polymarket CLOB minimum_tick_size for this market. Null means fall back to settings.execution_limits.';


do $ad4$
declare r record; v_known int;
begin
  select * into r from v_execution_limits;
  select count(*) into v_known from markets where min_order_size is not null;
  raise notice 'ad4_36: min order $%, share step %, price tick %, at most % percent of a level',
    r.min_order_usd, r.share_step, r.price_tick, round(r.max_book_fraction * 100);
  if r.provisional then
    raise notice 'ad4_36: these are VENUE-WIDE PROVISIONAL defaults. % market(s) carry their own.', v_known;
  end if;
end
$ad4$;


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_execution_limits to %I', r);
    end if;
  end loop;
end
$ad4$;
