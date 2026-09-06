-- ===========================================================================
-- ad4_40_synthesis.sql - WHAT HAS THE DESK ACTUALLY LEARNED?
--
-- THE GAP THIS FILLS
-- ------------------
-- Every page shows a slice: this city now, this bucket's price, this model's
-- error at 2 days out. Nothing anywhere answered the question the whole
-- archive exists to answer -
--
--     over the last N days, across these cities, WHAT KEEPS HAPPENING,
--     and how sure are we?
--
-- That is not a chart. It is a short list of sentences, each with the span it
-- covers, the cities it covers, how many observations stand behind it, and
-- whether there is yet enough evidence to believe it. A finding with 12 days
-- behind it and one with 900 must not look the same, so every row carries its
-- own count and its own verdict.
--
-- WHAT IS AND IS NOT IN HERE
-- --------------------------
-- Every finding below is computed from rows this database actually holds. No
-- finding is invented, assumed, or carried over from what "usually" happens in
-- weather markets - if the archive cannot support a claim, the row says
-- "collecting" and names how many days are still needed rather than making
-- the claim weakly.
--
-- A finding is BACKWARD (what the record shows) or FORWARD (what today's
-- forecasts imply). They are kept apart and labelled, because a forward
-- statement is a prediction and a backward one is a measurement.
--
-- RUN ORDER: after ad4_28_feature_cache.sql and ad4_31_predictive.sql.
-- Re-runnable. Read-only at run time - it creates views and writes nothing.
-- ===========================================================================

-- "1 cities" is the kind of detail that makes a number look generated rather
-- than measured, and every sentence below counts something.
create or replace function ad4_plural(n bigint, singular text, plural text default null)
returns text language sql immutable as $$
  select n::text || ' ' || case when n = 1 then singular else coalesce(plural, singular || 's') end;
$$;

drop view if exists v_synthesis_findings cascade;
drop view if exists v_learning_state cascade;

-- --------------------------------------------------------------------------
-- 0. How much evidence is enough.
--
--    One number per KIND of claim, because they are not comparable: a claim
--    about a rate needs enough trials for the rate to mean anything, and a
--    claim about a mean needs enough days for the mean to be stable. These
--    are stated here, once, so the thresholds are visible and arguable rather
--    than buried in nine different case expressions.
-- --------------------------------------------------------------------------
create table if not exists synthesis_thresholds (
  kind        text primary key,
  min_n       int  not null,
  note        text not null
);

insert into synthesis_thresholds (kind, min_n, note) values
  ('rate',      60, 'A percentage needs enough trials that a run of luck cannot carry it. 60 keeps the 95% interval inside about +/-13 points.'),
  ('mean',      30, 'A mean of a noisy daily quantity settles down by about 30 days.'),
  ('per_city',  20, 'Per-city claims are made on that city''s own days, so the bar is lower - but a city with a fortnight of history is still a guess.'),
  ('forward',    1, 'A forward statement counts what is on the board right now. It is a statement about today, not a finding.')
on conflict (kind) do update set min_n = excluded.min_n, note = excluded.note;


-- --------------------------------------------------------------------------
-- 1. The findings.
--
--    Every branch produces the same shape, so the UI renders one list and
--    does no thinking. A branch whose source table is missing contributes
--    nothing rather than erroring - the view still installs on a database
--    that has not run every file.
-- --------------------------------------------------------------------------
create view v_synthesis_findings as

-- ===================== BACKWARD: what the record shows =====================

-- 1a. WHICH WAY THE FORECAST LEANS.
-- The single most actionable thing in the archive: a systematic lean is a
-- correction you can apply, a random error is not.
with fc as (
  select f.model, f.lead_days, f.error_c, f.city_key, f.for_date
    from fact_forecast_outcome f
   where f.error_c is not null
     and f.for_date >= current_date - 180
),
lean as (
  select
    'forecast_lean'                                        as key,
    'backward'                                             as direction,
    'rate'                                                 as kind,
    180                                                    as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    avg(error_c)                                           as stat,
    (count(*) filter (where error_c > 0))::numeric / nullif(count(*), 0) as rate
  from fc
 where lead_days <= 1
),

