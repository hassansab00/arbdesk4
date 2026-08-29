"""
Conflict rules [HASSAN] (Task 8). Multiple strategies may hold the same
city-day.

| Situation                      | Rule                                                          |
|---------------------------------|----------------------------------------------------------------|
| Same band, same side            | Allowed. One combined position, attribution split proportionally |
| Same band, opposite sides       | BLOCKED. Pays spread both directions for zero net exposure. Logged to strategy_conflicts |
| Different bands, same city      | Allowed. Exposure aggregates against the city-day limit        |
"""
from collections import defaultdict


def resolve_conflicts(new_signals, open_positions):
    """
    Partitions new ENTER signals into (allowed, blocked), and returns the
    strategy_conflicts rows to log for anything blocked. EXIT signals and
    non-ENTER actions always pass through unblocked - conflicts are an
    entry-time concept.
    """
    side_by_band = defaultdict(set)
    holder_by_band_side = {}
    for pos in open_positions:
        side_by_band[pos["band_id"]].add(pos["side"])
        holder_by_band_side.setdefault((pos["band_id"], pos["side"]), pos.get("strategy_id"))

    allowed, blocked, conflict_rows = [], [], []
    for sig in new_signals:
        if sig.action != "ENTER":
            allowed.append(sig)
            continue

        opposite = "NO" if sig.side == "YES" else "YES"
        if opposite in side_by_band[sig.band_id]:
            other_strategy = holder_by_band_side.get((sig.band_id, opposite))
            blocked.append(sig)
            conflict_rows.append({
                "band_id": sig.band_id, "strategy_a": sig.strategy_id,
                "strategy_b": other_strategy, "kind": "opposite_side_same_band",
                "resolution": "blocked",
            })
            continue

        allowed.append(sig)
        side_by_band[sig.band_id].add(sig.side)
        holder_by_band_side.setdefault((sig.band_id, sig.side), sig.strategy_id)

    return allowed, blocked, conflict_rows


def city_day_exposure(open_positions):
    """
    Different bands, same city: allowed, but exposure aggregates against
    the city-day limit. Returns {(city_key, resolution_date): total_usd}.
    """
    out = defaultdict(float)
    for pos in open_positions:
        key = (pos.get("city_key"), pos.get("resolution_date"))
        out[key] += pos.get("shares", 0.0) * pos.get("avg_fill_price", 0.0)
    return dict(out)
