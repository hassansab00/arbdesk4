-- ===========================================================================
-- ad4_41_campaigns.sql - MAKE A CAMPAIGN SHOW WHAT IT WOULD ACTUALLY DO.
--
-- WHAT WAS WRONG
-- --------------
-- The Campaigns page listed rows. A deployment was a name, a strategy id, a
-- target kind, five status buttons and a P&L that read $0 until something
-- settled - which, for a desk whose strategies all ship disabled, is forever.
-- Nothing on it said what the campaign COVERED, whether the strategy behind it
-- was even switched on, whether anything it targets is trading today, or what
-- it would do in the next hour. So there was nothing to look at and no reason
-- to come back, which is exactly what "what is the value in it" means.
--
-- WHAT THIS ADDS
-- --------------
-- v_campaign_state: one row per deployment, answering the four questions a
-- campaign exists to answer, from live data rather than from what was typed
-- when it was created:
--
--   COVERS   - which cities the target actually resolves to, how many of them
--              have a live market today, and how many buckets that is
--   NOW      - what its strategy would fire on right now, and the best edge
--              inside its own scope
--   BLOCKED  - the first reason it cannot trade, in order: the strategy is
--              off, the window has not opened, the window has closed, the
--              status is not running, nothing live in scope, no signal
--   RECORD   - trades, wins and net P&L attributed to it
--
-- TARGETS ARE RESOLVED, NOT ECHOED. `{"values": ["Europe/Africa"]}` on a
-- cluster deployment is turned into the cities that are actually in that
-- region, by the same timezone rule the Clusters page uses - so a campaign
-- cannot silently cover nothing because a region was spelled differently.
--
-- RUN ORDER: after ad4_34_trade_plan.sql (which publishes v_trade_plan) and
-- ad4_33_control.sql (v_strategy_board). Re-runnable. Creates views only.
-- ===========================================================================

drop view if exists v_campaign_state cascade;
drop view if exists v_campaign_targets cascade;

-- --------------------------------------------------------------------------
-- 1. Which region a city is in.
--
--    The same rule as web/lib/region.ts, in SQL, because a campaign targeting
--    a cluster has to resolve to cities server-side or the page has to fetch
--    every city to find out what its own campaign covers.
--
--    Asia spans three groups, so the zone's own city decides. Listed rather
--    than inferred: no rule over the string gets both "Asia/Nicosia" (West)
--    and "Asia/Seoul" (East) right.
-- --------------------------------------------------------------------------
create or replace function ad4_region(p_timezone text, p_longitude numeric)
returns text language plpgsql immutable as $ad4$
declare
  v_area text := split_part(coalesce(p_timezone, ''), '/', 1);
  v_zone text := lower(regexp_replace(coalesce(p_timezone, ''), '^.*/', ''));
  v_west_asia text[] := array[
    'dubai','qatar','riyadh','kuwait','baghdad','tehran','muscat','bahrain',
    'amman','beirut','damascus','jerusalem','tel_aviv','gaza','nicosia',
    'famagusta','yerevan','baku','tbilisi','aden','kabul','karachi',
    'tashkent','ashgabat','dushanbe','bishkek','almaty','aqtau','aqtobe',
    'atyrau','oral','qostanay','qyzylorda','samarkand','kolkata','calcutta',
    'colombo','kathmandu','dhaka','thimphu'];
begin
  if v_area in ('America','US','Canada','Brazil','Mexico','Chile','Cuba','Jamaica') then
    return 'Americas';
  elsif v_area in ('Europe','Africa','Atlantic','Arctic','GB','Eire','Portugal','Poland','Turkey') then
    return 'Europe/Africa';
  elsif v_area in ('Australia','Pacific','NZ') then
    return 'Oceania';
  elsif v_area = 'Asia' then
    return case when v_zone = any(v_west_asia) then 'West Asia' else 'East Asia' end;
  elsif v_area = 'Indian' then
    return 'West Asia';
  end if;
  -- No usable timezone: fall back to longitude, the same bands the UI uses.
  if p_longitude is null then return 'Unknown';
  elsif p_longitude < -30  then return 'Americas';
  elsif p_longitude <  40  then return 'Europe/Africa';
  elsif p_longitude <  75  then return 'West Asia';
  elsif p_longitude < 150  then return 'East Asia';
  else                          return 'Oceania';
  end if;
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 2. What each deployment actually covers.
--
--    target is {"values": [...]}, and what those values MEAN depends on
--    target_kind: city keys for 'city' and 'list', region names for
--    'cluster'. A value that matches nothing at all is kept as a row with a
--    null city_key, so "you targeted three cities and two of them do not
--    exist" is visible rather than silently becoming "two cities".
-- --------------------------------------------------------------------------
create view v_campaign_targets as
with vals as (
  select d.deployment_id, d.target_kind,
         nullif(trim(v.value), '') as val
    from deployments d
    left join lateral jsonb_array_elements_text(
      case when jsonb_typeof(d.target -> 'values') = 'array' then d.target -> 'values'
           when jsonb_typeof(d.target) = 'array'             then d.target
           else '[]'::jsonb end) v(value) on true
)
select
  vals.deployment_id,
  vals.val                                   as target_value,
  c.city_key,
  c.display_name,
  ad4_region(c.timezone, c.longitude)        as region