-- 1b. IS THE DESK BEATING THE MARKET, on the buckets it disagreed about.
-- The only measurement that is about money rather than temperature.
disagreed as (
  select b.settled_yes, b.model_prob, b.market_price, b.city_key
    from fact_band_outcome b
   where b.settled_yes is not null
     and b.model_prob is not null and b.market_price is not null
     and b.model_prob - b.market_price > 0.05      -- the desk said cheaper than priced
     and b.for_date >= current_date - 180
),
beat as (
  select
    'desk_vs_market'                                       as key,
    'backward'                                             as direction,
    'rate'                                                 as kind,
    180                                                    as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    avg(market_price)                                      as stat,
    (count(*) filter (where settled_yes))::numeric / nullif(count(*), 0) as rate
  from disagreed
),

-- 1c. THE MORNING TELL.
-- Does a warm start actually carry to a warm finish? This is the mechanism
-- the whole intraday side of the desk rests on, and it had never been
-- measured against the archive.
morning as (
  select
    f.city_key, f.obs_date, f.morning_temp_c, f.max_c,
    cl.normal_max_c, cl.sd_max_c
  from derived_city_day_features f
  join derived_city_climate cl on cl.city_key = f.city_key
 where f.morning_temp_c is not null and f.max_c is not null
   and cl.sd_max_c is not null and cl.sd_max_c > 0
   and f.obs_date >= current_date - 180
),
tell as (
  select
    'morning_tell'                                         as key,
    'backward'                                             as direction,
    'rate'                                                 as kind,
    180                                                    as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    avg(max_c - normal_max_c)                              as stat,
    (count(*) filter (where max_c > normal_max_c))::numeric / nullif(count(*), 0) as rate
  from morning
 where morning_temp_c - normal_max_c > -2          -- started warm for the season
),

-- 1d. WHAT PERSISTENCE ALONE WOULD SCORE.
-- The bar. Any model that cannot beat "the same as yesterday" is not a model.
persist as (
  select
    'persistence_bar'                                      as key,
    'backward'                                             as direction,
    'rate'                                                 as kind,
    180                                                    as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    avg(abs(delta_max_c))                                  as stat,
    (count(*) filter (where abs(delta_max_c) <= 1.0))::numeric / nullif(count(*), 0) as rate
  from derived_city_day_features
 where delta_max_c is not null
   and obs_date >= current_date - 180
),

-- 1e. CLOUD.
-- The one feature the desk collects that is not temperature. If it carries no
-- signal, that is worth stating plainly rather than continuing to collect it.
cloudy as (
  select
    'cloud_effect'                                         as key,
    'backward'                                             as direction,
    'mean'                                                 as kind,
    180                                                    as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    avg(max_c - min_c) filter (where cloud_mean < 0.3)
      - avg(max_c - min_c) filter (where cloud_mean > 0.7) as stat,
    null::numeric                                          as rate
  from derived_city_day_features
 where cloud_mean is not null and max_c is not null and min_c is not null
   and obs_date >= current_date - 180
),

-- 1f. WHEN THE DAY IS DECIDED.
-- Every intraday entry rule depends on this and it was only ever a constant.
peaking as (
  select
    'peak_hour'                                            as key,
    'backward'                                             as direction,
    'per_city'                                             as kind,
    null::int                                              as span_days,
    count(*)::int                                          as n_cities,
    coalesce(sum(n_days), 0)::int                          as n,
    avg(peak_hour_local)                                   as stat,
    null::numeric                                          as rate
  from derived_weather_peak
),

-- ===================== FORWARD: what today implies =========================

