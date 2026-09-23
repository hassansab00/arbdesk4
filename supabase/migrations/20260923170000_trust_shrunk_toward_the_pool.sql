-- ===========================================================================
-- OBSERVATION TRUST, SHRUNK TOWARD THE POOL, WITH q_up AND q_down
-- (plan v2 P2.3).
--
-- cities.observation_trust was a raw fraction, null under ten ladders, and it
-- was fitted on the worse source. After P2.1 (every report the station files)
-- and P2.2 (the station's own reports, rounded the venue's way), measured 23
-- Sep on v_settlement_agreement:
--
--     638 settled ladders, 51 cities, 632 agree (99.06%)
--     misses: 4 one bucket where the venue settled BELOW our reading,
--             0 one bucket above, 2 further than one bucket
--     stored trust still on the old source: Beijing and Singapore 0.69,
--     Guangzhou 0.77, where the corrected source reads 0.92
--
-- Each city has about 12 ladders, so a raw fraction swings a whole 8 points
-- on one ladder. Shrink toward the pooled rate instead - a Beta prior with
-- weight k = 20 ladders (the plan's k):
--
--     estimate = (hits + k * pooled) / (n + k)
--
-- RULE 11, every learned parameter here:
--   prior        the pooled rate across every city with a reading
--   bounds       [0, 1] for trust, [0, 0.5] for q_up and q_down
--   min sample   the prior's weight: under 20 ladders a city sits nearer the
--                pool than its own record
--   max change   0.10 per nightly refresh; the first refresh of a new version
--                re-baselines, because the old values were fitted on a source
--                this plan replaced, not learned toward
--   version      observation_trust_version on the row, with its time
-- ===========================================================================

alter table public.cities add column if not exists observation_trust_n integer;
alter table public.cities add column if not exists observation_q_up numeric;
alter table public.cities add column if not exists observation_q_down numeric;
alter table public.cities add column if not exists observation_trust_version text;
alter table public.cities add column if not exists observation_trust_at timestamptz;

comment on column public.cities.observation_trust_n is
  'Settled ladders with a station reading behind observation_trust (plan v2 P2.3).';
comment on column public.cities.observation_q_up is
  'Shrunk rate at which the venue settled exactly one bucket ABOVE our station reading. P3.1 reads it.';
comment on column public.cities.observation_q_down is
  'Shrunk rate at which the venue settled exactly one bucket BELOW our station reading. P3.1 reads it.';

create or replace function public.refresh_observation_trust()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $ad4$
declare
  k          constant numeric := 20;
  max_step   constant numeric := 0.10;
  v_version  constant text    := 'beta_k20_pooled_v1';
  v_pool     record;
  v_set      int := 0;
begin
  if to_regclass('public.v_settlement_agreement') is null then
    return jsonb_build_object('ok', false,
      'error', 'v_settlement_agreement missing - run sql/ad4_82_settlement_agreement.sql');
  end if;

  select sum(agreed)::numeric          / nullif(sum(with_a_reading), 0) as p_trust,
         sum(one_bucket_up)::numeric   / nullif(sum(with_a_reading), 0) as p_up,
         sum(one_bucket_down)::numeric / nullif(sum(with_a_reading), 0) as p_down,
         sum(with_a_reading)                                              as n_all
    into v_pool
    from public.v_settlement_agreement;

  if v_pool.n_all is null or v_pool.n_all = 0 then
    return jsonb_build_object('ok', false, 'error', 'no settled ladder has a reading - nothing to fit');
  end if;

  with fit as (
    select a.city_key,
           coalesce(a.with_a_reading, 0)::int as n,
           least(1.0, greatest(0.0,
             (coalesce(a.agreed, 0)          + k * v_pool.p_trust) / (coalesce(a.with_a_reading, 0) + k))) as t,
           least(0.5, greatest(0.0,
             (coalesce(a.one_bucket_up, 0)   + k * v_pool.p_up)    / (coalesce(a.with_a_reading, 0) + k))) as up,
           least(0.5, greatest(0.0,
             (coalesce(a.one_bucket_down, 0) + k * v_pool.p_down)  / (coalesce(a.with_a_reading, 0) + k))) as down
      from public.v_settlement_agreement a
  ),
  stepped as (
    select f.city_key, f.n,
           -- A new version re-baselines; otherwise move at most max_step.
           case when c.observation_trust_version is distinct from v_version or c.observation_trust is null
                then f.t
                else c.observation_trust + least(max_step, greatest(-max_step, f.t - c.observation_trust)) end as t,
           case when c.observation_trust_version is distinct from v_version or c.observation_q_up is null
                then f.up
                else c.observation_q_up + least(max_step, greatest(-max_step, f.up - c.observation_q_up)) end as up,
           case when c.observation_trust_version is distinct from v_version or c.observation_q_down is null
                then f.down
                else c.observation_q_down + least(max_step, greatest(-max_step, f.down - c.observation_q_down)) end as down
      from fit f join public.cities c on c.city_key = f.city_key
  )
  update public.cities c
     set observation_trust         = round(s.t, 4),
         observation_q_up          = round(s.up, 4),
         observation_q_down        = round(s.down, 4),
         observation_trust_n       = s.n,
         observation_trust_version = v_version,
         observation_trust_at      = now()
    from stepped s
   where s.city_key = c.city_key;
  get diagnostics v_set = row_count;

  return jsonb_build_object('ok', true, 'updated', v_set, 'version', v_version,
    'pooled_trust', round(v_pool.p_trust, 4), 'pooled_q_up', round(v_pool.p_up, 4),
    'pooled_q_down', round(v_pool.p_down, 4), 'ladders', v_pool.n_all);
end;
$ad4$;

comment on function public.refresh_observation_trust() is
  'Plan v2 P2.3: observation_trust, q_up and q_down per city from v_settlement_agreement, shrunk toward the pooled rate (Beta prior, k = 20), bounded, moving at most 0.10 a night within one version. Called by pipeline_daily.';

revoke all on function public.refresh_observation_trust() from public, anon, authenticated;
grant execute on function public.refresh_observation_trust() to service_role;

do $ad4$
begin
  -- The view's one_bucket_* columns arrive with sql/ad4_82; until then the
  -- refresh would fail, so only run it where they exist.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'v_settlement_agreement'
                and column_name = 'one_bucket_up') then
    perform public.refresh_observation_trust();
  else
    raise notice 'v_settlement_agreement has no one_bucket_up yet - apply sql/ad4_82 first';
  end if;
end
$ad4$;
