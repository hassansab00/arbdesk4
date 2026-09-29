-- ===========================================================================
-- THE HIT RECORD SHOWS THE PRICED CENTRE (audit repair 3, part 2, 29 Sep).
--
-- The Predictive page's hit-and-miss record graded the call by the ladder the
-- engine priced, but its "forecast", "error", "mean |error|" and "bias" were
-- band_probabilities.forecast_max_c: the public forecast the engine STARTED
-- from, before the station correction, the MOS blend and the trajectory moved
-- it. Measured 29 Sep with this view's own rule, on the 257 settled day-ahead
-- calls (23-28 Sep) whose pricing run recorded a centre: the raw input missed
-- by 1.117 C on average (bias +0.012), the centre that was priced by 1.051 C
-- (bias +0.258), and 71 of the 257 differ by 1 C or more.
-- v_prediction_hindsight mixed the two in one column: day-ahead rows the raw
-- input, checkpoint rows the priced centre.
--
--   v_city_hit_history_live   two columns appended: centre_c, the centre of
--       the call's own pricing run (the newest row before the day, the run
--       called_at names), and centre_error_c = centre_c - observed_max_c.
--       band_probabilities.centre_c exists from 22 Sep 18:32Z (the first
--       settled day with one is 23 Sep); before that both are null - "not
--       recorded", never the raw input in its place. The 30 columns before
--       them are unchanged: in one snapshot the new definition's 30 columns
--       EXCEPT ALL the old view's gave 0 rows both ways (657 rows each), and
--       the summary's 22 columns likewise.
--   mv_city_hit_history       rebuilt with them (it cannot gain a column in
--       place): the page reads the live view while it is rebuilt, then the
--       stored copy again, as 20260927170000 did for mv_prediction_ladder.
--   v_city_hit_summary        centre_days, centre_mae_c, centre_bias_c
--       appended; mae_c and bias_c stay the raw input's, named as such on
--       the page.
--   v_prediction_hindsight    day-ahead rows take forecast_max_c and
--       forecast_error_c from the priced centre, as the checkpoint rows
--       always did.
--
-- Guarded and re-runnable.
-- ===========================================================================

do $mig$
begin
  if to_regclass('public.v_city_hit_history_live') is not null then
    execute $v$