from vals
left join cities c
  on (vals.target_kind in ('city', 'list') and c.city_key = vals.val)
  or (vals.target_kind = 'cluster'
      and ad4_region(c.timezone, c.longitude) = vals.val
      and coalesce(c.status, 'active') = 'active');

comment on view v_campaign_targets is
  'Every city a deployment resolves to, with the target value it came from. A target value that matches nothing keeps its row with a null city_key, so a typo is visible instead of quietly shrinking the campaign.';


-- --------------------------------------------------------------------------
-- 3. The campaign, live.
-- --------------------------------------------------------------------------
create view v_campaign_state as
with tgt as (
  select deployment_id,
         count(*) filter (where city_key is not null)                as n_cities,
         count(distinct target_value) filter (where city_key is null) as n_unresolved,
         array_agg(distinct target_value) filter (where city_key is null) as unresolved,
         array_agg(distinct city_key) filter (where city_key is not null) as cities
    from v_campaign_targets
   group by deployment_id
),
-- What is on the board inside each campaign's own scope, right now.
scope as (
  select
    t.deployment_id,
    count(*)                                                          as n_bands,
    count(distinct p.city_key)                                        as n_cities_live,
    count(*) filter (where p.tradeable)                               as n_tradeable,
    max(p.edge_net_pp) filter (where p.tradeable)                     as best_edge_pp,
    sum(p.fillable_usd_5c) filter (where p.tradeable)                 as fillable_usd,
    min(p.book_age_min)                                               as freshest_book_min
  from v_campaign_targets t
  join deployments d on d.deployment_id = t.deployment_id
  join v_trade_plan p on p.city_key = t.city_key
 where t.city_key is not null
 group by t.deployment_id
),
-- And what its OWN strategy would do with that scope.
firing as (
  select
    t.deployment_id,
    count(*)                                       as n_would_fire,
    max(p.edge_net_pp)                             as fire_best_edge_pp,
    min(p.minutes_to_peak) filter (where p.minutes_to_peak is not null) as soonest_peak_min,
    (array_agg(p.city_key order by p.edge_net_pp desc nulls last))[1]   as top_city,
    (array_agg(p.band_label order by p.edge_net_pp desc nulls last))[1] as top_band
  from v_campaign_targets t
  join deployments d   on d.deployment_id = t.deployment_id
  join v_trade_plan p  on p.city_key = t.city_key
 where t.city_key is not null
   and d.strategy_id = any(p.would_fire)
 group by t.deployment_id
),
-- The record. Attribution is by deployment_id on the ledger, which only the
-- settlement stage carries - so an unsettled campaign reads zero, correctly.
record as (
  select
    l.deployment_id,
    count(*)                                                                as n_settled,
    count(*) filter (where coalesce((l.detail->>'net_pnl')::numeric, 0) > 0) as n_won,
    sum(coalesce((l.detail->>'net_pnl')::numeric, 0))                       as net_pnl
  from ledger l
 where l.deployment_id is not null and l.stage = 'settlement'
 group by l.deployment_id
)
select
  d.deployment_id,
  d.name,
  d.strategy_id,
  sb.name                                     as strategy_name,
  coalesce(sb.enabled, false)                 as strategy_enabled,
  sb.verdict                                  as strategy_verdict,
  d.status,
  d.target_kind,
  d.condition,
  d.starts_at,
  d.ends_at,
  d.created_at,

  coalesce(tgt.n_cities, 0)::int              as cities_targeted,
  coalesce(tgt.n_unresolved, 0)::int          as targets_unresolved,
  tgt.unresolved                              as unresolved_values,
  tgt.cities                                  as cities,

  coalesce(scope.n_cities_live, 0)::int       as cities_live,
  coalesce(scope.n_bands, 0)::int             as bands_in_scope,
  coalesce(scope.n_tradeable, 0)::int         as bands_tradeable,
  round(scope.best_edge_pp * 100, 1)          as best_edge_pp,
  round(scope.fillable_usd)                   as fillable_usd,
  round(scope.freshest_book_min)              as book_age_min,

  coalesce(firing.n_would_fire, 0)::int       as would_fire_now,
  round(firing.fire_best_edge_pp * 100, 1)    as firing_best_edge_pp,
  firing.top_city                             as firing_top_city,
  firing.top_band                             as firing_top_band,
  round(firing.soonest_peak_min)              as soonest_peak_min,

  coalesce(record.n_settled, 0)::int          as trades_settled,
  coalesce(record.n_won, 0)::int              as trades_won,
  round(coalesce(record.net_pnl, 0), 2)       as net_pnl,

  case when d.ends_at is null then null
       else greatest(0, round(extract(epoch from (d.ends_at - now())) / 86400.0, 1)) end
                                              as days_left,

  -- THE FIRST REASON IT CANNOT TRADE, in the order that actually stops it.
  -- One reason, not a list: a campaign whose strategy is switched off does
  -- not also need to be told its window has not opened.
  case
    when coalesce(tgt.n_cities, 0) = 0
      then 'This campaign resolves to no cities at all — check the target.'
    when not coalesce(sb.enabled, false)
      then format('Strategy %s is switched off, so this campaign cannot trade whatever its status says.',
                  coalesce(sb.name, d.strategy_id))
    when d.status in ('draft', 'paused', 'closed')
      then format('Status is %s. Set it to running for it to act.', d.status)
    when d.starts_at is not null and d.starts_at > now()
      then format('Does not start until %s.', to_char(d.starts_at, 'YYYY-MM-DD HH24:MI'))
    when d.ends_at is not null and d.ends_at < now()
      then format('Ended %s.', to_char(d.ends_at, 'YYYY-MM-DD HH24:MI'))
    when coalesce(scope.n_bands, 0) = 0
      then 'Nothing live in scope — no market is open in any city this targets today.'
    when coalesce(scope.n_tradeable, 0) = 0
      then format('%s bucket(s) in scope, none tradeable after fees and depth.', scope.n_bands)
    when coalesce(firing.n_would_fire, 0) = 0
      then format('Live and armed, but %s has no setup in scope right now.',
                  coalesce(sb.name, d.strategy_id))
    else null
  end                                         as blocked_because,

  -- And what it would do, when nothing is blocking it.
  case
    when coalesce(firing.n_would_fire, 0) = 0 then null
    else format('%s would take %s right now; the best is %s in %s at %s points.',
                coalesce(sb.name, d.strategy_id),
                ad4_plural(firing.n_would_fire, 'bucket'),
                coalesce(firing.top_band, 'a bucket'),
                coalesce(firing.top_city, 'scope'),
                round(firing.fire_best_edge_pp * 100, 1))
  end                                         as doing_now
from deployments d
left join v_strategy_board sb on sb.strategy_id = d.strategy_id
left join tgt    on tgt.deployment_id    = d.deployment_id
left join scope  on scope.deployment_id  = d.deployment_id
left join firing on firing.deployment_id = d.deployment_id
left join record on record.deployment_id = d.deployment_id;

comment on view v_campaign_state is
  'One row per deployment answering what it covers, what its strategy would do inside that scope right now, the first reason it cannot trade, and its settled record. Everything is read live rather than echoed back from what was typed when it was created.';


-- --------------------------------------------------------------------------
-- 4. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_campaign_state', 'v_campaign_targets'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function ad4_region(text, numeric) to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare v_n int; v_blocked int;
begin
  select count(*), count(*) filter (where blocked_because is not null)
    into v_n, v_blocked from v_campaign_state;
  raise notice 'ad4_41: % campaign(s), % blocked from trading right now.', v_n, v_blocked;
end
$ad4$;
