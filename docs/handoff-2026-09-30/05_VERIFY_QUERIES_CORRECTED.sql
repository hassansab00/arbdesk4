-- Corrected verification queries (30 Sep 2026, the supplement's section 7).
-- They replace the ones in 04_VERIFY_QUERIES.sql that the supplement found
-- unsound; that file is kept as the record of what the handoff ran.
-- Read-only. Browser-facing views as anon: begin; set local role anon; ...; rollback;
-- Date bounds are explicit literals: change them, never let "current_date" silently move a window.

-- =====================================================================
-- Q1 (corrected). Model vs market, day-ahead, per period AND unit, bounded.
-- Raw-vs-centre MAE only on rows with BOTH errors (the paired denominator).
-- Market figures only on head_to_head rows, with that count.
-- =====================================================================
select case when for_date between date '2026-09-13' and date '2026-09-22' then 'a 13-22 Sep'
            when for_date between date '2026-09-23' and date '2026-09-29' then 'b 23-29 Sep' end period,
       unit,
       count(*) n_days,
       round(100.0 * avg(model_hit::int), 1) model_hit_pct_all,
       count(*) filter (where head_to_head) h2h_n,
       round(100.0 * avg(model_hit::int)  filter (where head_to_head), 1) h2h_model_hit_pct,
       round(100.0 * avg(market_hit::int) filter (where head_to_head), 1) h2h_market_hit_pct,
       round(avg(brier_model_common) filter (where head_to_head)::numeric, 3) h2h_brier_model,
       round(avg(brier_market)       filter (where head_to_head)::numeric, 3) h2h_brier_market,
       count(*) filter (where error_c is not null and centre_error_c is not null) paired_n,
       round(avg(abs(error_c))        filter (where error_c is not null and centre_error_c is not null)::numeric, 2) paired_raw_mae_c,
       round(avg(abs(centre_error_c)) filter (where error_c is not null and centre_error_c is not null)::numeric, 2) paired_centre_mae_c
from v_city_hit_history
where for_date between date '2026-09-13' and date '2026-09-29'
group by 1, 2 order by 1, 2;
-- Reading: different periods are different weather and different model
-- mixtures. A higher later rate does not isolate a software change (supplement C1).

-- Q1b (corrected). Per settled date, model and market on the SAME head_to_head rows.
select unit, for_date, count(*) filter (where head_to_head) h2h_n,
       count(*) filter (where head_to_head and model_hit)  h2h_model_hits,
       count(*) filter (where head_to_head and market_hit) h2h_market_hits,
       count(*) n_all, count(*) filter (where model_hit) model_hits_all
from v_city_hit_history
where for_date between date '2026-09-13' and date '2026-09-29'
group by unit, for_date order by for_date, unit;

-- =====================================================================
-- F1 (corrected). From each city's LOCAL day end to the page, pending
-- markets kept in the denominator (v_outcome_pipeline, #290). Service role.
-- =====================================================================
select unit, resolution_date, count(*) markets,
       count(*) filter (where state = 'on_page') on_page,
       count(*) filter (where state in ('awaiting_venue', 'venue_resolved_proof_incomplete',
                                        'confirmed_not_banked', 'banked_not_on_page')) pending,
       round(percentile_cont(0.5) within group (order by hours_day_end_to_banked_or_now)::numeric, 1) median_h_banked_or_so_far,
       max(hours_day_end_to_banked_or_now) worst_h_banked_or_so_far,
       round(percentile_cont(0.5) within group (order by extract(epoch from venue_closed_at - local_day_end) / 3600)::numeric, 1) median_h_venue_closed,
       round(percentile_cont(0.5) within group (order by extract(epoch from first_all_resolved_seen_at - local_day_end) / 3600)::numeric, 1) median_h_first_seen_resolved
from v_outcome_pipeline
where resolution_date between date '2026-09-28' and date '2026-10-03' and state <> 'day_not_ended'
group by 1, 2 order by 2, 1;

-- F1-c (corrected). Each active city's newest settled day against ITS OWN
-- newest ended local day (not "yesterday" in UTC), as anon.
begin;
set local role anon;
select c.unit, c.city_key,
       (select max(for_date) from v_city_hit_history h where h.city_key = c.city_key) newest_settled,
       ((now() at time zone c.timezone)::date - 1) newest_ended_local_day
from cities c where c.status = 'active' order by c.unit desc, c.city_key;
rollback;

-- =====================================================================
-- Checkpoint scoring (corrected): v_checkpoint_outcome (the market columns
-- corrected by book_mark() beside the originals), deduplicated by the
-- checkpoint's identity, split by engine version, bounded.
-- =====================================================================
select checkpoint, engine_version, count(*) n, count(distinct target_date) dates,
       round(avg(log_loss_model)::numeric, 3) ll_model,
       round(avg(log_loss_market) filter (where market_complete)::numeric, 3) ll_market_complete,
       count(*) filter (where market_complete) n_market_complete,
       count(*) filter (where market_corrected) n_market_corrected,
       round(100.0 * avg(hit::int), 1) model_hit_pct,
       round(100.0 * avg(market_hit::int) filter (where market_complete), 1) market_hit_pct_complete
from (select distinct on (city_key, target_date, checkpoint, engine_version) *
        from v_checkpoint_outcome
       where target_date between date '2026-09-24' and date '2026-09-29'
       order by city_key, target_date, checkpoint, engine_version, banked_at) x
group by 1, 2 order by 1, 2;

-- =====================================================================
-- S10 shadow scoring (corrected): split by model_version, joined to the
-- venue winner by city, local target date and checkpoint, the engine's
-- market quote used only when its decision time is within 15 minutes of
-- S10's (a joined row is not proof of synchronisation).
-- =====================================================================
with s as (
  select s.city_key, s.target_date, s.checkpoint, s.model_version, s.decided_at, s.probs, s.top_band_id
    from s10_shadow_checkpoints s
   where s.target_date between date '2026-09-27' and date '2026-10-03'),
o as (
  select v.city_key, v.target_date, v.checkpoint, v.winner_band_id, v.market_top_band_id,
         v.market_complete, v.decided_at engine_decided_at
    from v_checkpoint_outcome v)
select s.model_version, s.checkpoint, count(*) n, count(distinct s.target_date) dates,
       round(avg(-ln(greatest(coalesce((s.probs ->> o.winner_band_id)::numeric, 0), 1e-6)))::numeric, 3) s10_ll,
       round(100.0 * avg((s.top_band_id = o.winner_band_id)::int), 1) s10_hit_pct,
       count(*) filter (where o.market_complete and abs(extract(epoch from o.engine_decided_at - s.decided_at)) <= 900) market_synced_n,
       round(100.0 * avg((o.market_top_band_id = o.winner_band_id)::int)
             filter (where o.market_complete and abs(extract(epoch from o.engine_decided_at - s.decided_at)) <= 900), 1) market_hit_pct_synced
from s join o using (city_key, target_date, checkpoint)
group by 1, 2 order by 1, 2;

-- =====================================================================
-- F2-a (corrected). The page's OWN query and order; every active city must
-- be reachable, rows past 120 included.
-- =====================================================================
begin;
set local role anon;
with s as (
  select row_number() over (order by city_key, model, lead_days) rn, v.city_key
    from v_prediction_scorecard_all v
   where v.city_key in (select city_key from cities where status = 'active'))
select count(*) rows_total, count(distinct city_key) cities_reachable,
       (select count(*) from cities where status = 'active') active_cities,
       count(*) filter (where rn > 120) rows_past_120
from s;
rollback;

-- =====================================================================
-- Tournament gate (corrected reading). Per city, n_vs_live counts ONE row
-- per settlement date (the d1_eve checkpoint), so 20 is 20 dates for that
-- city. The pooled summary's n counts city-days: not independent dates.
-- =====================================================================
select lane, count(*) cities, max(n_vs_live) max_dates_one_city,
       count(*) filter (where n_vs_live >= 20 and gain_vs_live_lo > 0) would_pass
from derived_hit_recipe group by lane;

-- =====================================================================
-- Archive checks (corrected): counters are necessary, not sufficient.
-- Mark-only datasets are not deletes; compare keys and checksums and read
-- back through the reader (tools/p16_step32_proof.py does this for books).
-- =====================================================================
select job, status, started_at,
       detail ->> 'rows' exported, detail -> 'prune' ->> 'expected_rows' expected,
       detail -> 'prune' ->> 'deleted' deleted, detail ->> 'mode' mode, detail ->> 'file' file,
       detail ->> 'sha256' sha256
from ingest_log where job like 'archive%'
  and started_at between timestamptz '2026-10-01 00:00Z' and timestamptz '2026-10-01 06:00Z'
order by started_at;

-- Sizes: bytes, and each conversion named.
select pg_database_size(current_database()) bytes,
       round(pg_database_size(current_database()) / 1e6, 1) mb_decimal,
       round(pg_database_size(current_database()) / 1048576.0, 1) mib,
       storage_pressure() pressure_counts_in_mib;
