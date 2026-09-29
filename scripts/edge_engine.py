"""
AD4 edge layer (Task 6). Run after the probability engine.

For every band, both sides: walk the live book to find what is ACTUALLY
executable (never top-of-book), net it against the full cost model, apply
tradeability + anomaly rules, and write to `edges`.

BOOK SOURCE: reads `v_latest_book`, one order book per band, representing
the YES token. Its `bid_levels`/`ask_levels` are normalised
{"price","size"} ladders, best-first - the view derives them from whichever
shape the underlying `book_snapshots` actually has (raw_book, jsonb levels,
or the cumulative *_usd_*c depth tiers). Do NOT read `book_snapshots`
directly: those two columns are integer LEVEL COUNTS there. Polymarket weather bands trade YES and NO as
separate CTF tokens with their own books in principle, but nothing in the
spec names a second per-band book column, so the NO side is derived as the
complement of the YES book (NO ask at price q <=> YES bid at price 1-q).
If a live schema check finds a genuine separate NO-token book, swap
`levels_for_side` for a direct read - the rest of this module is unaffected.
"""
import datetime as dt
import sys
import math
from collections import defaultdict

from common import rest, rest_all, insert, get_cities, log_run, city_local_date
import cost_model
import market_state

# PROVISIONAL - claude_invented, no evidential basis. The size used purely
# to derive a representative "executable price" for edge_pp. Real order
# sizing happens later, in the calculator (Task 13b) and paper engine
# (Task 9), against the caller's actual budget - this is just the
# reference point stored on `edges` for ranking/display.
REFERENCE_SIZE_USD = 100.0
DEFAULT_MAX_SLIPPAGE_C = 0.05          # settings.max_slippage_cents fallback
DEFAULT_MIN_PRICE_YES = 0.07           # settings.tradeability_yes fallback
DEFAULT_MAX_BANDS_FROM_CENTRE = 4      # settings.tradeability_yes fallback
DEFAULT_IMPLAUSIBLE_EDGE = 0.40        # anomaly_rules.implausible_edge fallback

# THE IMPLAUSIBILITY RULE ABOVE IS A MAGNITUDE RULE, AND THAT IS NOT ENOUGH.
# It blocks any edge over 40 points whatever produced it, so an edge of 25
# points built out of a model probability of exactly 1.0 passes - the number
# is small, the claim behind it is not. See probability_engine.PROB_FLOOR for
# how a far-out band became a certainty in the first place, and for the 23
# such edges a day that reached the desk tradeable with no block reason.
#
# Matching the producer's floor rather than picking a threshold here: a
# probability that has arrived at the boundary is the model out of resolution,
# not the model confident. Bands it genuinely has an opinion about - 0.95,
# 0.99, 0.999 - are untouched, which is why this is not a confidence gate.
PROB_FLOOR = 1e-6


def prob_is_at_floor(model_prob):
    """True when a probability has reached the resolution the column stores.

    Both ends: the NO side of a floored band is `1.0 - p`, so the certainty
    shows up as a ceiling, and that is the side the 23 tradeable ones were on.
    """
    if model_prob is None:
        return False
    return model_prob <= PROB_FLOOR or model_prob >= 1.0 - PROB_FLOOR
UNLIMITED_BUDGET = 1e12
MAX_BOOK_AGE = dt.timedelta(hours=2)
MAX_PROBABILITY_AGE = dt.timedelta(hours=12)


# --------------------------------------------------------------------------
# Pure math - independently unit-testable, no network.
# --------------------------------------------------------------------------

