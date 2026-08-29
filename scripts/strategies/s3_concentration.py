"""
S3 - Concentration [CANDIDATE]. Side: YES.

Buy a contiguous group of bands around the forecast centre. Re-derived
from measured structure, not from prior-system results: bands are 1C or
2F wide, 9 closed plus 2 open tails, and the ladder re-centres daily
around Polymarket's own expectation - a forecast distribution narrower
than the ladder concentrates mass in a few central bands by construction.

Gate on regime: only fires when regime_label == 'SHARP' (enforced via
config.regime_filter) AND the measured mae_bands for that city is below
the configured threshold. All prior hit-rate claims are void - whether
the market misprices that concentration is unknown and must be measured
by the harness, not assumed here.
"""
from collections import defaultdict

from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_MAX_MAE_BANDS = 1.0     # provisional - matches the spec's own "under 1 band favours concentration" framing
DEFAULT_WIDTH_BANDS = 2         # how many bands either side of centre count as "concentrated"


class S3Concentration(Strategy):

    def entry_signals(self, ctx):
        out = []
        max_mae_bands = self.config.extra.get("max_mae_bands", DEFAULT_MAX_MAE_BANDS)
        width = self.config.extra.get("width_bands", DEFAULT_WIDTH_BANDS)

        by_city_day = defaultdict(list)
        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            if band.open_low or band.open_high:
                continue
            by_city_day[(band.city_key, band.resolution_date)].append(band)

        for _, bands in by_city_day.items():
            centred = [b for b in bands if b.mae_bands is not None and b.mae_bands < max_mae_bands]
            if not centred:
                continue
            bands_sorted = sorted(bands, key=lambda b: b.band_lo)
            best = max(bands_sorted, key=lambda b: (b.model_prob_yes or 0.0))
            idx = bands_sorted.index(best)
            window = bands_sorted[max(0, idx - width): idx + width + 1]

            for band in window:
                if not band.yes_tradeable or band.yes_edge_net_pp is None or band.yes_edge_net_pp <= 0:
                    continue
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side="YES",
                    action="ENTER", reason="concentration_around_forecast_centre",
                    price_at_fire=band.yes_price, prob_at_fire=band.model_prob_yes,
                    edge_at_fire=band.yes_edge_net_pp, suggested_shares=0.0,
                    confidence=band.confidence, regime_label=band.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, "YES", "ENTER",
                                           f"{round(band.yes_edge_net_pp, 3)}"),
                    payload={"mae_bands": band.mae_bands, "distance_from_peak_band": abs(idx - bands_sorted.index(band))},
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
            if band.regime_label not in ("SHARP", "NORMAL") or not band.yes_tradeable:
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side="YES",
                    action="EXIT", reason="regime_degraded", price_at_fire=band.yes_price,
                    prob_at_fire=band.model_prob_yes, edge_at_fire=band.yes_edge_net_pp,
                    suggested_shares=pos.get("shares", 0.0), confidence=band.confidence,
                    regime_label=band.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, "YES", "EXIT", "regime_degraded"),
                    payload={"position_id": pos.get("position_id")},
                ))
        return out
