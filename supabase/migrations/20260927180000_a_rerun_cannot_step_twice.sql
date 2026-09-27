-- ===========================================================================
-- A RERUN CANNOT STEP TWICE (plan v2.3 P5.14, part 1: the two learners that
-- price live)
--
-- Rule 11 bounds each learned value to MAX_STEP a night. scripts/
-- station_correction.py (P3.9) and scripts/station_mos.py (P2.9) measured that
-- step from the value stored LAST, and overwrite it in place. A second run on
-- the same night - the Relearn webhook, a manual dispatch, a GitHub re-run of
-- pipeline_daily - then stepped again from the first run's output, on the same
-- data: a cell held back at 0.25 C moved 0.50 C in one night. Both price live
-- (settings.station_correction_pricing since 27 Sep 06:32Z,
-- station_mos_pricing since 08:23Z). Re-runs happen: ingest_log shows
-- calibration 3 times on 22 Sep and twice on 24 Sep, the hit tournament twice
-- on 24 Sep. These two had run once each when this was written.
--
-- Each row now keeps the value it stepped from and that value's as_of. A run
-- steps from the newest value dated BEFORE its own night, so a re-run with the
-- same data writes the same numbers, and a re-run with new data still moves
-- each value at most one step from the previous night's.
--
-- Additive and re-runnable: two nullable columns per table (the 27 Sep rows
-- have no anchor; tonight's run anchors on them) and one check.
-- ===========================================================================

alter table public.derived_station_correction add column if not exists prev_bias_c numeric;
alter table public.derived_station_correction add column if not exists prev_as_of date;
alter table public.derived_mos_coefficients   add column if not exists prev_coef jsonb;
alter table public.derived_mos_coefficients   add column if not exists prev_as_of date;

comment on column public.derived_station_correction.prev_bias_c is
  'The bias this row stepped from: the value in force before its as_of night (plan v2.3 P5.14). A re-run the same night steps from here, not from bias_c.';
comment on column public.derived_station_correction.prev_as_of is
  'The as_of of prev_bias_c; always before as_of.';
comment on column public.derived_mos_coefficients.prev_coef is
  'The coefficients this row stepped from: those in force before its as_of night (plan v2.3 P5.14). A re-run the same night steps from here, not from coef.';
comment on column public.derived_mos_coefficients.prev_as_of is
  'The as_of of prev_coef; always before as_of.';

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'derived_station_correction_anchor_is_earlier'
                    and conrelid = 'public.derived_station_correction'::regclass) then
    alter table public.derived_station_correction
      add constraint derived_station_correction_anchor_is_earlier
      check (prev_as_of is null or prev_as_of < as_of);
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'derived_mos_coefficients_anchor_is_earlier'
                    and conrelid = 'public.derived_mos_coefficients'::regclass) then
    alter table public.derived_mos_coefficients
      add constraint derived_mos_coefficients_anchor_is_earlier
      check (prev_as_of is null or prev_as_of < as_of);
  end if;
end $$;
