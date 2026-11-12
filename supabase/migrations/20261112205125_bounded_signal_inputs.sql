-- Strategies consume stored pricing outputs, not UI depth and volume work.
create or replace view public.v_signal_inputs as
with eligible as materialized (
  select b.band_id, b.band_label, b.band_lo, b.band_hi, b.open_low,
         b.open_high, b.token_yes, b.token_no, m.city_key,
         m.resolution_date, m.unit
  from public.v_canonical_bands b
  join public.v_canonical_markets m on m.market_id = b.market_id
  join public.cities c on c.city_key = m.city_key
    and coalesce(c.status, 'active') = 'active'
  where m.resolution_date >= (now() at time zone coalesce(c.timezone, 'UTC'))::date
)
select b.*, e.side, e.model_prob, e.market_price, e.edge_net_pp,
       e.tradeable, e.block_reason, e.confidence, e.regime_label,
       e.fillable_usd_5c, bk.market_state, bk.spread,
       e.edge_id, e.computed_at as edge_computed_at,
       e.prob_id as edge_prob_id, e.book_snapshot_id as edge_book_snapshot_id
from eligible b
join lateral (
  select distinct on (x.side) x.* from public.edges x
  where x.band_id = b.band_id order by x.side, x.computed_at desc
) e on true
left join lateral (
  select x.market_state, x.spread from public.book_snapshots x
  where x.band_id = b.band_id order by x.observed_at desc limit 1
) bk on true;
revoke all on public.v_signal_inputs from public, anon, authenticated;
grant select on public.v_signal_inputs to service_role;
comment on view public.v_signal_inputs is
  'Service-only live signal inputs with per-side stored pricing receipts; excludes UI depth and volume aggregation.';
notify pgrst, 'reload schema';
