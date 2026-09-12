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

from common import rest, insert, get_cities
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
UNLIMITED_BUDGET = 1e12


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

def _upcoming_markets():
    return rest("v_canonical_markets", [
        ("select", "market_id,city_key,resolution_date"),
        ("resolution_date", f"gte.{dt.date.today().isoformat()}"),
    ])


def _bands_for_markets(market_ids):
    out = []
    chunk = 100
    for i in range(0, len(market_ids), chunk):
        batch = market_ids[i:i + chunk]
        rows = rest("v_canonical_bands", [
            ("select", "band_id,market_id,band_lo,band_hi,open_low,open_high,band_label,token_yes,token_no"),
            ("market_id", f"in.({','.join(str(m) for m in batch)})"),
        ])
        out.extend(rows)
    return out


def _latest_by_band(table, select, band_ids, order_col):
    latest = {}
    chunk = 100
    for i in range(0, len(band_ids), chunk):
        batch = band_ids[i:i + chunk]
        rows = rest(table, [
            ("select", select),
            ("band_id", f"in.({','.join(str(b) for b in batch)})"),
            ("order", f"band_id,{order_col}.desc"),
        ])
        for r in rows:
            bid = r["band_id"]
            if bid not in latest:
                latest[bid] = r
    return latest


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
        return
    band_ids = [b["band_id"] for b in bands]

    probs = _latest_by_band("band_probabilities",
                             "band_id,calibrated_prob,computed_at,confidence,regime_label,forecast_max_c,bias_applied_c,prob_id",
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

    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    out_rows = []
    n_anomalies = 0

    for b in bands:
        band_id = b["band_id"]
        prob_row = probs.get(band_id)
        book = books.get(band_id, {})
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

    if out_rows:
        insert("edges", out_rows)

    tradeable_n = sum(1 for r in out_rows if r["tradeable"])
    print(f"wrote {len(out_rows)} edge rows ({tradeable_n} tradeable, {n_anomalies} flagged anomalous)")


if __name__ == "__main__":
    main()
