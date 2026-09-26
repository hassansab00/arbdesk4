-- ===========================================================================
-- HIT AND MISS SCORES ONLY CALLS THAT WERE FROZEN BEFORE THE ANSWER
-- (plan v2 P4.6, and a fix to P4.3 and P4.2's market favourite)
--
-- 1. THE "WAS IT RIGHT?" PANEL GRADED HINDSIGHT. v_prediction_hindsight took
--    each band's LATEST band_probabilities row, with no time limit. The engine
--    re-prices through the day and after it, reading the running maximum, so
--    the "prediction" it graded was mostly the thermometer read back. Measured
--    26 Sep over its last 30 days: 647 scored city-days, 41.4% hits; 627 of
--    them last priced after 15:00 local on the day, 306 after the local day
--    had ended. For 25 Sep it showed 32 hits of 35, while the calls frozen at
--    fixed times that day hit 64-71%.
--
--    It now holds only calls frozen before the answer, one row per city-day
--    per moment (called_when):
--      day_ahead    the last probability computed before the city's local
--                   day began (v_city_hit_history, ad4_85, which already
--                   scores it this way)
--      d1_eve ...   each prediction_checkpoints row (P4.2): the ladder
--      postpeak_1h  published once at a fixed local time and never changed,
--                   graded against the winner the venue confirmed (P4.3).
--    after_peak marks postpeak_1h: an hour after the peak the day has mostly
--    answered itself, so the panel shows it apart and never as the headline.
--
-- 2. THE MARKET'S FAVOURITE WAS A DEAD BUCKET. tick.py and
--    bank_checkpoint_outcomes() priced a one-sided book at its last trade.
--    Measured 26 Sep over all 429 checkpoints: 2,493 of 4,719 band books
--    were ask-only, and 970 of those carried a last trade ABOVE their own best
--    ask - typically ask 0.001, last 0.999, a bucket nobody will pay a tenth
--    of a cent for "priced" at 99.9c. That bucket became the favourite, so
--    fact_checkpoint_outcome recorded the market right 5 times in 98; with
--    the last trade held inside the quoted side it was right 86 times in 98.
--
--    public.book_mark() is that rule, once: the mid of a two-sided book, else
--    the last trade clipped into the side that is quoted, else nothing.
--    bank_checkpoint_outcomes() uses it from now on. The 98 rows already
--    banked are immutable and keep what was written; v_checkpoint_calls
--    recomputes the market side of EVERY row from the raw books that
--    prediction_checkpoints kept, so old and new rows are graded alike.
--
-- 3. v_checkpoint_scoreboard (P4.6): per checkpoint, over all cities and per
--    city - city-days, the engine's and the market's top-pick hits, the
--    engine's stated confidence, and Brier and log loss for the engine, the
--    market and a uniform guess (the market's only on ladders it priced whole,
--    with the engine's over the same ladders beside it).
--
-- Re-runnable. The day-ahead half needs v_city_hit_history (sql/ad4_85),
-- which the migration harness does not build; without it that step is skipped.
-- ===========================================================================

create or replace function public.book_mark(bid numeric, ask numeric, last numeric)
returns numeric
language sql
immutable
set search_path = ''
as $$
  select case
           when bid is not null and ask is not null then (bid + ask) / 2
           when last is null then null
           when ask is not null then least(last, ask)      -- only the ask is quoted
           when bid is not null then greatest(last, bid)   -- only the bid is quoted
           else last
         end
$$;

comment on function public.book_mark(numeric, numeric, numeric) is
  'The market''s number for one band''s YES book: the mid of a two-sided book; else the last trade held inside the quoted side (a YES offered at 0.001 is worth no more than 0.001, whatever it last traded at); else null. Mirrors tick.market_price.';

-- The views below call it, and a function inside a view runs with the READER's
-- EXECUTE privilege (measured: anon read v_checkpoint_calls with "permission
-- denied for function book_mark" until this grant). It is arithmetic on its
-- three arguments and reads nothing.
revoke all on function public.book_mark(numeric, numeric, numeric) from public;
grant execute on function public.book_mark(numeric, numeric, numeric) to anon, authenticated, service_role;


-- ---------------------------------------------------------------------------
-- 2. banking: the same function as 20260924020000, with book_mark() for the
--    price and the favourite taken from those prices.
-- ---------------------------------------------------------------------------
create or replace function public.bank_checkpoint_outcomes()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare n integer;
begin
  with todo as (
    select p.*
      from public.prediction_checkpoints p
     where not exists (select 1 from public.fact_checkpoint_outcome f where f.checkpoint_id = p.checkpoint_id)
       and p.target_date < current_date + 1
  ),
  settled as (
    -- exactly one confirmed market for the city-day, or nothing is banked
    select t.checkpoint_id, min(mr.winning_band_id::text) as winner, min(mr.confirmed_at) as confirmed_at
      from todo t
      join public.v_venue_market_resolution mr
        on mr.city_key = t.city_key and mr.resolution_date = t.target_date
       and mr.resolution_state = 'confirmed' and mr.winning_band_id is not null
     group by t.checkpoint_id
    having count(*) = 1
  ),
  bands as (
    select t.checkpoint_id, e.key as band_id, e.value::numeric as p,
           (e.key = s.winner) as won,
           public.book_mark((t.market -> e.key ->> 'bid')::numeric,
                            (t.market -> e.key ->> 'ask')::numeric,
                            (t.market -> e.key ->> 'last')::numeric) as px
      from todo t
      join settled s using (checkpoint_id)
      cross join lateral jsonb_each_text(t.probs) e
  ),
  per as (
    select checkpoint_id,
           count(*)                                         as n_bands,
           bool_or(won)                                     as ladder_has_winner,
           coalesce(max(p) filter (where won), 0)           as p_win,
           sum(power(p - case when won then 1 else 0 end, 2)) as brier_model,
           bool_and(px is not null) and coalesce(sum(px), 0) > 0 as market_complete,
           sum(px)                                          as px_total,
           -- the favourite: the dearest priced band; ties to the larger id, as tick.py
           (array_agg(band_id order by px desc, band_id desc) filter (where px is not null))[1] as market_top
      from bands group by checkpoint_id
  ),
  mkt as (
    select b.checkpoint_id,
           max(b.px / nullif(p.px_total, 0)) filter (where b.won)                         as m_win,
           sum(power(b.px / nullif(p.px_total, 0) - case when b.won then 1 else 0 end, 2)) as brier_market
      from bands b join per p using (checkpoint_id)
     where p.market_complete
     group by b.checkpoint_id
  )
  insert into public.fact_checkpoint_outcome (
    checkpoint_id, city_key, target_date, checkpoint, engine_version, local_decision_time, decided_at,
    winner_band_id, n_bands, ladder_has_winner, model_prob_on_winner, hit, brier_model, log_loss_model,
    market_top_band_id, market_hit, market_complete, market_prob_on_winner, brier_market, log_loss_market,
    brier_uniform, log_loss_uniform, settled_at)
  select t.checkpoint_id, t.city_key, t.target_date, t.checkpoint, t.engine_version, t.local_decision_time, t.decided_at,
         s.winner, p.n_bands, p.ladder_has_winner, p.p_win, t.top_band_id = s.winner,
         round(p.brier_model + case when p.ladder_has_winner then 0 else 1 end, 6),
         round(-ln(greatest(p.p_win, 1e-6)), 6),
         p.market_top,
         case when p.market_top is null then null else p.market_top = s.winner end,
         p.market_complete,
         case when p.market_complete then round(coalesce(m.m_win, 0), 6) end,
         case when p.market_complete then round(m.brier_market + case when p.ladder_has_winner then 0 else 1 end, 6) end,
         case when p.market_complete then round(-ln(greatest(coalesce(m.m_win, 0), 1e-6)), 6) end,
         round(case when p.ladder_has_winner then 1 - 1.0 / p.n_bands else 1 + 1.0 / p.n_bands end, 6),
         round(case when p.ladder_has_winner then ln(p.n_bands::numeric) else -ln(1e-6) end, 6),
         s.confirmed_at
    from todo t
    join settled s using (checkpoint_id)
    join per p using (checkpoint_id)
    left join mkt m using (checkpoint_id)
  on conflict (checkpoint_id) do nothing;
  get diagnostics n = row_count;
  return n;
end $$;

revoke all on function public.bank_checkpoint_outcomes() from public, anon, authenticated;
grant execute on function public.bank_checkpoint_outcomes() to service_role;


-- ---------------------------------------------------------------------------
-- 3. every settled checkpoint, the engine's call and the market's, by name
-- ---------------------------------------------------------------------------
create or replace view public.v_checkpoint_calls as
with cp as (
  select p.checkpoint_id, p.city_key, p.target_date, p.checkpoint, p.local_decision_time, p.decided_at,
         p.engine_version, p.top_band_id, p.top_prob, p.centre_c, p.sigma_c, p.running_max_c,
         p.probs, p.market,
         f.winner_band_id, f.hit, f.n_bands, f.ladder_has_winner, f.model_prob_on_winner,
         f.brier_model, f.log_loss_model, f.brier_uniform, f.log_loss_uniform, f.settled_at
    from public.prediction_checkpoints p
    join public.fact_checkpoint_outcome f using (checkpoint_id)
),
px as (
  select cp.checkpoint_id, k.band_id, (k.band_id = cp.winner_band_id) as won,
         k.p,
         public.book_mark((cp.market -> k.band_id ->> 'bid')::numeric,
                          (cp.market -> k.band_id ->> 'ask')::numeric,
                          (cp.market -> k.band_id ->> 'last')::numeric) as px
    from cp
    cross join lateral (select e.key as band_id, e.value::numeric as p from jsonb_each_text(cp.probs) e) k
),
mkt as (
  select x.checkpoint_id,
         (array_agg(x.band_id order by x.px desc, x.band_id desc) filter (where x.px is not null))[1] as market_band_id,
         max(x.px)                                                  as market_price,
         bool_and(x.px is not null) and coalesce(sum(x.px), 0) > 0 as market_complete,
         sum(x.px)                                                  as px_total
    from px x group by x.checkpoint_id
),
scored as (
  select x.checkpoint_id,
         coalesce(max(x.px / m.px_total) filter (where x.won), 0)                   as m_win,
         sum(power(x.px / m.px_total - case when x.won then 1 else 0 end, 2))       as brier_market
    from px x join mkt m using (checkpoint_id)
   where m.market_complete
   group by x.checkpoint_id
)
select cp.city_key,
       cp.target_date                                   as for_date,
       cp.checkpoint,
       case cp.checkpoint when 'd1_eve' then 1 when 'morning' then 2 when 'noon' then 3
                          when 'prepeak_2h' then 4 when 'prepeak_1h' then 5 when 'postpeak_1h' then 6 end
                                                        as checkpoint_order,
       cp.checkpoint = 'postpeak_1h'                    as after_peak,
       cp.local_decision_time,
       cp.decided_at,
       cp.engine_version,
       tb.band_label                                    as called_band,
       round(cp.top_prob, 4)                            as called_prob,
       wb.band_label                                    as winner_band,
       cp.hit,
       round(cp.centre_c, 2)                            as centre_c,
       round(cp.sigma_c, 2)                             as sigma_c,
       cp.running_max_c,
       mb.band_label                                    as market_band,
       round(m.market_price, 4)                         as market_price,
       case when m.market_band_id is null then null else m.market_band_id = cp.winner_band_id end
                                                        as market_hit,
       coalesce(m.market_complete, false)               as market_complete,
       cp.n_bands,
       cp.ladder_has_winner,
       round(cp.model_prob_on_winner, 6)                as model_prob_on_winner,
       cp.brier_model,
       cp.log_loss_model,
       case when m.market_complete then round(s.m_win, 6) end as market_prob_on_winner,
       case when m.market_complete
            then round(s.brier_market + case when cp.ladder_has_winner then 0 else 1 end, 6) end
                                                        as brier_market,
       case when m.market_complete then round(-ln(greatest(s.m_win, 1e-6)), 6) end
                                                        as log_loss_market,
       cp.brier_uniform,
       cp.log_loss_uniform,
       cp.settled_at
  from cp
  left join mkt m using (checkpoint_id)
  left join scored s using (checkpoint_id)
  left join public.bands tb on tb.band_id = cp.top_band_id::uuid
  left join public.bands wb on wb.band_id = cp.winner_band_id::uuid
  left join public.bands mb on mb.band_id = m.market_band_id::uuid;

comment on view public.v_checkpoint_calls is
  'Every settled prediction checkpoint: the engine''s top bucket and stated probability, frozen at a fixed local time, beside the market''s favourite at that moment and the bucket the venue confirmed. The market side is recomputed from the stored books with book_mark(), so rows banked before 26 Sep are graded by the same rule as later ones. after_peak marks postpeak_1h, when the day has mostly answered itself.';

revoke all on public.v_checkpoint_calls from public;
grant select on public.v_checkpoint_calls to anon, authenticated, service_role;


create or replace view public.v_checkpoint_scoreboard as
select c.checkpoint,
       min(c.checkpoint_order)                                          as checkpoint_order,
       bool_and(c.after_peak)                                           as after_peak,
       coalesce(c.city_key, 'all')                                      as city_key,
       count(*)                                                         as city_days,
       count(*) filter (where c.hit)                                    as model_hits,
       round(avg(c.called_prob), 4)                                     as model_claimed,
       count(c.market_hit)                                              as market_called,
       count(*) filter (where c.market_hit)                             as market_hits,
       count(*) filter (where c.market_complete)                        as market_scored,
       round(avg(c.brier_model), 4)                                     as brier_model,
       round(avg(c.log_loss_model), 4)                                  as log_loss_model,
       round(avg(c.brier_uniform), 4)                                   as brier_uniform,
       round(avg(c.brier_model) filter (where c.market_complete), 4)    as brier_model_common,
       round(avg(c.brier_market), 4)                                    as brier_market,
       round(avg(c.log_loss_model) filter (where c.market_complete), 4) as log_loss_model_common,
       round(avg(c.log_loss_market), 4)                                 as log_loss_market,
       min(c.for_date)                                                  as first_day,
       max(c.for_date)                                                  as last_day
  from public.v_checkpoint_calls c
 group by grouping sets ((c.checkpoint), (c.checkpoint, c.city_key));

comment on view public.v_checkpoint_scoreboard is
  'Plan v2 P4.6: per checkpoint, over all cities (city_key ''all'') and per city - settled city-days, the engine''s and the market''s top-pick hits, the engine''s mean stated confidence, and Brier and log loss for the engine, the market and a uniform guess. The *_common columns are the engine over the ladders the market priced whole, the fair comparison with brier_market and log_loss_market.';

revoke all on public.v_checkpoint_scoreboard from public;
grant select on public.v_checkpoint_scoreboard to anon, authenticated, service_role;


-- ---------------------------------------------------------------------------
-- 1. the panel's view: frozen calls only
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.v_city_hit_history') is null then
    raise notice 'v_city_hit_history is not installed (sql/ad4_85): v_prediction_hindsight left as it is';
    return;
  end if;

  drop view if exists public.v_prediction_hindsight_summary;
  drop view if exists public.v_prediction_hindsight;
  create view public.v_prediction_hindsight as
  select h.city_key,
         h.for_date,
         h.model_call                                    as predicted_band,
         round(100::numeric * h.model_call_prob, 1)      as predicted_pct,
         h.winner                                        as actual_band,
         round(h.observed_max_c, 1)                      as observed_max_c,
         round(h.forecast_max_c, 1)                      as forecast_max_c,
         round(h.observed_max_c - h.forecast_max_c, 1)   as forecast_error_c,
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
    left join public.v_city_hit_history o on o.city_key = c.city_key and o.for_date = c.for_date;

  comment on view public.v_prediction_hindsight is
    'Per settled city-day and per moment the call was frozen (called_when): the bucket the engine favoured and its stated probability, the market''s favourite, and the bucket that paid. day_ahead is the last pricing before the city''s local day began (v_city_hit_history); the checkpoints are prediction_checkpoints rows graded against the venue''s confirmed winner (v_checkpoint_calls). Nothing here was priced after the moment it names. after_peak marks postpeak_1h. Read by the Predictive page.';

  revoke all on public.v_prediction_hindsight from public;
  grant select on public.v_prediction_hindsight to anon, authenticated, service_role;

  -- The totals, in the database: PostgREST returns at most 1,000 rows, and
  -- this view passes that within days (511 day-ahead and 98 checkpoint rows
  -- on 26 Sep, growing by one per city per moment per day).
  create or replace view public.v_prediction_hindsight_summary as
  select called_when,
         min(call_order)                                              as call_order,
         bool_and(after_peak)                                         as after_peak,
         count(*) filter (where hit is not null and predicted_pct is not null)       as days,
         count(*) filter (where hit and predicted_pct is not null)                   as hits,
         round(avg(predicted_pct) filter (where hit is not null), 2)                 as claimed_pct,
         count(market_hit)                                            as market_days,
         count(*) filter (where market_hit)                           as market_hits,
         count(*) filter (where market_hit is not null and hit)       as model_hits_on_market_days,
         round(avg(abs(forecast_error_c)), 3)                         as mae_c,
         round(avg(forecast_error_c), 3)                              as bias_c,
         min(for_date)                                                as first_day,
         max(for_date)                                                as last_day
    from public.v_prediction_hindsight
   group by called_when;

  comment on view public.v_prediction_hindsight_summary is
    'v_prediction_hindsight added up per moment the call was frozen: city-days, the engine''s hits and mean stated confidence, the market''s hits on the days it had a favourite and the engine''s hits on those same days, and the forecast error.';

  revoke all on public.v_prediction_hindsight_summary from public;
  grant select on public.v_prediction_hindsight_summary to anon, authenticated, service_role;
end $$;
