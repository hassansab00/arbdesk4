"""
S2 - Combination arb [HASSAN]. Side: BOTH. The general case; ladder arb is
one specific instance of it.

Scans, per city-day, the two CANONICAL fully-covering baskets rather than
every possible band subset - this is what keeps the scan in "normalised
space" (critical note #2): NO on every band but one is economically near-
identical to YES on that one, and a naive combinatorial scan would report
the same opportunity three times.

  Basket A - buy YES on every band: exactly one wins, guaranteed payout $1.
             riskless iff fee-inclusive cost < 1.
  Basket B - buy NO on every band: exactly 10 of 11 win (only the actual
             winner's NO leg pays 0), guaranteed payout $10.
             riskless iff fee-inclusive cost < 10.

Requires no forecast - pure arithmetic. Lowest-dependency strategy in the
set. Thresholds must be fee-inclusive (critical note #1) - uses
cost_model.taker_fee per leg, not raw prices. NO_BOOK bands (no price at
any level - critical note #3, distinct from a DEAD_LOSER band which IS
buyable near zero) make a basket incomplete and are excluded from being
called riskless, since the guarantee depends on holding every leg.

WHAT COUNTS AS A COMPLETE BASKET is the whole strategy: see BOOK_UNAVAILABLE
below. Testing `tradeable` instead of testing the BOOK excluded every dead
band - the ones this docstring says are buyable near zero - and so this
strategy never completed a single basket in its life.
"""
from collections import defaultdict

import cost_model
from strategies.base import Strategy, Signal, dedupe_key


def _leg_cost_and_fee(price):
    return price, cost_model.taker_fee(1.0, price)


# THE ONLY TWO REASONS A LEG CANNOT BE BOUGHT.
#
# Every other block reason on an edge - dead_band, below_7c_yes,
# no_verified_skill, anomaly, far_from_forecast - is a statement about the
# FORECAST or about whether the edge is worth taking. This strategy has no
# forecast (the docstring above: "Requires no forecast - pure arithmetic") and
# is not looking for an edge; it is looking for a set of prices that add up to
# less than a guaranteed payout. Those gates are about a different question and
# must not close a basket.
#
# A DEAD BAND IS THE CLEAREST CASE AND WAS THE ONE THAT BROKE IT. The docstring
# already said so - "distinct from a DEAD_LOSER band which IS buyable near
# zero" - and the code then tested `tradeable`, which is false for exactly
# those bands. On 21 Sep, of 11 bands in a market, 10 carried a YES price and
# only 2.9 were `tradeable`; 510 of the 1,021 edges in six hours were blocked
# `dead_band`. So the completeness test could never pass, and this strategy
# returned an empty list on every run since it was written - not "no arb
# found" but "never looked".
#
# Gating on the book instead: 71 of 99 city-days form a complete basket, the
# cheapest costs 1.0628 against a $1.00 payout and the average 1.2581, so the
# answer is still no arbitrage - but it is now an answer rather than a silence,
# and a mispricing would be seen.
BOOK_UNAVAILABLE = frozenset({"no_book", "stale_book"})


def _buyable(price, tradeable, block_reason):
    """Can this leg actually be bought right now, at this price?"""
    if price is None:
        return False
    return bool(tradeable) or block_reason not in BOOK_UNAVAILABLE


class S2CombinationArb(Strategy):

    def entry_signals(self, ctx):
        out = []
        by_city_day = defaultdict(list)
        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            by_city_day[(band.city_key, band.resolution_date)].append(band)

        for (city_key, resolution_date), bands in by_city_day.items():
            out.extend(self._scan_basket(bands, "YES", target_payout=1.0))
            out.extend(self._scan_basket(bands, "NO", target_payout=len(bands) - 1))
        return out

    def _scan_basket(self, bands, side, target_payout):
        prices = []
        for b in bands:
            if side == "YES":
                price, tradeable, reason = b.yes_price, b.yes_tradeable, b.yes_block_reason
            else:
                price, tradeable, reason = b.no_price, b.no_tradeable, b.no_block_reason
            if not _buyable(price, tradeable, reason):
                return []  # incomplete basket - cannot be guaranteed, not an arb
            prices.append((b, price))

        total_cost = sum(p for _, p in prices)
        total_fee = sum(cost_model.taker_fee(1.0, p) for _, p in prices)
        fee_inclusive_cost = total_cost + total_fee
        if fee_inclusive_cost >= target_payout:
            return []

        profit_per_unit = target_payout - fee_inclusive_cost
        band_ids = [b.band_id for b, _ in prices]
        anchor = prices[0][0]
        return [Signal(
            strategy_id=self.config.strategy_id, band_id=anchor.band_id, side=side,
            action="ENTER", reason="combination_arb_fee_inclusive_guaranteed_payoff",
            price_at_fire=fee_inclusive_cost, prob_at_fire=1.0, edge_at_fire=profit_per_unit,
            suggested_shares=0.0, confidence=1.0, regime_label=anchor.regime_label,
            severity="critical",
            dedupe_key=dedupe_key(self.config.strategy_id, anchor.band_id, side, "ENTER",
                                   f"{anchor.city_key}:{anchor.resolution_date}:{round(fee_inclusive_cost, 4)}"),
            payload={"basket_side": side, "band_ids": band_ids, "target_payout": target_payout,
                     "fee_inclusive_cost": fee_inclusive_cost, "profit_per_unit": profit_per_unit},
        )]

    def exit_signals(self, ctx, open_positions):
        # Arb baskets are held to settlement by construction (the guarantee
        # only holds if every leg is held) - nothing to exit early on.
        return []
