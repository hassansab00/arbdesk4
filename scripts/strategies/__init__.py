from strategies.base import Strategy, StrategyConfig, Signal, BandView, Context, dedupe_key
from strategies.s2_combination_arb import S2CombinationArb

# The strategies the live signal engine may run. s1 and s3-s9 retired on
# 29 Sep (plan v2 P8.2 step 5); their code is in strategies.legacy, for replay
# comparisons only. The engine strategies (s10-s12) decide in the tick, not
# through this registry (engine_shadow.STRATEGIES).
REGISTRY = {
    "s2_combination_arb": S2CombinationArb,
}

__all__ = ["Strategy", "StrategyConfig", "Signal", "BandView", "Context", "dedupe_key", "REGISTRY"]
