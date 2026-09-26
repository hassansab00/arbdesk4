"""Whether the loaders may use what the nightly loop learned (plan v2 P5.8).

The plan: learned parameters must beat the fixed priors on out-of-sample dates
in the replay (P7.3); until they do, "the loop ships with priors frozen and a
flag to enable it". The flag is settings.strategy_learning.enabled. It starts
false, and anything short of an explicit true - a missing row, a failed read -
means the priors.
"""
import sys


def enabled(rest):
    try:
        rows = rest("settings", [("select", "value"), ("key", "eq.strategy_learning")])
    except Exception as e:
        print(f"  note: strategy_learning unreadable ({e}); using the priors", file=sys.stderr)
        return False
    value = (rows[0].get("value") if rows else None) or {}
    return value.get("enabled") is True
