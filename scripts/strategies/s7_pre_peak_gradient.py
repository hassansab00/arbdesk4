"""
S7 - Pre-peak gradient entry [CANDIDATE]. Side: BOTH.

THE TRADE, AS TRADED BY HAND. An hour or less before the peak, look at how
the temperature has MOVED over the last few readings. Still climbing, and the
band above the current reading is still live and usually still cheap. Already
rolling over, and that band is dead and the market has not always noticed.
The level alone cannot tell those apart: 28.4C an hour before peak is a buy
after 26.9 / 27.7 / 28.4 and a sell after 29.1 / 28.8 / 28.4.

WHAT MAKES IT MECHANICAL rather than a feel. Three measured inputs, all from
sql/ad4_26_temp_trend.sql:

  slope_3_c_per_h   least squares over the last three readings, on their real
                    timestamps. Not last-minus-first: METAR is hourly but
                    SPECIs are not, and uneven spacing breaks the naive form.
  rolling_over      the short slope negative while the six-reading slope is
                    still positive. That is the turn, before the average
                    catches up to it.
  implied_max_c     the latest reading plus how much this city has
                    HISTORICALLY still climbed from this local hour. +0.9C/h
                    at 13:00 is ordinary in Phoenix and remarkable in Seattle,
                    and the archive knows which.

ENTRY (YES). Inside the entry window before peak, still climbing, and the band
that contains implied_max_c is tradeable and cheap. The pessimistic case has
to clear the band's floor too - a band the day only reaches on its best day is
not a trade, it is a hope.

ENTRY (NO). The mirror, and the half a level-only desk cannot see: rolling
over inside the window, sell the bands the day can no longer reach. The
running max is already banked, so those bands need a new maximum that the
slope says is not coming.

NOT S5. S5 waits for day_decided - the day provably over - and buys the band
holding the locked maximum. This fires BEFORE that, on the direction of
travel, and accepts the risk that comes with being early. They are allowed to
overlap; the conflict layer decides.

STALENESS IS FATAL HERE. A slope computed from a reading ninety minutes old
describes an hour that has already finished. max_reading_age_min is a hard
gate, not a preference.
"""
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_ENTRY_WINDOW_MIN = 60      # "an hour or less before peak"
DEFAULT_MAX_READING_AGE_MIN = 90   # older than this and the slope is history
DEFAULT_MIN_SLOPE = 0.10           # below instrument noise dressed up with a sign
DEFAULT_MAX_ENTRY_PRICE = 0.85
DEFAULT_MIN_PROFILE_DAYS = 20      # an unmeasured city gets no implied max


def _to_local(c, unit):
    """Bands are labelled in the city's own unit; the archive is Celsius."""
    if c is None:
        return None
    return c * 9.0 / 5.0 + 32.0 if unit == "F" else c


def _contains(band, value):
    if value is None:
        return False
    if band.open_low:
        return band.band_hi is not None and value < band.band_hi
    if band.open_high:
        return band.band_lo is not None and value >= band.band_lo
    if band.band_lo is None or band.band_hi is None:
        return False
    return band.band_lo <= value < band.band_hi


def _above(band, value):
    """Wholly above `value` - the bands a falling day can no longer reach."""
    if value is None or band.band_lo is None:
        return False
    return band.band_lo > value


