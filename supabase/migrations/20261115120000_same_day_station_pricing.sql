-- ===========================================================================
-- THE SAME-DAY FORECAST PATH PRICES FROM THE STATION-CORRECTED COMBINATION
--
-- Hassan, 15 Nov: "fully audit and analyze the weather forecasting prediction
-- model city by city ... If something needs improvement ... implement it".
-- The audit is docs/MODEL_AUDIT_2026-11-15.md.
--
-- What it found. The evening before a day, the engine prices it from the
-- station-corrected combination (P3.9). On the day itself, at lead 0, it went
-- back to the public forecast less a per-city bias: the morning call scored
-- worse than the call made 15 hours earlier (17 Oct - 14 Nov, first calls:
-- morning top pick right 34.6%, log loss 1.681; evening before 39.4%, 1.518).
--
-- The check. sd_corr:v1's forward rows (docs/SD_CORR_PREREG.md) priced every
-- same-day checkpoint from the combination live at that moment and its stored
-- width, beside the served call. Where the forecast path served (41 dates,
-- 5 Oct - 14 Nov; log loss, served minus corrected, 90% by date):
--   morning      +0.2413 [+0.2044, +0.2814]  1,866 calls, top pick 35.7% -> 40.1%
--   noon         +0.1566 [+0.0906, +0.2202]    608
--   2 h to peak  +0.1484 [+0.0816, +0.2104]    611
--   1 h to peak  +0.1232 [+0.0723, +0.1694]    608
--   1 h after    +0.0809 [+0.0363, +0.1276]    612
-- On the 20 dates after sd_corr's first look the morning gain is +0.2320
-- [+0.1864, +0.2758]. sd_corr as pre-registered (all five checkpoints against
-- whatever served) was rejected on 15 Nov, because from 17 Oct wxrd3 serves
-- noon and later and beats it there. This switch does not touch those calls:
-- it changes only what the FORECAST path prices on the day, which is the
-- morning call and the fallback when wxrd3 cannot serve.
--
-- probability_engine reads this switch with the correction's own rows
-- (settings.station_correction_pricing must be on). While it is on, a price
-- at lead 0 that no promoted model made takes the combination's own centre
-- (never the station-model blend, which that test did not include) and the
-- width stored beside it, whatever the row's lead; a row without a width
-- prices nothing here. Setting enabled to false returns same-day prices to
-- the public-forecast path on the next run. Day-ahead prices are unchanged.
-- ===========================================================================
do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  insert into public.settings (key, value)
  values ('station_same_day_pricing', jsonb_build_object(
    'enabled', true,
    'policy', 'station-same-day-v1',
    'decided_by', 'Hassan, 15 Nov 2026',
    'why', 'docs/MODEL_AUDIT_2026-11-15.md: sd_corr:v1 forward rows where the forecast path served, 41 dates: morning log loss +0.2413 [+0.2044, +0.2814], top pick 35.7% -> 40.1%; a gain at every later checkpoint. wxrd3 keeps noon and later.'))
  on conflict (key) do nothing;
end $$;