def _ladder(value):
    """Coerce a book side into a list of {"price","size"} dicts.

    Returns [] for anything that is not a usable ladder. That matters because
    book_snapshots.bid_levels / ask_levels are INTEGER level counts on the
    real schema - reading them straight killed the whole Probability + Edge
    run with "'int' object is not iterable". A band with no usable book
    should be skipped, not take the other 5,862 down with it.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for lvl in value:
        if not isinstance(lvl, dict):
            continue
        price, size = lvl.get("price"), lvl.get("size")
        if price is None or size is None:
            continue
        try:
            p, q = float(price), float(size)
            if math.isfinite(p) and math.isfinite(q) and 0 < p < 1 and q > 0:
                out.append({"price": p, "size": q})
        except (TypeError, ValueError):
            continue
    return out


def levels_for_side(snapshot, side):
    """Best-first {"price","size"} levels for the given side of one band."""
    raw = snapshot.get("raw_book") or {}
    asks = (_ladder(snapshot.get("ask_levels")) or _ladder(snapshot.get("asks"))
            or _ladder(raw.get("asks")))
    bids = (_ladder(snapshot.get("bid_levels")) or _ladder(snapshot.get("bids"))
            or _ladder(raw.get("bids")))
    if side == "YES":
        return sorted(asks, key=lambda l: l["price"])
    derived = [{"price": 1.0 - lvl["price"], "size": lvl["size"]} for lvl in bids]
    return sorted(derived, key=lambda l: l["price"])


def quoted_price_for_side(best_bid, best_ask, side):
    if side == "YES":
        return best_ask
    if best_bid is None:
        return None
    return 1.0 - best_bid


def compute_edge(model_prob, levels, quoted_price, max_slippage,
                  reference_usd=REFERENCE_SIZE_USD):
    """Depth-weighted executable price, net edge, and fillable depth curve."""
    ref = cost_model.walk_ladder(levels, usd_budget=reference_usd, max_slippage=max_slippage) \
        if levels else dict(shares=0.0, usd_spent=0.0, avg_price=None, quoted_price=quoted_price,
                             slippage=None, fully_filled=False, levels_consumed=0)

    executable_price = ref["avg_price"] if ref["avg_price"] is not None else quoted_price
    shares = ref["shares"]

    edge_pp = (model_prob - executable_price) if (model_prob is not None and executable_price is not None) else None
    est_fee = cost_model.taker_fee(shares, executable_price) if shares > 0 and executable_price is not None else 0.0
    fee_per_share = (est_fee / shares) if shares > 0 else est_fee
    # NOTE: executable_price is already the depth-weighted (slippage-inclusive)
    # average fill price, so edge_pp already nets slippage vs. quoted_price.
    # est_slippage is reported for diagnostics/audit, not subtracted again.
    edge_net_pp = (edge_pp - fee_per_share) if edge_pp is not None else None
    edge_per_dollar = (edge_net_pp / executable_price) if (edge_net_pp is not None and executable_price) else None

    fillable = {}
    for label, cap in (("2c", 0.02), ("5c", 0.05), ("10c", 0.10)):
        r = cost_model.walk_ladder(levels, usd_budget=UNLIMITED_BUDGET, max_slippage=cap) if levels \
            else dict(usd_spent=0.0)
        fillable[label] = r.get("usd_spent", 0.0)

    return dict(
        executable_price=executable_price, quoted_price=quoted_price, shares=shares,
        edge_pp=edge_pp, edge_net_pp=edge_net_pp, edge_per_dollar=edge_per_dollar,
        est_fee=est_fee, est_slippage=ref.get("slippage"),
        fillable_usd_2c=fillable["2c"], fillable_usd_5c=fillable["5c"], fillable_usd_10c=fillable["10c"],
    )


def band_distance_in_bands(centre_local, band_lo, band_hi, open_low, open_high):
    """How many band-widths this band's midpoint sits from the forecast centre."""
    if open_low or open_high or band_lo is None or band_hi is None:
        return 0.0
    width = band_hi - band_lo
    if not width:
        return 0.0
    mid = (band_lo + band_hi) / 2.0
    return abs(mid - centre_local) / width


def classify_tradeability(side, executable_price, distance_bands, mstate,
                           min_price_yes=DEFAULT_MIN_PRICE_YES,
                           max_bands_from_centre=DEFAULT_MAX_BANDS_FROM_CENTRE):
    if mstate == "NO_BOOK":
        return False, "no_book"
    if mstate in ("DEAD_LOSER", "DEAD_WINNER"):
        return False, "dead_band"
    if side == "YES":
        if executable_price is None or executable_price < min_price_yes:
            return False, "below_7c_yes"
        if distance_bands > max_bands_from_centre:
            return False, "far_from_forecast"
    return True, None