create or replace view public.v_city_hit_history_live as
with ladder as (
  select o.city_key, o.for_date, o.band_id,
         o.band_lo, o.band_hi, o.open_low, o.open_high,
         o.settled_yes, o.observed_max_c,
         -- THE VENUE'S OWN LABEL: "22°C", "70-71°F", "18°C or below". Built
         -- from the edges this read "22-23" for the single-degree bucket 22°C
         -- and "70-72" for 70-71°F, because a band is half-open [lo, hi): a
         -- Celsius bucket is ONE temperature, a Fahrenheit one two. The
         -- computed form below is only a fallback, and it is half-open too.
         coalesce(cb.band_label,
           case
             when o.open_low  then '<= ' || trim(to_char(o.band_hi - 1, 'FM999990.#'))
             when o.open_high then '>= ' || trim(to_char(o.band_lo, 'FM999990.#'))
             when o.band_hi - o.band_lo = 1 then trim(to_char(o.band_lo, 'FM999990.#'))
             else trim(to_char(o.band_lo, 'FM999990.#')) || '-' || trim(to_char(o.band_hi - 1, 'FM999990.#'))
           end) as band_label,
         -- The city's own midnight at the start of the day being called.
         (o.for_date::timestamp at time zone coalesce(c.timezone, 'UTC')) as day_starts_at
    from v_fact_band_outcome_clean o
    left join cities c on c.city_key = o.city_key
    left join v_canonical_bands cb on cb.band_id = o.band_id
   where o.observed_max_c is not null
),
settled as (
  select city_key, for_date
    from ladder
   group by city_key, for_date
  having bool_or(settled_yes)
),
priced as (
  select l.*,
         p.prob          as p_pre,
         p.forecast_max_c, p.sigma_c, p.confidence, p.regime_label, p.centre_c,
         p.computed_at   as priced_at,
         case when k.band_id is not null then k.market_price
              else e.market_price end as market_pre
    from ladder l
    join settled d on d.city_key = l.city_key and d.for_date = l.for_date
    -- The desk's probability as it stood before the day began: the one it
    -- would have traded on (calibrated where calibration existed).
    left join lateral (
      select coalesce(bp.calibrated_prob, bp.raw_prob) as prob,
             bp.forecast_max_c, bp.sigma_c, bp.confidence, bp.regime_label, bp.computed_at, bp.centre_c
        from band_probabilities bp
       where bp.band_id = l.band_id
         and bp.computed_at < l.day_starts_at
       order by bp.computed_at desc, bp.prob_id desc
       limit 1
    ) p on true
    -- The market's YES price at the same cutoff: the frozen mark, else the
    -- edge itself.
    left join derived_edge_marks k
           on k.band_id = l.band_id and k.mark = 'day' and k.cutoff_at = l.day_starts_at
    left join lateral (
      select x.market_price
        from edges x
       where x.band_id = l.band_id
         and x.side = 'YES'
         and x.computed_at < l.day_starts_at
         and k.band_id is null
       order by x.computed_at desc
       limit 1
    ) e on true
),
model_day as (
  select city_key, for_date,
         count(*)        as ladder_bands,
         count(p_pre)    as model_bands,
         sum(p_pre)      as model_mass
    from priced
   group by city_key, for_date
  having count(p_pre) > 0 and sum(p_pre) > 0
),
model_scored as (
  select p.*, d.ladder_bands, d.model_bands,
         p.p_pre / d.model_mass as p_model
    from priced p
    join model_day d on d.city_key = p.city_key and d.for_date = p.for_date
),
model_picks as (
  select m.city_key, m.for_date,
         max(m.ladder_bands)                                         as ladder_bands,
         max(m.model_bands)                                          as model_bands,
         max(m.observed_max_c)                                       as observed_max_c,
         max(m.forecast_max_c)                                       as forecast_max_c,
         max(m.sigma_c)                                              as sigma_c,
         max(m.confidence)                                           as confidence,
         max(m.regime_label)                                         as regime_label,
         max(m.priced_at)                                            as called_at,
         -- THE CENTRE THE CALL WAS PRICED ON (audit repair 3): from the newest
         -- row before the day, the same run as called_at - never the raw
         -- forecast_max_c the engine started from.
         (array_agg(m.centre_c order by m.priced_at desc nulls last, m.band_id))[1] as centre_c,
         max(m.day_starts_at)                                        as day_starts_at,
         max(m.band_label) filter (where m.settled_yes)              as winner,
         -- The most probable band; a tie goes to the lower one.
         (array_agg(m.band_label order by m.p_model desc nulls last,
                                          m.band_lo asc nulls first))[1] as model_call,
         max(m.p_model)                                              as model_call_prob,
         coalesce(max(m.p_model) filter (where m.settled_yes), 0)    as model_prob_on_winner,
         -- Multiclass Brier over the whole settled ladder; an unpriced band is 0.
         sum(power(coalesce(m.p_model, 0) - (case when m.settled_yes then 1 else 0 end), 2)) as brier_model,
         sum(power(1.0 / m.ladder_bands   - (case when m.settled_yes then 1 else 0 end), 2)) as brier_uniform
    from model_scored m
   group by m.city_key, m.for_date
),
-- Head to head: both sides, over the bands BOTH priced before the day,
-- renormalised over that same set, and only when it holds the winner.
common as (
  select * from priced where p_pre is not null and market_pre is not null
),
common_day as (
  select city_key, for_date,
         sum(p_pre)            as model_mass,
         sum(market_pre)       as market_mass,
         count(*)              as n_common,
         bool_or(settled_yes)  as winner_priced_by_both
    from common
   group by city_key, for_date
),
h2h as (
  select c.city_key, c.for_date, t.n_common,
         (array_agg(c.band_label order by c.market_pre desc nulls last,
                                          c.band_lo asc nulls first))[1]  as market_call,
         max(c.market_pre / t.market_mass)                                 as market_call_price,
         max(c.market_pre / t.market_mass) filter (where c.settled_yes)   as market_prob_on_winner,
         sum(power(c.p_pre / t.model_mass       - (case when c.settled_yes then 1 else 0 end), 2)) as brier_model_common,
         sum(power(c.market_pre / t.market_mass - (case when c.settled_yes then 1 else 0 end), 2)) as brier_market,
         sum(power(1.0 / t.n_common             - (case when c.settled_yes then 1 else 0 end), 2)) as brier_uniform_common
    from common c
    join common_day t on t.city_key = c.city_key and t.for_date = c.for_date
   where t.winner_priced_by_both and t.model_mass > 0 and t.market_mass > 0
   group by c.city_key, c.for_date, t.n_common
)
select
  m.city_key,
  c.display_name,
  c.unit,
  m.for_date,
  m.ladder_bands,
  m.model_bands,
  m.observed_max_c,
  m.forecast_max_c,
  round((m.forecast_max_c - m.observed_max_c)::numeric, 2)                  as error_c,
  m.sigma_c,
  m.confidence,
  m.regime_label,
  m.winner,
  m.model_call,
  round(m.model_call_prob::numeric, 4)                                      as model_call_prob,
  round(m.model_prob_on_winner::numeric, 4)                                 as model_prob_on_winner,
  (m.model_call = m.winner)                                                 as model_hit,
  round(m.brier_model::numeric, 4)                                          as brier_model,
  round(m.brier_uniform::numeric, 4)                                        as brier_uniform,
  m.called_at,
  round((extract(epoch from (m.day_starts_at - m.called_at)) / 3600.0)::numeric, 1) as hours_before_day,
  (h.city_key is not null)                                                  as head_to_head,
  h.n_common                                                                as bands_scored,
  h.market_call,
  round(h.market_call_price::numeric, 4)                                    as market_call_price,
  round(h.market_prob_on_winner::numeric, 4)                                as market_prob_on_winner,
  (h.market_call = m.winner)                                                as market_hit,
  round(h.brier_model_common::numeric, 4)                                   as brier_model_common,
  round(h.brier_market::numeric, 4)                                         as brier_market,
  round(h.brier_uniform_common::numeric, 4)                                 as brier_uniform_common,
  round(m.centre_c::numeric, 2)                                             as centre_c,
  round((m.centre_c - m.observed_max_c)::numeric, 2)                        as centre_error_c
