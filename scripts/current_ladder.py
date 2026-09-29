"""The current prediction between pricing runs (plan v2.3 P4.9).

The engine prices every open market six times a day into band_probabilities
(00:36, 04:36 ... 20:36Z); the hourly tick reads the stations. Between two
pricing runs a new station maximum can pass the bucket the card calls the
most likely, and P4.8 can only mark that pick "Out of date" until the next
run. Measured 29 Sep over the 72 tick hours to 21:36Z, with the card's rule on
each city's primary station series: 48 same-day city-days an hour, a pick
passed since its pricing 2.31 times an hour (4 at the 90th percentile, 8 at
most); 56 of 186 city-days had one.

What the tick does with this module, every hour:

  * reprice_targets(): the same-day city-days whose current pick (the newer of
    the newest pricing and the last ladder the tick published,
    v_current_prediction) the station has passed since it was priced - the
    card's rule (web/lib/cityCards.ts pickStanding) on the engine's floor
    bucket (probability_engine.floor_bucket, P3.1). Worst standing first, at
    most REPRICE_MAX. The tick prices them beside the due checkpoints, with
    the same process_city_day and the same floors; the measured cost is about
    0.3 s a priced city-day (71 ticks, 26-29 Sep).
  * ladder_row(): the row publish_current_ladders takes for a ladder the tick
    priced, at a due checkpoint or for a passed pick. That function keeps one
    per city-day, a whole ladder or nothing, the newest winning; nothing is
    appended to band_probabilities.
"""
import probability_engine as pe

# At most this many re-priced city-days a tick. Measured: at most 8 in an hour.
REPRICE_MAX = 12
# prediction_checkpoints' own tolerance (the engine rounds each band to six
# places; tick.SUM_TOLERANCE), and publish_current_ladders'.
SUM_TOLERANCE = 1e-4


def standing(floor_c, unit, bands, band_id):
    """Where a reading of floor_c (Celsius) leaves band_id, by the engine's
    floor bucket: 0 the reading has not passed it, 1 it is the bucket just
    below the reading's, 2 it is two or more below (it cannot win). None when
    there is no reading or no bucket holds it."""
    if floor_c is None:
        return None
    ladder, i = pe.floor_bucket(float(floor_c), unit, bands)
    if i is None:
        return None
    ids = [str(b["band_id"]) for b in ladder]
    if str(band_id) not in ids:
        return None
    p = ids.index(str(band_id))
    return 0 if p >= i else (1 if p == i - 1 else 2)


def reprice_targets(picks, floors, market_of, bands_by_market, unit_of, skip=()):
    """[(city_key, target_date)] to price again now, worst first.

    picks: v_current_prediction rows with is_top (one per open city-day).
    floors: {city_key: (local_date, floor_c)} - the tick's measured floors,
    the ones the new price will be made with. Only a city-day on the city's
    own local date qualifies, and only when the reading now stands worse
    against the pick than the floor that pick was priced with (a price made
    without a floor stood at 0)."""
    skip = {(c, str(t)) for c, t in skip}
    found = []
    for pick in picks:
        city, target = pick["city_key"], str(pick["target_date"])
        if (city, target) in skip:
            continue
        floor = floors.get(city)
        market = market_of.get((city, target))
        if not floor or str(floor[0]) != target or not market:
            continue
        bands = bands_by_market.get(market["market_id"]) or []
        unit = market.get("unit") or unit_of.get(city, "C")
        now = standing(floor[1], unit, bands, pick["band_id"])
        then = standing(pick.get("observed_floor_c"), unit, bands, pick["band_id"]) or 0
        if now is not None and now > then:
            found.append((now, city, target))
    found.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [(city, target) for _, city, target in found[:REPRICE_MAX]]


def ladder_row(city, target, market_id, rows, reasons, reason, checkpoint, version):
    """(the publish_current_ladders row, None) or (None, why)."""
    probs = {str(r["band_id"]): float(r["calibrated_prob"]) for r in rows
             if r.get("calibrated_prob") is not None}
    if not probs or len(probs) != len(rows):
        return None, "a band has no probability"
    total = sum(probs.values())
    if abs(total - 1) > SUM_TOLERANCE:
        return None, f"ladder sums to {total:.6f}"
    head = rows[0]
    label = next((r[len("priced_from:"):] for r in reasons or [] if r.startswith("priced_from:")), None)
    return {
        "city_key": city, "target_date": str(target), "market_id": str(market_id),
        # when this ladder was computed, as its band_probabilities row would say
        "priced_at": head.get("computed_at"),
        "reason": reason, "checkpoint": checkpoint, "engine_version": version,
        "priced_from": label,
        "centre_c": head.get("centre_c"), "sigma_c": head.get("sigma_c"),
        "observed_floor_c": head.get("observed_floor_c"),
        "ladder": probs,
    }, None