def opportunity_score(edge_net_pp, confidence, fillable_usd_5c):
    """score = edge_net_pp x confidence x log(1 + fillable_usd_5c).
    A large edge on an unfillable book must rank below a modest edge with
    real depth - this is the whole reason fillable_usd exists."""
    import math
    if edge_net_pp is None or confidence is None:
        return None
    return edge_net_pp * confidence * math.log(1.0 + max(0.0, fillable_usd_5c or 0.0))


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def floor_impossible_ids(markets, bands, floors, unit_of):
    """Band ids the day has already passed, per P3.1 (plan v2 P5.0 item 5).

    A market is judged only on its city's LOCAL today - the date the measured
    floor belongs to. floors is probability_engine._observed_floors(): a
    station maximum, never a model value (measured_floor refuses those), so a
    model reading can never make a band impossible here. Returns the ids that
    impossible_band_ids marks: two or more buckets under the venue-read floor,
    which not even a one-bucket station disagreement can reach.
    """
    import probability_engine as pe
    by_market = {}
    for b in bands:
        by_market.setdefault(b["market_id"], []).append(b)
    out = set()
    for m in markets:
        floor = floors.get(m["city_key"])
        if not floor or str(m.get("resolution_date")) != floor[0]:
            continue
        ladder = by_market.get(m["market_id"]) or []
        out |= pe.impossible_band_ids(floor[1], unit_of.get(m["city_key"], "C"), ladder)
    return out


# ---------------------------------------------------------------------------
# AGAINST THE MARKET (Hassan, 24 Sep: "never favour losing bets").
#
# Measured on settled day-ahead calls (v_city_hit_history, 13-23 Sep, head to
# head): when the engine's favourite bucket and the market's differed, the
# market's won C 62 of 155 and F 18 of 41; the engine's won C 34 and F 8. And
# on v_verified_fact_band_outcome (12-23 Sep) every size band of bets where
# the engine priced a bucket above the market lost money. So a trade that
# backs the engine against the market's favourite - YES on a bucket the market
# does not favour, NO on the one it does - is blocked while the engine loses
# those disagreements.
#
# Rule 11: the prior is ON (blocked). It lifts per unit only on evidence: at
# least AGAINST_MARKET_MIN_DAYS disagreement days in the last 30, with the
# lower 90% bound of (engine wins - market wins) per day above zero. The gate
# is recomputed every run from settled days only, never from the day it prices,
# and its inputs are written to ingest_log with each run.
# ---------------------------------------------------------------------------
AGAINST_MARKET_MIN_DAYS = 30
AGAINST_MARKET_WINDOW_DAYS = 30


