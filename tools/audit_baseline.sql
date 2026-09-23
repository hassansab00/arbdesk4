-- tools/audit_baseline.sql - the plan v2 baseline (docs/AD4_IMPROVEMENT_PLAN.md,
-- step P0.1 and Appendix A).
--
-- Read-only. Run it against the live database with the Supabase SQL tool, one
-- statement at a time, and record the output with its timestamp. The results
-- of the first run are in docs/baseline_2026-09-23.md.
--
-- A8, A9, A10 and A12 read tables and views that later plan steps create
-- (prediction_checkpoints, v_checkpoint_scoreboard, decisions,
-- strategy_params). They are listed at the end and cannot run until those
-- steps land. A11 is a shell command (the Actions API), not SQL.

-- ---- B0: size ---------------------------------------------------------------
select now() as measured_at, storage_pressure();

select relname, pg_total_relation_size(relid) / 1048576 as mb, n_live_tup
from pg_stat_user_tables
order by pg_total_relation_size(relid) desc
limit 12;

-- ---- A1: PUBLIC or anon execute on the four destructive functions -> 0 ----
select p.proname
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef
  and p.proname in ('prune_trades', 'prune_exported_paper_trades',
                    'paper_desk_reset', 'paper_desk_archive')
  and has_function_privilege('anon', p.oid, 'execute');

-- ---- A2: SECURITY DEFINER functions anon can execute -----------------------
-- The plan's target (after P1.2) is 0 outside a read-only allowlist. The P1.1
-- migration defines that allowlist; until then this counts all of them.
select count(*)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef
  and has_function_privilege('anon', p.oid, 'execute');

-- ---- A4: station vs venue agreement -> >= 97% ------------------------------
select round(avg(agreement_pct), 1) as avg_pct,
       sum(we_read_below) as below, sum(we_read_above) as above
from v_settlement_agreement;

-- ---- A5: past-dated markets still closed=false -> 0 ------------------------
select count(*)
from markets m join cities c using (city_key)
where m.closed = false
  and m.resolution_date < (now() at time zone c.timezone)::date - 1;

-- ---- A6: probability mass on impossible buckets, latest run -> 0 and 0 -----
-- The P3.1 rule: R = venue_round(observed floor), b_R the bucket holding R, and
-- a bucket is impossible iff it lies entirely below b_R - 1 (two or more
-- buckets under the reading). The bucket just below keeps q_down x the atom.
with latest as (
  select distinct on (bp.band_id) bp.band_id, bp.raw_prob, bp.observed_floor_c
  from band_probabilities bp
  where bp.computed_at > now() - interval '6 hours'
  order by bp.band_id, bp.computed_at desc),
j as (
  select m.market_id, m.city_key, m.resolution_date, m.unit, b.band_id, b.band_lo, b.band_hi,
         b.open_low, b.open_high, l.raw_prob, l.observed_floor_c,
         venue_round(l.observed_floor_c, m.unit) as r,
         row_number() over (partition by m.market_id
                            order by b.open_low desc, b.open_high, b.band_lo) as idx,
         row_number() over (partition by m.market_id order by l.raw_prob desc) as rk
  from latest l
  join v_canonical_bands b on b.band_id = l.band_id
  join v_canonical_markets m on m.market_id = b.market_id),
r_idx as (
  select market_id, min(idx) as idx_r
  from j
  where r is not null
    and ((open_low and r < band_hi) or (open_high and r >= band_lo)
         or (not open_low and not open_high and r >= band_lo and r < band_hi))
  group by market_id),
scored as (
  select j.*, (ri.idx_r is not null and j.idx < ri.idx_r - 1) as impossible
  from j left join r_idx ri using (market_id))
select count(*) as bands,
       count(distinct (city_key, resolution_date)) as ladders,
       count(*) filter (where impossible and raw_prob > 0.05) as impossible_priced_over_5pct,
       count(*) filter (where rk = 1 and impossible) as top_pick_is_impossible,
       round(sum(raw_prob) filter (where impossible), 4) as impossible_mass
from scored;

