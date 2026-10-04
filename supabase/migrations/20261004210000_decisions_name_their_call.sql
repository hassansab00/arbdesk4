-- ===========================================================================
-- DECISIONS NAME THEIR CALL, AND NO REFIT SERVES UNRECORDED (P2.2 part 3, 4 Oct 2026)
--
-- The external plan's P2.2 acceptance: the page's calls, the paper decisions
-- and the recorded evaluation name the same forecast identity, and a refit
-- does not silently replace the incumbent. Parts 1 and 2 built the contract
-- (v_prediction_contract), the registry and the page. Two gaps remained, each
-- measured live on 4 Oct:
--
-- 1. AN S10 DECISION NAMED THE WRONG CALL. The engine's six strategies write a
--    decisions row per city-day; checkpoint_id names the engine's checkpoint.
--    S11 and S12 decide on that call's ladder, so it is theirs. S10's three
--    decide on S10's ladder (s10_shadow_checkpoints), and all 1,833 of their
--    rows with a checkpoint named the engine's call; none named the S10 row,
--    or the S10 version, they acted on.
--      decisions.prediction_id / prediction_source   the call each decision
--          acted on, as v_prediction_contract names it (its prediction_id and
--          recorded_in). The tick writes them from this PR on
--          (scripts/engine_shadow.py); an S10 decision names the stored S10
--          row and acts on its stored ladder (scripts/s10_shadow.py).
--      v_decision_prediction   every decision, past ones too, resolved to
--          its call and saying how (link):
--            recorded      prediction_id written by the tick
--            checkpoint    S11/S12: the engine's checkpoint_id is their call
--            same_tick     an S10 decision before this PR: the rd1 row written
--                          in the same tick. Live, 1,470 of 1,844 s10_winner
--                          decisions lie within 38.7 s of one; none lie
--                          between 60 s and 10 min.
--            not_recorded  an S10 decision whose ladder no stored row holds:
--                          30 of them, on the engine's second captures of
--                          25 Sep - 1 Oct (S10 recomputed, and the write
--                          ignored the duplicate)
--            no_call       decided without a ladder (S10 has no d1_eve call:
--                          329; skipped: 15), or no checkpoint (27 Sep: 78
--                          per engine strategy)
--            signal_path   s1-s9: they decided on the old signal path, which
--                          is not a recorded call
--
-- 2. THE NIGHTLY REFITS SERVED UNRECORDED. Hassan, 4 Oct: "keep nightly
--    automatic" - the P3.9 station correction and the P2.9 MOS blend are
--    refitted and served every night, and that stays. What changes is that it
--    is no longer silent. record_model_versions() appends a model_registry
--    event whenever a version's state changes:
--      station_correction, station_mos, station_width
--    SERVED MEANS PRICED (review of #306): a version is served while the
--    engine priced with it in the last 36 h - every price names it
--    (band_probabilities -> model_versions.label; the tick's priced_from:
--    the correction, `+station-mos:` when the blend applied, `+station-width:`
--    when the width priced) - and no newer version of its family first priced
--    after its last price, while its switches are on (the MOS blend and the
--    width also need the correction's) and it has forward rows younger than
--    the switch's max_age_hours, as the engine reads them. Versions pricing
--    side by side are both served. It is retired at once when a switch goes
--    off or its rows expire, and once superseded or unpriced for 36 h. The newest fit in the
--    forward rows that has not priced is
--    recorded once as fitted (the width: shadow), with the reason, and an
--    older fit that never priced is retired as superseded by it. A row
--    registered by hand is left alone, except part 1's 'W2 per-city
--    width', retired once a nightly width version is registered. Hassan's
--    decision makes a fit serve the morning after; this records when it did.
--      calibration          settings.calibration_map (T to 3 decimals, the
--                           name part 1 seeded), applies -> served or fitted
--      s10, engine_variant  a model or variant version first written in the
--                           last two days -> shadow (never retired here)
--    The three nightly families have one horizon, "as priced": a price
--    label names the versions, not the
--    lead, so the switch's lead setting goes in the evidence. Per-horizon
--    promotion is for candidates, which move by a decision. Each served event
--    names the version it replaced as its
--    rollback target. pg_cron runs it hourly at :50 (database-side: no
--    Actions minutes); it also runs once here.
--
-- Re-runnable. Read as anon after applying: nothing here is granted to it.
-- ===========================================================================

alter table public.decisions add column if not exists prediction_id uuid;
alter table public.decisions add column if not exists prediction_source text;

comment on column public.decisions.prediction_id is
  'The call this decision acted on, as v_prediction_contract.prediction_id names it: the engine checkpoint for S11/S12, the S10 shadow row for S10. Written by the tick from 4 Oct (P2.2 part 3); v_decision_prediction resolves the rows before.';
comment on column public.decisions.prediction_source is
  'The record prediction_id is in (v_prediction_contract.recorded_in).';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'decisions_prediction_source') then
    alter table public.decisions add constraint decisions_prediction_source check (prediction_source in
      ('prediction_checkpoints', 's10_shadow_checkpoints', 'variant_shadow_checkpoints'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'decisions_prediction_named') then
    alter table public.decisions add constraint decisions_prediction_named check
      ((prediction_id is null) = (prediction_source is null));
  end if;
end $$;

-- The function comes before the views: tools/gen_provenance.py credits a
-- view with every table named after it up to the next view, so a view
-- followed by this function would be listed as reading the tables the
-- function reads.
-- ---------------------------------------------------------------------------
-- Every version a nightly fit serves or holds, recorded; the superseded one
-- retired. Returns what it appended.
-- ---------------------------------------------------------------------------
create or replace function public.record_model_versions()
returns jsonb
language plpgsql
set search_path = ''
as $fn$
declare
  appended      integer := 0;
  retired       integer := 0;
  seen          jsonb := '[]'::jsonb;
  window_h      constant numeric := 36;
  f             record;
  cur           record;
  old           record;
  sw            jsonb;
  corr_sw       jsonb;
  prev          text;
  st            text;
  hz            text;
  leads         text;
  why_not       text;
  sources       text;
  priced        jsonb;
  newer         text;
  own_on        boolean;
  may_serve     boolean;
  corr_on       boolean := false;
  max_age       numeric;
  corr_age      numeric := 36;
  fresh         jsonb;
begin
  if to_regclass('public.settings') is not null then
    select value into corr_sw from public.settings where key = 'station_correction_pricing';
    corr_on := coalesce((corr_sw ->> 'enabled')::boolean, false);
    corr_age := coalesce((corr_sw ->> 'max_age_hours')::numeric, 36);
  end if;
  -- SERVED MEANS PRICED (review of #306). Whether a version serves depends on
  -- which rows are fresh, which city-days a fit rewrote, each call's lead and
  -- the switches; the engine records the answer on every price it makes - the
  -- label names the correction, `+station-mos:` when the blend applied and
  -- `+station-width:` when the width priced (forecast_provenance). So a
  -- version is served while it priced in the last window_h hours
  -- (band_probabilities -> model_versions.label, and the tick's checkpoints,
  -- priced_from) and no newer version of its family first priced after its
  -- last price, while the engine can still price with it (its switches on,
  -- its forward rows fresh). Two versions pricing side by side - a city-day
  -- the new fit did not rewrite - are both served. It is retired once
  -- superseded, not priced for window_h hours, switched off or its rows
  -- expired. The newest fit in the forward rows that has not priced is
  -- recorded once, as fitted (the width: shadow), with the reason; an older
  -- fit that never priced is retired as superseded by it.
  for f in
    select * from (values
      (1, 'station_correction', 'station_correction_pricing', 'station-correction:[0-9-]+:[0-9a-f]+', 'station-correction:%', 'min',
       'select version, max(computed_at) as computed_at from public.derived_corrected_forecast where version is not null
         group by version order by max(computed_at) desc, version desc limit 1',
       'select distinct version from public.derived_corrected_forecast
         where version is not null and computed_at >= now() - $1 * interval ''1 hour'''),
      (2, 'station_mos', 'station_mos_pricing', 'station-mos:[0-9-]+:[0-9a-f]+', 'station-mos:%', 'min',
       'select version, max(computed_at) as computed_at from public.derived_mos_forecast where version is not null and blend_c is not null
         group by version order by max(computed_at) desc, version desc limit 1',
       'select distinct m.version from public.derived_mos_forecast m
         where m.version is not null and m.blend_c is not null and m.computed_at >= now() - $1 * interval ''1 hour''
           and exists (select 1 from public.derived_corrected_forecast c
                        where c.city_key = m.city_key and c.for_date = m.for_date and c.version = m.p39_version
                          and c.computed_at >= now() - $2 * interval ''1 hour'')'),
      (3, 'station_width', 'station_width_pricing', 'station-width:[0-9-]+:[0-9a-f]+', 'station-width:%', 'max',
       'select width_version as version, max(computed_at) as computed_at from public.derived_corrected_forecast where width_version is not null
         group by width_version order by max(computed_at) desc, width_version desc limit 1',
       'select distinct width_version from public.derived_corrected_forecast
         where width_version is not null and computed_at >= now() - $1 * interval ''1 hour''')
    ) as v(ord, family, setting, rx, pattern, lead_kind, newest_fit, fresh_q)
    order by ord
  loop
    if to_regclass('public.settings') is null then
      continue;
    end if;
    select value into sw from public.settings where key = f.setting;
    -- ONE HORIZON, "as priced" (review of #306). A price label names the
    -- versions, not the lead, so which leads a nightly version served cannot
    -- be told from its prices; the switch's lead setting goes in the evidence
    -- instead. Per-horizon promotion is for candidates, which move by a
    -- decision; the nightly fits serve by Hassan's rule, where the engine
    -- prices with them.
    hz := 'as priced';
    leads := case when f.lead_kind = 'min' then 'min_lead_days = ' || coalesce(sw ->> 'min_lead_days', '1')
                  else 'max_lead_days = ' || coalesce(sw ->> 'max_lead_days', '1') end;
    own_on := coalesce((sw ->> 'enabled')::boolean, false);
    -- The engine uses a version only while its switch is on, and the MOS
    -- blend and the width only inside the correction's branch: prices made
    -- before a switch went off do not keep it serving (review of #306).
    may_serve := own_on and (f.family = 'station_correction' or corr_on);
    -- And only from forward rows younger than its switch's max_age_hours, as
    -- probability_engine reads them (review of #306): a version priced before
    -- its rows expired cannot price again. The width rides on the
    -- correction's rows, so the correction's age; a MOS row blends only into
    -- a fresh correction row of its city-day made by its p39_version
    -- (_blend_station_model). 36 is the engine's default (STATION_MAX_AGE_HOURS).
    max_age := case when f.family = 'station_mos' then coalesce((sw ->> 'max_age_hours')::numeric, 36) else corr_age end;
    fresh := '{}'::jsonb;
    if to_regclass('public.derived_corrected_forecast') is not null
       and (f.family <> 'station_mos' or to_regclass('public.derived_mos_forecast') is not null) then
      execute 'select coalesce(jsonb_object_agg(v, true), ''{}''::jsonb) from (' || f.fresh_q || ') x(v)'
        into fresh using max_age, corr_age;
    end if;

    -- What this family priced in the window, per version: {version: {first, last, n}}.
    sources := '';
    if to_regclass('public.band_probabilities') is not null and to_regclass('public.model_versions') is not null then
      sources := format('select substring(m.label from %L) as v, b.computed_at as at
                           from public.band_probabilities b join public.model_versions m on m.version_id = b.forecast_version
                          where b.computed_at >= now() - %s * interval ''1 hour'' and m.label ~ %L', f.rx, window_h, f.rx);
    end if;
    if to_regclass('public.prediction_checkpoints') is not null then
      sources := sources || case when sources = '' then '' else ' union all ' end
              || format('select substring(p.priced_from from %L), p.decided_at from public.prediction_checkpoints p
                          where p.decided_at >= now() - %s * interval ''1 hour'' and p.priced_from ~ %L', f.rx, window_h, f.rx);
    end if;
    priced := '{}'::jsonb;
    if sources <> '' then
      execute format('select coalesce(jsonb_object_agg(v, jsonb_build_object(''first'', first_at, ''last'', last_at, ''n'', n)), ''{}'')
                        from (select v, min(at) as first_at, max(at) as last_at, count(*) as n from (%s) x group by v) y', sources)
        into priced;
    end if;

    -- Served: priced, not superseded, its switches on and its rows fresh.
    for cur in
      select k as version, (val ->> 'first')::timestamptz as first_at, (val ->> 'last')::timestamptz as last_at,
             (val ->> 'n')::bigint as n
        from jsonb_each(priced) as e(k, val)
       order by (val ->> 'first')::timestamptz
    loop
      select k into newer from jsonb_each(priced) as e(k, val)
       where (val ->> 'first')::timestamptz > cur.last_at order by (val ->> 'first')::timestamptz limit 1;
      if newer is not null or not may_serve or not fresh ? cur.version then
        continue;
      end if;
      seen := seen || jsonb_build_object('family', f.family, 'version', cur.version, 'horizon', hz, 'state', 'served');
      if exists (select 1 from public.v_model_registry r
                  where r.family = f.family and r.version = cur.version and r.horizon = hz and r.state = 'served') then
        continue;
      end if;
      select r.version into prev from public.v_model_registry r
       where r.family = f.family and r.version <> cur.version and r.state = 'served'
       order by r.decided_at desc, r.event_id desc limit 1;
      insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note)
      values (f.family, cur.version, hz, 'served', 'rule:nightly refit (Rule 11); Hassan 4 Oct',
              format('priced %s times, %s to %s (band_probabilities, prediction_checkpoints); settings.%s.enabled = %s, %s',
                     cur.n, cur.first_at, cur.last_at, f.setting, coalesce(sw ->> 'enabled', 'absent'), leads),
              prev,
              'Served the morning after its fit, by Hassan''s decision of 4 Oct (docs/P22_PREDICTION_CONTRACT.md).');
      appended := appended + 1;
    end loop;

    -- Retired: a served version switched off, superseded, or no longer priced.
    -- Oldest first, by first price as "superseded" reads it: the events of one
    -- run share decided_at, so event_id is the page's tie-break, and two
    -- versions retiring together leave the newer fit the newest retired one
    -- (review of #306).
    for old in
      select r.version, r.horizon from public.v_model_registry r
       where r.family = f.family and r.state = 'served' and r.version like f.pattern
       order by (priced -> r.version ->> 'first')::timestamptz nulls first, r.decided_at, r.event_id
    loop
      newer := null;
      if may_serve and priced ? old.version then
        select k into newer from jsonb_each(priced) as e(k, val)
         where (val ->> 'first')::timestamptz > (priced -> old.version ->> 'last')::timestamptz
         order by (val ->> 'first')::timestamptz limit 1;
        if newer is null and fresh ? old.version then
          continue;
        end if;
      end if;
      insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
      values (f.family, old.version, old.horizon, 'retired', 'rule:nightly refit',
              case when not own_on then format('settings.%s.enabled is off', f.setting)
                   when not may_serve then 'station correction is off'
                   when newer is not null
                   then format('superseded by %s, first priced %s, after this one''s last price %s',
                               newer, priced -> newer ->> 'first', priced -> old.version ->> 'last')
                   when priced ? old.version and f.family = 'station_mos'
                   then format('no forward row it can price from: its rows older than %s h (settings.station_mos_pricing.max_age_hours) or the correction rows they were made from older than %s h (settings.station_correction_pricing.max_age_hours)',
                               max_age, corr_age)
                   when priced ? old.version
                   then format('its forward rows are older than %s h (settings.station_correction_pricing.max_age_hours), so it cannot price',
                               max_age)
                   else format('not priced in the last %s h', window_h) end);
      retired := retired + 1;
    end loop;

    -- The newest fit that has not priced: recorded once, as fitted (the width: shadow).
    if to_regclass(case when f.family = 'station_mos' then 'public.derived_mos_forecast'
                        else 'public.derived_corrected_forecast' end) is not null then
      execute f.newest_fit into cur;
      if cur.version is not null and not priced ? cur.version
         and not exists (select 1 from public.model_registry r where r.family = f.family and r.version = cur.version) then
        why_not := case
          when not own_on then 'the switch is off'
          when f.family <> 'station_correction' and not corr_on then 'station correction is off'
          when not fresh ? cur.version then 'it has no forward row young enough to price from'
          else 'it has not priced yet' end;
        st := case when f.family = 'station_width' then 'shadow' else 'fitted' end;
        seen := seen || jsonb_build_object('family', f.family, 'version', cur.version, 'horizon', hz, 'state', st);
        insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
        values (f.family, cur.version, hz, st, 'rule:nightly refit; ' || why_not,
                format('newest forward rows computed %s; not priced in the last %s h; settings.%s.enabled = %s, %s%s',
                       cur.computed_at, window_h, f.setting, coalesce(sw ->> 'enabled', 'absent'), leads,
                       case when f.family <> 'station_correction'
                            then format('; settings.station_correction_pricing.enabled = %s', corr_on::text) else '' end));
        appended := appended + 1;
      end if;
      -- An older fit that never served is superseded by the newest one before
      -- it priced: retired, so the registry holds one candidate per family,
      -- not one a night (the width's switch is off: a new shadow each night).
      if cur.version is not null then
        for old in
          select r.version, r.horizon from public.v_model_registry r
           where r.family = f.family and r.version like f.pattern and r.version <> cur.version
             and r.state in ('fitted', 'shadow')
           order by r.decided_at, r.event_id
        loop
          insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
          values (f.family, old.version, old.horizon, 'retired', 'rule:nightly refit',
                  format('superseded by the newer fit %s before it priced', cur.version));
          retired := retired + 1;
        end loop;
      end if;
    end if;

    -- Part 1 seeded the width's method row (station_width 'W2 per-city
    -- width', shadow) before the nightly width versions were recorded. Once
    -- one is registered - at the first run, or whenever the first width fit
    -- arrives - that row is retired. Only that row: every other row
    -- registered by hand is left alone (review of #306). Its events stay.
    if f.family = 'station_width' then
      for old in
        select r.version, r.horizon, n.version as newest from public.v_model_registry r
         cross join lateral (select v.version from public.v_model_registry v
                              where v.family = 'station_width' and v.version like 'station-width:%'
                              order by v.decided_at desc, v.event_id desc limit 1) n
         where r.family = 'station_width' and r.version = 'W2 per-city width' and r.state <> 'retired'
      loop
        insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
        values ('station_width', old.version, old.horizon, 'retired', 'rule:nightly refit',
                format('the nightly station_width versions are recorded one by one from P2.2 part 3; the newest is %s', old.newest));
        retired := retired + 1;
      end loop;
    end if;
  end loop;

  -- The calibration map: one version, named by its temperature (as part 1 seeded it).
  if to_regclass('public.settings') is not null then
    select value into sw from public.settings where key = 'calibration_map';
    if sw ? 'T' then
      select 'temperature:T=' || round((sw ->> 'T')::numeric, 3)::text as version into cur;
      st := case when coalesce((sw ->> 'applies')::boolean, false) then 'served' else 'fitted' end;
      seen := seen || jsonb_build_object('family', 'calibration', 'version', cur.version, 'horizon', 'all checkpoints', 'state', st);
      if not exists (select 1 from public.v_model_registry r
                      where r.family = 'calibration' and r.version = cur.version
                        and r.horizon = 'all checkpoints' and r.state = st) then
        select r.version into prev from public.v_model_registry r
         where r.family = 'calibration' and r.horizon = 'all checkpoints' and r.version <> cur.version and r.state = 'served'
         order by r.decided_at desc, r.event_id desc limit 1;
        insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to)
        values ('calibration', cur.version, 'all checkpoints', st, 'settings.calibration_map',
                format('fitted %s on %s settlement dates; applies = %s; validation log loss %s -> %s',
                       coalesce(sw ->> 'fitted_at', '?'), coalesce(sw ->> 'settlement_dates', '?'),
                       coalesce(sw ->> 'applies', 'absent'),
                       coalesce(sw ->> 'validation_log_loss_before', '?'), coalesce(sw ->> 'validation_log_loss_after', '?')),
                prev);
        appended := appended + 1;
        for old in
          select r.version from public.v_model_registry r
           where r.family = 'calibration' and r.horizon = 'all checkpoints' and r.version <> cur.version
             and r.version like 'temperature:%' and r.state <> 'retired'
           order by r.decided_at, r.event_id
        loop
          insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
          values ('calibration', old.version, 'all checkpoints', 'retired', 'settings.calibration_map',
                  format('superseded by %s', cur.version));
          retired := retired + 1;
        end loop;
      end if;
    end if;
  end if;

  -- A shadow model or variant version first written in the last two days and
  -- not yet registered: recorded as shadow. Its test, if any, is registered by
  -- hand with its pre-registration (and a blind event).
  for cur in
    select 's10' as family, s.model_version as version, min(s.decided_at)::text as first_at, count(*) as n
      from public.s10_shadow_checkpoints s
     where s.decided_at >= now() - interval '2 days'
     group by s.model_version
    union all
    select 'engine_variant', v.variant_version, min(v.decided_at)::text, count(*)
      from public.variant_shadow_checkpoints v
     where v.decided_at >= now() - interval '2 days'
     group by v.variant_version
  loop
    if not exists (select 1 from public.model_registry r where r.family = cur.family and r.version = cur.version) then
      insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
      values (cur.family, cur.version, 'same day', 'shadow', 'rule:first shadow row',
              format('first shadow row %s; %s rows in the last two days', cur.first_at, cur.n));
      appended := appended + 1;
    end if;
  end loop;

  return jsonb_build_object('appended', appended, 'retired', retired, 'current', seen);
end
$fn$;

comment on function public.record_model_versions() is
  'P2.2 part 3: appends a model_registry event for each nightly-fit version (station correction, MOS, station width, calibration) in the state its switch gives it, retires the version it supersedes, and registers new S10 and variant versions as shadow. Idempotent; pg_cron runs it hourly at :50.';

revoke all on function public.record_model_versions() from public, anon, authenticated;
grant execute on function public.record_model_versions() to service_role;

select public.record_model_versions();

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_record_model_versions', '50 * * * *', 'select public.record_model_versions()');
  end if;
end $$;

create or replace view public.v_decision_prediction as
with d as (
  select d.decision_id, d.run_id, d.decided_at, d.strategy_id, d.city_key, d.resolution_date,
         d.action, d.reason_code, d.checkpoint_id, d.prediction_id, d.prediction_source,
         d.strategy_id in ('s10_winner', 's10_growth', 's10_lock')                  as reads_s10,
         d.strategy_id in ('s10_winner', 's10_growth', 's10_lock',
                           's11_ladder', 's11_lock', 's12_no')                       as engine_strategy
    from public.decisions d
),
r as (
  select d.*,
         t.checkpoint_id                                                             as s10_row
    from d
    left join public.prediction_checkpoints p on p.checkpoint_id = d.checkpoint_id
    -- Before this PR every S10 decision read rd1, written in the same tick:
    -- the nearest rd1 row at the call's city, date and checkpoint within 60 s.
    left join lateral (
      select s.checkpoint_id from public.s10_shadow_checkpoints s
       where d.reads_s10 and d.prediction_id is null
         and s.city_key = p.city_key and s.target_date = p.target_date and s.checkpoint = p.checkpoint
         and s.model_version like 'rd1:%'
         and abs(extract(epoch from s.decided_at - d.decided_at)) < 60
       order by abs(extract(epoch from s.decided_at - d.decided_at)), s.checkpoint_id
       limit 1) t on true
)
select r.decision_id,
       r.run_id,
       r.decided_at,
       r.strategy_id,
       r.city_key,
       r.resolution_date,
       r.action,
       r.reason_code,
       case when r.prediction_id is not null                        then 'recorded'
            when not r.engine_strategy                              then 'signal_path'
            when r.reason_code = 'no_ladder' or r.checkpoint_id is null then 'no_call'
            when not r.reads_s10                                    then 'checkpoint'
            when r.s10_row is not null                              then 'same_tick'
            else 'not_recorded' end                                 as link,
       coalesce(r.prediction_source,
                case when r.engine_strategy and r.reason_code <> 'no_ladder' and r.checkpoint_id is not null
                     then case when r.reads_s10
                               then case when r.s10_row is not null then 's10_shadow_checkpoints' end
                               else 'prediction_checkpoints' end end)              as recorded_in,
       coalesce(r.prediction_id,
                case when r.engine_strategy and r.reason_code <> 'no_ladder'
                     then case when r.reads_s10 then r.s10_row else r.checkpoint_id end end) as prediction_id,
       r.checkpoint_id                                              as engine_checkpoint_id
  from r;

comment on view public.v_decision_prediction is
  'P2.2 part 3: every decision resolved to the call it acted on (prediction_id and recorded_in, as v_prediction_contract names them) and how: recorded, checkpoint, same_tick, not_recorded, no_call, signal_path. The service role''s.';

revoke all on public.v_decision_prediction from public, anon, authenticated;
grant select on public.v_decision_prediction to service_role;

-- The page lists the standing versions and the newest retired one of each
-- family, and counts the rest. The registry gains retired versions every
-- night, so the view marks the newest (newest_retired) and counts each
-- family's retired versions (retired_in_family), and the page asks for just
-- those rows: a row cap can no longer drop a standing version or the count
-- (review of #306). Events of one run share decided_at (now() is the
-- transaction's), so the tie breaks on event_id, which the page reads too.
-- Appended as the last three columns: the view's other columns, its rows and
-- its grants are unchanged.
create or replace view public.v_learning_status as
select r.family,
       r.version,
       r.horizon,
       r.state,
       case r.state
         when 'captured' then 'data capture'
         when 'fitted'   then 'candidate fitting'
         when 'shadow'   then 'evaluation'
         when 'eligible' then 'evaluation'
         when 'served'   then 'serving'
         else 'retired' end                                      as stage,
       r.blind,
       r.decided_at,
       r.decided_by,
       r.evidence,
       r.rollback_to,
       r.note,
       r.event_id,
       r.state = 'retired'
         and row_number() over (partition by r.family, r.state = 'retired'
                                order by r.decided_at desc, r.event_id desc) = 1 as newest_retired,
       count(*) filter (where r.state = 'retired') over (partition by r.family)  as retired_in_family
  from public.v_model_registry r;
