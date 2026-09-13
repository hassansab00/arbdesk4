-- Phase 2A: outcome truth.
--
-- The existing fact tables are proprietary, append-only evidence. They must
-- never be rewritten, but some historic rows were frozen from an incomplete
-- observed day before a final Polymarket outcome existed. This migration adds
-- independent evidence and verified projections over those facts. Raw rows
-- remain available for audit/export; model feedback only reads projections
-- whose outcome identity has been confirmed.
begin;

create table if not exists public.weather_resolution_evidence (
  evidence_id text primary key,
  city_key text not null references public.cities(city_key),
  for_date date not null,
  observed_max_c numeric not null check (observed_max_c between -100 and 70),
  unit text not null default 'C' check (unit = 'C'),
  source_authority text not null check (length(trim(source_authority)) > 0),
  station_id text not null check (length(trim(station_id)) > 0),
  source_url text not null check (length(trim(source_url)) > 0),
  record_status text not null check (record_status in ('verified','superseded','disputed')),
  observed_at timestamptz,
  captured_at timestamptz not null default clock_timestamp(),
  parser_version text not null check (length(trim(parser_version)) > 0),
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  raw_payload jsonb not null,
  supersedes_evidence_id text references public.weather_resolution_evidence(evidence_id)
);

comment on table public.weather_resolution_evidence is
  'APPEND ONLY. Versioned authoritative station evidence for a final city-day maximum. Corrections append a new row and reference the superseded evidence; raw source payloads are never overwritten.';

create index if not exists weather_resolution_city_day_time
  on public.weather_resolution_evidence(city_key, for_date, captured_at desc);
create index if not exists weather_resolution_supersedes
  on public.weather_resolution_evidence(supersedes_evidence_id)
  where supersedes_evidence_id is not null;

alter table public.weather_resolution_evidence enable row level security;
revoke all on public.weather_resolution_evidence from public, anon, authenticated, service_role;
grant select, insert on public.weather_resolution_evidence to service_role;
-- Browser views need only the verdict fields. Raw payloads and source URLs
-- remain worker-only even in the single-user/no-login deployment.
grant select (evidence_id, city_key, for_date, observed_max_c, unit,
              source_authority, station_id, record_status, observed_at,
              captured_at, parser_version, payload_sha256,
              supersedes_evidence_id)
  on public.weather_resolution_evidence to anon, authenticated;

drop policy if exists weather_resolution_safe_read on public.weather_resolution_evidence;
create policy weather_resolution_safe_read on public.weather_resolution_evidence
  for select to anon, authenticated using (true);

drop trigger if exists weather_resolution_immutable on public.weather_resolution_evidence;
create trigger weather_resolution_immutable
  before update or delete on public.weather_resolution_evidence
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists weather_resolution_no_truncate on public.weather_resolution_evidence;
create trigger weather_resolution_no_truncate
  before truncate on public.weather_resolution_evidence
  for each statement execute function arbdesk_private.immutable_record();

-- `paper_resolution_evidence` already stores matching Gamma and CLOB payloads
-- for a binary condition. Expose only the non-secret identity/verdict columns
-- needed by security-invoker views; the raw payload stays service-only.
grant select (proof_id, condition_id, token_yes, token_no, winning_token, captured_at)
  on public.paper_resolution_evidence to anon, authenticated;
drop policy if exists paper_resolution_safe_read on public.paper_resolution_evidence;
create policy paper_resolution_safe_read on public.paper_resolution_evidence
  for select to anon, authenticated using (true);

create or replace view public.v_verified_weather_outcomes
with (security_invoker = true)
as
with ranked as (
  select e.*,
         row_number() over (
           partition by e.city_key, e.for_date
           order by e.captured_at desc, e.evidence_id desc
         ) as rn
  from public.weather_resolution_evidence e
)
select evidence_id, city_key, for_date, observed_max_c, unit,
       source_authority, station_id, observed_at, captured_at,
       parser_version, payload_sha256, supersedes_evidence_id
from ranked
where rn = 1 and record_status = 'verified';

create or replace view public.v_venue_band_resolution
with (security_invoker = true)
as
with matched as (
  select b.band_id, b.market_id, b.condition_id, b.token_yes, b.token_no,
         e.proof_id, e.winning_token, e.captured_at
  from public.bands b
  join public.paper_resolution_evidence e
    on e.condition_id = b.condition_id
   and e.token_yes = b.token_yes
   and e.token_no = b.token_no
), summarized as (
  select band_id, market_id, condition_id, token_yes, token_no,
         count(*)::integer as evidence_count,
         count(distinct winning_token)::integer as winner_count,
         max(captured_at) as confirmed_at,
         min(winning_token) as winning_token
  from matched
  group by band_id, market_id, condition_id, token_yes, token_no
)
select band_id, market_id, condition_id, token_yes, token_no,
       evidence_count, confirmed_at,
       case when winner_count = 1 then winning_token end as winning_token,
       case when winner_count = 1 then winning_token = token_yes end as settled_yes,
       case when winner_count = 1 then 'confirmed' else 'disputed' end as resolution_state
