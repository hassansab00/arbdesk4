"""
S9 - Ladder basket [CANDIDATE]. Side: YES. Class: basket.

THE ASK. "Several buckets with above-average return, or at least positive
return." S8 does exactly two buckets under a fixed 70c cap. This does the
general version: any contiguous run of 2..N buckets, chosen because the
arithmetic says it pays, not because it fits a price rule.

THE ARITHMETIC. Buy the same number of shares S in each of K buckets. Exactly
one bucket settles at $1.00, so:

    cost   = S x (sum of prices + fee per share)
    payout = S x $1.00, but only if the winner is one of your K
    P(win) = sum of the model probabilities of those K

    EV per dollar staked = (sum_prob - total_cost) / total_cost

That last line is the whole strategy. It is positive exactly when the model's
probability for the covered range exceeds what the range costs - and the size
of it is the expected return, which is what "above average" has to be measured
against. A 70c pair returning 43% and an 85c triple returning 18% are not
ranked by their price; they are ranked by that number.

WHY CONTIGUOUS. The outcome is a temperature. A contiguous run of buckets is a
coherent claim - "the day finishes between here and here". A basket with a hole
in it is two claims, and the hole is usually where the forecast points, because
the buckets either side of the forecast are the expensive ones. Enumerating
arbitrary subsets would also, at its limit, just buy every bucket with a
positive individual edge, which is S1 run K times and not a basket at all.

WHY A CEILING ON K. Buy the whole ladder and you have paid the overround to own
a certainty: total cost goes above $1.00 and the EV goes negative by exactly
the book's edge. The cap keeps the basket a claim about where the day lands
rather than an expensive way to be right.

FEES ARE NOT A ROUNDING ERROR HERE. Polymarket charges shares x 0.05 x p x
(1-p) PER LEG, and that curve peaks at 50c. A four-bucket basket of mid-priced
buckets carries roughly four times the per-leg fee of a two-bucket basket at
the extremes, and a flat percentage would rank the baskets in the wrong order.

RANKING. Every candidate window is scored and the BEST one fires - not the
first one that clears the floor. A strategy that fired on the first acceptable
window would systematically pick the narrowest, because narrow windows are
enumerated first and are cheapest.
"""
from strategies.base import Strategy, Signal, dedupe_key

DEFAULT_MAX_BUCKETS = 4
DEFAULT_MIN_EV_PER_DOLLAR = 0.08   # 8c expected on the dollar, after fees
DEFAULT_MIN_WIN_PROB = 0.55        # a basket that usually loses is not a basket
DEFAULT_MIN_LIQUIDITY_USD = 100.0
DEFAULT_MAX_TOTAL_COST = 0.92      # above this the upside cannot pay for the risk
FEE_RATE = 0.05


def leg_fee(p):
    """Polymarket's taker fee per share on one leg."""
    return FEE_RATE * p * (1.0 - p)


def basket_math(legs):
    """cost, fee, total, probability and EV per dollar for one candidate.

    Kept as a free function so the tests can hold it to the same numbers as
    web/lib/cover.ts without constructing a strategy.
    """
    cost = sum(b.yes_price for b in legs)
    fee = sum(leg_fee(b.yes_price) for b in legs)
    total = cost + fee
    prob = sum(b.model_prob_yes for b in legs)
    ev = (prob - total) / total if total > 0 else None
    return {"cost": cost, "fee": fee, "total": total, "prob": prob, "ev_per_dollar": ev}


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


def _touching(a, b):
    """b sits immediately above a on the ladder."""
    return (a.band_hi is not None and b.band_lo is not None
            and abs(a.band_hi - b.band_lo) < 1e-6)


