-- ===========================================================================
-- THE TRAJECTORY'S HOURS AND THE HIT TOURNAMENT'S FORECASTS OUTLAST THE
-- WEATHER TABLES' KEEP (plan v2 P1.6 phase 2, step 6 part (b), 29 Sep)
--
-- Hassan, 29 Sep: "do step 6 ten step 5". Step 5 lowers the weather tables'
-- keep to about 30 days; trajectory.py and hit_tournament.py read 120.
--
-- 1. derived_city_day_hours: each city's local day as its 24 hourly maxima,
--    written by refresh_city_day_hours every night while the day is whole,
--    never rewritten once the prune has cut into it.
-- 2. v_trajectory_evidence: the readings for whole days, the cache before
--    them; its as-of climb over the 30 days before each day - the window the
--    served profile uses (#264) - instead of every earlier day the readings
--    held (a window the prune chose: 60 days lately).
-- 3. derived_hit_forecasts and freeze_hit_forecasts; v_hit_forecasts_live is
--    the old v_hit_forecasts; v_hit_forecasts serves it for the days both
--    forecast tables hold and the frozen rows before.
-- 4. prune_observations, prune_forecasts and prune_forecast_models refuse to
--    delete what these have not kept. Their bodies are the live ones
--    (20260914110000 and 20260929120000) with that one guard added.
--
-- The bodies are the ones in sql/ad4_86_trajectory.sql, ad4_88_hit_tournament
-- .sql, ad4_97_evidence_cache.sql and ad4_95_prune_forecast_models.sql.
-- Nothing is deleted. The caches fill on the next refresh. Re-runnable.
-- ===========================================================================

create table if not exists public.derived_city_day_hours (
  city_key    text        not null,
  obs_date    date        not null,          -- the city's local date
  temp_c      numeric[]   not null,          -- 24 slots: slot h + 1 is local hour h
  n_hours     int         not null,          -- slots holding a reading
  computed_at timestamptz not null default now(),
  primary key (city_key, obs_date)
);

comment on table public.derived_city_day_hours is
  'Per city and local day: the highest reading in each local hour (24 slots, slot h + 1 is hour h), cached from weather_observations while the day is whole and never rewritten once the prune has cut into it. What v_trajectory_evidence reads for the days the readings no longer hold (plan v2 P1.6 phase 2).';

alter table public.derived_city_day_hours enable row level security;
revoke all on public.derived_city_day_hours from anon, authenticated;
grant select, insert, update, delete on public.derived_city_day_hours to service_role;

create table if not exists public.derived_hit_forecasts (
  city_key       text        not null,
  for_date       date        not null,
  lane           text        not null,
  model          text        not null,
  forecast_max_c numeric,
  known_at       timestamptz not null,
  frozen_at      timestamptz not null default now(),
  primary key (city_key, for_date, lane, model, known_at)
);

comment on table public.derived_hit_forecasts is
  'v_hit_forecasts_live as it stood the last night both forecast tables held the day whole: what v_hit_forecasts serves for the days the forecast tables no longer hold (plan v2 P1.6 phase 2). Written by freeze_hit_forecasts.';

alter table public.derived_hit_forecasts enable row level security;
revoke all on public.derived_hit_forecasts from public, anon, authenticated;
grant select, insert, update, delete on public.derived_hit_forecasts to service_role;

create or replace view v_trajectory_evidence as
with
-- THE FIRST WHOLE DAY OF EACH CITY (plan v2 P1.6 phase 2). The prune cuts
-- every city at one instant; a local day that began before the oldest
-- reading held is cut (refresh_feature_cache's rule). From that day on the
-- readings are whole and read directly; before it the day comes from
-- derived_city_day_hours, written every night while it was whole.
held as (
  select min(valid_at) as oldest from weather_observations
),
whole_from as (
  select c.city_key,
         coalesce(case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                              at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                       then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                       else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date end,
                  'infinity'::date) as first_whole
  from cities c cross join held h
),
hourly as (
  select o.city_key,
         (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as local_date,
         extract(hour from o.valid_at at time zone coalesce(c.timezone, 'UTC'))::int as local_hour,
         max(o.temp_c)                                                          as temp_c
  from weather_observations o
  join cities c on c.city_key = o.city_key
  join whole_from f on f.city_key = o.city_key
  where o.temp_c is not null
    and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= f.first_whole
  group by 1, 2, 3
  union all
  select d.city_key, d.obs_date, (s.slot - 1)::int, s.temp_c
  from derived_city_day_hours d
  join whole_from f on f.city_key = d.city_key
  cross join lateral unnest(d.temp_c) with ordinality as s(temp_c, slot)
  where d.obs_date < f.first_whole
    and s.temp_c is not null
),
-- THE CLIMB PROFILE AS OF THE DAY BEFORE (plan v2 P3.4). derived_climb_profile
-- is measured over every day it has, the graded day included, so a
-- trajectory fitted against it had already seen part of the answer. These
-- columns carry the same statistic built only from the days BEFORE each row's
-- date - the window stops one day short - and trajectory.py fits on them.
--
-- THE 30 DAYS BEFORE IT, as the profile the engine serves (29 Sep, plan v2
-- P1.6 phase 2). This read every earlier day the readings held, which was
-- whatever the prune left - 60 days lately, and it would have been the ~30
-- of step 5 with no one choosing it. The served profile reads the last 30
-- whole days because that window scored best out of sample (0.5359 CRPS
-- against 0.5444 at 60 days, docs/HISTORY_WINDOWS_2026-09-29.md), so the
-- evidence the trajectory is fitted on now describes the same statistic the
-- engine prices with. On 29 Sep's 19,990 rows this moved the as-of climb
-- by 0.226 C and its spread by 0.145 C on average; every row kept 20 days.
ahead as (
  select city_key, local_date, local_hour,
         max(temp_c) over (partition by city_key, local_date order by local_hour
                           rows between current row and unbounded following) - temp_c
                                                                        as climb_left_c,
         count(*) over (partition by city_key, local_date)             as n_hours
  from hourly
),
asof as (
  select city_key, local_date, local_hour,
         avg(climb_left_c)          over w as climb_left_asof_c,
         stddev_samp(climb_left_c)  over w as climb_sd_asof_c,
         count(*)                   over w as climb_n_asof
  from ahead
  where n_hours >= 12
  window w as (partition by city_key, local_hour order by local_date
               range between interval '30 days' preceding and interval '1 day' preceding)
),
walked as (
  select city_key, local_date, local_hour, temp_c,
         -- THE RUNNING MAXIMUM IS THE FLOOR, and it has to be the maximum of
         -- the hours SO FAR, not of the day: using the day's would hand the
         -- fit the answer it is being asked to predict.
         max(temp_c) over (partition by city_key, local_date order by local_hour
                           rows between unbounded preceding and current row)    as running_max_c,
         max(temp_c) over (partition by city_key, local_date)                   as series_max_c,
         count(*)    over (partition by city_key, local_date)                   as n_hours
  from hourly
),
published as (
  select distinct on (q.city_key, q.for_date)
         q.city_key, q.for_date,
         q.forecast_max_c - coalesce(q.bias_applied_c, 0) as centre_c,
         -- THE BASELINE IS THE FORECAST'S OWN WIDTH, NEVER THE PUBLISHED ONE.
         -- Where the trajectory fired, sigma_c IS the trajectory's sigma while
         -- forecast_max_c - bias_applied_c is still the forecast's centre.
         -- Pairing those two scores the trajectory against a baseline wearing
         -- the trajectory's own narrow spread: the baseline loses by
         -- construction, more hours get applied, more rows are written that
         -- way, and the layer ends up proving itself.
         --
         -- NULL forecast_sigma_c IS NOT COALESCED TO sigma_c, and the reason
         -- is that the two mean different things at different times. Every row
         -- written before derived_trajectory first existed (2026-09-22
         -- 17:50:46) has forecast_sigma_c = sigma_c as a matter of fact -
         -- nothing could have replaced it - and those 66,345 rows were
         -- stamped. The 1,056 rows written by the one intraday run between
         -- that fit and this column existing are the only rows where the
         -- forecast's width is genuinely unrecoverable, and guessing it from a
         -- neighbouring run would be inventing the number this view exists to
         -- report. They are dropped; those city-days fall back to the
         -- preceding run of the same day, which is a real published
         -- forecast-path row.
         q.forecast_sigma_c                                 as sigma_c,
         q.computed_at
  from (
    select m.city_key, m.resolution_date as for_date,
           bp.forecast_max_c, bp.bias_applied_c, bp.sigma_c,
           bp.forecast_sigma_c, bp.computed_at
    from band_probabilities bp
    join bands b   on b.band_id = bp.band_id
    join markets m on m.market_id = b.market_id
    where bp.sigma_c is not null and bp.sigma_c > 0
      and bp.forecast_sigma_c is not null and bp.forecast_sigma_c > 0
      and bp.forecast_max_c is not null
  ) q
  order by q.city_key, q.for_date, q.computed_at desc
)
select
  w.city_key,
  w.local_date,
  w.local_hour,
  w.temp_c,
  w.running_max_c,
  coalesce(v.observed_max_c, w.series_max_c)  as final_max_c,
  (v.observed_max_c is not null)              as final_is_verified,
  cp.typical_climb_left_c                     as climb_left_c,
  cp.climb_left_sd_c                          as climb_sd_c,
  cp.n_days                                   as climb_n_days,
  p.centre_c                                  as forecast_c,
  p.sigma_c                                   as forecast_sigma_c,
  -- as of the 30 days before; null under 20 of them, as the profile's own floor
  case when a.climb_n_asof >= 20 then round(a.climb_left_asof_c::numeric, 2) end as climb_left_asof_c,
  case when a.climb_n_asof >= 20 then round(a.climb_sd_asof_c::numeric, 2) end   as climb_sd_asof_c,
  a.climb_n_asof::int                         as climb_n_asof
from walked w
join derived_climb_profile cp
  on cp.city_key = w.city_key and cp.local_hour = w.local_hour
join published p
  on p.city_key = w.city_key and p.for_date = w.local_date
left join v_verified_weather_outcomes v
  on v.city_key = w.city_key and v.for_date = w.local_date
left join asof a
  on a.city_key = w.city_key and a.local_date = w.local_date and a.local_hour = w.local_hour
where w.n_hours >= 12
  and cp.typical_climb_left_c is not null
  and cp.climb_left_sd_c is not null;

comment on view v_trajectory_evidence is
  'One row per city, settled day and local hour: the reading and running maximum at that hour, what the climb profile says is still to come (now, and as of the 30 days before), what the desk actually published for that day, and what the day finally reached. Whole days from weather_observations, earlier ones from derived_city_day_hours. The fitting set for scripts/trajectory.py.';

create or replace view public.v_hit_forecasts_live as
with day as (
  select distinct city_key, for_date, cutoff_at from public.v_hit_ladders
)
select d.city_key, d.for_date, 'asof'::text as lane, f.model, f.forecast_max_c, f.issued_at as known_at
  from day d
  cross join lateral (
    select distinct on (i.model) i.model, i.forecast_max_c, i.issued_at
      from public.v_forecast_issued i
     where i.city_key = d.city_key and i.for_date = d.for_date
       and i.issued_at <= d.cutoff_at
       and i.issued_at_source in ('provider_update_time', 'ingest_time')
       and i.forecast_max_c is not null
     order by i.model, i.issued_at desc) f
union all
select d.city_key, d.for_date, 'asof', m.model, m.forecast_max_c, m.observed_at
  from day d
  cross join lateral (
    select distinct on (w.model) w.model, w.forecast_max_c, w.observed_at
      from public.weather_forecast_models w
     where w.city_key = d.city_key and w.for_date = d.for_date
       and w.source = 'open-meteo-models-current'
       and w.observed_at <= d.cutoff_at
     order by w.model, w.observed_at desc) m
union all
select d.city_key, d.for_date, 'research', f.model, f.forecast_max_c, f.run_at
  from day d
  join public.weather_forecasts f
    on f.city_key = d.city_key and f.for_date = d.for_date
   and f.source = 'open-meteo-previous-runs' and f.lead_days = 1
   and f.forecast_max_c is not null
union all
select d.city_key, d.for_date, 'research', w.model, w.forecast_max_c, w.run_at
  from day d
  join public.weather_forecast_models w
    on w.city_key = d.city_key and w.for_date = d.for_date
   and w.source = 'open-meteo-previous-runs' and w.lead_days = 1;

comment on view public.v_hit_forecasts_live is
  'v_hit_forecasts computed from the forecast tables as they stand: correct for every day both tables still hold. freeze_hit_forecasts copies it nightly; v_hit_forecasts serves it (plan v2 P1.6 phase 2).';

-- The oldest for_date both tables hold whole: from it on the live rows, before
-- it the frozen ones. greatest() skips an empty table; both empty, all frozen.
create or replace view public.v_hit_forecasts as
with held as (
  select greatest((select min(for_date) from public.weather_forecasts),
                  (select min(for_date) from public.weather_forecast_models)) as from_date
)
select l.city_key, l.for_date, l.lane, l.model, l.forecast_max_c, l.known_at
  from public.v_hit_forecasts_live l, held h
 where l.for_date >= coalesce(h.from_date, 'infinity'::date)
union all
select f.city_key, f.for_date, f.lane, f.model, f.forecast_max_c, f.known_at
  from public.derived_hit_forecasts f, held h
 where f.for_date < coalesce(h.from_date, 'infinity'::date);

comment on view public.v_hit_forecasts is
  'Per settled city-day and model: the newest forecast known by 18:00 local the evening before (lane asof), and previous-runs values at nominal lead 1 whose issue time is unverified (lane research, never promotes). Plan v2.1 P3.8. Live while both forecast tables hold the day, frozen (derived_hit_forecasts) before (plan v2 P1.6 phase 2).';

revoke all on public.v_hit_forecasts_live from public, anon, authenticated;
grant select on public.v_hit_forecasts_live to service_role;

create or replace function public.refresh_city_day_hours(p_city text default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_oldest  timestamptz;
  v_city    text;
  v_tz      text;
  v_first   date;
  v_n       int;
  v_written int := 0;
  v_cities  int := 0;
begin
  select min(valid_at) into v_oldest from weather_observations;
  if v_oldest is null then
    return jsonb_build_object('ok', true, 'city', p_city, 'days_written', 0,
                              'note', 'weather_observations holds no readings');
  end if;

  for v_city, v_tz in
    select city_key, coalesce(timezone, 'UTC') from cities
     where p_city is null or city_key = p_city
     order by city_key
  loop
    -- The first whole local day: the one the oldest reading held falls on,
    -- unless it began before that reading (refresh_feature_cache's rule).
    v_first := (v_oldest at time zone v_tz)::date;
    if (v_first::timestamp at time zone v_tz) < v_oldest then
      v_first := v_first + 1;
    end if;

    -- Every local day the readings hold, as 24 slots of hourly maxima. A cut
    -- day is inserted when it was never cached and never updated.
    insert into derived_city_day_hours (city_key, obs_date, temp_c, n_hours, computed_at)
    select v_city, x.d, array_agg(h.temp_c order by g.slot), count(h.temp_c)::int, now()
      from (select distinct (o.valid_at at time zone v_tz)::date as d
              from weather_observations o
             where o.city_key = v_city and o.temp_c is not null) x
      cross join generate_series(0, 23) as g(slot)
      left join (select (o.valid_at at time zone v_tz)::date as d,
                        extract(hour from o.valid_at at time zone v_tz)::int as hr,
                        max(o.temp_c) as temp_c
                   from weather_observations o
                  where o.city_key = v_city and o.temp_c is not null
                  group by 1, 2) h
        on h.d = x.d and h.hr = g.slot
     group by x.d
    on conflict (city_key, obs_date) do update
       set temp_c = excluded.temp_c, n_hours = excluded.n_hours, computed_at = now()
     where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_written := v_written + v_n;
    v_cities := v_cities + 1;
  end loop;

  return jsonb_build_object(
    'ok', true, 'city', p_city, 'cities', v_cities, 'days_written', v_written,
    'whole_from_instant', v_oldest,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.refresh_city_day_hours(text) is
  'Write each city''s local days as 24 hourly maxima into derived_city_day_hours: every day the readings hold whole, and a cut day only if it was never cached (plan v2 P1.6 phase 2). Called once a night for every city - retired ones too, since the prune cuts theirs - by common.refresh_feature_cache.';

revoke all on function public.refresh_city_day_hours(text) from public, anon, authenticated;
grant execute on function public.refresh_city_day_hours(text) to service_role;


create or replace function public.freeze_hit_forecasts()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_from    date;
  v_removed int;
  v_written int;
begin
  -- The oldest for_date both forecast tables hold: from it on, the live rows
  -- are whole, and they replace whatever was frozen for those days.
  select greatest((select min(for_date) from weather_forecasts),
                  (select min(for_date) from weather_forecast_models))
    into v_from;
  if v_from is null then
    return jsonb_build_object('ok', true, 'rows_written', 0,
                              'note', 'neither forecast table holds a row');
  end if;

  delete from derived_hit_forecasts where for_date >= v_from;
  get diagnostics v_removed = row_count;

  insert into derived_hit_forecasts (city_key, for_date, lane, model, forecast_max_c, known_at, frozen_at)
  select city_key, for_date, lane, model, forecast_max_c, known_at, now()
    from v_hit_forecasts_live
   where for_date >= v_from;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'from', v_from, 'rows_replaced', v_removed, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_hit_forecasts),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_hit_forecasts() is
  'Copy v_hit_forecasts_live into derived_hit_forecasts for every day both forecast tables still hold, replacing what was frozen for them; the days before are left as frozen (plan v2 P1.6 phase 2). Called once a night by common.refresh_feature_cache.';

revoke all on function public.freeze_hit_forecasts() from public, anon, authenticated;
grant execute on function public.freeze_hit_forecasts() to service_role;


create or replace function public.prune_observations(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before timestamptz default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before timestamptz := coalesce(
    p_before,
    (current_date - p_keep_days)::timestamptz
  );
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_cached_before bigint;
  v_uncovered bigint;
  v_freed text;
  v_unkept bigint;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the model needs months, not days'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_observations in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.weather_observations
   where valid_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*) into v_uncovered
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
  ) x
  where not exists (
    select 1
      from public.derived_city_day_features f
     where f.city_key = x.city_key
       and f.obs_date = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_features. Run select refresh_feature_cache(); first - pruning now would destroy them.',
        v_uncovered,
        v_cut
      ),
      'uncovered_city_days', v_uncovered
    );
  end if;

  -- ...and in derived_city_day_hours, which v_trajectory_evidence reads for
  -- every day the readings no longer hold (plan v2 P1.6 phase 2). A day with
  -- no temperature has no hours to keep.
  select count(*) into v_unkept
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where not exists (
    select 1
      from public.derived_city_day_hours h
     where h.city_key = x.city_key
       and h.obs_date = x.d
  );

  if v_unkept > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_hours. Run common.refresh_feature_cache first (it refreshes the hours) - the trajectory evidence reads them after the prune.',
        v_unkept,
        v_cut
      ),
      'unkept_city_days', v_unkept
    );
  end if;

  select count(*) into v_cached_before
    from public.derived_city_day_features;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'older_than', v_before,
      'cached_city_days', v_cached_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_observations
   where valid_at < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_observations')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'older_than', v_before,
    'cached_city_days', v_cached_before,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_observations(integer, boolean, timestamptz, bigint) is
  'Delete archived observations only when p_expected_rows matches the verified export count and every affected city-local day is cached - in derived_city_day_features and, for the trajectory evidence, derived_city_day_hours. Committed calls require the expected count.';

revoke all on function public.prune_observations(integer, boolean, timestamptz, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_observations(integer, boolean, timestamptz, bigint)
  to service_role;


create or replace function public.prune_forecasts(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before date default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before date := coalesce(p_before, current_date - p_keep_days);
  v_doomed bigint;
  v_keep bigint;
  v_skill bigint;
  v_skill_at timestamptz;
  v_newest_doomed date;
  v_freed text;
  v_unfrozen bigint;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - skill is measured over months'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_forecasts in share row exclusive mode;
  end if;

  select count(*), max(for_date) into v_doomed, v_newest_doomed
    from public.weather_forecasts
   where for_date < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*), max(computed_at) into v_skill, v_skill_at
    from public.derived_forecast_skill;

  if v_skill = 0 then
    return jsonb_build_object(
      'ok', false,
      'error', 'derived_forecast_skill is empty - these forecasts have never been scored, and the score is what survives the prune. Run the daily pipeline first.',
      'would_delete', v_doomed
    );
  end if;

  if v_skill_at is null or v_skill_at::date < v_newest_doomed then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'derived_forecast_skill was last computed %s, before the newest forecast being removed (%s). Their contribution was never measured. Run the daily pipeline first.',
        coalesce(v_skill_at::date::text, 'never'),
        v_newest_doomed
      ),
      'would_delete', v_doomed
    );
  end if;

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2): hit_tournament.py reads 120 days, the table keeps about 30.
  select count(*) into v_unfrozen
  from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run common.refresh_feature_cache first (it freezes them) - the hit tournament reads them after the prune.',
        v_unfrozen,
        v_before
      ),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecasts
   where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'skill_rows', v_skill,
      'skill_computed_at', v_skill_at,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecasts
   where for_date < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_forecasts')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'skill_rows', v_skill,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_forecasts to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_forecasts(integer, boolean, date, bigint) is
  'Delete archived forecasts only when p_expected_rows matches the verified export count, derived_forecast_skill proves the rows were scored, and every v_hit_forecasts row for the days going is frozen in derived_hit_forecasts. Committed calls require the expected count.';

