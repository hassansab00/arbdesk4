"""
AD4 strategy framework (Task 8) - the contract every strategy conforms to.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import allocator


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
    # How often this city's station maximum falls inside the band the venue
    # settled on, 0-1. None means UNMEASURED - under ten settled ladders there
    # is no opinion to have - and every gate below treats it as "let through".
    observation_trust: Optional[float] = None
    # Live-weather fields (Task 13d), needed by S5/S10 running-max logic.
    running_max_c: Optional[float] = None
    minutes_to_peak: Optional[int] = None
    peak_window_state: Optional[str] = None
    day_decided: bool = False
    window_width_h: Optional[float] = None
    s5_allowed: bool = False
    # The shape of today so far (sql/ad4_26_temp_trend.sql). running_max_c
    # says how hot it has been; these say which way it is pointing and how
    # fast, which is the difference between a band that is still live and one
    # the day has already walked away from. All default to None so a desk that
    # has not run ad4_26 simply never fires the strategies that need them,
    # rather than firing on assumed values.
    slope_3_c_per_h: Optional[float] = None
    slope_6_c_per_h: Optional[float] = None
    trend_direction: Optional[str] = None      # climbing fast | climbing | flat | falling | falling fast
    rolling_over: bool = False
    latest_temp_c: Optional[float] = None
    reading_age_min: Optional[float] = None
    # Measured per city and local hour, from this desk's own archive: how much
    # further the day still climbed from here, historically.
    typical_climb_left_c: Optional[float] = None
    implied_max_c: Optional[float] = None      # latest reading + typical climb left
    implied_max_low_c: Optional[float] = None  # the p10 case
    implied_max_high_c: Optional[float] = None # the p90 case
    pct_already_peaked: Optional[float] = None
    # Today's forecast maximum for this city, in Celsius. Carried on the band
    # so a strategy can ask "does this bucket contain where the day is going"
    # without a second lookup.
    forecast_max_c: Optional[float] = None
    decision_evidence: dict = field(default_factory=dict)
    # THE DECISION SNAPSHOT: exactly what was believed at the moment a signal
    # could fire, frozen so a post-mortem reads the inputs of the decision and
    # not a reconstruction from numbers that have since been recomputed.
    #
    # Every one of these is a moving target. band_probabilities is rewritten
    # every pricing run, calibration is refitted from settled outcomes, cost
    # parameters change, and the book moves by the second - so by the time a
    # trade settles, none of the rows that produced it still say what they
    # said. All 65 fills on the live desk carried NULL for forecast_version,
    # calibration_version and cost_version, which made every post-mortem
    # guesswork.
    decision_snapshot: dict = field(default_factory=dict)


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
        if not self._city_thermometer_is_good_enough(band):
            return False
        return True

    def _city_thermometer_is_good_enough(self, band: BandView) -> bool:
        """A strategy that reads the observation may say how right it has to be.

        s5_running_max_lock and s7_pre_peak_gradient are built entirely on the
        station feed - the maximum is banked and this band holds it, the slope
        says the day is still climbing. Measured 2026-09-22 against the venue's
        own declared winners, that feed names the winning band 9 times in 10
        across 29 cities and about 2 in 3 across three of them, and both
        strategies were firing in all of them on identical terms.

        NONE IS UNMEASURED, NOT UNTRUSTWORTHY. Under ten settled ladders
        v_settlement_agreement has no opinion, and a new city has not failed -
        it has not been judged. Treating None as zero would stop every city
        trading the day this shipped.

        A strategy that sets no floor is unaffected, which is why this belongs
        in the base class rather than in two overrides that drift apart.
        """
        floor = (self.config.extra or {}).get("min_observation_trust")
        if floor is None or band.observation_trust is None:
            return True
        return float(band.observation_trust) >= float(floor)

    @abstractmethod
    def entry_signals(self, ctx: Context) -> list:
        ...

    @abstractmethod
    def exit_signals(self, ctx: Context, open_positions: list) -> list:
        ...

    def size(self, signal: Signal, portfolio) -> float:
        """
        Fractional Kelly on the signal's own edge, capped by capital_cap_pct.

        WHAT THIS USED TO BE: `cap_usd / price`, the same fraction of bankroll
        on every signal a strategy ever fired. A band at 5c with a twenty-point
        edge and a band at 60c with a two-point edge got identical money. The
        cap was doing all the work and the edge none of it, which is the
        difference between a position size and a constant.

        WHAT IT IS NOW. A dollar of payout costs price + the venue's
        0.05 * q * (1-q) fee, so the growth-optimal fraction of bankroll is
        (p - c) / (1 - c), scaled by allocator.DEFAULT_KELLY_FRACTION because
        our p is not good enough for full Kelly - the market's top two bands
        hold the winner 26.1% of the time against the model's 15.3%.

        capital_cap_pct STILL BINDS, as a ceiling rather than the answer.
        Hassan's number is a risk limit and a risk limit that the maths can
        talk its way past is not one. Kelly sizes down from it, never up.

        NO PROBABILITY, NO KELLY. s2_combination_arb has no model probability
        - its whole premise is arithmetic on the prices - and neither does any
        signal fired before the fitter has a view. Those keep the flat cap,
        which is the honest behaviour for a bet whose edge is not expressed as
        a probability, rather than a Kelly fraction computed from a guess.

        THE LADDER IS NOT VISIBLE FROM HERE. This sizes one signal against one
        price. Bands of the same market-day are mutually exclusive and should
        be solved together - allocator.allocate() does that, and the caller
        that holds the whole ladder is where it belongs. This is the floor
        under that, not a replacement for it.
        """
        bankroll = portfolio.bankroll if portfolio else 0.0
        if not bankroll or not signal.price_at_fire:
            return 0.0

        cap_usd = bankroll * (self.config.capital_cap_pct / 100.0)

        p = signal.prob_at_fire
        cost = allocator.effective_cost(signal.price_at_fire)
        if p is None or cost is None:
            return cap_usd / signal.price_at_fire

        full = (float(p) - cost) / (1.0 - cost)
        if full <= 0:
            return 0.0
        kelly_usd = bankroll * full * allocator.DEFAULT_KELLY_FRACTION
        return min(kelly_usd, cap_usd) / signal.price_at_fire


def dedupe_key(strategy_id, band_id, side, action, bucket):
    """
    Stable key so the same condition does not re-fire every poll cycle.
    `bucket` should be something that only changes when the underlying
    condition meaningfully changes (e.g. the regime label, or a rounded
    edge bucket), not the raw timestamp.
    """
    return f"{strategy_id}:{band_id}:{side}:{action}:{bucket}"
