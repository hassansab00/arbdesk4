"""
S4 - Tail fade [CANDIDATE]. Side: NO.

Take the NO side on outer bands. The only SOURCED support is the fee
curve: p(1-p) approaches zero at the extremes, so trading near the tails
is structurally the cheapest place to transact - a real, measured cost
advantage, not a claim about hit rate.

Risk profile: small frequent gains, large rare losses. Requires a hard
per-city-day cap (enforced via config.capital_cap_pct, same mechanism as
every other strategy - no separate cap invented here). Never fires on
UNCERTAIN or BLOCKED regime - enforced via config.regime_filter, which
must be set to exclude them when this strategy is enabled.
"""
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_TAIL_BANDS = 2   # open tails + this many adjacent closed bands count as "outer"


class S4TailFade(Strategy):

    def _is_outer(self, band, ctx, tail_bands):
        if band.open_low or band.open_high:
            return True
        siblings = sorted(ctx.bands_for_city_day(band.city_key, band.resolution_date),
                           key=lambda b: (b.band_lo if b.band_lo is not None else -1e9))
        closed = [b for b in siblings if not b.open_low and not b.open_high]
        if band not in closed:
            return False
        idx = closed.index(band)
        return idx < tail_bands or idx >= len(closed) - tail_bands

    def entry_signals(self, ctx):
        out = []
        tail_bands = self.config.extra.get("tail_bands", DEFAULT_TAIL_BANDS)
        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            if band.regime_label in ("UNCERTAIN", "BLOCKED"):
                continue
            if not self._is_outer(band, ctx, tail_bands):
                continue
            if not band.no_tradeable or band.no_edge_net_pp is None or band.no_edge_net_pp <= 0:
                continue
            out.append(Signal(
                strategy_id=self.config.strategy_id, band_id=band.band_id, side="NO",
                action="ENTER", reason="tail_fade_cheap_to_transact_near_extreme",
                price_at_fire=band.no_price, prob_at_fire=(1 - band.model_prob_yes) if band.model_prob_yes is not None else None,
                edge_at_fire=band.no_edge_net_pp, suggested_shares=0.0,
                confidence=band.confidence, regime_label=band.regime_label, severity="high",
                dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, "NO", "ENTER",
                                       f"{round(band.no_edge_net_pp, 3)}"),
                payload={"open_low": band.open_low, "open_high": band.open_high},
            ))
        return out

    def exit_signals(self, ctx, open_positions):
        out = []
        by_band = {b.band_id: b for b in ctx.bands}
        for pos in open_positions:
            if pos.get("strategy_id") != self.config.strategy_id:
                continue
            band = by_band.get(pos["band_id"])
            if band is None:
                continue
            if band.regime_label in ("UNCERTAIN", "BLOCKED") or not band.no_tradeable:
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side="NO",
                    action="EXIT", reason="regime_or_liquidity_degraded", price_at_fire=band.no_price,
                    prob_at_fire=(1 - band.model_prob_yes) if band.model_prob_yes is not None else None,
                    edge_at_fire=band.no_edge_net_pp, suggested_shares=pos.get("shares", 0.0),
                    confidence=band.confidence, regime_label=band.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, "NO", "EXIT", "degraded"),
                    payload={"position_id": pos.get("position_id")},
                ))
        return out