revoke all on function public.prune_forecasts(integer, boolean, date, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_forecasts(integer, boolean, date, bigint)
  to service_role;


create or replace function public.prune_forecast_models(
  p_keep_days     integer,
  p_dry_run       boolean default true,
  p_before        date    default null,
  p_expected_rows bigint  default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  -- A cutoff may be older than the floor, never newer (plan v2 P1.1).
  v_before date := least(coalesce(p_before, current_date - p_keep_days), current_date - p_keep_days);
  -- Tonight's mirror exports every row observed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_keep   bigint;
  v_young  bigint;
  v_unfrozen bigint;
begin
  -- THIRTY DAYS, as weather_forecasts: station correction fits on 45 days and
  -- scores on 30 more, and below the keep it reads the archive, not this.
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the model forecasts are fitted over weeks'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late write for an old date must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.weather_forecast_models in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where observed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.weather_forecast_models where for_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows dated before %s were observed since %s and are not in '
                      'the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
                              'note', format('nothing dated before %s', v_before));
  end if;

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2, step 6): hit_tournament.py reads 120 days, the table keeps less.
  select count(*) into v_unfrozen from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run '
                      'common.refresh_feature_cache first (it freezes them) - the hit tournament reads '
                      'them after the prune.', v_unfrozen, v_before),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecast_models where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecast_models where for_date < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecast_models')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

comment on function public.prune_forecast_models(integer, boolean, date, bigint) is
  'Delete weather_forecast_models rows dated before the cutoff (at least 30 days back), only when the caller''s count read back from the committed archive file matches exactly, none was observed since yesterday''s UTC midnight (the repo mirror copies those after the prune), and every v_hit_forecasts row for those days is frozen in derived_hit_forecasts.';

revoke execute on function public.prune_forecast_models(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_models(integer, boolean, date, bigint) to service_role;