-- 1g. HOW UNUSUAL THE NEXT FEW DAYS LOOK.
ahead as (
  select
    'days_ahead'                                           as key,
    'forward'                                              as direction,
    'forward'                                              as kind,
    null::int                                              as span_days,
    count(distinct city_key)::int                          as n_cities,
    count(*)::int                                          as n,
    max(abs(sigma))                                        as stat,
    (count(*) filter (where abs(sigma) >= 1))::numeric / nullif(count(*), 0) as rate
  from (
    select f.city_key,
           (f.forecast_max_c - cl.normal_max_c) / nullif(cl.sd_max_c, 0) as sigma
      from (select distinct on (city_key, for_date) city_key, for_date, forecast_max_c
              from weather_forecasts
             where for_date between current_date and current_date + 3
               and forecast_max_c is not null
             order by city_key, for_date, run_at desc) f
      join derived_city_climate cl on cl.city_key = f.city_key
     where cl.sd_max_c is not null and cl.sd_max_c > 0
  ) s
 where sigma is not null
),

-- 1h. WHAT IS ACTUALLY TAKEABLE RIGHT NOW.
-- edges is keyed by band, not by city: the city is two joins away, which is
-- exactly why a count of "cities with an edge" was never on any page.
takeable as (
  select
    'live_edges'                                           as key,
    'forward'                                              as direction,
    'forward'                                              as kind,
    null::int                                              as span_days,
    count(distinct m.city_key)::int                        as n_cities,
    count(*)::int                                          as n,
    max(e.edge_net_pp)                                     as stat,
    null::numeric                                          as rate
  from edges e
  join bands b   on b.band_id   = e.band_id
  join markets m on m.market_id = b.market_id
 where e.edge_net_pp is not null and e.edge_net_pp > 0
   and coalesce(e.tradeable, true)
),

all_findings as (
  select * from lean    union all
  select * from beat    union all
  select * from tell    union all
  select * from persist union all
  select * from cloudy  union all
  select * from peaking union all
  select * from ahead   union all
  select * from takeable
)

