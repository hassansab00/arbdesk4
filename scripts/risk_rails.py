"""The fixed risk rails (plan v2 P5.9, part 1). Never learned.

DECIDED 24 Sep: Hassan delegated the decision ("do what's the most optimal
option"); these are the plan's defaults. The database enforces them in
queue_plan (supabase/migrations/20260924110000_fixed_risk_rails.sql), so an
order that breaks one is refused whoever proposed it. This module hands the
same numbers to the solver so it plans inside them instead of being refused.

ONE SOURCE. settings 'risk_rails' holds the live values and both halves read
it; DEFAULTS here is only the fallback when the row cannot be read, and a
test holds it equal to the migration's seed.

Clusters (8% per cluster-day) are part 2: city_clusters.py fits them weekly
from forecast-error correlations, and its room() holds a city-day's new spend
to the cluster rail and to the correlation-weighted exposure on the same date.

DRAWDOWN (part 2, the plan's rule, never learned): the Kelly fraction is
multiplied by max(DRAWDOWN_MIN_SCALE, 1 - drawdown / DRAWDOWN_SPAN), drawdown
measured from the ledger's high-water mark. At 10% down a bet is half size; from
15% down it is a quarter, and no lower: the daily-loss rail and the kill switch
are what stop trading, not this.
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


DRAWDOWN_MIN_SCALE = 0.25
DRAWDOWN_SPAN = 0.20


def drawdown_scale(equity_usd, high_water_usd):
    """max(0.25, 1 - drawdown/0.20); 1 with no high-water mark or at a new high."""
    if not high_water_usd or float(high_water_usd) <= 0:
        return 1.0
    dd = max(0.0, 1.0 - float(equity_usd) / float(high_water_usd))
    return max(DRAWDOWN_MIN_SCALE, 1.0 - dd / DRAWDOWN_SPAN)


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
