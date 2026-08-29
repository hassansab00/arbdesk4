"""
AD4 strategy framework (Task 8) - the contract every strategy conforms to.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    strategy_id: str
    name: str
    side: str                  # 'YES' | 'NO' | 'BOTH'
    universe: list             # city_keys, or ['ALL']
    regime_filter: list        # ['SHARP','NORMAL'] etc
    conflict_class: str
    capital_cap_pct: float
    max_concurrent: int
    enabled: bool = False      # ALL SIX SHIP DISABLED
    # Addition beyond the spec's literal dataclass: a home for per-strategy
    # tunables (S1's entry_mode/price_threshold, S6's target profit, etc.)
    # so base.py doesn't need a new field for every strategy's own knobs.
    extra: dict = field(default_factory=dict)


@dataclass
class Signal:
    strategy_id: str
    band_id: str
    side: str
    action: str                # 'ENTER' | 'EXIT'
    reason: str
    price_at_fire: float
    prob_at_fire: float
    edge_at_fire: float
    suggested_shares: float
    confidence: float
    regime_label: str
    severity: str
    dedupe_key: str
    payload: dict = field(default_factory=dict)


@dataclass
class BandView:
    """Everything a strategy needs about one band, joined and current.
    Built by whatever assembles `ctx` (paper_engine live, or a test
    fixture) - strategies never touch the network directly."""
    band_id: str
    city_key: str
    resolution_date: str
    band_lo: Optional[float]
    band_hi: Optional[float]
    open_low: bool
    open_high: bool
    band_label: str
    unit: str
    model_prob_yes: Optional[float]
    yes_price: Optional[float]           # executable ask, YES
    no_price: Optional[float]            # executable ask, NO (complement-derived)
    yes_edge_net_pp: Optional[float]
    no_edge_net_pp: Optional[float]
    yes_tradeable: bool
    yes_block_reason: Optional[str]
    no_tradeable: bool
    no_block_reason: Optional[str]
    confidence: float
    regime_label: str
    market_state: str
    fillable_usd_5c_yes: float = 0.0
    fillable_usd_5c_no: float = 0.0
    token_yes: Optional[str] = None
    token_no: Optional[str] = None
    lead_days: Optional[int] = None
    spread: Optional[float] = None
    mae_bands: Optional[float] = None    # derived_forecast_skill.mae_bands for this city/lead
    # Live-weather fields (Task 13d), needed by S5/S10 running-max logic.
    running_max_c: Optional[float] = None
    minutes_to_peak: Optional[int] = None
    peak_window_state: Optional[str] = None
    day_decided: bool = False
    window_width_h: Optional[float] = None
    s5_allowed: bool = False


@dataclass
class Context:
    bands: list                 # list[BandView], the live universe
    settings: dict
    now: object                 # datetime, injectable for tests/backtest replay
    capacity: dict = field(default_factory=dict)      # city_key -> derived_capacity row
    correlation: dict = field(default_factory=dict)   # frozenset({city_a,city_b}) -> err_corr

    def bands_for_city_day(self, city_key, resolution_date):
        return [b for b in self.bands if b.city_key == city_key and b.resolution_date == resolution_date]

    def bands_for_city(self, city_key):
        return [b for b in self.bands if b.city_key == city_key]

    def in_universe(self, band: BandView, universe):
        return universe == ["ALL"] or band.city_key in universe


class Strategy(ABC):
    def __init__(self, config: StrategyConfig):
        self.config = config

    def applies_to(self, band: BandView) -> bool:
        if not self.config.enabled:
            return False
        if self.config.universe != ["ALL"] and band.city_key not in self.config.universe:
            return False
        if self.config.regime_filter and band.regime_label not in self.config.regime_filter:
            return False
        return True

    @abstractmethod
    def entry_signals(self, ctx: Context) -> list:
        ...

    @abstractmethod
    def exit_signals(self, ctx: Context, open_positions: list) -> list:
        ...

    def size(self, signal: Signal, portfolio) -> float:
        """
        Default sizing: a flat fraction of current bankroll, per the spec's
        "all limits expressed as fractions of current bankroll, never
        hardcoded dollars" rule. Strategies with more specific sizing logic
        (S6's target-profit N, S2's arb sizing) override this.
        """
        bankroll = portfolio.bankroll if portfolio else 0.0
        if not bankroll or not signal.price_at_fire:
            return 0.0
        cap_usd = bankroll * (self.config.capital_cap_pct / 100.0)
        return cap_usd / signal.price_at_fire


def dedupe_key(strategy_id, band_id, side, action, bucket):
    """
    Stable key so the same condition does not re-fire every poll cycle.
    `bucket` should be something that only changes when the underlying
    condition meaningfully changes (e.g. the regime label, or a rounded
    edge bucket), not the raw timestamp.
    """
    return f"{strategy_id}:{band_id}:{side}:{action}:{bucket}"
