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
--    event for each version it has not recorded, in the state its switch
--    gives it, and retires the version it supersedes:
--      station_correction   the newest derived_corrected_forecast.version
--      station_mos          the newest derived_mos_forecast.version (blended)
--      station_width        the newest derived_corrected_forecast.width_version
--    - read from the forward rows the engine prices from, not the fitted
--    coefficients (review of #306). Each is served only when its switch is on,
--    its rows are within max_age_hours, and, for the MOS blend and the width,
--    the correction is on, serving, and the one they were made from (both are
--    applied inside its branch); otherwise fitted (the width: shadow), and the
--    event says why. Hassan's decision makes a served version serve the
--    morning after its fit.
--      calibration          settings.calibration_map (T to 3 decimals, the
--                           name part 1 seeded), applies -> served or fitted
--      s10, engine_variant  a model or variant version first written in the
--                           last two days -> shadow (never retired here)
--    The horizon comes from the switch (min_lead_days / max_lead_days), so a
--    change of horizon is a new event too: noon does not authorise the
--    morning. Each served event names the version it replaced as its
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
  f             record;
  cur           record;
  old           record;
  sw            jsonb;
  corr_sw       jsonb;
  prev          text;
  st            text;
  hz            text;
  by_           text;
  why_not       text;
  own_on        boolean;
  fresh         boolean;
  max_age       numeric;
  corr_on       boolean := false;
  corr_serving  text;
begin
  if to_regclass('public.settings') is not null then
    select value into corr_sw from public.settings where key = 'station_correction_pricing';
    corr_on := coalesce((corr_sw ->> 'enabled')::boolean, false);
  end if;
  -- WHAT PRICES, NOT WHAT WAS FITTED (review of #306). probability_engine
  -- prices from the forward rows - derived_corrected_forecast (its version,
  -- and width_version for the width) and derived_mos_forecast (blended only
  -- where its p39_version is the correction row's version) - and only rows
  -- within max_age_hours. The fits write their coefficient tables first and
  -- the forward rows in a later request, so a coefficient version may never
  -- price. The current version of each family is the newest in its forward
  -- rows, and it is served only when everything the engine checks holds;
  -- otherwise the event says what does not. The correction comes first: the
  -- MOS blend and the width are applied inside its branch
  -- (probability_engine._station_corrected_for), so neither serves without it.
  for f in
    select * from (values
      (1, 'station_correction', 'station_correction_pricing', 'station-correction:%', 'min',
       'select version, null::text as made_from, max(computed_at) as computed_at, count(*) as n
          from public.derived_corrected_forecast where version is not null
         group by version order by max(computed_at) desc, version desc limit 1'),
      (2, 'station_mos', 'station_mos_pricing', 'station-mos:%', 'min',
       'select version, p39_version as made_from, max(computed_at) as computed_at, count(*) as n
          from public.derived_mos_forecast where version is not null and blend_c is not null
         group by version, p39_version order by max(computed_at) desc, version desc limit 1'),
      (3, 'station_width', 'station_width_pricing', 'station-width:%', 'max',
       'select width_version as version, version as made_from, max(computed_at) as computed_at, count(*) as n
          from public.derived_corrected_forecast where width_version is not null
         group by width_version, version order by max(computed_at) desc, width_version desc limit 1')
    ) as v(ord, family, setting, pattern, lead_kind, q)
    order by ord
  loop
    if to_regclass('public.settings') is null
       or to_regclass(case when f.family = 'station_mos' then 'public.derived_mos_forecast'
                           else 'public.derived_corrected_forecast' end) is null then
      continue;
    end if;
    execute f.q into cur;
    if cur.version is null then
      continue;
    end if;
    select value into sw from public.settings where key = f.setting;
    if f.lead_kind = 'min' then
      hz := 'lead >= ' || coalesce(sw ->> 'min_lead_days', '1');
    else
      hz := 'lead <= ' || coalesce(sw ->> 'max_lead_days', '1');
    end if;
    own_on := coalesce((sw ->> 'enabled')::boolean, false);
    -- the width rides on the correction's rows, so their age is the correction's
    max_age := coalesce(((case when f.family = 'station_width' then corr_sw else sw end) ->> 'max_age_hours')::numeric, 36);
    fresh := cur.computed_at >= now() - max_age * interval '1 hour';
    why_not := case
      when not own_on then 'the switch is off'
      when not fresh then format('its newest rows (%s) are older than %s h', cur.computed_at, max_age)
      when f.family <> 'station_correction' and not corr_on then 'station correction is off'
      when f.family <> 'station_correction' and corr_serving is distinct from cur.made_from
        then format('it was made from %s, not the correction serving (%s)',
                    coalesce(cur.made_from, 'none'), coalesce(corr_serving, 'none'))
      end;
    st := case when why_not is null then 'served' when f.family = 'station_width' then 'shadow' else 'fitted' end;
    by_ := case when why_not is null then 'rule:nightly refit (Rule 11); Hassan 4 Oct'
                else 'rule:nightly refit; ' || why_not || ', so it does not serve' end;
    if f.family = 'station_correction' then
      corr_serving := case when st = 'served' then cur.version end;
    end if;
    seen := seen || jsonb_build_object('family', f.family, 'version', cur.version, 'horizon', hz, 'state', st);
    -- Already the latest state of this version at this horizon: nothing to add.
    if exists (select 1 from public.v_model_registry r
                where r.family = f.family and r.version = cur.version and r.horizon = hz and r.state = st) then
      continue;
    end if;
    select r.version into prev from public.v_model_registry r
     where r.family = f.family and r.horizon = hz and r.version <> cur.version and r.state = 'served'
     order by r.decided_at desc, r.event_id desc limit 1;
    insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note)
    values (f.family, cur.version, hz, st, by_,
            format('%s forward rows, newest computed %s%s; settings.%s.enabled = %s%s',
                   cur.n, cur.computed_at,
                   case when cur.made_from is not null then format(', made from %s', cur.made_from) else '' end,
                   f.setting, coalesce(sw ->> 'enabled', 'absent'),
                   case when f.family <> 'station_correction'
                        then format('; correction serving: %s', coalesce(corr_serving, 'none')) else '' end),
            prev,
            case when st = 'served' then 'Served the morning after its fit, by Hassan''s decision of 4 Oct (docs/P22_PREDICTION_CONTRACT.md).' end);
    appended := appended + 1;
    -- What it supersedes: every other version and horizon of the family's
    -- own nightly versions still standing - yesterday's fit, or this one at
    -- a horizon the switch no longer gives it. A method row seeded by hand is
    -- left as it is.
    for old in
      select r.version, r.horizon from public.v_model_registry r
       where r.family = f.family and not (r.version = cur.version and r.horizon = hz)
         and r.version like f.pattern and r.state <> 'retired'
    loop
      insert into public.model_registry (family, version, horizon, state, decided_by, evidence)
      values (f.family, old.version, old.horizon, 'retired', 'rule:nightly refit',
              format('superseded by %s at %s (%s)', cur.version, hz, cur.computed_at));
      retired := retired + 1;
    end loop;
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