from summarized;

create or replace view public.v_venue_market_resolution
with (security_invoker = true)
as
select m.market_id, m.city_key, m.resolution_date,
       count(b.band_id)::integer as band_count,
       count(*) filter (where r.resolution_state = 'confirmed')::integer as confirmed_bands,
       count(*) filter (where r.resolution_state = 'disputed')::integer as disputed_bands,
       count(*) filter (where r.resolution_state = 'confirmed' and r.settled_yes)::integer as yes_winners,
       max(r.confirmed_at) as confirmed_at,
       case
         when count(b.band_id) = 0 then 'missing_bands'
         when count(*) filter (where r.resolution_state = 'disputed') > 0 then 'disputed'
         when count(*) filter (where r.resolution_state = 'confirmed') = count(b.band_id)
          and count(*) filter (where r.resolution_state = 'confirmed' and r.settled_yes) = 1
           then 'confirmed'
         when count(*) filter (where r.resolution_state = 'confirmed') > 0 then 'partial'
         else 'unverified'
       end as resolution_state,
       (array_agg(b.band_id order by b.band_id)
          filter (where r.resolution_state = 'confirmed' and r.settled_yes))[1] as winning_band_id
from public.markets m
left join public.bands b on b.market_id = m.market_id
left join public.v_venue_band_resolution r on r.band_id = b.band_id
group by m.market_id, m.city_key, m.resolution_date;

create or replace view public.v_verified_fact_band_outcome
with (security_invoker = true)
as
select f.*
from public.fact_band_outcome f
join public.bands b on b.band_id = f.band_id
join public.v_venue_band_resolution br on br.band_id = f.band_id
join public.v_venue_market_resolution mr on mr.market_id = b.market_id
where br.resolution_state = 'confirmed'
  and mr.resolution_state = 'confirmed'
  and f.settled_yes is not distinct from br.settled_yes;

create or replace view public.v_verified_fact_forecast_outcome
with (security_invoker = true)
as
select f.*
from public.fact_forecast_outcome f
join public.v_verified_weather_outcomes w
  on w.city_key = f.city_key and w.for_date = f.for_date
where abs(f.observed_max_c - w.observed_max_c) <= 0.01;

create or replace view public.v_outcome_evidence_health
with (security_invoker = true)
as
select
  (select count(*)::bigint from public.fact_band_outcome) as raw_band_facts,
  (select count(*)::bigint from public.v_verified_fact_band_outcome) as verified_band_facts,
  (select count(*)::bigint
     from public.fact_band_outcome f
     join public.v_venue_band_resolution r using (band_id)
    where r.resolution_state = 'confirmed'
      and f.settled_yes is distinct from r.settled_yes) as mismatched_band_facts,
  (select count(*)::bigint from public.fact_forecast_outcome) as raw_forecast_facts,
  (select count(*)::bigint from public.v_verified_fact_forecast_outcome) as verified_forecast_facts,
  (select count(*)::bigint from public.paper_resolution_evidence) as venue_evidence_rows,
  (select count(*)::bigint from public.weather_resolution_evidence) as weather_evidence_rows,
  (select count(*)::bigint from public.v_venue_market_resolution where resolution_state = 'confirmed') as confirmed_markets,
  (select max(captured_at) from public.paper_resolution_evidence) as latest_venue_evidence_at,
  (select max(captured_at) from public.weather_resolution_evidence) as latest_weather_evidence_at;

-- Existing proprietary facts remain visible in their raw tables, but these
-- two decision surfaces now consume only cross-source-confirmed outcomes.
create or replace view public.v_calibration
with (security_invoker = true)
as
select
  width_bucket(model_prob, 0, 1, 10) as bucket,
  round((width_bucket(model_prob, 0, 1, 10) - 0.5) / 10.0, 3)::numeric as predicted_mid,
  count(*)::int as n,
  round(avg(model_prob), 4) as predicted,
  round(avg(case when settled_yes then 1.0 else 0.0 end), 4) as observed,
  round(avg(case when settled_yes then 1.0 else 0.0 end) - avg(model_prob), 4) as gap,
  round(avg(market_price), 4) as market_avg,
  round(avg(abs(model_prob - case when settled_yes then 1 else 0 end)), 4) as model_mae,
  round(avg(abs(market_price - case when settled_yes then 1 else 0 end)), 4) as market_mae
from public.v_verified_fact_band_outcome
where model_prob is not null
group by 1, 2
order by 1;

