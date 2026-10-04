-- ===========================================================================
-- THE PAGE READS THE CONTRACT (P2.2 part 2, 4 Oct 2026)
--
-- Part 1 (20261004190000) built v_prediction_contract and the registry for the
-- service role. The page reads as anon, through owner-rights views over the
-- service role's tables (v_checkpoint_calls, v_prediction_hindsight). Two more:
--
--   v_learning_status    each predictor version's latest registry state, in
--                        the plan's four words: data capture, candidate
--                        fitting, evaluation, serving (and retired).
--   v_prediction_lineup  the last 8 target dates' calls side by side - the
--                        served engine, S10 rd1 and rd3, the da_floor variant
--                        - each with its version, role, top bucket and centre,
--                        and the venue's winner once settled.
--
-- BLINDED VERSIONS SHOW NO PAST CALL. rd3 and da_floor are under pre-registered
-- forward tests (docs/CHALLENGER_C_PREREG.md, docs/P11_DA_FLOOR_PREREG.md) that
-- compute and report no score before their first look. A lineup row puts each
-- call beside the day's winner, so a blinded version's settled call on this
-- page would be its score read early. model_registry gains `blind`; while a
-- version's latest event is blind, its calls show only for today and later,
-- and never with a winner, hit or probability on the winner. The first look
-- unblinds it with a new event.
--
-- ONE CALL PER PREDICTOR PER CHECKPOINT. The engine re-captures a checkpoint
-- when its version changes mid-day (72 second captures in the 8 days to 4 Oct,
-- live); the lineup keeps the first, the rule v_checkpoint_calls.first_call
-- grades (20261004140000).
--
-- AND THE VERSION PART 1 MISSED. S10's first file, rd1:2026-09-25:389620c0d9,
-- wrote the shadow rows of 27 Sep 12:36Z - 30 Sep 07:36Z (656, live) until the
-- refit on the repaired labels replaced it (docs/PLAN_PROGRESS.md, check 6).
-- It is appended as retired.
--
-- Read as anon after applying (CLAUDE.md). Re-runnable.
-- ===========================================================================

alter table public.model_registry add column if not exists blind boolean not null default false;

comment on column public.model_registry.blind is
  'True while the version is under a pre-registered test that reads no score before its first look: the page shows its calls, never their outcome. A new event unblinds it.';

-- OWNER RIGHTS, STILL THE SERVICE ROLE'S. Part 1's two views were
-- security_invoker; read through the page's owner-rights views below they
-- would check as anon, which can read none of their tables (CLAUDE.md, 23 Sep).
-- As owner-rights views they are still granted to the service role alone, so
-- anon reads them only through the two page views.
alter view public.v_prediction_contract set (security_invoker = false);

create or replace view public.v_model_registry with (security_invoker = false) as
select distinct on (family, version, horizon)
       family, version, horizon, state, decided_at, decided_by, evidence, rollback_to, note, event_id, blind
  from public.model_registry
 order by family, version, horizon, decided_at desc, event_id desc;

insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note, blind)
select v.family, v.version, v.horizon, 'shadow', v.decided_by, v.evidence, v.rollback_to, v.note, true
  from (values
    ('s10', 'rd3:2026-09-25:555719d4a1', 'same day', 'docs/CHALLENGER_C_PREREG.md',
     'forward shadow since 3 Oct; the pre-registration computes no forward score before its first look (rd3''s 20th settled date, from 25 Oct)',
     'rd1:2026-09-25:f5372ebb05', 'blinded until the first look'),
    ('engine_variant', 'da_floor:v1', 'same day', 'docs/P11_DA_FLOOR_PREREG.md',
     'the pre-registration reads only counts before 20 target dates are scored (about 25 Oct)',
     null, 'blinded until the first look')
  ) as v(family, version, horizon, decided_by, evidence, rollback_to, note)
 where not exists (select 1 from public.model_registry r
                    where r.family = v.family and r.version = v.version
                      and r.horizon = v.horizon and r.blind);

insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note)
select 's10', 'rd1:2026-09-25:389620c0d9', 'same day', 'retired', 'docs/PLAN_PROGRESS.md (check 6, 30 Sep)',
       's10_shadow_checkpoints 27 Sep 12:36Z - 30 Sep 07:36Z, 656 rows; replaced from 30 Sep 08:36Z by rd1:2026-09-25:f5372ebb05, the same fit on the repaired labels',
       null, 'S10''s first file; its rows stay in the shadow record under this version.'
 where not exists (select 1 from public.model_registry r
                    where r.family = 's10' and r.version = 'rd1:2026-09-25:389620c0d9'
                      and r.horizon = 'same day' and r.state = 'retired');

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
       r.note
  from public.v_model_registry r;

comment on view public.v_learning_status is
  'P2.2 part 2: each predictor version''s latest registry state, in the plan''s words (data capture, candidate fitting, evaluation, serving). Owner rights, read by /predictive.';

create or replace view public.v_prediction_lineup as
with calls as (
  select c.*,
         row_number() over (
           partition by c.city_key, c.target_date, c.checkpoint, c.model_family,
                        case when c.model_family = 'engine' then '' else c.artifact_version end
           order by c.as_of, c.prediction_id collate "C")            as nth
    from public.v_prediction_contract c
   where c.target_date >= (current_date - 7)
),
blinded as (
  select distinct family, version from public.v_model_registry where blind
),
winner as (
  select distinct on (o.city_key, o.target_date) o.city_key, o.target_date, o.winner_band_id
    from public.fact_checkpoint_outcome o
   where o.target_date >= (current_date - 7) and o.winner_band_id is not null
   order by o.city_key, o.target_date, o.settled_at desc nulls last
),
shown as (
  select c.*,
         (b.version is not null)                                    as blind,
         (b.version is not null
          and (c.target_date < current_date or w.winner_band_id is not null)) as withheld,
         w.winner_band_id
    from calls c
    left join blinded b on b.family = c.model_family and b.version = c.artifact_version
    left join winner w on w.city_key = c.city_key and w.target_date = c.target_date
   where c.nth = 1
)
select s.city_key,
       s.target_date,
       s.checkpoint,
       s.model_family,
       s.artifact_version                                          as version,
       s.serving_role,
       s.as_of,
       s.station,
       s.blind,
       s.withheld,
       case when not s.withheld then s.top_band_id end             as top_band_id,
       case when not s.withheld then tb.band_label end             as top_label,
       case when not s.withheld then s.top_prob end                as top_prob,
       case when not s.withheld then s.priced_centre_c end         as priced_centre_c,
       case when not s.withheld then s.uncertainty_c end           as uncertainty_c,
       case when not s.blind then s.winner_band_id end             as winner_band_id,
       case when not s.blind then wb.band_label end                as winner_label,
       case when not s.blind and s.winner_band_id is not null
            then s.top_band_id = s.winner_band_id end              as hit,
       -- a band the ladder did not price counts as zero, as everywhere on the page
       case when not s.blind and s.winner_band_id is not null
            then coalesce((s.probs ->> s.winner_band_id)::numeric, 0) end as prob_on_winner
  from shown s
  left join public.v_canonical_bands tb on tb.band_id::text = s.top_band_id
  left join public.v_canonical_bands wb on wb.band_id::text = s.winner_band_id;

comment on view public.v_prediction_lineup is
  'P2.2 part 2: the last 8 target dates'' calls side by side - the served engine (its first capture of each checkpoint), S10 and the engine variants - with version, role, top bucket and the venue''s winner once settled. A blinded version (model_registry.blind) shows its calls for today and later only, and never an outcome. Owner rights, read by /predictive.';

revoke all on public.v_learning_status, public.v_prediction_lineup from public, anon, authenticated;
grant select on public.v_learning_status, public.v_prediction_lineup to anon, authenticated, service_role;
