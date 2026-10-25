-- da_floor:v1 first look (docs/P11_DA_FLOOR_PREREG.md), run read-only against
-- production on 25 Oct 2026 ~11:00Z (first at ~10:40Z with top-1 ties to the
-- lowest band_index; rerun with the prereg's bucket-id ties, Codex on #434:
-- one line changed, 15 Oct noon served top-1 20 -> 19). One line per (target date, checkpoint) of
-- the scored rows; the result's md5 was 7a1dff736d20affe227968329fcb4778
-- (12,303 bytes) and aggregates.txt has the same md5.
--
-- Scored row: a da_floor:v1 row at one of the five same-day checkpoints, matched
-- to the served first call of the same city, date and checkpoint (the first
-- prediction_checkpoints row by decided_at, checkpoint_id: v_checkpoint_calls'
-- first_call), graded by fact_checkpoint_outcome, with a day-ahead call, a served
-- ladder not calibrated, the winner on the ladder. Every scored row's two ladders
-- hold the same buckets (checked: 4,666 of 4,666).
--
-- Scores as fec-v1 (tools/fec_same_day.score): log loss -ln(max(p_winner, 1e-6)),
-- top-1 the largest p with ties broken by bucket id, as the prereg fixes it (the
-- smallest band_id in byte order, as scripts/variant_shadow.py picks its top
-- bucket), Brier summed over the ladder's buckets.
--
-- Columns: date | checkpoint | n | sum(ll_served - ll_da_floor) | top-1 served |
-- top-1 da_floor | sum(brier_served - brier_da_floor) | n book whole |
-- sum(ll_served - ll_market) | sum(ll_da_floor - ll_market) | market top-1 |
-- n with rd1 | sum(ll_served - ll_rd1) | sum(ll_da_floor - ll_rd1) | rd1 top-1
-- (the market and rd1: descriptive, on the rows where they exist; rd1 is
-- s10_shadow_checkpoints model_version rd1:2026-09-25:f5372ebb05, its first row).
with v as (select * from variant_shadow_checkpoints where variant_version='da_floor:v1'
             and checkpoint in ('morning','noon','prepeak_2h','prepeak_1h','postpeak_1h')),
pc as (select * from (select q.*, row_number() over (partition by city_key, target_date, checkpoint order by decided_at, checkpoint_id) rn from prediction_checkpoints q) z where rn=1),
j as (select v.shadow_id, v.city_key, v.target_date, v.checkpoint, v.probs dprobs, pc.checkpoint_id, pc.probs sprobs, f.winner_band_id
      from v join pc on pc.city_key=v.city_key and pc.target_date=v.target_date and pc.checkpoint=v.checkpoint
             join fact_checkpoint_outcome f on f.checkpoint_id=pc.checkpoint_id
      where v.day_ahead_centre_c is not null and not coalesce(v.served_calibrated,false) and f.settled_at is not null
        and f.winner_band_id is not null and f.ladder_has_winner and v.probs ? f.winner_band_id),
lad as (select j.shadow_id, e.key band_id, b.band_index, (j.sprobs->>e.key)::float8 ps, (j.dprobs->>e.key)::float8 pd, (e.key = j.winner_band_id) won
        from j cross join lateral jsonb_each(j.sprobs) e join bands b on b.band_id = e.key::uuid),
r as (select j.shadow_id, j.city_key, j.target_date, j.checkpoint, j.winner_band_id,
   -ln(greatest(max(ps) filter (where won),1e-6)) ll_s, -ln(greatest(max(pd) filter (where won),1e-6)) ll_d,
   ((array_agg(won order by ps desc, band_id collate "C" asc))[1])::int top_s,
   ((array_agg(won order by pd desc, band_id collate "C" asc))[1])::int top_d,
   sum((ps - won::int)^2) br_s, sum((pd - won::int)^2) br_d
  from j join lad using (shadow_id) group by 1,2,3,4,5),
m as (select r.*, c.market_complete, c.log_loss_market::float8 ll_m, c.market_hit::int km,
        s10.ll_r, s10.top_r
      from r left join v_checkpoint_calls c on c.first_call and c.city_key=r.city_key and c.for_date=r.target_date and c.checkpoint=r.checkpoint
      left join lateral (select -ln(greatest(coalesce((s.probs->>r.winner_band_id)::float8,0),1e-6)) ll_r,
                                (s.top_band_id = r.winner_band_id)::int top_r
                         from s10_shadow_checkpoints s where s.model_version='rd1:2026-09-25:f5372ebb05' and s.city_key=r.city_key
                           and s.target_date=r.target_date and s.checkpoint=r.checkpoint order by s.decided_at limit 1) s10 on true),
g as (select target_date::text d, checkpoint c, count(*) n, round(sum(ll_s-ll_d)::numeric,9) s_ll, sum(top_s) k_s, sum(top_d) k_d, round(sum(br_s-br_d)::numeric,9) s_br,
   count(*) filter (where market_complete) nm, round(coalesce(sum(ll_s-ll_m) filter (where market_complete),0)::numeric,9) s_sm,
   round(coalesce(sum(ll_d-ll_m) filter (where market_complete),0)::numeric,9) s_dm, coalesce(sum(km) filter (where market_complete),0) km,
   count(ll_r) nr, round(coalesce(sum(ll_s-ll_r) filter (where ll_r is not null),0)::numeric,9) s_sr,
   round(coalesce(sum(ll_d-ll_r) filter (where ll_r is not null),0)::numeric,9) s_dr, coalesce(sum(top_r),0) kr
  from m group by 1,2)
select md5(s) md5, length(s), s from (select string_agg(concat_ws('|',d,c,n,s_ll,k_s,k_d,s_br,nm,s_sm,s_dm,km,nr,s_sr,s_dr,kr), E'\n' order by d,c) s from g) x;
