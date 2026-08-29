"""
S5 - Running-max lock [CANDIDATE]. Side: YES.

Once observed temperature has locked a band's floor and remaining heating
cannot exceed it, buy bands now near-certain but still cheap.

SEASONAL GATE - measured, mandatory, enforced in code: when
derived_weather_peak.window_width_h for the city and month exceeds ~12h,
there is no identifiable remaining-heating-window and this strategy must
not fire. `band.s5_allowed` (from regime.py's classify(), Task 5) is the
single source of truth for this gate - S5 never recomputes it.

Depends on the observed trade cutoff (`day_decided`, computed in
scripts/live_weather.py, Task 13d): sustained decline across consecutive
observations, past the point remaining daylight could recover it -
tuned against data there, not guessed here. This MVP only fires once
`day_decided` is true (the band containing the locked running max is, at
that point, the winning band with certainty modulo measurement/rounding
noise) - a deliberately conservative simplification of "remaining heating
capacity" reasoning, documented rather than guessing an early-entry
threshold with no data to tune it against yet.
"""
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_MAX_ENTRY_PRICE = 0.90   # provisional - don't pay near-$1 for a "still cheap" lock


def _local_value(band):
    if band.running_max_c is None:
        return None
    return band.running_max_c * 9.0 / 5.0 + 32.0 if band.unit == "F" else band.running_max_c


def _contains(band, value):
    if band.open_low:
        return value < band.band_hi
    if band.open_high:
        return value >= band.band_lo
    return band.band_lo <= value < band.band_hi


class S5RunningMaxLock(Strategy):

    def entry_signals(self, ctx):
        out = []
        max_entry_price = self.config.extra.get("max_entry_price", DEFAULT_MAX_ENTRY_PRICE)

        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            if not band.s5_allowed:
                continue
            if not band.day_decided:
                continue
            local_max = _local_value(band)
            if local_max is None or not _contains(band, local_max):
                continue
            if not band.yes_tradeable or band.yes_price is None or band.yes_price >= max_entry_price:
                continue

            out.append(Signal(
                strategy_id=self.config.strategy_id, band_id=band.band_id, side="YES",
                action="ENTER", reason="running_max_locked_band_still_cheap",
                price_at_fire=band.yes_price, prob_at_fire=band.model_prob_yes,
                edge_at_fire=band.yes_edge_net_pp, suggested_shares=0.0,
                confidence=band.confidence, regime_label=band.regime_label, severity="critical",
                dedupe_key=dedupe_key(self.config.strategy_id, band.band_id, "YES", "ENTER", "day_decided"),
                payload={"running_max_c": band.running_max_c},
            ))
        return out

    def exit_signals(self, ctx, open_positions):
        # Once locked and held, the position is closed by settlement, not
        # by an early exit rule - the whole point is that the outcome is
        # already decided. No early-exit logic here by design.
        return []
