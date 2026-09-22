-- ===========================================================================
-- RETENTION THAT ANSWERS TO THE TIER, INSTEAD OF A GUESS MADE ONCE.
--
-- 522 MB against a 500 MB free tier - 104.5%, up from 508 MB the same
-- morning. The obvious diagnosis is a broken prune, and it is wrong. Measured
-- 2026-09-22, the archive-then-prune cycle is working and is caught up:
--
--     beyond its keep window     eligible to prune
--     book_snapshots  29,935     1,770   the rest are cited by an edge or are
--                                        the only snapshot of their band-hour
--     research         4,465     4,465
--     resolution       3,089     3,089
--     edges            3,322     3,322
--
-- and the export is not capped - it walks the whole keyset until exhausted,
-- so nothing is falling behind. Everything eligible comes out on the next run
-- and that is about 25 MB. Two days later it is back.
--
-- THE REAL PROBLEM IS THAT THE STEADY STATE HAS NO HEADROOM. Seven datasets
-- each hold a window that was chosen on its own merits, and the sum of those
-- windows is a database slightly larger than the plan it runs on. Nobody set
-- that; it is what seven reasonable local decisions add up to.
--
-- So the windows stop being constants. Each dataset now declares TWO numbers:
-- the window it wants, and the shortest window it can survive on. While the
-- database is under the high-water mark every dataset gets what it wants.
-- Over it, every dataset drops to its floor until the next run brings the
-- size back down. That makes storage self-correcting instead of a decision
-- somebody has to remember to take again in a fortnight.
--
-- THE FLOORS ARE NOT ARBITRARY and they are not zero:
--
--     observations  90 -> 60   prune_observations refuses under 30 outright;
--                              the fit trains on months
--     forecasts     90 -> 60   same shape, same fitter
--     trades        90 -> 14   v_band_volume and v_city_volume both cut at
--                              lookback_hours = 24, so nothing live reads a
--                              trade older than a day
--     edges         14 ->  7   halves the window AND unpins book snapshots,
--                              11,726 of which are held only by an edge that
--                              is itself over a week old
--     books          7 ->  4   costs the monitor's intra-day chart some
--                              resolution, which is why it is the floor and
--                              not the default
--     research       2 ->  1
--     resolution     3 ->  1   nothing reads settlement evidence after the
--                              settlement is frozen; the archive has it
--
-- NOTHING IS DELETED, under pressure or otherwise. Every one of these goes to
-- data/archive/<dataset>/ in the repository FIRST, with the prune refusing
-- unless the verified archive count matches. A shorter window moves the line
-- between "in Postgres" and "in git", and moves it back when the pressure
-- lifts.
--
-- Idempotent: one settings row and one read-only function.
-- ===========================================================================

insert into settings (key, value)
values ('storage_budget', '{
  "note": "Supabase free tier is 500 MB. high_water_pct is where retention drops to its floors; target_pct is where it relaxes again. The gap between them is hysteresis - without it the archiver flaps between windows every run.",
  "tier_mb": 500,
  "high_water_pct": 92,
  "target_pct": 85
}'::jsonb)
on conflict (key) do nothing;

update settings
   set value = value || '["storage_budget"]'::jsonb
 where key = 'ui_editable_keys'
   and jsonb_typeof(value) = 'array'
   and not (value @> '["storage_budget"]'::jsonb);


create or replace function public.storage_pressure()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $ad4$
declare
  v_cfg   jsonb;
  v_tier  numeric;
  v_high  numeric;
  v_target numeric;
  v_mb    numeric;
  v_pct   numeric;
begin
  select value into v_cfg from settings where key = 'storage_budget';
  v_tier   := coalesce((v_cfg ->> 'tier_mb')::numeric, 500);
  v_high   := coalesce((v_cfg ->> 'high_water_pct')::numeric, 92);
  v_target := coalesce((v_cfg ->> 'target_pct')::numeric, 85);

  v_mb  := round(pg_database_size(current_database()) / 1024.0 / 1024.0, 1);
  v_pct := round(100.0 * v_mb / nullif(v_tier, 0), 1);

  return jsonb_build_object(
    'db_mb',          v_mb,
    'tier_mb',        v_tier,
    'pct_of_tier',    v_pct,
    'high_water_pct', v_high,
    'target_pct',     v_target,
    -- `over` is what the archiver reads. It is deliberately the only boolean:
    -- a caller that has to compare two percentages itself will eventually
    -- compare them differently somewhere else.
    'over',           (v_pct >= v_high),
    'verdict',        case
                        when v_pct >= v_high
                          then format('%s%% of the tier - retention drops to its floors until this is under %s%%', v_pct, v_target)
                        when v_pct >= v_target
                          then format('%s%% of the tier - inside the hysteresis band, windows unchanged', v_pct)
                        else format('%s%% of the tier - every dataset keeps its full window', v_pct)
                      end);
end;
$ad4$;

comment on function public.storage_pressure() is
  'Database size against the plan, and whether retention should fall back to its floors. The archive job reads `over`; everything else is for a human looking at why a window changed.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function public.storage_pressure() to %I', r);
    end if;
  end loop;
end
$ad4$;
