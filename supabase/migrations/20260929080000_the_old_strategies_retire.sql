-- ===========================================================================
-- THE OLD STRATEGIES RETIRE (plan v2 P8.2 step 5).
--
-- Hassan, 29 Sep, asked whether to keep waiting for the engine's first orders:
-- "Retire all of s1, s3-s9".
--
-- What he was shown (29 Sep ~07:30Z, measured): the engine has placed 0
-- orders; the P5.12 replay passed 1,536 of 1,536 rows, all NONE or WAIT; on
-- 28 Sep the old strategies decided 92 BUY and 87 SELL; closed on their shadow
-- ledgers, all time: s1 101 trades, net -$783.04 (9 won); s3 29, -$64.46 (8);
-- s4 41, +$120.71 (33). The plan's lineage (P8.1): S10 replaces S5 and S7, S11
-- replaces S3, S8 and S9 (s11_lock the S6 idea), S12 replaces S4, S13
-- (research) replaces S1. S2 stays.
--
-- What retiring does:
--   * strategy_state -> 'retired' through set_strategy_state, which records
--     the reason in strategy_state_history and sets strategies.enabled false.
--     Retired is terminal: the lifecycle guard refuses every way back on
--     (paper-contracts proves it).
--   * The signal engine loads enabled strategies only, and paper_plans takes
--     signals from enabled strategies only, so no new entries.
--   * The SHADOW LEDGERS STAY ACTIVE. Their open positions (s1 6, s3 12, s4 8
--     on 29 Sep) still exit by the ledger's own stop-loss/take-profit
--     (paper_exits reads the account, not the strategy) and settle at
--     resolution (settle_paper_inventory). paper_desk_retire refuses a desk
--     that still holds shares; the ledgers are retired once they are flat.
--   * s5 and s7 get the retirement stamp the board reads (the others carry
--     theirs from 22 Sep, 20260922150000 and 20260922234500).
--
-- Re-runnable: a strategy already retired, or missing (the test harness's
-- fixture has none of these ids), is skipped; the stamp is guarded.
-- ===========================================================================
do $retire$
declare
  v_id text;
  v_reason constant text :=
    'Hassan, 29 Sep: "Retire all of s1, s3-s9" (plan v2 P8.2 step 5). Replaced by the engine strategies '
    '(P8.1: S10 for S5/S7, S11 for S3/S8/S9 and the S6 idea, S12 for S4, S13 research for S1).';
begin
  foreach v_id in array array['s1_buy_low_sell_signal', 's3_concentration', 's4_tail_fade',
                              's5_running_max_lock', 's6_anchor_insurance', 's7_pre_peak_gradient',
                              's8_two_bucket_cover', 's9_ladder_basket'] loop
    if exists (select 1 from public.strategy_state where strategy_id = v_id and state <> 'retired') then
      perform public.set_strategy_state(v_id, 'retired', v_reason);
    end if;
  end loop;
end $retire$;

select set_config('arbdesk.change_reason',
                  'plan v2 P8.2 step 5: the retirement stamp the board reads (Hassan, 29 Sep)', true);
update public.strategies
   set extra = coalesce(extra, '{}'::jsonb) || jsonb_build_object(
         'retired_on',      '2026-09-29',
         'retired_because', 'consolidated into S10 (plan v2 P8.1-P8.2); Hassan, 29 Sep',
         'retired_record',  case strategy_id
             when 's5_running_max_lock'  then '43 marked, -23.2c on the dollar, 25.6% right'
             when 's7_pre_peak_gradient' then '2 marked - under 30, retired on the consolidation, not on this'
           end)
 where strategy_id in ('s5_running_max_lock', 's7_pre_peak_gradient')
   and not (coalesce(extra, '{}'::jsonb) ? 'retired_on');
