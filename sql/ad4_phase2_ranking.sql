-- ===========================================================================
-- Task 6 addendum - opportunity ranking.
-- Run after sql/ad4_phase2.sql. Idempotent (create or replace view).
--
-- score = edge_net_pp x confidence x log(1 + fillable_usd_5c)
-- A large edge on an unfillable book must rank below a modest edge with
-- real depth - this is the whole reason fillable_usd exists. Ranking is
-- NOT by raw edge.
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
  (e.edge_net_pp * coalesce(e.confidence, 0) * ln(1 + greatest(e.fillable_usd_5c, 0))) as score
from v_latest_edge e
join bands b   on b.band_id = e.band_id
join markets m on m.market_id = b.market_id
join cities c  on c.city_key = m.city_key
left join v_latest_book bk on bk.band_id = b.band_id
where m.resolution_date >= current_date
order by score desc nulls last;