select
  f.key,
  f.direction,
  f.kind,
  f.span_days,
  f.n_cities,
  f.n,
  round(f.stat::numeric, 2)                          as stat,
  round((f.rate * 100)::numeric, 0)                  as rate_pct,
  t.min_n,

  -- Is there enough behind this to say it out loud?
  case when f.n >= t.min_n then 'established' else 'collecting' end   as status,
  greatest(0, t.min_n - f.n)                                          as more_needed,

  -- THE SENTENCE. Written here rather than in the browser so the wording and
  -- the numbers cannot drift apart, and so an operator reading the database
  -- directly gets the same answer as one reading the page.
  case f.key
    when 'forecast_lean' then
      case when f.n = 0 then 'No settled forecasts yet, so no lean can be measured.'
      else format('Day-ahead forecasts ran %s °C %s on average across %s — days came in hotter than forecast %s%% of the time.',
                  abs(round(f.stat::numeric, 2)),
                  case when f.stat > 0 then 'cool' else 'hot' end,
                  ad4_plural(f.n_cities, 'city', 'cities'), round(f.rate * 100)) end
    when 'desk_vs_market' then
      case when f.n = 0 then 'The desk has not yet disagreed with the market on a bucket that settled.'
      else format('Where the desk called a bucket at least 5 points cheaper than the market, it won %s%% of the time against an average market price of %s¢.',
                  round(f.rate * 100), round(f.stat * 100)) end
    when 'morning_tell' then
      case when f.n = 0 then 'Not enough mornings recorded to test whether a warm start carries.'
      else format('When a city started the day warm for the season, it finished above its own normal %s%% of the time, by %s °C on average.',
                  round(f.rate * 100), round(f.stat::numeric, 1)) end
    when 'persistence_bar' then
      case when f.n = 0 then 'No consecutive days recorded yet, so the persistence bar is unknown.'
      else format('Yesterday''s maximum alone lands within 1 °C of today''s on %s%% of days — the bar any forecast has to beat. The average day-to-day move is %s °C.',
                  round(f.rate * 100), round(f.stat::numeric, 1)) end
    when 'cloud_effect' then
      case when f.n = 0 or f.stat is null then 'Not enough days with cloud recorded to say whether it matters.'
      else format('Clear days swing %s °C %s between night and afternoon than overcast ones — cloud is %s.',
                  abs(round(f.stat::numeric, 1)),
                  case when f.stat > 0 then 'wider' else 'narrower' end,
                  case when abs(f.stat) >= 1 then 'carrying real signal' else 'barely moving the day' end) end
    when 'peak_hour' then
      case when f.n_cities = 0 then 'No city has had its peak hour measured yet — the desk is still assuming 15:00.'
      else format('Across %s measured the day''s maximum lands at %s local on average, from %s days of readings.',
                  ad4_plural(f.n_cities, 'city', 'cities'),
                  to_char(make_time(floor(f.stat)::int, (60 * (f.stat - floor(f.stat)))::int, 0), 'HH24:MI'),
                  f.n) end
    when 'days_ahead' then
      case when f.n = 0 then 'No forward forecasts on the board for the next four days.'
      else format('Of %s forecast over the next four days, %s%% are more than one standard deviation from normal; the most extreme is %s sigma.',
                  ad4_plural(f.n, 'city-day'), round(f.rate * 100), round(f.stat::numeric, 1)) end
    when 'live_edges' then
      case when f.n = 0 then 'No bucket on the board currently prices below what the desk thinks it is worth.'
      else format('%s across %s price below what the desk thinks they are worth; the largest gap is %s points.',
                  ad4_plural(f.n, 'bucket'), ad4_plural(f.n_cities, 'city', 'cities'),
                  round(f.stat * 100)) end
  end                                                as headline,

  -- The basis, stated separately so the claim and its support are never one
  -- sentence the reader has to unpick.
  case
    when f.n = 0 then 'No observations behind this yet.'
    when f.direction = 'forward' then
      format('Counted from what is on the board right now: %s across %s.',
             ad4_plural(f.n, 'row'), ad4_plural(f.n_cities, 'city', 'cities'))
    else format('%s across %s%s. %s',
                ad4_plural(f.n, 'observation'), ad4_plural(f.n_cities, 'city', 'cities'),
                case when f.span_days is null then '' else format(' over the last %s days', f.span_days) end,
                case when f.n >= t.min_n
                     then 'Enough to stand on.'
                     else format('Needs %s more before this is worth acting on.', t.min_n - f.n) end)
  end                                                as basis,

  t.note                                             as threshold_note
from all_findings f
join synthesis_thresholds t on t.kind = f.kind;

comment on view v_synthesis_findings is
  'One sentence per thing the archive has established, each with the span it covers, the cities it covers, how many observations stand behind it, and whether there is yet enough evidence to act on it. Backward findings are measurements; forward ones are statements about what is on the board right now.';


-- --------------------------------------------------------------------------
-- 2. Is the desk still learning, and from what?
--
--    Learning is not a mood. It is four dated artefacts, each written by a
--    named job: the evidence that has been frozen, the accuracy that has been
--    measured from it, the correction that has been fitted, and the forward
--    predictions that correction has produced. This says which of those exist
--    and how old each is, so "learning in progress" is a fact rather than a
--    label on a spinner.
-- --------------------------------------------------------------------------
create view v_learning_state as
with ev as (
  select count(*)::int n, max(captured_at) at, min(for_date) since, max(for_date) latest
    from fact_forecast_outcome
),
sk as (
  select count(*)::int n, max(computed_at) at from derived_forecast_skill
),
fit as (
  select count(*)::int n, max(fitted_at) at,
         count(*) filter (where beats_persistence)::int n_better
    from derived_weather_model
),
pred as (
  select count(*)::int n, max(run_at) at from derived_model_forecast
),
cities as (select count(*)::int n from cities where coalesce(status, 'active') = 'active')
select
  'evidence' as stage, 1 as step,
  ev.n as rows, ev.at as last_run,
  format('%s frozen%s', ad4_plural(ev.n, 'settled day'),
         case when ev.since is null then '' else format(', %s to %s', ev.since, ev.latest) end) as detail,
  'Data Bank' as job,
  case when ev.n = 0 then 'not started' when ev.n < 200 then 'collecting' else 'ready' end as state,
  'Nothing can be learned from a day until it has settled and been written down.' as why
  from ev
