import { cityInstant } from './time';

export const HORIZONS = [
  ['day_ahead','Day ahead'], ['d1_eve','Evening before · 18:00 local'], ['morning','Morning · 09:00 local'], ['noon','12:00 local · scheduled'],
  ['prepeak_2h','2 h before typical peak'], ['prepeak_1h','1 h before typical peak'],
  ['postpeak_1h','1 h after typical peak'], ['overall','Overall · common scheduled calls'],
] as const;
export type Learning = { phase: string; as_of: string; decision: string; reason: string;
  train_dates: number; validation_dates: number; version: string; validated_through: string | null;
  active: boolean; effective_params: { temperature: number; tilt: number }; readiness?: Readiness };
export type Readiness = { sample_ready: boolean; validation_window_days: number; policy: string;
  counts: Array<{ name: string; available: number; required: number; missing: number }> };
export type ModelSelection = { selected: string; decision: string; reason: string; fit_at: string;
  train_dates: number; validation_dates: number; validated_through: string | null; version: string;
  effective: { selected: string; active: boolean; reason: string }; readiness?: Readiness };
export type EarlyComparison = { calls: number; dates: number; raw_centre_hits: number;
  corrected_hits: number; correction_helped: number; correction_hurt: number;
  mean_log_loss_gain: number | null };
export type DailyScore = { date: string; n: number; hits: number; hit_rate: number | null;
  pending: number | null; claimed: number | null; early_comparison: EarlyComparison };
export type CaptureCall = { date: string; checkpoint: string; hit: number | null;
  forecast_input?: { source_model?: string; run_at?: string; raw_max_c?: number; sigma_c?: number;
    uncertainty?: { applied: boolean; policy: string; reason: string; advance_lead_days?: number } | null } | null;
  scheduled_local?: string; received_at?: string; received_local?: string; frozen_at?: string | null;
  delay_minutes?: number; cutoff_local?: string | null; before_expected_peak: boolean; reason: string;
  minutes_before_expected_peak?: number | null;
  observed_timing?: { relation: string; peak_local?: string; minutes_before_peak?: number } | null };
export type CaptureTiming = { policy: string; calls: number; eligible_calls: number; excluded_calls: number;
  exclusions: Record<string, number>; strict: Pick<CityReport, 'n' | 'hits' | 'hit_rate' | 'interval95' |
    'dates' | 'pending' | 'verified' | 'legacy' | 'metrics' | 'baseline' | 'raw' | 'calibration_gap' | 'versions' | 'recent_dates' | 'market_comparison'>;
  recent_calls: CaptureCall[] };
export type CityReport = { city: string; display_name: string; timezone: string; scope: string; horizon: string;
  n: number; hits: number; hit_rate: number | null; interval95: number[] | null; dates: number;
  pending: number | null; verified: number; legacy: number; calibration_gap: number | null;
  period_start: string; period_end: string;
  capture_timing?: CaptureTiming;
  observed_peak_timing?: { policy: string; calls: number; unknown_calls: number; pending_calls: number;
    before: { n: number; hits: number; hit_rate: number | null };
    before_60m?: { n: number; hits: number; hit_rate: number | null };
    before_120m?: { n: number; hits: number; hit_rate: number | null };
    at_or_after: { n: number; hits: number; hit_rate: number | null } };
  market_comparison?: { n: number; dates: number;
    model: { hit: number | null; log_loss: number | null; brier: number | null };
    market: { hit: number | null; log_loss: number | null; brier: number | null } };
  metrics: { log_loss: number | null; brier: number | null };
  baseline: { n: number; hit: number | null; log_loss: number | null };
  raw: { n: number; hit: number | null; log_loss: number | null };
  why: { missed_dates: number; reviewed_dates: number; weather: Record<string, number>;
    mean_peak_shift_h: number | null; public_forecast_mae_c: number | null; public_forecast_bias_c: number | null };
  learning: Learning[]; versions: Record<string,number>;
  model_selection?: ModelSelection | null;
  recent_dates?: DailyScore[];
  early_comparison?: EarlyComparison;
  early_adaptation?: Array<{phase: string; active: boolean; decision: string; reason: string; version: string;
    effective_strength: number; train_dates: number; validation_dates: number; readiness: Readiness}>;
  early_learning?: Array<{lead_days: number; as_of: string; computed_at: string; version: string;
    calibration: {applies?: boolean; reason?: string; n_fit?: number; n_validation?: number} | null}>;
  paired_audit?: { calls: number; dates: number; raw_hits: number; baseline_hits: number;
    raw_only_hits: number; baseline_only_hits: number; both_missed: number;
    raw_warmer_misses: number; raw_cooler_misses: number; mean_report_age_min: number | null } };
