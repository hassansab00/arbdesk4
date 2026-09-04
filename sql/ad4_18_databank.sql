-- ===========================================================================
-- ad4_18_databank.sql - the proprietary record.
--
-- Everything AD4 currently knows is either LIVE or DERIVED, and both are
-- destroyed by their own next run:
--
--   weather_forecasts keeps every run, but the FORECAST THAT WAS ACTED ON is
--     never marked, so "what did we believe at the moment we priced this" is
--     unrecoverable a day later.
--   band_probabilities is append-only but nothing joins it to what actually
--     happened, so the desk has never once measured whether a 30% band settled
--     30% of the time.
--   edges is recomputed in place. The edge that a signal fired on is gone.
--   book_snapshots is kept, but the price AT THE MOMENT OF A DECISION is not
--     picked out of it, so slippage between decision and fill is unmeasurable.
--
-- A trading desk's only durable asset is its own history of predictions
-- against outcomes. That is the thing nobody else has and the thing every
-- improvement is measured against - and AD4 has been throwing it away.
--
-- This file adds an IMMUTABLE record. Facts are written once, after the
-- outcome is known, and never updated. Rows here are not a cache of the
-- live tables: they are the evidence, and they must survive any later
-- rebuild of the views above.
--
-- Run order: after sql/ad4_17_city_stats.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Forecast against outcome. One row per (city, date, model, lead).
--
--    This is the training set for everything: bias, error distribution, how
--    skill decays with lead time, and whether a second model helps.
-- --------------------------------------------------------------------------
create table if not exists fact_forecast_outcome (
  city_key        text        not null,
  for_date        date        not null,
  model           text        not null,
  lead_days       int         not null,
  run_at          timestamptz,
  forecast_max_c  numeric     not null,
  observed_max_c  numeric     not null,
  -- forecast minus observed: positive means the forecast ran hot
  error_c         numeric     generated always as (forecast_max_c - observed_max_c) stored,
  abs_error_c     numeric     generated always as (abs(forecast_max_c - observed_max_c)) stored,
  obs_source      text,
  n_obs           int,
  captured_at     timestamptz not null default now(),
  primary key (city_key, for_date, model, lead_days)
);

comment on table fact_forecast_outcome is
  'IMMUTABLE. What each model said, at each lead, against what happened. Written once by scripts/databank.py after a day settles.';


-- --------------------------------------------------------------------------
-- 2. What the desk believed about each band, against what the band did.
--
--    The calibration question lives here and nowhere else: of every band this
--    desk priced at 30%, how many settled yes? A desk that cannot answer that
--    is guessing about its own accuracy.
-- --------------------------------------------------------------------------
create table if not exists fact_band_outcome (
  band_id           uuid        not null,
  city_key          text        not null,
  for_date          date        not null,
  band_lo           numeric,
  band_hi           numeric,
  open_low          boolean,
  open_high         boolean,
  -- what we thought
  model_prob        numeric,
  sigma_c           numeric,
  confidence        numeric,
  regime_label      text,
  forecast_max_c    numeric,
  -- what the market thought, at the same moment
  market_price      numeric,
  edge_net_pp       numeric,
  volume_usd        numeric,
  depth_5c          numeric,
  priced_at         timestamptz,
  -- what happened
  observed_max_c    numeric,
  settled_yes       boolean     not null,
  captured_at       timestamptz not null default now(),
  primary key (band_id)
);

comment on table fact_band_outcome is
  'IMMUTABLE. Model probability and market price for one band, beside whether it actually settled yes. The calibration and edge-realisation evidence.';


