"""
S6 - Anchor + insurance [CANDIDATE]. Side: BOTH.

A primary position plus covering legs on other bands. The cap rule is
arithmetic, not remembered evidence - enforced as a hard invariant, not a
judgement call:

  For every covered outcome, payout >= total_cost.
  If any covered outcome produces a net loss, the structure is rejected.

Target-profit form, all covered bands buying N shares of YES:
  cost   = N x sum(executable_ask_i for i in covered_set)
  payout = N                        (exactly one band wins)
  profit = N x (1 - sum(ask))       if a covered band wins
  loss   = N x sum(ask)             if an uncovered band wins
  N required for target profit G:   N = G / (1 - sum(ask))
  Feasible only when sum(ask) < 1, AND only after fees.

Because payout (N) and cost (N x sum(ask)) are the same for every covered
band, "payout >= total_cost for every covered outcome" reduces to the one
basket-wide check sum(ask) <= 1 (fee-inclusive) - there is no per-band
case where it could differ, since which covered band wins doesn't change
either side of that inequality.
"""
from collections import defaultdict

import cost_model
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_WIDTH_BANDS = 2
DEFAULT_TARGET_PROFIT_USD = 50.0   # provisional - UI/settings-settable per deployment


class S6AnchorInsurance(Strategy):

    def entry_signals(self, ctx):
        out = []
        width = self.config.extra.get("width_bands", DEFAULT_WIDTH_BANDS)
        target_profit = self.config.extra.get("target_profit_usd", DEFAULT_TARGET_PROFIT_USD)

        by_city_day = defaultdict(list)
        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            if band.open_low or band.open_high:
                continue
            by_city_day[(band.city_key, band.resolution_date)].append(band)

        for _, bands in by_city_day.items():
            tradeable = [b for b in bands if b.yes_tradeable and b.yes_price is not None]
            if not tradeable:
                continue
            anchor = max(tradeable, key=lambda b: (b.yes_edge_net_pp or -1e9))
            bands_sorted = sorted(bands, key=lambda b: b.band_lo)
            idx = bands_sorted.index(anchor)
            covered = [b for b in bands_sorted[max(0, idx - width): idx + width + 1]
                       if b.yes_tradeable and b.yes_price is not None]
            if len(covered) < 2:
                continue  # "anchor + insurance" needs at least one covering leg

            sum_ask = sum(b.yes_price for b in covered)
            total_fee_per_share = sum(cost_model.taker_fee(1.0, b.yes_price) for b in covered)
            fee_inclusive_sum_ask = sum_ask + total_fee_per_share

            # HARD INVARIANT: reject if any covered outcome nets a loss.
            # payout=N, cost=N*fee_inclusive_sum_ask for every covered band
            # alike, so this is the one check that applies to all of them.
            if fee_inclusive_sum_ask >= 1.0:
                continue

            n_shares = target_profit / (1.0 - fee_inclusive_sum_ask)
            band_ids = [b.band_id for b in covered]

            out.append(Signal(
                strategy_id=self.config.strategy_id, band_id=anchor.band_id, side="YES",
                action="ENTER", reason="anchor_insurance_covered_basket",
                price_at_fire=fee_inclusive_sum_ask, prob_at_fire=sum(b.model_prob_yes or 0.0 for b in covered),
                edge_at_fire=1.0 - fee_inclusive_sum_ask, suggested_shares=n_shares,
                confidence=anchor.confidence, regime_label=anchor.regime_label, severity="high",
                dedupe_key=dedupe_key(self.config.strategy_id, anchor.band_id, "YES", "ENTER",
                                       f"{anchor.city_key}:{anchor.resolution_date}:{round(fee_inclusive_sum_ask, 4)}"),
                payload={"band_ids": band_ids, "sum_ask": sum_ask, "fee_inclusive_sum_ask": fee_inclusive_sum_ask,
                         "n_shares_for_target": n_shares, "target_profit_usd": target_profit},
            ))
        return out

    def exit_signals(self, ctx, open_positions):
        # Covered baskets are held to settlement, same reasoning as S2 -
        # the payout>=cost guarantee only holds while every leg is held.
        return []

    def size(self, signal, portfolio):
        # Overrides the default flat-fraction sizing: N is already computed
        # from the target-profit formula in entry_signals, capped by the
        # same capital_cap_pct ceiling every strategy respects.
        n_from_target = signal.suggested_shares
        cap_shares = super().size(signal, portfolio)
        return min(n_from_target, cap_shares) if cap_shares else n_from_target
