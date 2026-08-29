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
"""
from collections import defaultdict

import cost_model
from strategies.base import Strategy, Signal, dedupe_key


def _leg_cost_and_fee(price):
    return price, cost_model.taker_fee(1.0, price)


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
            price = b.yes_price if side == "YES" else b.no_price
            tradeable = b.yes_tradeable if side == "YES" else b.no_tradeable
            if price is None or not tradeable:
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
