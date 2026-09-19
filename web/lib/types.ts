// Mirrors the shapes actually produced by sql/ad4_phase2*.sql views and
// sql/ad4_rpc.sql RPCs. Kept intentionally loose (optional fields) since
// this build has no live schema to generate types from - see
// docs/schema_assumptions.md. Treat these as documentation, not guarantees;
// widen a field to `unknown`/optional rather than assume shape at runtime.

export type MarketState = "NO_BOOK" | "DEAD_LOSER" | "DEAD_WINNER" | "ONE_SIDED" | "WIDE" | "LIVE";
export type RegimeLabel = "SHARP" | "NORMAL" | "UNCERTAIN" | "BLOCKED";
export type Side = "YES" | "NO";

export interface Opportunity {
  edge_id: number;
  side: Side;
  model_prob: number | null;
  market_price: number | null;
  edge_net_pp: number | null;
  edge_per_dollar: number | null;
  fillable_usd_5c: number | null;
  confidence: number | null;
  regime_label: RegimeLabel | null;
  tradeable: boolean;
  block_reason: string | null;
  band_id: string;
  band_label: string | null;
  band_lo: number | null;
  band_hi: number | null;
  open_low: boolean;
  open_high: boolean;
  token_yes: string | null;
  token_no: string | null;
  city_key: string;
  resolution_date: string;
  unit: "C" | "F";
  display_name: string | null;
  icao: string | null;
  station_name: string | null;
  timezone: string | null;
  band_width: number | null;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  market_state: MarketState | null;
  // Traded volume, from sql/ad4_phase2.sql's v_band_volume / v_city_volume.
  // Distinct from fillable_usd_* (book depth): depth is what the current
  // quote can absorb, volume is whether this market trades at all.
  volume_usd: number | null;
  n_trades: number | null;
  last_trade_at: string | null;
  city_volume_usd: number | null;
  thin_market: boolean | null;
  liquidity_factor: number | null;
  score_depth_only: number | null;
  score: number | null;
  // Provenance, added by sql/ad4_13_reconcile.sql. Volume has two possible
  // sources and the book ladder has three; the UI never shows one of these
  // numbers without being able to say which it is.
  //   volume_source     'book_24h' | 'trades_observed' | 'none'
  //   ask_levels_source 'raw_book' | 'levels_jsonb' | 'synthetic_tiers' | 'none'
  volume_source: VolumeSource | null;
  volume_stale: boolean | null;
  city_volume_source: VolumeSource | null;
  ask_levels_source: LadderSource | null;
  bid_levels_source: LadderSource | null;
  ask_depth_usd: number | null;
  bid_depth_usd: number | null;
  book_observed_at: string | null;
}

export type VolumeSource = "book_24h" | "trades_observed" | "none";
export type LadderSource = "raw_book" | "levels_jsonb" | "synthetic_tiers" | "none";

export const VOLUME_SOURCE_LABEL: Record<VolumeSource, string> = {
  book_24h: "exchange 24h volume, from the latest book snapshot",
  trades_observed: "summed from our own captured trades",
  none: "no volume from either source",
};

export const LADDER_SOURCE_LABEL: Record<LadderSource, string> = {
  raw_book: "real order book",
  levels_jsonb: "real order book",
  synthetic_tiers: "APPROXIMATED from tiered depth totals - not a real ladder",
  none: "no book",
};

export interface BookLevel {
  price: number;
  size: number;
}

// v_latest_book. NOTE: on the real database book_snapshots.bid_levels /
// ask_levels are integer LEVEL COUNTS, not ladders - the view replaces them
// with normalised jsonb ladders (sql/ad4_13_reconcile.sql), which is why
// these are typed as BookLevel[] here. *_levels_source says whether the
// ladder is a real book or one approximated from tiered depth totals.
export interface LatestBook {
  band_id: string;
  observed_at: string;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  market_state: MarketState | null;
  bid_levels: BookLevel[] | null;
  ask_levels: BookLevel[] | null;
  bid_levels_source: LadderSource | null;
  ask_levels_source: LadderSource | null;
  bid_depth_usd: number | null;
  ask_depth_usd: number | null;
}

export interface City {
  city_key: string;
  display_name: string | null;
  icao: string | null;
  station_name: string | null;
  timezone: string | null;
  unit: "C" | "F";
  band_width: number | null;
  latitude: number | null;
  longitude: number | null;
  resolution_source: string | null;
  status: string | null;
}

