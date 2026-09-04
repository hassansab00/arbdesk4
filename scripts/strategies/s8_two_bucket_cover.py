"""
S8 - Two-bucket cover [CANDIDATE]. Side: YES. Class: basket.

THE TRADE. Buy the two most likely buckets together, as long as the pair costs
less than a set fraction of the dollar - $0.70 by default. Exactly one bucket
settles Yes, so a pair costing $0.70 returns $1.00 if either lands: 43% on the
stake, and it lands whenever the day finishes anywhere in a two-bucket span.

WHY IT IS NOT FREE MONEY, AND WHAT THE GATES ARE FOR. Two buckets at 35c each
are cheap for a reason - usually because the market thinks the day lands
somewhere else. Cost alone is a trap, so the price test is only the first of
four:

  1 COST      the pair, INCLUDING FEES, under max_pair_cost. Polymarket's fee
              is shares x 0.05 x p x (1-p), which peaks near 50c - two 35c
              buckets carry meaningfully more fee than a 90c/5c pair, and
              ignoring that is how a 0.70 rule quietly becomes 0.72.
  2 EDGE      the pair's MODEL probability has to exceed what it costs. Paying
              70c for a pair the model makes 55% is a losing trade executed
              tidily.
  3 ADJACENCY the two buckets must be neighbours. Two non-adjacent buckets
              covering a bimodal guess is not a cover, it is two bets, and it
              leaves the gap between them - where the forecast actually
              points - uncovered.
  4 ANCHOR    one of the two must contain where the day is actually heading -
              the forecast, or the observation-implied maximum from
              sql/ad4_26. "The two most likely" is a statement about the
              model's distribution; this makes it a statement about the day.

Gate 4 is what the brief meant by "backed by temp and forecast analysis". Both
anchors are accepted and either satisfies it: the forecast is the better
estimate early in the day, the implied maximum is the better one an hour
before peak, and requiring both would silently switch the strategy off
whenever they disagreed - which is exactly when it is most interesting.

SIZING. Both legs are sized to the SAME number of shares, because the payout
is one bucket settling at $1.00 and an unbalanced pair turns a cover into a
directional bet with extra steps. The two signals share a dedupe bucket so the
conflict layer treats them as one position.
"""
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_MAX_PAIR_COST = 0.70
DEFAULT_MIN_PAIR_PROB = 0.72      # must beat the cost, with room for the fee
DEFAULT_MIN_LIQUIDITY_USD = 100.0
FEE_RATE = 0.05                   # Polymarket taker fee coefficient


def pair_fee(p_a, p_b):
    """Fee per share on the pair. shares x 0.05 x p x (1-p), per leg.

    Not a flat percentage: the real curve peaks at 1.25% near 50c and falls to
    nothing at both extremes, so two mid-priced buckets cost far more to enter
    than a cheap/expensive pair at the same total.
    """
    return (FEE_RATE * p_a * (1.0 - p_a)) + (FEE_RATE * p_b * (1.0 - p_b))


def _to_local(c, unit):
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


def _adjacent(a, b):
    """Neighbours on the ladder. Open-ended buckets touch the bucket beside
    them, which the numeric test alone would miss."""
    if a.band_hi is not None and b.band_lo is not None and abs(a.band_hi - b.band_lo) < 1e-6:
        return True
    if b.band_hi is not None and a.band_lo is not None and abs(b.band_hi - a.band_lo) < 1e-6:
        return True
    return False


class S8TwoBucketCover(Strategy):

    def entry_signals(self, ctx):
        x = self.config.extra
        max_cost = x.get("max_pair_cost", DEFAULT_MAX_PAIR_COST)
        min_prob = x.get("min_pair_prob", DEFAULT_MIN_PAIR_PROB)
        min_liq = x.get("min_liquidity_usd", DEFAULT_MIN_LIQUIDITY_USD)

        seen = set()
        out = []
        for band in ctx.bands:
            if not self.applies_to(band):
                continue
            key = (band.city_key, band.resolution_date)
            if key in seen:
                continue
            seen.add(key)

            ladder = [
                b for b in ctx.bands_for_city_day(band.city_key, band.resolution_date)
                if b.model_prob_yes is not None and b.yes_price is not None and b.yes_tradeable
            ]
            if len(ladder) < 2:
                continue

            ladder.sort(key=lambda b: b.model_prob_yes, reverse=True)
            a, b2 = ladder[0], ladder[1]

            # 3 - neighbours, or it is two bets rather than a cover
            if not _adjacent(a, b2):
                continue

            cost = a.yes_price + b2.yes_price
            fee = pair_fee(a.yes_price, b2.yes_price)
            total = cost + fee
            if total >= max_cost:
                continue

            prob = a.model_prob_yes + b2.model_prob_yes
            if prob < min_prob or prob <= total:
                continue

            if min(a.fillable_usd_5c_yes, b2.fillable_usd_5c_yes) < min_liq:
                continue

            # 4 - one of the two has to contain where the day is going
            unit = a.unit
            anchors = [
                ("implied_max", _to_local(a.implied_max_c, unit)),
                ("forecast", _to_local(a.forecast_max_c, unit)),
            ]
            anchored = next(
                (name for name, v in anchors
                 if v is not None and (_contains(a, v) or _contains(b2, v))),
                None,
            )
            if anchored is None:
                continue

            bucket = f"pair:{a.band_id}:{b2.band_id}"
            payload = {
                "pair": [a.band_label, b2.band_label],
                "pair_cost": round(cost, 4),
                "pair_fee": round(fee, 4),
                "pair_cost_with_fee": round(total, 4),
                "pair_model_prob": round(prob, 4),
                # What the trade actually returns if either bucket lands.
                "return_pct": round((1.0 - total) / total * 100.0, 2),
                "anchored_on": anchored,
                "implied_max_c": a.implied_max_c,
            }
            for leg in (a, b2):
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=leg.band_id, side="YES",
                    action="ENTER", reason="two_bucket_cover_under_cost_cap",
                    price_at_fire=leg.yes_price, prob_at_fire=leg.model_prob_yes,
                    edge_at_fire=leg.yes_edge_net_pp, suggested_shares=0.0,
                    confidence=min(a.confidence, b2.confidence),
                    regime_label=leg.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, leg.band_id,
                                          "YES", "ENTER", bucket),
                    payload=payload,
                ))
        return out

    def size(self, signal, portfolio):
        """Equal shares on both legs.

        The default sizing is cap_usd / price, which buys MORE shares of the
        cheaper leg - and a cover with unequal legs pays out unevenly, which
        is a directional bet wearing a cover's name. Sizing off the pair's
        total cost gives both legs the same share count.
        """
        bankroll = portfolio.bankroll if portfolio else 0.0
        total = (signal.payload or {}).get("pair_cost_with_fee")
        if not bankroll or not total:
            return 0.0
        cap_usd = bankroll * (self.config.capital_cap_pct / 100.0)
        return cap_usd / total

    def exit_signals(self, ctx, open_positions):
        """Held to settlement by design.

        The cover's edge is the whole distribution of outcomes, not a path
        through it. Selling one leg when it drifts leaves the other naked,
        which is the opposite of what was bought.
        """
        return []
