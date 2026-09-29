-- ===========================================================================
-- THE EDGES KEEP THE PRICES THE RECORD READS (plan v2 P1.6 phase 3, step 3.1,
-- 29 Sep)
--
-- Hassan, 29 Sep: "PROCEED WIT TE NEXT STEP".
--
-- edges keeps every pricing for 14 days (7 under pressure) and the newest of
-- each band and side for ever: 97,414 rows, 32.5 MB (29 Sep). Two readers take
-- a SUPERSEDED row of it - the market's YES price as it stood at a cutoff:
--
--   v_hit_ladders             the newest by 18:00 local on the eve
--                             (hit_tournament.py, 120 days)
--   v_city_hit_history_live   the newest before the local day (the hit-and-
--                             miss page through mv_city_hit_history, and the
--                             edge engine's against-market gate, 30 days)
--
-- and the prune has been taking both. Measured 29 Sep: v_city_hit_history has
-- no head-to-head day before 22 Sep (0 of 335 city-days 13-21 Sep; 48 a day
-- since), and v_hit_ladders no market price before 23 Sep.
--
-- This copies those two rows of every band - its marks - into
-- derived_edge_marks once the cutoff is six hours past (freeze_edge_marks,
-- nightly in common.refresh_feature_cache), makes both readers take the
-- frozen price first, and makes v_prunable_edge_history hold back every mark
-- not copied yet. The archive's keep for edges then drops to two days
-- (scripts/archive_observations.py), the prune function's own floor.
--
-- The same text as sql/ad4_80 (table, v_edge_marks_live,
-- v_prunable_edge_history), sql/ad4_88 (v_hit_ladders), sql/ad4_85
-- (v_city_hit_history, which sql/ad4_89 serves as v_city_hit_history_live ->
-- mv_city_hit_history -> v_city_hit_history) and sql/ad4_97
-- (freeze_edge_marks). The page cache exists only where sql/ad4_89 ran; its
-- columns are unchanged, so the materialized view over it stands.
--
-- Proven before applying (tools/p16_step31_proof.py), one REPEATABLE READ
-- snapshot: both readers return the same rows as live with the marks table
-- empty, filled, and with edges cut at two days. Numbers in
-- docs/PLAN_PROGRESS.md.
-- ===========================================================================

create table if not exists public.derived_edge_marks (
  band_id      uuid        not null,
  mark         text        not null check (mark in ('eve', 'day')),
  cutoff_at    timestamptz not null,
  computed_at  timestamptz not null,
  market_price numeric,
  edge_id      bigint,
  source       text        not null default 'edges',
  frozen_at    timestamptz not null default now(),
  primary key (band_id, mark)
);

comment on table public.derived_edge_marks is
  'Each band''s YES edge at the two cutoffs the record reads - the newest by 18:00 local on the eve (mark eve, v_hit_ladders) and the newest before the local day (mark day, v_city_hit_history) - copied by freeze_edge_marks once the cutoff is six hours past, so the edges prune can take the rest. Never rewritten (plan v2 P1.6 phase 3).';

alter table public.derived_edge_marks enable row level security;
revoke all on public.derived_edge_marks from public, anon, authenticated;
grant select, insert, update, delete on public.derived_edge_marks to service_role;

-- Every band's marks as edges holds them now. What freeze_edge_marks copies,
-- and what the view below holds back until it has.
create or replace view public.v_edge_marks_live as
select k.band_id, k.mark, k.cutoff_at, x.edge_id, x.computed_at, x.market_price
  from (
    select b.band_id, v.mark, v.cutoff_at
      from public.bands b
      join public.markets m on m.market_id = b.market_id
      left join public.cities c on c.city_key = m.city_key
      cross join lateral (values
        ('eve'::text, (((m.resolution_date - 1)::timestamp + interval '18 hours')
                        at time zone coalesce(c.timezone, 'UTC'))),
        ('day'::text, (m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC')))
      ) v(mark, cutoff_at)
     where m.resolution_date is not null
  ) k
  cross join lateral (
    select e.edge_id, e.computed_at, e.market_price
      from public.edges e
     where e.band_id = k.band_id
       and e.side = 'YES'
       and e.computed_at <= k.cutoff_at
       and (k.mark = 'eve' or e.computed_at < k.cutoff_at)
     order by e.computed_at desc
     limit 1) x;

comment on view public.v_edge_marks_live is
  'Each band''s YES edge at its two cutoffs as edges holds it now: the newest by 18:00 local on the eve (eve) and the newest before the local day (day). freeze_edge_marks copies it; v_prunable_edge_history holds each one back until it has (plan v2 P1.6 phase 3).';

revoke all on public.v_edge_marks_live from public, anon, authenticated;
grant select on public.v_edge_marks_live to service_role;

create or replace view v_prunable_edge_history as
with ranked as (
  select e.edge_id,
         e.band_id,
         e.side,
         e.computed_at,
         -- Newest first within the band AND SIDE, because YES and NO are
         -- priced separately and v_latest_edge keeps one of each. Ranking by
         -- band alone would offer up the newest NO row of every band.
         row_number() over (partition by e.band_id, e.side
                            order by e.computed_at desc) as rn
    from public.edges e
),
-- A mark derived_edge_marks does not hold yet, at the same cutoff: the
-- readers match on it, so a frozen price for another cutoff serves nobody.
unfrozen_marks as (
  select m.edge_id
    from public.v_edge_marks_live m
   where not exists (select 1 from public.derived_edge_marks d
                      where d.band_id = m.band_id and d.mark = m.mark
                        and d.cutoff_at = m.cutoff_at)
)
select s.*
  from public.edges s
  join ranked r on r.edge_id = s.edge_id
  left join unfrozen_marks u on u.edge_id = s.edge_id
 where r.rn > 1
   and u.edge_id is null;

comment on view v_prunable_edge_history is
  'Edge rows that are not the newest pricing of their band and side, nor a mark (v_edge_marks_live) derived_edge_marks does not hold yet. v_latest_edge takes exactly one row per band and side and every page is built on it; v_hit_ladders and v_city_hit_history read the marks, frozen first. Age is applied by the caller.';

create or replace view public.v_hit_ladders as
with day as (
  select o.city_key, o.for_date,
         (((o.for_date - 1)::timestamp + interval '18 hours') at time zone c.timezone) as cutoff_at
    from public.v_fact_band_outcome_clean o
    join public.cities c using (city_key)
   group by o.city_key, o.for_date, c.timezone
  having count(*) filter (where o.settled_yes) = 1
)
select d.city_key, d.for_date, d.cutoff_at, o.band_id,
       cb.band_lo, cb.band_hi, cb.open_low, cb.open_high, cm.unit,
       o.settled_yes, v.observed_max_c,
       (select coalesce(bp.calibrated_prob, bp.raw_prob)
          from public.band_probabilities bp
         where bp.band_id = o.band_id and bp.computed_at <= d.cutoff_at
         order by bp.computed_at desc, bp.prob_id desc limit 1)          as live_prob,
       -- The eve's mark, frozen once its cutoff passed (derived_edge_marks,
       -- sql/ad4_80), else the edge itself: the edges prune keeps two days
       -- and never takes a mark it has not copied (plan v2 P1.6 phase 3).
       case when k.band_id is not null then k.market_price
            else (select e.market_price
                    from public.edges e
                   where e.band_id = o.band_id and e.side = 'YES' and e.computed_at <= d.cutoff_at
                   order by e.computed_at desc limit 1) end               as market_price
  from day d
  join public.v_fact_band_outcome_clean o on o.city_key = d.city_key and o.for_date = d.for_date
  join public.v_canonical_bands cb on cb.band_id = o.band_id
  join public.v_canonical_markets cm on cm.market_id = cb.market_id
  left join public.v_verified_weather_outcomes v
         on v.city_key = d.city_key and v.for_date = d.for_date
  left join public.derived_edge_marks k
         on k.band_id = o.band_id and k.mark = 'eve' and k.cutoff_at = d.cutoff_at;

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
         p.forecast_max_c, p.sigma_c, p.confidence, p.regime_label,
         p.computed_at   as priced_at,
         case when k.band_id is not null then k.market_price
              else e.market_price end as market_pre
    from ladder l
    join settled d on d.city_key = l.city_key and d.for_date = l.for_date
    -- The desk's probability as it stood before the day began: the one it
    -- would have traded on (calibrated where calibration existed).
    left join lateral (
      select coalesce(bp.calibrated_prob, bp.raw_prob) as prob,
             bp.forecast_max_c, bp.sigma_c, bp.confidence, bp.regime_label, bp.computed_at
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
  round(h.brier_uniform_common::numeric, 4)                                 as brier_uniform_common
from model_picks m
left join h2h h on h.city_key = m.city_key and h.for_date = m.for_date
left join cities c on c.city_key = m.city_key
where m.winner is not null
order by m.for_date desc, m.city_key;
$v$;
  end if;
end
$mig$;

create or replace function public.freeze_edge_marks()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_written int;
begin
  insert into derived_edge_marks (band_id, mark, cutoff_at, computed_at, market_price, edge_id, source)
  select m.band_id, m.mark, m.cutoff_at, m.computed_at, m.market_price, m.edge_id, 'edges'
    from v_edge_marks_live m
   where m.cutoff_at < now() - interval '6 hours'
     and not exists (select 1 from derived_edge_marks d
                      where d.band_id = m.band_id and d.mark = m.mark)
  on conflict (band_id, mark) do nothing;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_edge_marks),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_edge_marks() is
  'Copy each band''s YES edge at its eve and pre-day cutoffs (v_edge_marks_live) into derived_edge_marks once the cutoff is six hours past; a frozen mark is never rewritten (plan v2 P1.6 phase 3). Called once a night by common.refresh_feature_cache; the edges prune holds back every mark not copied yet.';

revoke all on function public.freeze_edge_marks() from public, anon, authenticated;
grant execute on function public.freeze_edge_marks() to service_role;
