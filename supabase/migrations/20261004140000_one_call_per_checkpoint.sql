-- ===========================================================================
-- ONE CALL PER CHECKPOINT (4 Oct 2026; the external improvement plan's P0.1)
--
-- prediction_checkpoints holds one row per city, day, checkpoint and ENGINE
-- VERSION, and the version is the commit the tick ran on. The tick looks back
-- GRACE_MIN = 75 minutes and runs hourly, so a checkpoint whose time falls in
-- the 15 minutes two ticks share is due in both; when a commit landed on main
-- between them (the nightly archive and mirror commit there), the second tick
-- held no row under its own version and wrote the checkpoint again, an hour
-- after its time. Live on 4 Oct: 2,498 settled calls for 2,417 city-day
-- checkpoints - 81 second captures (prepeak_2h 33, prepeak_1h 24,
-- postpeak_1h 21, noon 2, d1_eve 1), on 25 Sep - 3 Oct; 19 of them named a
-- different top bucket. Every score counted them all, and the panel's "city-
-- days" were rows.
--
-- v_checkpoint_calls keeps every versioned row (nothing is hidden or removed)
-- and appends first_call - the first capture by decided_at, checkpoint_id
-- breaking a tie - and calls_at_checkpoint. v_checkpoint_scoreboard and the
-- checkpoint half of v_prediction_hindsight (and through it the summary)
-- count first calls only; v_prediction_hindsight appends the scheduled local
-- time and the engine version. scripts/tick.py stops writing a checkpoint any
-- version already holds.
--
-- The statements are the ones in 20260926090000 and 20260930004500 with
-- those changes (tests/test_one_call_per_checkpoint.py holds them so), as
-- CREATE OR REPLACE with columns only appended, so the dependents and grants
-- stay. Applied only where the views exist. Re-runnable.
-- ===========================================================================

do $mig$
begin
  if to_regclass('public.v_checkpoint_calls') is null then
    raise notice 'v_checkpoint_calls not installed here; nothing to replace';
    return;
  end if;
  execute $v$create or replace view public.v_checkpoint_calls as
with cp as (
  select p.checkpoint_id, p.city_key, p.target_date, p.checkpoint, p.local_decision_time, p.decided_at,
         p.engine_version, p.top_band_id, p.top_prob, p.centre_c, p.sigma_c, p.running_max_c,
         p.probs, p.market,
         f.winner_band_id, f.hit, f.n_bands, f.ladder_has_winner, f.model_prob_on_winner,
         f.brier_model, f.log_loss_model, f.brier_uniform, f.log_loss_uniform, f.settled_at,
         p.first_call, p.calls_at_checkpoint
    from (
      -- ONE CALL PER CHECKPOINT (4 Oct): the first capture of the city, day
      -- and checkpoint by when it was decided, the id breaking a tie, ranked
      -- over every capture whether or not it has been graded yet.
      select q.*,
             row_number() over (partition by q.city_key, q.target_date, q.checkpoint
                                order by q.decided_at, q.checkpoint_id) = 1        as first_call,
             count(*) over (partition by q.city_key, q.target_date, q.checkpoint)  as calls_at_checkpoint
        from public.prediction_checkpoints q
    ) p
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
       cp.settled_at,
       -- appended (4 Oct): whether this row is the checkpoint's call, and how
       -- many engine versions captured the checkpoint
       cp.first_call,
       cp.calls_at_checkpoint::int                      as calls_at_checkpoint
  from cp
  left join mkt m using (checkpoint_id)
  left join scored s using (checkpoint_id)
  left join public.bands tb on tb.band_id = cp.top_band_id::uuid
  left join public.bands wb on wb.band_id = cp.winner_band_id::uuid
  left join public.bands mb on mb.band_id = m.market_band_id::uuid;$v$;
  execute $v$create or replace view public.v_checkpoint_scoreboard as
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
 where c.first_call
 group by grouping sets ((c.checkpoint), (c.checkpoint, c.city_key));$v$;
end
$mig$;

do $mig$
begin
  if to_regclass('public.v_prediction_hindsight') is null or to_regclass('public.v_checkpoint_calls') is null then
    raise notice 'v_prediction_hindsight not installed here; nothing to replace';
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
       h.market_hit,
       -- appended (4 Oct): the checkpoint's scheduled local time and the
       -- engine version that made the call; the day-ahead call has neither
       null::timestamp                                 as scheduled_local,
       null::text                                      as engine_version
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
       c.market_hit,
       c.local_decision_time,
       c.engine_version
  from public.v_checkpoint_calls c
  left join public.v_city_hit_history o on o.city_key = c.city_key and o.for_date = c.for_date
 where c.first_call
  $v$;
end
$mig$;
