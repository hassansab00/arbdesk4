-- ===========================================================================
-- THE CARD SHOWS WHAT WAS PRICED (plan v2.3 P4.8)
--
-- 1. THE CENTRE ON THE CARD WAS NOT THE CENTRE THAT WAS PRICED. The Predictive
--    city card showed band_probabilities.forecast_max_c as "Forecast centre ...
--    after bias correction". That column is the public forecast the engine
--    STARTED from; the centre the ladder was integrated on is centre_c, after
--    the bias, the station correction (P3.9), the station-model blend (P2.9)
--    or the day's trajectory. Measured 27 Sep on the 12:36Z pricing of 28 Sep:
--        wuhan   forecast_max_c 28.9   centre_c 26.005
--        paris   forecast_max_c 23.9   centre_c 25.640
--        london  forecast_max_c 19.6   centre_c 19.058
--    The ladder views never exposed centre_c, so the card could not show it.
--
-- 2. THE CARD DID NOT SAY WHEN ITS PICK WAS PRICED. Its "priced" time was the
--    newest EDGE, and the probabilities are written every four hours (00:36,
--    04:36 ... 20:36Z), while the station is read hourly. Measured 27 Sep over
--    the same-day checkpoints of 24-27 Sep (noon, prepeak_2h, prepeak_1h,
--    postpeak_1h; 488 instants): at 68 of them the newest pricing's top bucket
--    got under 1% in the fresh ladder the tick computed at that moment, 38 of
--    123 at postpeak_1h; the card's pricing was on average 139-156 minutes old.
--    The card now receives the pricing's own time and the day's maximum it
--    already counted (observed_floor_c), and marks a pick the station has
--    since passed (web/lib/cityCards.ts pickStanding, the engine's P3.1 rule).
--
-- So v_prediction_ladder_bands, v_prediction_ladder_live, the stored copy and
-- the v_prediction_ladder wrapper gain, from the SAME band_probabilities row
-- as model_prob (never a second read that could belong to another run):
--     centre_c, forecast_sigma_c, observed_floor_c,
--     prob_at      band_probabilities.computed_at
--     priced_from  model_versions.label of its forecast_version (the path)
-- APPENDED: create or replace view keeps every existing column's position,
-- name and type, so v_city_prediction_confidence and the page keep working.
-- The stored copy cannot gain columns in place, so it is rebuilt under its own
-- name (refresh_page_cache() refreshes it by that name) while the wrapper reads
-- the live view, all inside the migration's one transaction.
--
-- 3. v_city_prediction_confidence picked its "modal band" among CLOSED buckets
--    only (band_lo and band_hi not null), from calibrated_prob, ties by
--    band_lo. On 27 Sep 15:4xZ 6 of 92 open city-days had an open tail as the
--    card's pick, and on exactly those 6 this view named another bucket. It
--    now picks among every bucket, from model_prob, ties by band_id - the rule
--    the card (a stable sort over band_id order) and the tick
--    (sorted by (-p, band_id)) already share. Its expected_max_c is the priced
--    centre (centre_c); the raw forecast is appended as raw_forecast_max_c.
--    No page reads it today; it is fixed so no selector disagrees.
--
-- Re-runnable, and a no-op where the ladder is not installed (the migration
-- harness builds it only from sql/, which it does not apply).
-- ===========================================================================

do $migration$
declare
  bands regclass := to_regclass('public.v_prediction_ladder_bands');
  live  regclass := to_regclass('public.v_prediction_ladder_live');
  mv    regclass := to_regclass('public.mv_prediction_ladder');
  wrap  regclass := to_regclass('public.v_prediction_ladder');
begin
  if bands is null or live is null or mv is null or wrap is null then
    raise notice 'P4.8: the prediction ladder is not installed here (sql/ad4_68, sql/ad4_89); nothing to do';
    return;
  end if;
  if exists (select 1 from pg_attribute where attrelid = mv and attname = 'priced_from' and not attisdropped) then
    raise notice 'P4.8: the stored ladder already carries priced_from; nothing to do';
    return;
  end if;

  execute $v$
    create or replace view public.v_prediction_ladder_bands as
    select
      m.city_key,
      m.resolution_date as for_date,
      m.market_id,
      b.band_id,
      b.band_index,
      b.band_label,
      b.band_lo,
      b.band_hi,
      b.open_low,
      b.open_high,
      m.closed,
      fb.observed_max_c as settled_value,
      coalesce(
        case when vb.resolution_state = 'confirmed' then vb.settled_yes end,
        fb.settled_yes
      ) as won,
      p.raw_prob,
      p.calibrated_prob,
      coalesce(p.calibrated_prob, p.raw_prob) as model_prob,
      p.forecast_max_c,
      p.sigma_c,
      p.confidence,
      p.regime_label,
      case
        when vb.resolution_state = 'confirmed' and vb.settled_yes is not null then 'venue'
        when fb.settled_yes is not null then 'weather'
      end as outcome_source,
      coalesce(
        case when vb.resolution_state = 'confirmed' then vb.confirmed_at end,
        fb.captured_at
      ) as settled_at,
      p.centre_c,
      p.forecast_sigma_c,
      p.observed_floor_c,
      p.computed_at as prob_at,
      (select mv.label from public.model_versions mv where mv.version_id = p.forecast_version) as priced_from
    from v_canonical_markets m
    join cities ct on ct.city_key = m.city_key
                  and coalesce(ct.status, 'active') = 'active'
    join v_canonical_bands b on b.market_id = m.market_id
    left join lateral (
      select bp.raw_prob, bp.calibrated_prob, bp.forecast_max_c, bp.sigma_c,
             bp.confidence, bp.regime_label,
             bp.centre_c, bp.forecast_sigma_c, bp.observed_floor_c, bp.computed_at, bp.forecast_version
        from band_probabilities bp
       where bp.band_id = b.band_id
       order by bp.computed_at desc, bp.prob_id desc
       limit 1
    ) p on true
    left join mv_venue_band_resolution vb on vb.band_id = b.band_id
    left join v_fact_band_outcome_clean fb on fb.band_id = b.band_id
    where m.resolution_date >= (current_date - 45)
      and m.resolution_date <= (current_date + 16)
  $v$;

  execute $v$
    create or replace view public.v_prediction_ladder_live as
    select
      lb.city_key,
      lb.for_date,
      lb.market_id,
      lb.band_id,
      lb.band_index,
      lb.band_label,
      lb.band_lo,
      lb.band_hi,
      lb.open_low,
      lb.open_high,
      lb.closed,
      lb.settled_value,
      lb.won,
      lb.raw_prob,
      lb.calibrated_prob,
      lb.model_prob,
      lb.forecast_max_c,
      lb.sigma_c,
      lb.confidence,
      lb.regime_label,
      e.side,
      e.market_price,
      e.edge_pp,
      e.edge_net_pp,
      e.fillable_usd_5c as depth_5c,
      e.tradeable,
      e.block_reason,
      e.computed_at as edge_at,
      lb.outcome_source,
      lb.settled_at,
      lb.centre_c,
      lb.forecast_sigma_c,
      lb.observed_floor_c,
      lb.prob_at,
      lb.priced_from
    from v_prediction_ladder_bands lb
    left join lateral (
      select distinct on (x.side)
             x.side, x.market_price, x.edge_pp, x.edge_net_pp, x.fillable_usd_5c,
             x.tradeable, x.block_reason, x.computed_at
        from edges x
       where x.band_id = lb.band_id
       order by x.side, x.computed_at desc
    ) e on true
  $v$;

  -- The stored copy is rebuilt under its own name. For the length of this
  -- transaction the wrapper reads the live view (same 30 columns, same types,
  -- the 5 new ones appended), so the old copy has no dependant and can go; no
  -- reader sees the step between, because the migration is one transaction.
  execute $v$
    create or replace view public.v_prediction_ladder as
    select city_key, for_date, market_id, band_id, band_index, band_label, band_lo, band_hi,
           open_low, open_high, closed, settled_value, won, raw_prob, calibrated_prob, model_prob,
           forecast_max_c, sigma_c, confidence, regime_label, side, market_price, edge_pp,
           edge_net_pp, depth_5c, tradeable, block_reason, edge_at, outcome_source, settled_at,
           centre_c, forecast_sigma_c, observed_floor_c, prob_at, priced_from
      from public.v_prediction_ladder_live
  $v$;
  drop materialized view public.mv_prediction_ladder;
  execute $v$
    create materialized view public.mv_prediction_ladder as
    select v.*, coalesce(v.side, '-') as cache_side_key from public.v_prediction_ladder_live v
  $v$;
  create unique index mv_prediction_ladder_key on public.mv_prediction_ladder (band_id, cache_side_key);
  revoke all on public.mv_prediction_ladder from public, anon, authenticated;
  grant select on public.mv_prediction_ladder to service_role;

  -- The wrapper reads the stored copy again: its 30 columns unchanged, 5 appended.
  execute $v$
    create or replace view public.v_prediction_ladder as
    select city_key, for_date, market_id, band_id, band_index, band_label, band_lo, band_hi,
           open_low, open_high, closed, settled_value, won, raw_prob, calibrated_prob, model_prob,
           forecast_max_c, sigma_c, confidence, regime_label, side, market_price, edge_pp,
           edge_net_pp, depth_5c, tradeable, block_reason, edge_at, outcome_source, settled_at,
           centre_c, forecast_sigma_c, observed_floor_c, prob_at, priced_from
      from public.mv_prediction_ladder
  $v$;

  comment on materialized view public.mv_prediction_ladder is
    'Stored rows of v_prediction_ladder_live, refreshed by refresh_page_cache() (plan v2 P6.5). The page reads v_prediction_ladder, which selects from here.';
  comment on view public.v_prediction_ladder_bands is
    'One row per band the desk priced in the last 45 days and the next 16: the latest probability and how the band settled. centre_c, prob_at and priced_from come from the same band_probabilities row as model_prob (plan v2.3 P4.8). v_prediction_ladder is this plus the latest edge per side.';
end
$migration$;

-- ---------------------------------------------------------------------------
-- 3. One rule for the most likely bucket.
-- ---------------------------------------------------------------------------
do $confidence$
begin
  if to_regclass('public.v_city_prediction_confidence') is null
     or to_regclass('public.derived_forecast_skill') is null
     or not exists (select 1 from pg_attribute
                     where attrelid = to_regclass('public.v_prediction_ladder')
                       and attname = 'centre_c' and not attisdropped) then
    raise notice 'P4.8: v_city_prediction_confidence or its inputs are not installed here; nothing to do';
    return;
  end if;
  execute $v$
    create or replace view public.v_city_prediction_confidence as
    with skill as (
      select distinct on (city_key, lead_days)
             city_key, lead_days, n_days, mae_c, bias_c, p90_abs_err_c, pct_within_one_band, band_width_c
        from derived_forecast_skill
       order by city_key, lead_days, computed_at desc
    ), top_band as (
      -- EVERY bucket is a candidate, the open tails too, on the probability the
      -- card reads (model_prob); ties go to the lower band_id, as in the card
      -- and in tick.py.
      select distinct on (city_key, for_date)
             city_key, for_date, band_id, band_label, band_lo, band_hi, model_prob,
             centre_c, forecast_max_c, sigma_c, confidence, regime_label, market_price, edge_net_pp,
             tradeable, block_reason
        from v_prediction_ladder
       where model_prob is not null and for_date >= current_date and side = 'YES'
       order by city_key, for_date, model_prob desc, band_id
    ), spread as (
      select city_key, for_date,
             count(*)::integer as bands_priced,
             round(sum(model_prob), 3) as prob_mass,
             count(*) filter (where tradeable)::integer as tradeable_bands,
             round(100::numeric * sum(model_prob) filter (where band_lo is null), 1) as tail_low_pct,
             round(100::numeric * sum(model_prob) filter (where band_hi is null), 1) as tail_high_pct
        from v_prediction_ladder
       where model_prob is not null and for_date >= current_date and side = 'YES'
       group by city_key, for_date
    ), centre as (
      -- The bucket holding the PRICED centre, tails included.
      select distinct on (l.city_key, l.for_date)
             l.city_key, l.for_date, l.band_label as centre_band
        from v_prediction_ladder l
        join cities ct on ct.city_key = l.city_key
        cross join lateral (select case when ct.unit = 'F' then l.centre_c * 9.0 / 5.0 + 32 else l.centre_c end as x) c
       where l.for_date >= current_date and l.side = 'YES' and l.centre_c is not null
         and ((l.open_low and c.x < l.band_hi)
              or (l.open_high and c.x >= l.band_lo)
              or (l.band_lo is not null and l.band_hi is not null and c.x >= l.band_lo and c.x < l.band_hi))
       order by l.city_key, l.for_date, l.band_lo nulls first
    )
    select t.city_key,
           c.display_name,
           t.for_date,
           t.for_date - current_date as lead_days,
           round(t.centre_c, 1) as expected_max_c,
           round(case when c.unit = 'F' then t.centre_c * 9.0 / 5.0 + 32 else t.centre_c end, 1) as expected_max_display,
           coalesce(c.unit, 'C') as display_unit,
           t.band_label as modal_band,
           ce.centre_band,
           ce.centre_band is distinct from t.band_label as skewed,
           t.band_lo,
           t.band_hi,
           round(100::numeric * t.model_prob, 1) as modal_pct,
           round(100::numeric * t.market_price, 1) as market_pct,
           round(t.edge_net_pp, 1) as edge_net_pp,
           t.tradeable,
           t.block_reason,
           round(t.sigma_c, 2) as stated_sigma_c,
           t.confidence,
           t.regime_label,
           s.n_days as skill_n_days,
           round(s.mae_c, 2) as measured_mae_c,
           round(s.bias_c, 2) as measured_bias_c,
           round(s.p90_abs_err_c, 2) as measured_p90_err_c,
           s.pct_within_one_band,
           sp.bands_priced,
           sp.tradeable_bands,
           sp.tail_low_pct,
           sp.tail_high_pct,
           case
             when s.n_days is null then format('No measured accuracy at %s day(s) ahead yet - prediction only.', t.for_date - current_date)
             when t.sigma_c is null then format('Typically %s C out at this lead over %s day(s); the model states no sigma.', round(s.mae_c, 1), s.n_days)
             when s.p90_abs_err_c > 2.0 * t.sigma_c then format('OVERCONFIDENT: states +/-%s C, but 1 day in 10 misses by %s C or more (%s day(s) measured). Edges from this are overstated.', round(t.sigma_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
             when s.p90_abs_err_c < 0.75 * t.sigma_c then format('Cautious: states +/-%s C, measured worst tenth is %s C over %s day(s). Edges from this are understated.', round(t.sigma_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
             else format('States +/-%s C, measured %s C typical and %s C in the worst tenth over %s day(s) - consistent.', round(t.sigma_c, 1), round(s.mae_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
           end as honesty,
           round(t.forecast_max_c, 1) as raw_forecast_max_c
      from top_band t
      left join skill s on s.city_key = t.city_key and s.lead_days = t.for_date - current_date
      left join spread sp on sp.city_key = t.city_key and sp.for_date = t.for_date
      left join centre ce on ce.city_key = t.city_key and ce.for_date = t.for_date
      left join cities c on c.city_key = t.city_key
     order by t.city_key, t.for_date
  $v$;
end
$confidence$;
