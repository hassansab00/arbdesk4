from strategies.base import Strategy, StrategyConfig, Signal, BandView, Context, dedupe_key
from strategies.s1_buy_low_sell_signal import S1BuyLowSellSignal
from strategies.s2_combination_arb import S2CombinationArb
from strategies.s3_concentration import S3Concentration
from strategies.s4_tail_fade import S4TailFade
from strategies.s5_running_max_lock import S5RunningMaxLock
from strategies.s6_anchor_insurance import S6AnchorInsurance
from strategies.s7_pre_peak_gradient import S7PrePeakGradient
from strategies.s8_two_bucket_cover import S8TwoBucketCover

REGISTRY = {
    "s1_buy_low_sell_signal": S1BuyLowSellSignal,
    "s2_combination_arb": S2CombinationArb,
    "s3_concentration": S3Concentration,
    "s4_tail_fade": S4TailFade,
    "s5_running_max_lock": S5RunningMaxLock,
    "s6_anchor_insurance": S6AnchorInsurance,
    "s7_pre_peak_gradient": S7PrePeakGradient,
    "s8_two_bucket_cover": S8TwoBucketCover,
}

__all__ = ["Strategy", "StrategyConfig", "Signal", "BandView", "Context", "dedupe_key", "REGISTRY"]