create or replace view public.v_edge_realisation
with (security_invoker = true)
as
with b as (
  select
    case
      when edge_net_pp < -0.05 then '< -5pp'
      when edge_net_pp < -0.01 then '-5 to -1pp'
      when edge_net_pp <  0.01 then 'flat'
      when edge_net_pp <  0.05 then '+1 to +5pp'
      when edge_net_pp <  0.10 then '+5 to +10pp'
      else '> +10pp'
    end as edge_bucket,
    case
      when edge_net_pp < -0.05 then 1 when edge_net_pp < -0.01 then 2
      when edge_net_pp <  0.01 then 3 when edge_net_pp <  0.05 then 4
      when edge_net_pp <  0.10 then 5 else 6 end as ord,
    settled_yes, model_prob, market_price, edge_net_pp, volume_usd
  from public.v_verified_fact_band_outcome
  where edge_net_pp is not null and market_price is not null
)
select edge_bucket, min(ord) as ord, count(*)::int as n,
       round(avg(edge_net_pp) * 100, 2) as claimed_pp,
       round(avg(case when settled_yes then 1 - market_price else -market_price end) * 100, 2) as realised_pp,
       round(avg(case when settled_yes then 1.0 else 0.0 end), 4) as hit_rate,
       round(avg(market_price), 4) as avg_price,
       round(sum(coalesce(volume_usd, 0))) as volume_usd
from b group by edge_bucket order by min(ord);

-- Same Predictive contracts, but a past point is "settled" only when the
-- independent weather evidence agrees with the immutable fact row.
create or replace view public.v_forecast_convergence
with (security_invoker = true)
as
with latest as (
  select distinct on (city_key, for_date, model, lead_days)
         city_key, for_date, model, lead_days, forecast_max_c, run_at
  from public.weather_forecasts
  where for_date >= current_date - 45
    and for_date <= current_date + 16
    and forecast_max_c is not null
  order by city_key, for_date, model, lead_days, run_at desc
), observed as (
  select city_key, for_date, max(observed_max_c) as observed_max_c
  from public.v_verified_fact_forecast_outcome
  where for_date >= current_date - 45
  group by city_key, for_date
)
select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at,
       o.observed_max_c,
       case when o.observed_max_c is not null
            then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
       l.for_date < current_date as is_past,
       o.observed_max_c is not null as is_settled
from latest l
left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;

create or replace view public.v_prediction_scorecard
with (security_invoker = true)
as
select city_key, model, lead_days,
       count(*)::int as n_days,
       round(avg(abs_error_c), 3) as mae_c,
       round(avg(error_c), 3) as bias_c,
       round(stddev_samp(error_c), 3) as error_sd_c,
       round(max(abs_error_c), 2) as worst_c,
       round(100.0 * count(*) filter (where floor(forecast_max_c) = floor(observed_max_c))::numeric
             / nullif(count(*), 0), 1) as hit_rate_pct,
       round(100.0 * count(*) filter (where abs_error_c <= 1.0)::numeric
             / nullif(count(*), 0), 1) as within_1c_pct,
       min(for_date) as since, max(for_date) as until
from public.v_verified_fact_forecast_outcome
where for_date >= current_date - 365
group by city_key, model, lead_days
having count(*) >= 5
order by city_key, model, lead_days;

create or replace view public.v_edge_scaling
with (security_invoker = true)
as
with b as (
  select width_bucket(edge_net_pp, 0, 25, 5) as edge_bucket,
         edge_net_pp, model_prob, market_price, settled_yes
  from public.v_verified_fact_band_outcome
  where edge_net_pp is not null and market_price is not null
)
select edge_bucket,
       concat(((edge_bucket - 1) * 5)::text, '-', (edge_bucket * 5)::text, ' pp') as edge_band,
       count(*)::int as n,
       round(avg(edge_net_pp), 2) as claimed_edge_pp,
       round(100.0 * avg(case when settled_yes then 1 - market_price else -market_price end), 2) as realised_pp,
       round(100.0 * avg(case when settled_yes then 1 else 0 end), 1) as settled_yes_pct,
       round(100.0 * avg(model_prob), 1) as mean_model_prob_pct
from b where edge_bucket between 1 and 5
group by edge_bucket order by edge_bucket;

revoke all on public.v_verified_weather_outcomes,
              public.v_venue_band_resolution,
              public.v_venue_market_resolution,
              public.v_verified_fact_band_outcome,
              public.v_verified_fact_forecast_outcome,
              public.v_outcome_evidence_health from public;
grant select on public.v_verified_weather_outcomes,
                public.v_venue_band_resolution,
                public.v_venue_market_resolution,
                public.v_verified_fact_band_outcome,
                public.v_verified_fact_forecast_outcome,
                public.v_outcome_evidence_health,
                public.v_calibration,
                public.v_edge_realisation,
                public.v_forecast_convergence,
                public.v_prediction_scorecard,
                public.v_edge_scaling
  to anon, authenticated, service_role;

commit;