class S9LadderBasket(Strategy):

    def entry_signals(self, ctx):
        x = self.config.extra
        max_k = int(x.get("max_buckets", DEFAULT_MAX_BUCKETS))
        min_ev = x.get("min_ev_per_dollar", DEFAULT_MIN_EV_PER_DOLLAR)
        min_win = x.get("min_win_prob", DEFAULT_MIN_WIN_PROB)
        min_liq = x.get("min_liquidity_usd", DEFAULT_MIN_LIQUIDITY_USD)
        max_total = x.get("max_total_cost", DEFAULT_MAX_TOTAL_COST)

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
                if b.model_prob_yes is not None and b.yes_price is not None
                and b.yes_tradeable and b.band_lo is not None
            ]
            if len(ladder) < 2:
                continue
            ladder.sort(key=lambda b: b.band_lo)

            unit = ladder[0].unit
            anchors = [
                ("implied_max", _to_local(ladder[0].implied_max_c, unit)),
                ("forecast", _to_local(ladder[0].forecast_max_c, unit)),
            ]

            best = None
            # Every contiguous window of 2..max_k. The ladder is ~11 buckets, so
            # this is a few dozen candidates - cheap, and exhaustive beats
            # clever here because the objective is not monotonic in width.
            for i in range(len(ladder)):
                for k in range(2, max_k + 1):
                    win = ladder[i:i + k]
                    if len(win) < k:
                        break
                    if any(not _touching(a, b) for a, b in zip(win, win[1:])):
                        break          # a gap: no wider window from i is contiguous either
                    if min(b.fillable_usd_5c_yes for b in win) < min_liq:
                        continue

                    m = basket_math(win)
                    if m["ev_per_dollar"] is None or m["total"] >= max_total:
                        continue
                    if m["prob"] < min_win or m["ev_per_dollar"] < min_ev:
                        continue

                    # The window has to contain where the day is actually going.
                    # Without this the highest-EV window is often the cheap tail,
                    # which is cheap because the day is not going there.
                    anchored = next(
                        (name for name, v in anchors
                         if v is not None and any(_contains(b, v) for b in win)),
                        None,
                    )
                    if anchored is None:
                        continue

                    cand = (m["ev_per_dollar"], win, m, anchored)
                    if best is None or cand[0] > best[0]:
                        best = cand

            if best is None:
                continue

            _, win, m, anchored = best
            bucket = "basket:" + ":".join(b.band_id for b in win)
            payload = {
                "band_ids": [b.band_id for b in win],
                "basket_group": bucket,
                "buckets": [b.band_label for b in win],
                "n_buckets": len(win),
                "cost": round(m["cost"], 4),
                "fee": round(m["fee"], 4),
                "total_cost": round(m["total"], 4),
                "win_prob": round(m["prob"], 4),
                "ev_per_dollar": round(m["ev_per_dollar"], 4),
                # what it pays if the day lands anywhere in the range
                "return_if_win_pct": round((1.0 - m["total"]) / m["total"] * 100.0, 2),
                "anchored_on": anchored,
                "range": [win[0].band_lo, win[-1].band_hi],
            }
            for leg in win:
                out.append(Signal(
                    strategy_id=self.config.strategy_id, band_id=leg.band_id, side="YES",
                    action="ENTER", reason="ladder_basket_positive_ev",
                    price_at_fire=leg.yes_price, prob_at_fire=leg.model_prob_yes,
                    edge_at_fire=leg.yes_edge_net_pp, suggested_shares=0.0,
                    confidence=min(b.confidence for b in win),
                    regime_label=leg.regime_label, severity="high",
                    dedupe_key=dedupe_key(self.config.strategy_id, leg.band_id,
                                          "YES", "ENTER", bucket),
                    payload=payload,
                ))
        return out

    def size(self, signal, portfolio):
        """Equal shares on every leg.

        Sized off the basket's TOTAL cost, not the leg's price. The default
        sizing would buy more shares of the cheaper buckets, which turns a claim
        about a range into a bet on its cheap end.
        """
        bankroll = portfolio.bankroll if portfolio else 0.0
        total = (signal.payload or {}).get("total_cost")
        if not bankroll or not total:
            return 0.0
        cap_usd = bankroll * (self.config.capital_cap_pct / 100.0)
        return cap_usd / total

    def exit_signals(self, ctx, open_positions):
        """Held to settlement.

        The basket's edge is the whole distribution of outcomes. Selling one leg
        when it drifts leaves a hole in the range - which is the one shape this
        strategy exists to avoid.
        """
        return []
