-- ===========================================================================
-- RETIRE THE FIVE STRATEGIES WHOSE ENTRY TEST IS THE MODEL AGAINST THE PRICE.
--
-- NOTHING IS DELETED. The five rows stay, every signal they ever fired stays,
-- and v_strategy_board goes on marking that history to settlement. One column
-- moves - strategies.enabled - which is the same column the Strategies page
-- toggle writes, so this is exactly as reversible as a click.
--
-- WHAT WAS MEASURED, on 2026-09-22, from v_strategy_board. Every signal is
-- marked to settlement at the price that justified IT: no desk, no cash, no
-- approval. "on the dollar" is return_on_stake_pct - what a dollar committed
-- to that strategy's own calls came back as.
--
--     s1_buy_low_sell_signal    738 marked   -17.8c on the dollar   10.8% right
--     s3_concentration          819 marked    -9.1c on the dollar   15.8% right
--     s6_anchor_insurance      1105 marked   -20.4c on the dollar   54.8% of
--                                            its baskets landed a leg and it
--                                            still lost, because a five-band
--                                            cover pays once and costs five
--     s8_two_bucket_cover         4 marked   -11.0c on the dollar   under 30,
--     s9_ladder_basket            4 marked   -11.0c on the dollar   so NOT
--                                            judged on their own record - see
--                                            below
--
-- THESE ARE POST-COHERENCE NUMBERS AND THEY ARE THE ONES THAT COUNT. The same
-- query run four hours earlier gave s1 -7.6c over 615 marked and s3 -5.6c over
-- 702. Between the two, 20260922120000_a_ladder_cannot_resolve_to_nothing
-- landed: 410 market-days had frozen with every band settled_yes = false, an
-- eleven-band ladder that resolved to nothing, and marking a signal against a
-- day with no winner is not a measurement. With those days excluded and the
-- rest re-marked, every strategy reads WORSE, not better. Quoting the earlier
-- pair would have understated the case by ten points and disagreed with the
-- column printed beside it on the page.
--
-- WHY THESE FIVE AND NOT THE WORST FIVE. s5_running_max_lock is -32.8c on the
-- dollar, worse than any of them, and it is NOT retired. The sort is not by
-- P&L, it is by what the entry test reads:
--
--     s1   fires when edge_net_pp clears a bar          model vs price
--     s3   ranks the ladder by model_prob_yes and
--          requires yes_edge_net_pp > 0                 model vs price
--     s6   picks its anchor by max(yes_edge_net_pp)     model vs price
--     s8   ranks by model_prob_yes, gates the pair on
--          min_pair_prob                                model vs price
--     s9   gates on ev_per_dollar, computed from
--          sum(model_prob_yes)                          model vs price
--
--     s5   the running maximum is already banked and the band holds it
--     s7   the slope, the minutes to peak, and whether the day rolled over
--     s2   sum(ask) < 1, pure arithmetic
--
-- The three kept ones record model_prob_yes on the signal they fire, but not
-- one of them DECIDES on it. That is the whole distinction, and it is why s8
-- and s9 go with four settled signals each: they are not retired on a record
-- they do not have, they are retired because they are the same bet. s5's
-- -32.8c is 37 signals on a gate that only opens once the day is already
-- decided - a separate defect, and not one more signals would fix.
--
-- WHY THAT BET LOSES HERE, measured at the D-0 midday instant across the
-- roster: buying YES loses in every one of ten price buckets, -3.9c to -38.2c,
-- eight of them outside the confidence interval. The venue quotes a median 5.7
-- of a market's ~11 bands, so the quoted subset carries roughly 13.5% of
-- overround and it sits inside the bid-ask spread, not in any band's price.
-- And the model does not beat the quote it is being compared against: the
-- market's top two bands contain the winner 26.1% of the time, the model's
-- top two 15.3%. A strategy that acts on (model - price) is acting on the
-- weaker of the two numbers, after paying to cross.
--
-- WHAT IS NOT CLAIMED. s4_tail_fade is in this family too - its single gate is
-- no_edge_net_pp > 0 - and it is -2.2c on the dollar over 451 marked signals.
-- It is left ON because retiring it was not the decision taken. Nothing here
-- rehabilitates it.
--
-- THIS MIGRATION DOES NOT ENFORCE, IT RECORDS. Unlike
-- 20260919230000_retire_five_cities.sql, whose subject is a roster nobody
-- toggles, `enabled` is a switch on a page. So the guard is the retirement
-- STAMP, not the flag: a re-run finds extra->>'retired_on' already set and
-- changes nothing. If Hassan switches one of these back on tomorrow, this file
-- re-running will not quietly switch it off again.
-- ===========================================================================

update public.strategies
   set enabled = false,
       extra = coalesce(extra, '{}'::jsonb) || jsonb_build_object(
         'retired_on',      '2026-09-22',
         'retired_because', 'its entry test is the model against the price, and that bet loses in every price bucket at D-0 midday',
         'retired_record',  case strategy_id
             when 's1_buy_low_sell_signal' then '738 marked, -17.8c on the dollar, 10.8% right'
             when 's3_concentration'       then '819 marked, -9.1c on the dollar, 15.8% right'
             when 's6_anchor_insurance'    then '1105 marked, -20.4c on the dollar, 54.8% of baskets landed a leg'
             when 's8_two_bucket_cover'    then '4 marked - under 30, retired on the shared mechanism, not on this'
             when 's9_ladder_basket'       then '4 marked - under 30, retired on the shared mechanism, not on this'
           end)
 where strategy_id in ('s1_buy_low_sell_signal',
                       's3_concentration',
                       's6_anchor_insurance',
                       's8_two_bucket_cover',
                       's9_ladder_basket')
   and not (coalesce(extra, '{}'::jsonb) ? 'retired_on');
