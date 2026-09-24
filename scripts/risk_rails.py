"""The fixed risk rails (plan v2 P5.9, part 1). Never learned.

DECIDED 24 Sep: Hassan delegated the decision ("do what's the most optimal
option"); these are the plan's defaults. The database enforces them in
queue_plan (supabase/migrations/20260924110000_fixed_risk_rails.sql), so an
order that breaks one is refused whoever proposed it. This module hands the
same numbers to the solver so it plans inside them instead of being refused.

ONE SOURCE. settings 'risk_rails' holds the live values and both halves read
it; DEFAULTS here is only the fallback when the row cannot be read, and a
test holds it equal to the migration's seed.

Clusters (8% per cluster-day) are part 2: fitted weekly from forecast-error
correlations. Until then a city is its own cluster and the 3% city-day rail
is the one that binds.
"""
import sys

DEFAULTS = {
    "daily_loss_frac": 0.05,
    "city_day_frac": 0.03,
    "cluster_day_frac": 0.08,
    "max_price": 0.97,
    "close_buffer_min": 15,
}


def load(rest=None):
    """(rails, halted, halt_reason) from settings, falling back to DEFAULTS."""
    if rest is None:
        from common import rest as rest
    rails, halted, reason = dict(DEFAULTS), False, None
    try:
        rows = rest("settings", [("select", "key,value"), ("key", "in.(risk_rails,trading_halt)")])
    except Exception as e:
        print(f"  note: rails unreadable ({e}); using the defaults", file=sys.stderr)
        return rails, halted, reason
    for r in rows or []:
        v = r.get("value") or {}
        if r.get("key") == "risk_rails":
            rails.update({k: v[k] for k in DEFAULTS if v.get(k) is not None})
        elif r.get("key") == "trading_halt":
            halted, reason = bool(v.get("halted")), v.get("reason")
    return rails, halted, reason


def ladder_budget(rails, equity_usd, on_market_usd):
    """The most NEW spend one city-day may take, as a fraction of equity.

    The rail caps what the account holds plus reserves plus buys on the
    city-day at city_day_frac of equity; what is already on it comes off.
    """
    if equity_usd <= 0:
        return 0.0
    room = rails["city_day_frac"] * equity_usd - max(on_market_usd, 0.0)
    return max(room, 0.0) / equity_usd


def daily_loss_hit(rails, equity_usd, pnl_today_usd):
    """True once today's realised loss reaches daily_loss_frac of the day's starting equity."""
    start = equity_usd - pnl_today_usd
    return pnl_today_usd < 0 and -pnl_today_usd >= rails["daily_loss_frac"] * start
