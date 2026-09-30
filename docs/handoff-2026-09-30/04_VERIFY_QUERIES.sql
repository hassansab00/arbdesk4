-- Verification queries for the 30 Sep 2026 handoff (docs/handoff-2026-09-30/).
-- Supabase project jittmxhzgqpifitwupss. Run with the Supabase execute_sql tool.
-- Every number in 01_HANDOFF.md and 02_FIX_SPECS.md came from one of these.
-- Browser-facing views are checked as anon: begin; set local role anon; ...; rollback;
-- Read-only: nothing here writes.

-- =====================================================================
-- Q1. The prediction against the market, day-ahead (01_HANDOFF.md 1.2)
-- Source view: v_city_hit_history (the last pricing before the local day began,
-- and the market's favourite at the same moment). Periods as reported.
-- =====================================================================
select case when for_date <= '2026-09-22' then 'a 13-22 Sep' else 'b 23-29 Sep' end period, unit,
  count(*) n, round(100.0*avg(model_hit::int),1) model_hit_pct,
  count(*) filter (where head_to_head) h2h,
  round(100.0*avg(model_hit::int)  filter (where head_to_head),1) h2h_model_pct,
  round(100.0*avg(market_hit::int) filter (where head_to_head),1) h2h_market_pct,
  round(avg(brier_model_common) filter (where head_to_head)::numeric,3) brier_model,
  round(avg(brier_market)       filter (where head_to_head)::numeric,3) brier_market,
  round(avg(abs(error_c))::numeric,2)        raw_input_mae_c,
  round(avg(abs(centre_error_c))::numeric,2) priced_centre_mae_c, count(centre_error_c) n_centre
from v_city_hit_history group by 1,2 order by 1,2;

-- Q1b. The same per settled date (to see the trend day by day).
select unit, for_date, count(*) n, count(*) filter (where model_hit) model_hits,
  count(*) filter (where head_to_head and market_hit) market_hits
from v_city_hit_history group by unit, for_date order by for_date, unit;

-- =====================================================================
-- F1-a. How late each day reaches the hit/miss record (01_HANDOFF.md 1.3a)
-- Hours from the city's local day end to the first fact_band_outcome row.
-- Measured 22-29 Sep: US 4.4-6.2 h before 25 Sep, 24.0 h from 25 Sep; C 10-13 h.
-- =====================================================================
with d as (
  select c.unit, f.city_key, f.for_date, min(f.captured_at) banked_at, c.timezone
  from fact_band_outcome f join cities c using (city_key)
  where f.for_date >= current_date - 8 and c.status = 'active'
  group by 1,2,3,5)
select unit, for_date, count(*) city_days,
  round(percentile_cont(0.5) within group (order by extract(epoch from banked_at - ((for_date + 1)::timestamp at time zone timezone))/3600)::numeric,1) median_h_after_day_end,
  round(max(extract(epoch from banked_at - ((for_date + 1)::timestamp at time zone timezone))/3600)::numeric,1) max_h,
  min(banked_at) first_banked, max(banked_at) last_banked
from d group by 1,2 order by 2,1;

-- F1-b. How late the venue confirmation is recorded (02_FIX_SPECS.md F1, cause 2)
-- Measured 25-28 Sep: F 22.8-24.1 h median, C 10.3-13.0 h.
select c.unit, m.resolution_date, count(*) markets, count(m.resolution_verified_at) verified,
  round(percentile_cont(0.5) within group (order by extract(epoch from m.resolution_verified_at - ((m.resolution_date + 1)::timestamp at time zone c.timezone))/3600)::numeric,1) median_h_after_day_end,
  min(m.resolution_verified_at) first_verified, max(m.resolution_verified_at) last_verified
from markets m join cities c using (city_key)
where m.resolution_date >= current_date - 6 and c.status = 'active'
group by 1,2 order by 2,1;

-- F1-c. What the browser sees: each city's newest settled day, as anon.
-- On 30 Sep 08:4xZ: every US city 28 Sep; 35 of 37 C cities 29 Sep (mexico_city, panama_city 28 Sep).
begin;
set local role anon;
select c.unit, c.city_key,
  (select max(for_date) from v_city_hit_history h where h.city_key = c.city_key) latest_day,
  (select count(*)      from v_city_hit_history h where h.city_key = c.city_key) settled_days
from cities c where c.status = 'active' order by c.unit desc, c.city_key;
rollback;

-- F1-d. Is the tick's P4.7 step starved? (02_FIX_SPECS.md F1, cause 3)
-- 29 Sep 06:36Z - 30 Sep 08:36Z: 4 of 15 runs skipped at <1 s; the rest reached 2-34 of ~150.
select started_at, status, detail->'budget_s' budget_s, detail->'candidates' candidates,
  detail->'unreached' unreached, detail->'evidence_captured' evidence,
  detail->'banked_checkpoints' banked, detail->'skips' skips
from ingest_log where job = 'P4.7_confirm_recent' and started_at > now() - interval '30 hours'
order by started_at;

-- =====================================================================
-- F2-a. "Hit rate, per city, per lead": how many cities the page's first 120 rows cover
-- 30 Sep, as anon: 797 rows (555 C, 242 F); first 120 = 6 C cities + 2 of 11 US.
-- After F2 the page must show all 48 active cities.
-- =====================================================================
begin;
set local role anon;
with s as (select row_number() over () rn, v.* from v_prediction_scorecard_all v
           where v.city_key in (select city_key from cities where status = 'active'))
select c.unit, count(*) total_rows, count(*) filter (where rn <= 120) rows_in_first_120,
  count(distinct s.city_key) cities, count(distinct s.city_key) filter (where rn <= 120) cities_in_first_120
from s join cities c using (city_key) group by c.unit;
rollback;

-- =====================================================================
-- F3. The forecast tournament's standing (02_FIX_SPECS.md F3.1)
-- =====================================================================
select unit, lane, n_days, champion_log_loss, live_log_loss, market_log_loss,
  gain_vs_live, gain_vs_live_lo, gain_vs_live_hi, gain_vs_market, gain_vs_market_lo, gain_vs_market_hi, computed_at
from derived_hit_summary where computed_at = (select max(computed_at) from derived_hit_summary)
order by lane, unit;

-- How many cities could pass the per-city gate (needs n_vs_live >= 20 and a positive lower bound).
select lane, count(*) cities, max(n_vs_live) max_n_vs_live,
  count(*) filter (where n_vs_live >= 20 and gain_vs_live_lo > 0) would_pass
from derived_hit_recipe group by lane;

-- Storage cost of the reasons: ladders vs band rows in 24 h (517 vs 5,687 on 30 Sep).
select (select count(*) from band_probabilities where computed_at > now() - interval '24 hours') rows_24h,
  (select count(distinct (b.market_id, bp.computed_at)) from band_probabilities bp join bands b using (band_id)
    where bp.computed_at > now() - interval '24 hours') ladders_24h;

-- =====================================================================
-- B. The archive night (03_CHECKS_DUE.md B)
-- =====================================================================
-- B-1. Every archive dataset: exported = expected = deleted.
select job, status, started_at,
  detail->>'rows' exported, detail->'prune'->>'expected_rows' expected, detail->'prune'->>'deleted' deleted,
  detail->'prune'->>'band_days_before' band_days_before, detail->'prune'->>'band_days_after' band_days_after,
  detail->>'keep_days' keep_days, detail->>'archived_through' archived_through, detail->>'file' file
from ingest_log where job like 'archive%' and started_at > current_date + interval '2 hours'
order by started_at;

-- B-7. Sizes.
select pg_size_pretty(pg_database_size(current_database())) db,
  pg_size_pretty(pg_total_relation_size('public.book_snapshots')) book_snapshots,
  (select count(*) from book_snapshots) book_rows;
select storage_pressure();

-- =====================================================================
-- C. The weekly weather-model refit (03_CHECKS_DUE.md C)
-- =====================================================================
-- C-2.
select count(distinct city_key) cities, min(fitted_at), max(fitted_at)
from derived_weather_model where fitted_at >= '2026-10-05';
-- C-3.
select version, as_of, fitted_at, n from strategy_params where param = 'city_clusters' order by fitted_at desc limit 3;

-- =====================================================================
-- Health spot checks used on 30 Sep
-- =====================================================================
-- The engine's shadow jobs on the latest intraday run.
select job, status, started_at, detail->>'earned_weights_error' weights_error, detail->'strategies_earning' earning,
  detail->'positions' positions, detail->'skipped' skipped, detail->'errors' errors
from ingest_log where job in ('signal_engine','paper_exits') order by started_at desc limit 4;
-- S10's model version by time (the refit took effect at 30 Sep 08:36Z).
select model_version, count(*), min(decided_at), max(decided_at)
from s10_shadow_checkpoints where decided_at > now() - interval '2 days' group by 1 order by 3;
-- Learning switch (off on 30 Sep).
select key, value, updated_at from settings where key = 'strategy_learning';