-- ---- A7: prices written after the market's local day ended -----------------
-- The plan's acceptance counts rows since the P3.2 deploy. The baseline counts
-- the last 7 days.
select count(*) as rows_after_local_day, count(distinct bp.band_id) as bands,
       min(bp.computed_at), max(bp.computed_at)
from band_probabilities bp
join v_canonical_bands b using (band_id)
join v_canonical_markets m using (market_id)
join cities c using (city_key)
where bp.computed_at > now() - interval '7 days'
  and (bp.computed_at at time zone c.timezone)::date > m.resolution_date;

-- ---- B1: decision-time hit rates (pre-P4 proxy for A9) ---------------------
-- For each venue-resolved ladder (13-21 Sep, the audit's window) and each
-- local-clock checkpoint:
--   model top     = the band with the highest calibrated_prob (what
--                   edge_engine trades on), from each band's newest
--                   band_probabilities row in the 6 h up to the checkpoint;
--   market top    = the band with the highest mid (best_ask when the mid is
--                   null), from each band's newest book_snapshots row in the
--                   3 h up to the checkpoint.
-- Hit rates count only ladders where every band has both a price and a book
-- at that checkpoint (both_full). The pre-peak checkpoints need
-- derived_weather_peak and are left to P4.2. The two lookback windows are
-- this query's own choice; P4 replaces this proxy with checkpoint rows
-- written at the time.
with mk as (
  select vm.market_id, vm.city_key, vm.resolution_date, vm.winning_band_id, c.timezone
  from v_venue_market_resolution vm join cities c using (city_key)
  where vm.winning_band_id is not null
    and vm.resolution_date between '2026-09-13' and '2026-09-21'),
cp as (
  select mk.*, x.checkpoint, ((mk.resolution_date + x.dt) at time zone mk.timezone) as t
  from mk cross join (values ('d1_eve',    interval '-6 hours'),
                             ('day_start', interval '0 hours'),
                             ('morning',   interval '9 hours'),
                             ('noon',      interval '12 hours')) x(checkpoint, dt)),
bands as (
  select cp.*, b.band_id from cp join v_canonical_bands b on b.market_id = cp.market_id),
priced as (
  select bands.*, p.calibrated_prob, p.raw_prob, k.mid, k.best_ask
  from bands
  left join lateral (
    select calibrated_prob, raw_prob from band_probabilities bp
    where bp.band_id = bands.band_id and bp.computed_at <= bands.t
      and bp.computed_at > bands.t - interval '6 hours'
    order by bp.computed_at desc limit 1) p on true
  left join lateral (
    select mid, best_ask from book_snapshots bs
    where bs.band_id = bands.band_id and bs.observed_at <= bands.t
      and bs.observed_at > bands.t - interval '3 hours'
    order by bs.observed_at desc limit 1) k on true),
per_day as (
  select checkpoint, market_id, winning_band_id,
    count(*) as n_bands, count(calibrated_prob) as n_priced,
    count(coalesce(mid, best_ask)) as n_booked,
    (array_agg(band_id order by calibrated_prob desc nulls last))[1] as model_top,
    (array_agg(band_id order by raw_prob desc nulls last))[1] as raw_top,
    (array_agg(band_id order by coalesce(mid, best_ask) desc nulls last))[1] as mkt_top
  from priced group by 1, 2, 3)
select checkpoint,
  count(*) as city_days,
  count(*) filter (where n_priced = n_bands and n_booked = n_bands) as both_full,
  round(100.0 * avg((model_top = winning_band_id)::int)
        filter (where n_priced = n_bands and n_booked = n_bands), 1) as model_cal_hit_pct,
  round(100.0 * avg((raw_top = winning_band_id)::int)
        filter (where n_priced = n_bands and n_booked = n_bands), 1) as model_raw_hit_pct,
  round(100.0 * avg((mkt_top = winning_band_id)::int)
        filter (where n_priced = n_bands and n_booked = n_bands), 1) as market_hit_pct
from per_day group by 1 order by 1;

-- ---- Not runnable yet (their tables come from later steps) -----------------
-- A8  prediction_checkpoints coverage              (P4.1 / P4.2)
-- A9  v_checkpoint_scoreboard                      (P4.6)
-- A10 decisions re-deciding every open position    (P5.11)
-- A12 strategy_params within bounds                (P5.8)
