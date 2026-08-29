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
  score: number | null;
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
}