export interface LiveWeather {
  city_key: string;
  updated_at: string;
  observed_at: string | null;
  temp_c: number | null;
  temp_f: number | null;
  humidity: number | null;
  wind_speed_kt: number | null;
  wind_dir_compass: string | null;
  pressure_hpa: number | null;
  visibility_m: number | null;
  sky_condition: string | null;
  precip_1h: number | null;
  running_max_c: number | null;
  running_max_at: string | null;
  temp_change_1h: number | null;
  trend: "RISING" | "FALLING" | "FLAT" | null;
  minutes_to_peak: number | null;
  peak_window_state: "BEFORE" | "INSIDE" | "AFTER" | null;
  day_decided: boolean;
  /** Which feed wrote this row: NWS, IEM, or open-meteo (sql/ad4_30). */
  source: string | null;
  /**
   * `station` is an instrument reading at the ICAO the market settles on.
   * `model` is interpolated model output - an opinion about that coordinate.
   * Only the first is evidence about settlement, and a page that cannot tell
   * them apart is making a claim it cannot support.
   */
  source_kind: "station" | "model" | null;
  /** How many of today's readings exist for this city's OWN local day. */
  readings_today: number | null;
  /**
   * What running_max_c rests on (sql/ad4_71_observation_health.sql).
   *
   * `series` is a real maximum: two or more readings of this city's own day.
   * `floor_only` is one reading, or only the live thermometer - the day
   * reached AT LEAST that and may have reached far more, which is a floor
   * under the maximum and not the maximum. `absent` is nothing measured today.
   *
   * A page that prints running_max_c without this is making the same claim for
   * all three, and for 37 of the 54 cities the observation feed runs about a
   * day behind, so the difference is not an edge case.
   */
  running_max_basis: "series" | "floor_only" | "absent" | null;
}

export interface WeatherEvent {
  event_id: number;
  detected_at: string;
  city_key: string;
  kind: string;
  severity: "low" | "medium" | "high" | "critical";
  temp_c: number | null;
  change_c: number | null;
  detail: Record<string, unknown> | null;
}

export interface SignalRow {
  signal_id?: number;
  strategy_id: string;
  band_id: string | null;
  /** Written by scripts/signals.py. A signal that cannot name its city is
   *  unreadable in the UI, which is what this column exists to prevent. */
  city_key: string | null;
  /** Free-form context from whatever raised it - for an anomaly, the
   *  anomaly_id and the rule's own detail blob. */
  payload: Record<string, unknown> | null;
  side: string | null;
  action: "ENTER" | "EXIT" | "ALERT";
  reason: string;
  price_at_fire: number | null;
  prob_at_fire: number | null;
  edge_at_fire: number | null;
  confidence: number | null;
  regime_label: string | null;
  severity: "low" | "medium" | "high" | "critical";
  fired_at: string;
  status: "pending_approval" | "filled" | "unfilled" | "dismissed";
}

export interface PaperTrade {
  trade_id?: number;
  strategy_id: string;
  band_id: string;
  side: Side;
  shares: number;
  avg_fill_price: number;
  exit_price: number | null;
  gross_pnl: number | null;
  net_pnl: number | null;
  fee_paid: number | null;
  regime_label: string | null;
  opened_at: string;
  closed_at: string | null;
}

export interface BacktestRun {
  run_id: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "complete" | "failed";
  created_at: string;
  finished_at: string | null;
  label: string | null;
  error: string | null;
}

export interface CalcRecommendation {
  mode: "budget" | "target_profit";
  legs: Array<Record<string, unknown>>;
  total_cost?: number;
  ev_net?: number;
  best_case?: number;
  worst_case?: number;
  breakeven?: number;
  p_covered?: number;
  n_shares?: number;
  sum_ask?: number;
  insurance_cap_pass: boolean | null;
  fillability_pct?: number;
  capacity_warning?: boolean;
  correlation_warning?: boolean;
  feasible?: boolean;
  reason?: string;
  volume_usd?: number;
  volume_warning?: boolean;
  thin_bands?: string[];
}

/** One row per city from v_city_stats (sql/ad4_17_city_stats.sql): the
 *  weather, the model and the market side by side. */
export interface CityStats {
  city_key: string;
  display_name: string | null;
  icao: string | null;
  timezone: string | null;
  unit: "C" | "F";
  latitude: number | null;
  longitude: number | null;
  // weather, against this city's own climatological normal
  baseline: "seasonal" | "trailing_30d" | "none" | null;
  baseline_days: number | null;
  normal_max_c: number | null;
  volatility_c: number | null;
  now_c: number | null;
  running_max_c: number | null;
  forecast_max_c: number | null;
  forecast_model: string | null;
  anomaly_c: number | null;
  hotness_sigma: number | null;
  // model
  mae_c: number | null;
  bias_c: number | null;
  skill_days: number | null;
  model_spread_c: number | null;
  n_models: number | null;
  sigma_multiplier: number | null;
  // market
  volume_24h: number | null;
  n_trades_24h: number | null;
  depth_5c: number | null;
  live_bands: number | null;
  n_tradeable: number | null;
  best_edge_pp: number | null;
  avg_edge_pp: number | null;
  // clock
  peak_hour_local: number | null;
  window_width_h: number | null;
  peak_window_state: string | null;
  day_decided: boolean | null;
  observed_at: string | null;
  // forecast provenance (sql/ad4_17 / ad4_19). A temperature with no
  // provenance is unarguable-with: the board showed Chicago at 98F off a
  // week-old seven-day-lead row and nothing on screen said so.
  forecast_lead_days?: number | null;
  forecast_at?: string | null;
  observed_max_3d_c?: number | null;
  forecast_suspect?: boolean | null;
}