-- --------------------------------------------------------------------------
-- 3. Signal -> trade -> settlement. Did acting on it make money?
--
--    Separate from fact_band_outcome on purpose: a band can be right while a
--    signal on it loses, because entry price, fees and fill size all sit
--    between being right and being paid.
-- --------------------------------------------------------------------------
create table if not exists fact_signal_outcome (
  signal_id       bigint      not null,
  strategy_id     text,
  band_id         uuid,
  city_key        text,
  for_date        date,
  side            text,
  action          text,
  reason          text,
  fired_at        timestamptz,
  severity        text,
  -- what it looked like when it fired
  price_at_fire   numeric,
  prob_at_fire    numeric,
  edge_at_fire    numeric,
  -- what happened to it
  status          text,
  filled          boolean,
  fill_price      numeric,
  shares          numeric,
  settled_yes     boolean,
  gross_pnl       numeric,
  net_pnl         numeric,
  -- the gap between the price that justified the signal and the price paid
  slippage_c      numeric,
  captured_at     timestamptz not null default now(),
  primary key (signal_id)
);

comment on table fact_signal_outcome is
  'IMMUTABLE. Every signal, and whether acting on it paid. Keeps entry slippage, which is where a correct call turns into a loss.';


-- --------------------------------------------------------------------------
-- 4. Indexes for the questions actually asked of these tables.
-- --------------------------------------------------------------------------
create index if not exists fact_fc_city_lead on fact_forecast_outcome (city_key, lead_days, for_date desc);
create index if not exists fact_fc_model     on fact_forecast_outcome (model, lead_days);
create index if not exists fact_band_city    on fact_band_outcome (city_key, for_date desc);
create index if not exists fact_band_prob    on fact_band_outcome (model_prob) where model_prob is not null;
create index if not exists fact_sig_strategy on fact_signal_outcome (strategy_id, fired_at desc);


-- --------------------------------------------------------------------------
-- 5. Calibration: of the bands we priced at p, how many settled yes?
--
--    Ten buckets of predicted probability. `observed` should track
--    `predicted`; where it does not, the model is systematically over- or
--    under-confident at that probability, and by exactly how much.
--
--    n is reported because a bucket with four rows in it means nothing, and
--    a calibration curve read off thin buckets is worse than none at all.
-- --------------------------------------------------------------------------
create or replace view v_calibration as
select
  width_bucket(model_prob, 0, 1, 10)                as bucket,
  round((width_bucket(model_prob, 0, 1, 10) - 0.5) / 10.0, 3)::numeric  as predicted_mid,
  count(*)::int                                     as n,
  round(avg(model_prob), 4)                         as predicted,
  round(avg(case when settled_yes then 1.0 else 0.0 end), 4) as observed,
  round(avg(case when settled_yes then 1.0 else 0.0 end) - avg(model_prob), 4) as gap,
  round(avg(market_price), 4)                       as market_avg,
  -- did the market or the model know better, in this bucket?
  round(avg(abs(model_prob - case when settled_yes then 1 else 0 end)), 4)   as model_mae,
  round(avg(abs(market_price - case when settled_yes then 1 else 0 end)), 4) as market_mae
from fact_band_outcome
where model_prob is not null
group by 1, 2
order by 1;


-- --------------------------------------------------------------------------
-- 6. Edge realisation: when we claimed an edge, did it pay?
--
--    The single most important question a desk can ask itself. Bands are
--    bucketed by the edge claimed at pricing time; `realised_pp` is what the
--    outcome actually delivered against the price paid. A desk whose claimed
--    edge does not appear in the realised column has an edge in a spreadsheet
--    and nowhere else.
-- --------------------------------------------------------------------------
create or replace view v_edge_realisation as
with b as (
  select
    case
      when edge_net_pp <  -0.05 then '< -5pp'
      when edge_net_pp <  -0.01 then '-5 to -1pp'
      when edge_net_pp <   0.01 then 'flat'
      when edge_net_pp <   0.05 then '+1 to +5pp'
      when edge_net_pp <   0.10 then '+5 to +10pp'
      else '> +10pp'
    end                                     as edge_bucket,
    case
      when edge_net_pp <  -0.05 then 1 when edge_net_pp < -0.01 then 2
      when edge_net_pp <   0.01 then 3 when edge_net_pp <  0.05 then 4
      when edge_net_pp <   0.10 then 5 else 6 end as ord,
    settled_yes, model_prob, market_price, edge_net_pp, volume_usd
  from fact_band_outcome
  where edge_net_pp is not null and market_price is not null
)
select
  edge_bucket,
  min(ord)                                                     as ord,
  count(*)::int                                                as n,
  round(avg(edge_net_pp) * 100, 2)                             as claimed_pp,
  -- buying YES at market_price, one dollar back if it settles yes
  round(avg(case when settled_yes then 1 - market_price else -market_price end) * 100, 2)
                                                               as realised_pp,
  round(avg(case when settled_yes then 1.0 else 0.0 end), 4)   as hit_rate,
  round(avg(market_price), 4)                                  as avg_price,
  round(sum(coalesce(volume_usd, 0)))                          as volume_usd