from model_picks m
left join h2h h on h.city_key = m.city_key and h.for_date = m.for_date
left join cities c on c.city_key = m.city_key
where m.winner is not null
order by m.for_date desc, m.city_key;
$v$;
  end if;
end $mig$;

-- The stored copy, rebuilt once: skipped when it already has the columns.
do $mig$
begin
  if to_regclass('public.mv_city_hit_history') is null or to_regclass('public.v_city_hit_history_live') is null then
    raise notice 'mv_city_hit_history is not installed (sql/ad4_89): nothing to rebuild';
    return;
  end if;
  if exists (select 1 from pg_attribute
              where attrelid = 'public.mv_city_hit_history'::regclass
                and attname = 'centre_error_c' and not attisdropped) then
    return;
  end if;
  -- the page reads the live rows while the stored copy is rebuilt
  execute $v$
    create or replace view public.v_city_hit_history as
    select city_key, display_name, unit, for_date, ladder_bands, model_bands, observed_max_c, forecast_max_c, error_c, sigma_c, confidence, regime_label, winner, model_call, model_call_prob, model_prob_on_winner, model_hit, brier_model, brier_uniform, called_at, hours_before_day, head_to_head, bands_scored, market_call, market_call_price, market_prob_on_winner, market_hit, brier_model_common, brier_market, brier_uniform_common
      from public.v_city_hit_history_live
  $v$;
  drop materialized view public.mv_city_hit_history;
  execute $v$
    create materialized view public.mv_city_hit_history as
    select v.* from public.v_city_hit_history_live v
  $v$;
  create unique index mv_city_hit_history_key on public.mv_city_hit_history (city_key, for_date);
  revoke all on public.mv_city_hit_history from public, anon, authenticated;
  grant select on public.mv_city_hit_history to service_role;
  comment on materialized view public.mv_city_hit_history is
    'Stored rows of v_city_hit_history_live, refreshed by refresh_page_cache() (plan v2 P6.5). The page reads v_city_hit_history, which selects from here.';
  execute $v$
    create or replace view public.v_city_hit_history as
    select city_key, display_name, unit, for_date, ladder_bands, model_bands, observed_max_c, forecast_max_c, error_c, sigma_c, confidence, regime_label, winner, model_call, model_call_prob, model_prob_on_winner, model_hit, brier_model, brier_uniform, called_at, hours_before_day, head_to_head, bands_scored, market_call, market_call_price, market_prob_on_winner, market_hit, brier_model_common, brier_market, brier_uniform_common, centre_c, centre_error_c
      from public.mv_city_hit_history
  $v$;
end $mig$;

do $mig$
begin
  if to_regclass('public.v_city_hit_summary') is null then
    return;
  end if;
  execute $v$