/** One row per live band from v_opportunity_context (sql/ad4_22): how the
 *  price and the forecast have MOVED, and whether the market has repriced
 *  since the forecast last changed. */
export interface OpportunityContext {
  band_id: string;
  city_key: string;
  resolution_date: string;
  mid_now: number | null;
  book_at: string | null;
  mid_1h: number | null;
  mid_6h: number | null;
  mid_24h: number | null;
  drift_1h: number | null;
  drift_6h: number | null;
  drift_24h: number | null;
  forecast_now_c: number | null;
  forecast_prev_c: number | null;
  forecast_move_c: number | null;
  forecast_at: string | null;
  /** The forecast is newer than the book: the price was set against older
   *  information than the desk is holding. */
  forecast_ahead_of_book: boolean | null;
  forecast_lead_hours: number | null;
  hours_to_resolution: number | null;
}

/**
 * sql/ad4_34_trade_plan.sql - an edge with WHEN and WHO attached.
 *
 * v_trade_plan is a superset of v_opportunities: every column above, plus the
 * timing of the day, where the day is heading relative to THIS band, and the
 * strategies whose entry test the row passes right now. Pages that need the
 * trade rather than the statistic read this one.
 */
export interface TradePlan extends Opportunity {
  // What the entry costs, at the ask rather than the mid.
  entry_price: number | null;
  round_trip_cost: number | null;
  beats_round_trip: boolean | null;

  // When.
  window_state: "BEFORE" | "INSIDE" | "AFTER" | null;
  minutes_to_peak: number | null;
  in_entry_window: boolean | null;
  timing_note: string | null;
  peak_hour: number | null;
  peak_source: string | null;
  local_hour: number | null;
  slope_3_c_per_h: number | null;
  direction: string | null;
  rolling_over: boolean | null;
  day_decided: boolean | null;
  reading_age_min: number | null;
  latest_temp_c: number | null;
  running_max_c: number | null;
  implied_max_c: number | null;
  implied_max_low_c: number | null;
  implied_max_high_c: number | null;

  // Where the day is going, relative to this band.
  holds_implied_max: boolean | null;
  holds_running_max: boolean | null;
  holds_pessimistic_max: boolean | null;
  out_of_reach: boolean | null;

  // Who would take it. would_fire is every strategy whose entry test passes;
  // would_fire_enabled is the subset that is switched on - the gap between
  // them is the cost of leaving a strategy off.
  would_fire: string[] | null;
  would_fire_enabled: string[] | null;
  action: string | null;
  book_age_min: number | null;
}

/** sql/ad4_34_trade_plan.sql - s8's two-bucket cover, per city and day. */
export interface CityDayPlan {
  city_key: string;
  resolution_date: string;
  label_a: string | null;
  label_b: string | null;
  band_a: string;
  band_b: string;
  price_a: number | null;
  price_b: number | null;
  pair_prob: number | null;
  pair_cost_with_fee: number | null;
  adjacent: boolean | null;
  thinner_leg_usd: number | null;
  implied_max_c: number | null;
  covers_implied_max: boolean | null;
  window_state: string | null;
  minutes_to_peak: number | null;
  s8_would_fire: boolean | null;
  pair_note: string | null;
}

/** sql/ad4_33_control.sql - the strategy roster with its own record. */
export interface StrategyBoardRow {
  strategy_id: string;
  name: string | null;
  side: string | null;
  origin: string | null;
  enabled: boolean;
  conflict_class: string | null;
  universe: unknown;
  regime_filter: unknown;
  capital_cap_pct: number | null;
  max_concurrent: number | null;
  fired_30d: number;
  waiting: number;
  last_fired_at: string | null;
  filled_all_time: number;
  won_all_time: number;
  net_pnl: number | null;
  avg_slippage_c: number | null;
  win_rate_pct: number | null;
  verdict: string;
}

/** sql/ad4_33_control.sql - when the day is decided, per city. */
export interface TradeTiming {
  city_key: string;
  display_name: string | null;
  local_hour: number | null;
  peak_hour: number | null;
  window_width_h: number | null;
  peak_source: string;
  peak_measured: boolean;
  window_opens_hour: number | null;
  window_closes_hour: number | null;
  minutes_to_peak: number | null;
  latest_temp_c: number | null;
  running_max_c: number | null;
  slope_3_c_per_h: number | null;
  direction: string | null;
  rolling_over: boolean | null;
  reading_age_min: number | null;
  implied_max_c: number | null;
  implied_max_low_c: number | null;
  implied_max_high_c: number | null;
  day_decided: boolean | null;
  window_state: string;
  in_entry_window: boolean;
  timing_note: string;
}