def against_market_gate(rows):
    """{unit: {"on": bool, "days": n, "engine_won": a, "market_won": b, "lo90": x}}
    from v_city_hit_history rows (unit, model_call, market_call, model_hit,
    market_hit, head_to_head). A unit with no record keeps the prior: on."""
    by_unit = {}
    for r in rows:
        if not r.get("head_to_head") or not r.get("model_call") or not r.get("market_call"):
            continue
        if r["model_call"] == r["market_call"]:
            continue
        u = "F" if r.get("unit") == "F" else "C"
        # +1 engine right, -1 market right, 0 neither
        by_unit.setdefault(u, []).append((1 if r.get("model_hit") else 0) - (1 if r.get("market_hit") else 0))
    out = {}
    for u in ("C", "F"):
        d = by_unit.get(u, [])
        n = len(d)
        mean = sum(d) / n if n else 0.0
        var = (sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else 0.0
        lo90 = mean - 1.645 * math.sqrt(var / n) if n > 1 else None
        proven = n >= AGAINST_MARKET_MIN_DAYS and lo90 is not None and lo90 > 0
        out[u] = {"on": not proven, "days": n,
                  "engine_won": sum(1 for x in d if x > 0), "market_won": sum(1 for x in d if x < 0),
                  "lo90": None if lo90 is None else round(lo90, 4)}
    return out


def against_market_rows(rows):
    """Indexes (into `rows`) of trades that back the engine against the market's
    favourite, for ONE market's edge rows. Empty when the two agree, or when
    either favourite cannot be named. model_prob is the side's own probability,
    so the YES rows carry the engine's view and the YES prices the market's."""
    yes = [(i, r) for i, r in enumerate(rows) if r["side"] == "YES"]
    priced = [(i, r) for i, r in yes if r.get("model_prob") is not None]
    quoted = [(i, r) for i, r in yes if r.get("market_price") is not None]
    if not priced or not quoted:
        return set()
    engine_fav = max(priced, key=lambda x: x[1]["model_prob"])[1]["band_id"]
    market_fav = max(quoted, key=lambda x: x[1]["market_price"])[1]["band_id"]
    if engine_fav == market_fav:
        return set()
    return {i for i, r in enumerate(rows)
            if (r["side"] == "YES" and r["band_id"] != market_fav)
            or (r["side"] == "NO" and r["band_id"] == market_fav)}


def _against_market_record():
    since = (dt.date.today() - dt.timedelta(days=AGAINST_MARKET_WINDOW_DAYS)).isoformat()
    try:
        return rest_all("v_city_hit_history", {
            "select": "unit,model_call,market_call,model_hit,market_hit,head_to_head",
            "for_date": f"gte.{since}"}, order="city_key.asc,for_date.asc")
    except Exception as e:
        print(f"  ! settled record unavailable ({str(e)[:120]}) - the against-market gate keeps its prior (on)",
              file=sys.stderr)
        return []


def _upcoming_markets():
    # THE CITY'S DAY, NOT THE RUNNER'S (plan v2 P3.2). v_priceable_markets is
    # an active city's market that is not closed and whose local day has not
    # ended. The old filter was closed = false and resolution_date >= the UTC
    # date: over 7 days to 23 Sep it priced 5,401 rows after a local day had
    # ended and skipped 310 market-runs of western cities still mid-day.
    #
    # CLOSED MARKETS ARE STILL NOT UPCOMING ONES. Pricing them (18 of 51 of
    # 21 Sep's markets, 198 bands) cost rows no strategy could read and made
    # `stale_book` read like a broken snapshot job; the view keeps that rule.
    return rest("v_priceable_markets", [
        ("select", "market_id,city_key,resolution_date"),
    ])


def _bands_for_markets(market_ids):
    out = []
    chunk = 100
    for i in range(0, len(market_ids), chunk):
        batch = market_ids[i:i + chunk]
        rows = rest_all("v_canonical_bands", [
            ("select", "band_id,market_id,band_lo,band_hi,open_low,open_high,band_label,token_yes,token_no"),
            ("market_id", f"in.({','.join(str(m) for m in batch)})"),
        ], order="band_id", page_size=500)
        out.extend(rows)
    return out


def _latest_by_band(table, select, band_ids, order_col):
    latest = {}
    chunk = 100
    for i in range(0, len(band_ids), chunk):
        batch = band_ids[i:i + chunk]
        rows = rest_all(table, [
            ("select", select),
            ("band_id", f"in.({','.join(str(b) for b in batch)})"),
        ], order=f"band_id,{order_col}.desc", page_size=500)
        for r in rows:
            bid = r["band_id"]
            if bid not in latest:
                latest[bid] = r
    return latest


def _age_exceeds(value, maximum, now=None):
    """True when a PostgREST timestamp is absent, invalid, future, or stale."""
    if not value:
        return True
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        current = now or dt.datetime.now(dt.timezone.utc)
        age = current - parsed.astimezone(dt.timezone.utc)
        return age < dt.timedelta(minutes=-5) or age > maximum
    except (TypeError, ValueError):
        return True


def _settings():
    try:
        rows = rest("settings", {"select": "key,value"})
        return {r["key"]: r["value"] for r in rows}
    except Exception as e:
        print(f"  ! settings read failed, using defaults: {e}", file=sys.stderr)
        return {}


def _implausible_edge_threshold():
    try:
        rows = rest("anomaly_rules", [("rule_id", "eq.implausible_edge"), ("select", "threshold")])
        if rows and rows[0].get("threshold") is not None:
            return float(rows[0]["threshold"])
    except Exception as e:
        print(f"  ! anomaly_rules read failed, using default: {e}", file=sys.stderr)
    return DEFAULT_IMPLAUSIBLE_EDGE


def _log_anomaly(band_id, side, edge_net_pp, detail):
    try:
        insert("anomalies", [{
            "detected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "band_id": band_id, "kind": "implausible_edge", "side": side,
            "value": edge_net_pp, "detail": detail,
        }])
    except Exception as e:
        print(f"  ! could not log anomaly (schema mismatch?): {e}", file=sys.stderr)


def main():
    cities = get_cities(require_coords=False)
    unit_of = {c["city_key"]: (c.get("unit") or "C") for c in cities}

    markets = _upcoming_markets()
    market_city = {m["market_id"]: m["city_key"] for m in markets}
    market_ids = list(market_city.keys())

    bands = _bands_for_markets(market_ids)
    if not bands:
        print("no bands for upcoming markets - nothing to price")
        # A run with nothing to price still says so: v_run_arrivals reads a
        # dispatched run with no edge_engine row as one that never ran.
        log_run("edge_engine", "ok", 0, {"bands": 0, "markets": len(markets),
                                         "note": "no bands for upcoming markets"})
        return
    band_market = {b["band_id"]: b["market_id"] for b in bands}
    band_ids = [b["band_id"] for b in bands]

    probs = _latest_by_band("v_latest_prob",
                             "band_id,calibrated_prob,computed_at,confidence,regime_label,forecast_max_c,bias_applied_c,prob_id,pricing_eligible,pricing_block_reason,skill_source",
                             band_ids, "computed_at")
    # v_latest_book, NOT book_snapshots. On the real schema
    # book_snapshots.bid_levels / ask_levels are integer LEVEL COUNTS - the
    # ladder lives in raw_book, with pre-aggregated depth in the *_usd_*c
    # columns. sql/ad4_13_reconcile.sql rebuilds v_latest_book to expose
    # normalised {"price","size"} ladders under those same two names, which is
    # why this needs no other change. Reading the table directly is what threw
    #     TypeError: 'int' object is not iterable
    books = _latest_by_band("v_latest_book", "*", band_ids, "observed_at")

    settings = _settings()
    max_slippage_c = (settings.get("max_slippage_cents") or {}).get("value", 5) / 100.0
    tradeability_yes = settings.get("tradeability_yes") or {}
    min_price_yes = tradeability_yes.get("min_price", DEFAULT_MIN_PRICE_YES)
    max_bands_from_centre = tradeability_yes.get("max_bands_from_centre", DEFAULT_MAX_BANDS_FROM_CENTRE)
    implausible_edge_threshold = _implausible_edge_threshold()

    # THE HARD FLOOR GATE (plan v2 P5.0 item 5). The engine prices a band the
    # day has passed at the probability floor, which prob_is_at_floor already
    # blocks - but the measurement layer (P3.1) can leave such a band a small
    # non-floor probability, and nothing here stopped a YES on it. A band
    # impossible_band_ids marks is never a YES, whatever its price says.
    try:
        import probability_engine as pe
        impossible = floor_impossible_ids(markets, bands, pe._observed_floors(), unit_of)
    except Exception as e:
        print(f"  ! floor gate unavailable ({str(e)[:120]}) - YES blocked on every market "
              f"resolving today", file=sys.stderr)
        impossible = None
    # Without floors, fail closed only where a floor could matter: markets
    # settling on their own city's local today.
    tz_of = {c["city_key"]: c.get("timezone") for c in cities}
    today_markets = {m["market_id"] for m in markets
                     if str(m.get("resolution_date")) == city_local_date(
                         dt.datetime.now(dt.timezone.utc).isoformat(), tz_of.get(m["city_key"]))}

    now = dt.datetime.now(dt.timezone.utc)
    computed_at = now.isoformat()
    out_rows = []
    n_anomalies = 0
    stale_books = 0
    missing_books = 0
    stale_probabilities = 0
    blocked_probabilities = 0

    for b in bands:
        band_id = b["band_id"]
        prob_row = probs.get(band_id)
        book = books.get(band_id, {})
        book_is_stale = _age_exceeds(book.get("observed_at"), MAX_BOOK_AGE, now) if book else True
        probability_is_stale = _age_exceeds(
            prob_row.get("computed_at"), MAX_PROBABILITY_AGE, now) if prob_row else True
        probability_is_eligible = bool(prob_row and prob_row.get("pricing_eligible", True))
        if not book:
            missing_books += 1
        elif book_is_stale:
            stale_books += 1
        if prob_row and probability_is_stale:
            stale_probabilities += 1
        if prob_row and not probability_is_eligible:
            blocked_probabilities += 1
        best_bid, best_ask = book.get("best_bid"), book.get("best_ask")
        mstate = book.get("market_state") or market_state.classify(best_bid, best_ask)

        city_key = market_city.get(b["market_id"])
        unit = unit_of.get(city_key, "C")

        centre_local = None
        confidence = None
        regime_label = None
        prob_id = None
        if prob_row:
            confidence = prob_row.get("confidence")
            regime_label = prob_row.get("regime_label")
            prob_id = prob_row.get("prob_id")
            fc, bias = prob_row.get("forecast_max_c"), prob_row.get("bias_applied_c") or 0.0
            if fc is not None:
                centre_c = fc - bias
                centre_local = centre_c * 9.0 / 5.0 + 32.0 if unit == "F" else centre_c

        distance_bands = band_distance_in_bands(
            centre_local if centre_local is not None else 0.0,
            b.get("band_lo"), b.get("band_hi"), b.get("open_low"), b.get("open_high"),
        ) if centre_local is not None else 0.0

        for side in ("YES", "NO"):
            model_prob = None
            if prob_row and prob_row.get("calibrated_prob") is not None:
                model_prob = prob_row["calibrated_prob"] if side == "YES" else 1.0 - prob_row["calibrated_prob"]

            levels = levels_for_side(book, side) if book else []
            quoted = quoted_price_for_side(best_bid, best_ask, side)
            edge = compute_edge(model_prob, levels, quoted, max_slippage_c)

            tradeable, block_reason = classify_tradeability(
                side, edge["executable_price"], distance_bands, mstate,
                min_price_yes=min_price_yes, max_bands_from_centre=max_bands_from_centre,
            )

            if edge["edge_net_pp"] is not None and abs(edge["edge_net_pp"]) > implausible_edge_threshold:
                tradeable = False
                block_reason = "anomaly"
                n_anomalies += 1
                _log_anomaly(band_id, side, edge["edge_net_pp"],
                             {"model_prob": model_prob, "executable_price": edge["executable_price"]})

            if model_prob is None:
                tradeable = False
                block_reason = block_reason or "stale_data"
            elif prob_is_at_floor(model_prob):
                # Checked before staleness because this is a statement about
                # the value itself, not about its age: a fresh certainty is
                # exactly as untradeable as a stale one.
                tradeable = False
                block_reason = "prob_at_floor"
            elif probability_is_stale:
                tradeable = False
                block_reason = "stale_probability"
            elif not probability_is_eligible:
                tradeable = False
                block_reason = prob_row.get("pricing_block_reason") or "unmeasured_probability"
            if book_is_stale:
                tradeable = False
                block_reason = "no_book" if not book else "stale_book"
            if side == "YES" and (band_id in impossible if impossible is not None
                                  else b["market_id"] in today_markets):
                # Last, so it is the reason shown: of everything that can block
                # this row it is the one that never lifts.
                tradeable = False
                block_reason = "floor_impossible" if impossible is not None else "floor_unknown"

            out_rows.append({
                "band_id": band_id, "computed_at": computed_at, "side": side,
                "model_prob": model_prob, "market_price": edge["executable_price"],
                "quoted_price": edge["quoted_price"], "edge_pp": edge["edge_pp"],
                "edge_net_pp": edge["edge_net_pp"], "edge_per_dollar": edge["edge_per_dollar"],
                "fillable_usd_2c": edge["fillable_usd_2c"], "fillable_usd_5c": edge["fillable_usd_5c"],
                "fillable_usd_10c": edge["fillable_usd_10c"], "est_fee": edge["est_fee"],
                "est_slippage": edge["est_slippage"], "book_snapshot_id": book.get("snapshot_id"),
                "prob_id": prob_id, "confidence": confidence, "regime_label": regime_label,
                "tradeable": tradeable, "block_reason": block_reason,
            })

    # THE AGAINST-MARKET GATE, per market, after every row of it is priced.
    gate = against_market_gate(_against_market_record())
    by_market = {}
    for i, r in enumerate(out_rows):
        by_market.setdefault(band_market[r["band_id"]], []).append(i)
    against_blocked = 0
    for market_id, idxs in by_market.items():
        unit = unit_of.get(market_city.get(market_id), "C")
        if not gate.get(unit, {"on": True})["on"]:
            continue
        rows = [out_rows[i] for i in idxs]
        for j in against_market_rows(rows):
            r = rows[j]
            if r["tradeable"]:
                r["tradeable"] = False
                r["block_reason"] = "against_market"
                against_blocked += 1

    if out_rows:
        insert("edges", out_rows)

    tradeable_n = sum(1 for r in out_rows if r["tradeable"])
    attention = missing_books + stale_books + stale_probabilities + blocked_probabilities
    log_run(
        "edge_engine", "attention" if attention else "ok", len(out_rows),
        {"bands": len(bands), "tradeable": tradeable_n,
         "missing_books": missing_books, "stale_books": stale_books,
         "stale_probabilities": stale_probabilities,
         "blocked_probabilities": blocked_probabilities,
         "against_market_blocked": against_blocked, "against_market_gate": gate},
    )
    print(f"wrote {len(out_rows)} edge rows ({tradeable_n} tradeable, {n_anomalies} flagged anomalous)")


if __name__ == "__main__":
    main()
