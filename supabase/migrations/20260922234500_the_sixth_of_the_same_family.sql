-- ===========================================================================
-- s4_tail_fade: THE SIXTH OF THE SAME FAMILY, RETIRED ON THE SAME GROUND.
--
-- On 2026-09-22 five strategies were switched off because their entry test
-- reduces to (model - price - fee) > 0, and that bet loses in all ten price
-- buckets at the D-0 midday instant. s4 belongs to that family - its single
-- numeric gate is
--
--     if not band.no_tradeable or band.no_edge_net_pp is None
--        or band.no_edge_net_pp <= 0:  continue
--
-- which is the same quantity on the NO side - and it was left ON, because
-- retiring it was not the decision taken. That was correct then: it was not
-- in the five Hassan approved, and widening the scope of an approval is not
-- mine to do.
--
-- It was put to him twice as an open decision with the number attached, and
-- on the second pass he answered by instructing the whole remaining
-- checklist. So this is his call being carried out, not a scope I widened -
-- and it is one flag to reverse if he meant otherwise.
--
-- THE RECORD, from v_strategy_board, marked to settlement with no desk
-- involved: 451 marked signals, -2.2c on the dollar, 77.4% of its calls
-- right. That last number is the interesting one and the reason s4 outlived
-- the other five by a day: being right three times in four is a good rule
-- that still loses money, because a NO leg on a tail band costs 90-odd cents
-- to win a few. Fading a tail is correct far more often than it pays for
-- itself, which is exactly what a hit rate cannot tell you and what
-- return_on_stake_pct does.
--
-- NOTHING IS DELETED. The row stays, its 451 signals stay, and the board
-- keeps marking them. One column moves, and it is the column the Strategies
-- page toggle writes.
--
-- Same stamp guard as the five: a re-run finds retired_on set and changes
-- nothing, so switching s4 back on from the page is not quietly undone.
-- ===========================================================================

update public.strategies
   set enabled = false,
       extra = coalesce(extra, '{}'::jsonb) || jsonb_build_object(
         'retired_on',      '2026-09-22',
         'retired_because', 'its entry test is the model against the price - the same family as the five retired earlier today, on the NO side',
         'retired_record',  '451 marked, -2.2c on the dollar, 77.4% of its calls right and still losing')
 where strategy_id = 's4_tail_fade'
   and not (coalesce(extra, '{}'::jsonb) ? 'retired_on');
