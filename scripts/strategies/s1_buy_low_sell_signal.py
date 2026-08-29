"""
S1 - Buy-low / sell-on-signal [HASSAN]. Side: BOTH.

Merged from two variants at Hassan's direction: one entry rule,
configurable as time-based (enter at D-2/D-1) or price-based (enter below
a threshold).

GATE (not optional, enforced in code): S1 pays a taker fee and crosses the
spread on entry AND exit. If the projected edge does not exceed two
spreads plus two fees, no signal is emitted - this is the hypothesis H4
bar from the spec.
"""
import cost_model
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_PRICE_THRESHOLD = 0.30
DEFAULT_LEAD_DAYS_TRIGGER = 2
DEFAULT_TARGET_MARGIN_PP = 0.05
DEFAULT_EXIT_DAY_OF_RESOLUTION = True
DEFAULT_MIN_LIQUIDITY_USD = 200.0


class S1BuyLowSellSignal(Strategy):

    def _side_state(self, band, side):
        if side == "YES":
            return band.yes_price, band.yes_edge_net_pp, band.yes_tradeable, band.fillable_usd_5c_yes
        return band.no_price, band.no_edge_net_pp, band.no_tradeable, band.fillable_usd_5c_no

    def _round_trip_bar(self, price, spread):
        rt = cost_model.round_trip_cost(shares=1.0, entry_price=price, exit_price=price,
                                         spread=spread or 0.0)
        return rt["total_cost"]

    def entry_signals(self, ctx):
        out = []
        mode = self.config.extra.get("entry_mode", "price")
        price_threshold = self.config.extra.get("price_threshold", DEFAULT_PRICE_THRESHOLD)
        lead_trigger = self.config.extra.get("lead_days_trigger", DEFAULT_LEAD_DAYS_TRIGGER)

        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            sides = ("YES", "NO") if self.config.side == "BOTH" else (self.config.side,)
            for side in sides:
                price, edge_net, tradeable, fillable = self._side_state(band, side)
                if not tradeable or price is None or edge_net is None:
                    continue

                if mode == "time":
                    triggered = getattr(band, "lead_days", None) is not None and band.lead_days <= lead_trigger
                else:
                    triggered = price <= price_threshold
                if not triggered:
                    continue

                spread = getattr(band, "spread", None)
                bar = self._round_trip_bar(price, spread)
                if edge_net <= bar:
                    continue  # H4 gate: must beat two spreads + two fees, not just beat the market

                # suggested_shares is filled in by the caller via
                # strategy.size(signal, portfolio) once real bankroll/risk
                # state is available (paper_engine, at approval time) -
                # entry_signals has no portfolio context to size against.
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side=side,
                    action="ENTER", reason=f"s1_{mode}_entry_edge_exceeds_round_trip",
                    price_at_fire=price, prob_at_fire=band.model_prob_yes if side == "YES" else (1 - band.model_prob_yes if band.model_prob_yes is not None else None),
                    edge_at_fire=edge_net, suggested_shares=0.0,
                    confidence=band.confidence, regime_label=band.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, side, "ENTER",
                                           f"{mode}:{round(edge_net, 3)}"),
                    payload={"round_trip_bar": bar, "mode": mode},
                ))
        return out

    def exit_signals(self, ctx, open_positions):
        out = []
        target_margin = self.config.extra.get("target_margin_pp", DEFAULT_TARGET_MARGIN_PP)
        min_liquidity = self.config.extra.get("min_liquidity_usd", DEFAULT_MIN_LIQUIDITY_USD)
        by_band = {b.band_id: b for b in ctx.bands}

        for pos in open_positions:
            if pos.get("strategy_id") != self.config.strategy_id:
                continue
            band = by_band.get(pos["band_id"])
            if band is None:
                continue
            side = pos["side"]
            price, edge_net, tradeable, fillable = self._side_state(band, side)
            prob = band.model_prob_yes if side == "YES" else (1 - band.model_prob_yes if band.model_prob_yes is not None else None)

            reason = None
            severity = "high"
            if price is not None and prob is not None and (prob - price) <= target_margin:
                reason, severity = "target_converged", "high"
            elif band.day_decided and price is not None and abs(prob - price) < target_margin:
                reason, severity = "confirmation_near_certain", "high"
            elif prob is not None and pos.get("prob_at_entry") is not None and abs(prob - pos["prob_at_entry"]) >= 0.5:
                reason, severity = "invalidation_model_moved_off_band", "critical"
            elif pos.get("risk_breached"):
                reason, severity = "risk_limit_breached", "critical"
            elif fillable is not None and fillable < min_liquidity:
                reason, severity = "liquidity_collapsed", "medium"
            elif pos.get("hours_to_resolution") is not None and pos["hours_to_resolution"] <= self.config.extra.get("time_exit_hours", 2):
                reason, severity = "time_exit", "high"

            if reason:
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side=side,
                    action="EXIT", reason=reason, price_at_fire=price, prob_at_fire=prob,
                    edge_at_fire=edge_net, suggested_shares=pos.get("shares", 0.0),
                    confidence=band.confidence, regime_label=band.regime_label, severity=severity,
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, side, "EXIT", reason),
                    payload={"position_id": pos.get("position_id")},
                ))
        return out