union all
select 'accuracy', 2, sk.n, sk.at,
  format('%s scored', ad4_plural(sk.n, 'city/model/lead combination')),
  'Measure Forecast Skill',
  case when sk.n = 0 then 'not started' when sk.n < 20 then 'collecting' else 'ready' end,
  'How wrong each public model has been, per city, per lead day. This is what sizing uses.'
  from sk
union all
select 'correction', 3, fit.n, fit.at,
  format('%s of %s fitted, %s beating persistence', fit.n,
         ad4_plural(cities.n, 'city', 'cities'), fit.n_better),
  'Weather Model',
  case when fit.n = 0 then 'not started'
       when fit.n < cities.n / 2 then 'collecting'
       else 'ready' end,
  'The desk''s own correction to the public forecast, fitted per city. A city with no fit is traded off the raw public number.'
  from fit, cities
union all
select 'prediction', 4, pred.n, pred.at,
  format('%s on the board', ad4_plural(pred.n, 'forward prediction')),
  'Model Forecast',
  case when pred.n = 0 then 'not started' else 'ready' end,
  'The corrected forecast, forward. This is what the probabilities are built on.'
  from pred;

comment on view v_learning_state is
  'The four dated artefacts that make up "the desk has learned something": evidence frozen, accuracy measured, correction fitted, predictions produced. Each with its row count, when it last ran, the job that produces it, and whether it is ready.';


-- --------------------------------------------------------------------------
-- 2b. Register P2.1 on the Workflows page.
--
--     It is the one workflow with NO schedule: relearning is something you
--     ask for after a run of days has settled, not something that should
--     happen every six hours whether there is new evidence or not. Registered
--     as mode 'manual' so the gate lets a button press through and refuses a
--     cadence, and so it appears on the Workflows page with a Run button and a
--     run history like everything else.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.settings') is null then return; end if;

  insert into settings (key, value) values ('n8n_webhooks', '{}'::jsonb)
    on conflict (key) do nothing;
  insert into settings (key, value) values ('workflow_schedules', '{}'::jsonb)
    on conflict (key) do nothing;

  update settings
     set value = value || jsonb_build_object(
           'P2.1_relearn',
           jsonb_build_object('url', '', 'path', 'ad4-relearn', 'label', 'Relearn'))
   where key = 'n8n_webhooks' and not (value ? 'P2.1_relearn');

  update settings
     set value = value || jsonb_build_object(
           'P2.1_relearn', jsonb_build_object('mode', 'manual', 'every_minutes', 0))
   where key = 'workflow_schedules' and not (value ? 'P2.1_relearn');
end
$ad4$;


-- --------------------------------------------------------------------------
-- 3. Grants. Read-only.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_synthesis_findings', 'v_learning_state', 'synthesis_thresholds'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant all on synthesis_thresholds to service_role';
  end if;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function ad4_plural(bigint, text, text) to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare v_est int; v_col int;
begin
  select count(*) filter (where status = 'established'),
         count(*) filter (where status = 'collecting')
    into v_est, v_col from v_synthesis_findings;
  raise notice 'ad4_40: % finding(s) established, % still collecting.', v_est, v_col;
end
$ad4$;

select direction, status, n, n_cities, headline from v_synthesis_findings order by direction, key;
