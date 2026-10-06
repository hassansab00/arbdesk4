-- ===========================================================================
-- rd1's LATE HOURS ARE A VERSION OF THEIR OWN (the P.5 report, question 3;
-- Hassan, 6 Oct: the strategies decide before and during each city's own
-- peak). Madrid's post-peak checkpoint falls at 18:xx local, an hour the
-- served fit rd1:2026-09-25:f5372ebb05 has no parameters for, so S10 never
-- decided it (7 of 7 days, 29 Sep - 5 Oct).
--
-- The late hour is fitted beside the served fit, the same way
-- (tools/fit_remaining_day.py --late-of), and its rows record this version.
-- The served fit, its hours 7-17 and its version are untouched, so Challenger
-- C's pre-registered forward comparison, which selects rd1's rows by that
-- version (tools/fec_s10_forward.py INCUMBENT), is unchanged.
-- ===========================================================================
insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note)
select 's10', 'rd1:2026-09-25:66d6600c72', 'same day', 'shadow',
       'Hassan 6 Oct (the P.5 report, question 3)',
       'rd1''s late hour 18, beside rd1:2026-09-25:f5372ebb05: walk-forward over 15,695 city-days (329 days, 48 cities), each month fitted on the days before it; log loss 0.122 against the floor-atom proxy''s 1.491, gain 1.369 (95% 1.318-1.421); top bucket 97.6% against 72.3%',
       null,
       'Only for the local hours the served rd1 fit does not cover (18). Hours 7-17 keep rd1:2026-09-25:f5372ebb05.'
 where not exists (select 1 from public.model_registry r
                    where r.family = 's10' and r.version = 'rd1:2026-09-25:66d6600c72'
                      and r.horizon = 'same day' and r.state = 'shadow');