export function rankCities(rows: CityReport[]) {
  return [...rows].sort((a,b) => (b.hit_rate ?? -1)-(a.hit_rate ?? -1) || b.n-a.n || a.display_name.localeCompare(b.display_name));
}
/** Missing strict evidence never falls back to the scheduled hit rate. */
export function timingView(row: CityReport, strict: boolean): CityReport {
  if (!strict) return row;
  const score = row.capture_timing?.strict;
  return { ...row, ...(score ?? { n: 0, hits: 0, hit_rate: null, interval95: null, dates: 0,
    pending: null, verified: 0, legacy: 0, calibration_gap: null, metrics: { log_loss: null, brier: null },
    baseline: { n: 0, hit: null, log_loss: null }, raw: { n: 0, hit: null, log_loss: null }, versions: {}, recent_dates: [] }),
    // These diagnostics describe the complete scheduled cohort, not the subset.
    why: { missed_dates: 0, reviewed_dates: 0, weather: {}, mean_peak_shift_h: null,
      public_forecast_mae_c: null, public_forecast_bias_c: null },
    paired_audit: undefined, early_comparison: undefined, observed_peak_timing: undefined,
    market_comparison: score?.market_comparison };
}
export function readinessWords(r?: Readiness) {
  if (!r) return 'Readiness counts awaiting the updated scoring run.';
  return r.counts.map(c=>`${c.name}: ${c.available}/${c.required}${c.missing ? ` (${c.missing} missing)` : ''}`).join(' · ')
    + (r.sample_ready ? '. Sample ready; validation must still pass.' : '. Comparable independent dates still required.')
    + ` Latest ${r.validation_window_days} days are held for validation; source/weight versions stay separate.`;
}
export function timingReason(reason: string): string {
  return ({ eligible: 'Before expected peak', missing_server_receipt: 'No server freeze receipt',
    explicit_postpeak_call: 'Explicit after-peak call', missing_saved_peak: 'No saved peak estimate',
    at_or_after_expected_peak: 'At / after expected peak', incomplete_strict_date: 'Incomplete strict date' } as Record<string,string>)[reason] ?? reason;
}
/** Compare instants, not a local wall timestamp parsed in the viewer's zone. */
export function captureDelay(scheduled: string | null, captured: string | null, zone?: string | null): number | null {
  const m = scheduled?.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})/);
  if (!m || !captured || !zone) return null;
  const plan = cityInstant(m[1],Number(m[2])+Number(m[3])/60,zone);
  const actual = Date.parse(captured);
  return plan && Number.isFinite(actual) ? (actual-plan.getTime())/60000 : null;
}
/** Aggregate only the selected cities' same target dates; missing is not a miss. */
export function recentCityScores(rows: CityReport[]) {
  const dates = new Map<string, { date: string; n: number; hits: number; cities: Set<string>; pending: number | null }>();
  for (const row of rows) for (const d of row.recent_dates ?? []) {
    const total = dates.get(d.date) ?? { date: d.date, n: 0, hits: 0, cities: new Set<string>(), pending: 0 };
    total.n += d.n; total.hits += d.hits;
    if (d.n) total.cities.add(row.city);
    total.pending = total.pending == null || d.pending == null ? null : total.pending + d.pending;
    dates.set(d.date, total);
  }
  return [...dates.values()].sort((a,b)=>b.date.localeCompare(a.date)).slice(0,7)
    .map(d=>({...d, cities: d.cities.size, hit_rate: d.n ? d.hits/d.n : null}));
}
export function evidenceWords(r: CityReport) {
  const parts: string[] = [];
  if (!r.n) return 'Awaiting eligible settled calls';
  if (r.dates < 20) parts.push('Limited independent dates');
  const w = r.why;
  parts.push(`${w.reviewed_dates}/${w.missed_dates} missed dates reviewed`);
  const weather = Object.entries(w.weather).sort((a,b)=>b[1]-a[1]).slice(0,2);
  if (weather.length) parts.push(weather.map(([name,n])=>`${name} surprise on ${n}`).join(', '));
  if (w.mean_peak_shift_h != null && w.mean_peak_shift_h >= .5) parts.push(`Peak shifted ${w.mean_peak_shift_h.toFixed(1)} h on reviewed misses`);
  return parts.join(' · ');
}