from b
group by edge_bucket
order by min(ord);


-- --------------------------------------------------------------------------
-- 7. Coverage. How much proprietary data exists yet, and from when.
-- --------------------------------------------------------------------------
create or replace view v_databank_coverage as
select 'forecast outcomes' as dataset, count(*)::bigint as rows,
       min(for_date)::text as since, max(for_date)::text as latest,
       count(distinct city_key)::int as cities
  from fact_forecast_outcome
union all
select 'band outcomes', count(*), min(for_date)::text, max(for_date)::text, count(distinct city_key)
  from fact_band_outcome
union all
select 'signal outcomes', count(*), min(for_date)::text, max(for_date)::text, count(distinct city_key)
  from fact_signal_outcome;


-- --------------------------------------------------------------------------
-- 8. Grants. Read-only to the browser; the collector writes as service_role.
-- --------------------------------------------------------------------------
do $ad4$
declare
  o text;
  r text;
begin
  foreach o in array array['fact_forecast_outcome', 'fact_band_outcome', 'fact_signal_outcome',
                            'v_calibration', 'v_edge_realisation', 'v_databank_coverage'] loop
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on %I from %I', o, r);
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant all on %I to service_role', o);
    end if;
  end loop;
end
$ad4$;

-- The fact tables are append-only evidence. RLS keeps the browser to SELECT
-- even if a future GRANT is careless.
do $ad4$
declare t text;
begin
  foreach t in array array['fact_forecast_outcome', 'fact_band_outcome', 'fact_signal_outcome'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists %I on %I', t || '_read', t);
    execute format('create policy %I on %I for select using (true)', t || '_read', t);
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 8b. The calibration map is a setting, so let the UI show and clear it.
-- --------------------------------------------------------------------------
do $ad4$
begin
  -- Guarded like every other file here: this one can be run against a database
  -- that has not seen ad4_14 yet, and a hard reference would take the whole
  -- script down over an optional nicety.
  if to_regclass('public.settings') is null then
    raise notice 'ad4_18: settings absent - skipped registering calibration_map';
    return;
  end if;
  update settings
     set value = value || '["calibration_map"]'::jsonb
   where key = 'ui_editable_keys'
     and jsonb_typeof(value) = 'array'
     and not (value @> '["calibration_map"]'::jsonb);
end
$ad4$;


-- --------------------------------------------------------------------------
-- 9. Report.
-- --------------------------------------------------------------------------
do $ad4$
declare r record;
begin
  for r in select * from v_databank_coverage loop
    raise notice 'ad4_18: % - % row(s)%', r.dataset, r.rows,
      case when r.rows > 0 then format(', %s to %s, %s cities', r.since, r.latest, r.cities) else '' end;
  end loop;
  raise notice 'ad4_18: the bank fills from scripts/databank.py - Actions -> Data Bank, daily.';
  raise notice 'ad4_18: calibration needs a few hundred settled bands before v_calibration means anything.';
end
$ad4$;