create or replace view public.v_city_hit_summary as
select
  h.city_key,
  h.display_name,
  h.unit,
  count(*)                                                        as days,
  count(*) filter (where h.model_hit)                             as model_hits,
  round(avg(case when h.model_hit then 1.0 else 0 end), 4)        as model_hit_rate,
  round(avg(h.model_prob_on_winner), 4)                           as avg_model_prob_on_winner,
  round(avg(h.brier_model), 4)                                    as brier_model,
  round(avg(h.brier_uniform), 4)                                  as brier_uniform,
  round(avg(abs(h.error_c)), 3)                                   as mae_c,
  round(avg(h.error_c), 3)                                        as bias_c,
  round(avg(h.hours_before_day), 1)                               as avg_hours_before_day,
  min(h.for_date)                                                 as first_day,
  max(h.for_date)                                                 as last_day,
  count(*) filter (where h.head_to_head)                          as h2h_days,
  count(*) filter (where h.head_to_head and h.model_hit)          as h2h_model_hits,
  count(*) filter (where h.head_to_head and h.market_hit)         as market_hits,
  round(avg(case when h.market_hit then 1.0 else 0 end)
          filter (where h.head_to_head), 4)                       as market_hit_rate,
  round(avg(h.brier_model_common) filter (where h.head_to_head), 4) as brier_model_h2h,
  round(avg(h.brier_market)       filter (where h.head_to_head), 4) as brier_market,
  count(*) filter (where h.head_to_head
                     and h.brier_model_common < h.brier_market)   as days_we_beat_the_market,
  case
    when count(*) < 5                                              then 'too few settled days to judge'
    when avg(h.brier_model) >= avg(h.brier_uniform)                then 'no better than guessing'
    when count(*) filter (where h.head_to_head) < 5                then 'better than guessing; too few days the market also priced to compare'
    when avg(h.brier_model_common) filter (where h.head_to_head)
      <= avg(h.brier_market) filter (where h.head_to_head)         then 'sharper than the market here'
    else                                                                'informative, but the market is sharper'
  end                                                             as verdict,
  -- the priced centre's own record, on the days it was recorded
  count(h.centre_error_c)                                         as centre_days,
  round(avg(abs(h.centre_error_c)), 3)                            as centre_mae_c,
  round(avg(h.centre_error_c), 3)                                 as centre_bias_c
from public.v_city_hit_history h
group by h.city_key, h.display_name, h.unit
order by days desc, h.city_key
  $v$;
end $mig$;

do $mig$
begin
  if to_regclass('public.v_prediction_hindsight') is null or to_regclass('public.v_checkpoint_calls') is null then
    return;
  end if;
  execute $v$
create or replace view public.v_prediction_hindsight as
select h.city_key,
       h.for_date,
       h.model_call                                    as predicted_band,
       round(100::numeric * h.model_call_prob, 1)      as predicted_pct,
       h.winner                                        as actual_band,
       round(h.observed_max_c, 1)                      as observed_max_c,
       -- the priced centre, as the checkpoint rows below (audit repair 3)
       round(h.centre_c, 1)                            as forecast_max_c,
       round(h.observed_max_c - h.centre_c, 1)         as forecast_error_c,
       h.model_hit                                     as hit,
       round(h.sigma_c, 2)                             as stated_sigma_c,
       h.regime_label,
       'banked'::text                                  as outcome_source,
       'day_ahead'::text                               as called_when,
       0                                               as call_order,
       false                                           as after_peak,
       h.called_at,
       h.market_call                                   as market_band,
       h.market_hit
  from public.v_city_hit_history h
union all
select c.city_key,
       c.for_date,
       c.called_band,
       round(100::numeric * c.called_prob, 1),
       c.winner_band,
       round(o.observed_max_c, 1),
       round(c.centre_c, 1),
       round(o.observed_max_c - c.centre_c, 1),
       c.hit,
       round(c.sigma_c, 2),
       null::text,
       'venue'::text,
       c.checkpoint,
       c.checkpoint_order,
       c.after_peak,
       c.decided_at,
       c.market_band,
       c.market_hit
  from public.v_checkpoint_calls c
  left join public.v_city_hit_history o on o.city_key = c.city_key and o.for_date = c.for_date
  $v$;
  comment on view public.v_prediction_hindsight is
    'Per settled city-day and per moment the call was frozen (called_when): the bucket the engine favoured and its stated probability, the market''s favourite, and the bucket that paid. day_ahead is the last pricing before the city''s local day began (v_city_hit_history); the checkpoints are prediction_checkpoints rows graded against the venue''s confirmed winner (v_checkpoint_calls). forecast_max_c is the centre that was priced in both halves (null where it was not recorded), never the raw forecast. Nothing here was priced after the moment it names. after_peak marks postpeak_1h. Read by the Predictive page.';
end $mig$;