class S7PrePeakGradient(Strategy):

    def entry_signals(self, ctx):
        x = self.config.extra
        window = x.get("entry_window_min", DEFAULT_ENTRY_WINDOW_MIN)
        max_age = x.get("max_reading_age_min", DEFAULT_MAX_READING_AGE_MIN)
        min_slope = x.get("min_slope_c_per_h", DEFAULT_MIN_SLOPE)
        max_price = x.get("max_entry_price", DEFAULT_MAX_ENTRY_PRICE)

        out = []
        for band in ctx.bands:
            if not self.applies_to(band):
                continue

            # ---- the window ------------------------------------------------
            mtp = band.minutes_to_peak
            if mtp is None or mtp <= 0 or mtp > window:
                continue

            # ---- the reading has to be current -----------------------------
            if band.reading_age_min is None or band.reading_age_min > max_age:
                continue
            if band.slope_3_c_per_h is None or band.latest_temp_c is None:
                continue

            # ---- the day is already over ----------------------------------
            # Nothing here applies once the maximum is banked; that is S5's.
            if band.day_decided:
                continue

            unit = band.unit
            implied = _to_local(band.implied_max_c, unit)
            implied_low = _to_local(band.implied_max_low_c, unit)
            running_max = _to_local(band.running_max_c, unit)

            # ---- still climbing: buy where the day is heading --------------
            if (not band.rolling_over
                    and band.slope_3_c_per_h > min_slope
                    and implied is not None
                    and _contains(band, implied)
                    and band.yes_tradeable
                    and band.yes_price is not None
                    and band.yes_price < max_price):
                # Even on a poor finish the day has to reach this band's floor.
                # Without this the strategy buys the band the day reaches only
                # on its best tenth of afternoons.
                reachable = (band.open_low
                             or band.band_lo is None
                             or (implied_low is not None and implied_low >= band.band_lo))
                if reachable:
                    out.append(Signal(
                        strategy_id=self.config.strategy_id, band_id=band.band_id, side="YES",
                        action="ENTER", reason="climbing_into_band_before_peak",
                        price_at_fire=band.yes_price, prob_at_fire=band.model_prob_yes,
                        edge_at_fire=band.yes_edge_net_pp, suggested_shares=0.0,
                        confidence=band.confidence, regime_label=band.regime_label,
                        severity="high",
                        dedupe_key=dedupe_key(self.config.strategy_id, band.band_id,
                                              "YES", "ENTER", "pre_peak_climb"),
                        payload={
                            "slope_3_c_per_h": band.slope_3_c_per_h,
                            "slope_6_c_per_h": band.slope_6_c_per_h,
                            "minutes_to_peak": mtp,
                            "latest_temp_c": band.latest_temp_c,
                            "implied_max_c": band.implied_max_c,
                            "implied_max_low_c": band.implied_max_low_c,
                            "typical_climb_left_c": band.typical_climb_left_c,
                            "reading_age_min": band.reading_age_min,
                        },
                    ))

            # ---- rolling over: sell what the day can no longer reach --------
            # The running max is banked. A band above it now needs a NEW
            # maximum, and the short slope says the afternoon is done making
            # them. Measured against the running max, not the latest reading:
            # the latest may already have fallen well below the day's high.
            if (band.rolling_over
                    and running_max is not None
                    and _above(band, running_max)
                    and band.no_tradeable
                    and band.no_price is not None
                    and band.no_price < max_price):
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=band.band_id, side="NO",
                    action="ENTER", reason="rolled_over_band_unreachable",
                    price_at_fire=band.no_price, prob_at_fire=band.model_prob_yes,
                    edge_at_fire=band.no_edge_net_pp, suggested_shares=0.0,
                    confidence=band.confidence, regime_label=band.regime_label,
                    severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, band.band_id,
                                          "NO", "ENTER", "pre_peak_roll"),
                    payload={
                        "slope_3_c_per_h": band.slope_3_c_per_h,
                        "slope_6_c_per_h": band.slope_6_c_per_h,
                        "minutes_to_peak": mtp,
                        "running_max_c": band.running_max_c,
                        "band_lo": band.band_lo,
                        "reading_age_min": band.reading_age_min,
                    },
                ))
        return out

    def exit_signals(self, ctx, open_positions):
        """Exit a YES the moment the day turns against it.

        This strategy's whole premise is direction of travel, so the exit is
        the same test failing: the position was taken on a climb, the climb has
        rolled over, and the band is now above the banked maximum. Holding to
        settlement would be a different strategy - one with no reason to have
        entered early in the first place.
        """
        by_band = {}
        for b in ctx.bands:
            by_band[b.band_id] = b

        out = []
        for pos in open_positions:
            if pos.get("strategy_id") != self.config.strategy_id:
                continue
            if (pos.get("side") or "").upper() != "YES":
                continue
            band = by_band.get(pos.get("band_id"))
            if band is None or not band.rolling_over:
                continue
            running_max = _to_local(band.running_max_c, band.unit)
            if not _above(band, running_max):
                continue
            out.append(Signal(
                strategy_id=self.config.strategy_id, band_id=band.band_id, side="YES",
                action="EXIT", reason="climb_rolled_over_band_now_unreachable",
                price_at_fire=band.yes_price, prob_at_fire=band.model_prob_yes,
                edge_at_fire=band.yes_edge_net_pp, suggested_shares=0.0,
                confidence=band.confidence, regime_label=band.regime_label,
                severity="critical",
                dedupe_key=dedupe_key(self.config.strategy_id, band.band_id,
                                      "YES", "EXIT", "rolled_over"),
                payload={"running_max_c": band.running_max_c,
                         "slope_3_c_per_h": band.slope_3_c_per_h},
            ))
        return out
