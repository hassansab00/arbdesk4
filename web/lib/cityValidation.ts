export const HORIZONS = [
  ['day_ahead','Day ahead'], ['d1_eve','Evening before'], ['morning','Morning'], ['noon','Noon'],
  ['prepeak_2h','2 h before typical peak'], ['prepeak_1h','1 h before typical peak'],
  ['postpeak_1h','1 h after typical peak'], ['overall','Overall · common scheduled calls'],
] as const;
export type Learning = { phase: string; as_of: string; decision: string; reason: string;
  train_dates: number; validation_dates: number; version: string; validated_through: string | null;
  active: boolean; effective_params: { temperature: number; tilt: number } };
export type ModelSelection = { selected: string; decision: string; reason: string; fit_at: string;
  train_dates: number; validation_dates: number; validated_through: string | null; version: string;
  effective: { selected: string; active: boolean; reason: string } };
export type CityReport = { city: string; display_name: string; timezone: string; scope: string; horizon: string;
  n: number; hits: number; hit_rate: number | null; interval95: number[] | null; dates: number;
  pending: number | null; verified: number; legacy: number; calibration_gap: number | null;
  period_start: string; period_end: string;
  metrics: { log_loss: number | null; brier: number | null };
  baseline: { n: number; hit: number | null; log_loss: number | null };
  raw: { n: number; hit: number | null; log_loss: number | null };
  why: { missed_dates: number; reviewed_dates: number; weather: Record<string, number>;
    mean_peak_shift_h: number | null; public_forecast_mae_c: number | null; public_forecast_bias_c: number | null };
  learning: Learning[]; versions: Record<string,number>;
  model_selection?: ModelSelection | null;
  paired_audit?: { calls: number; dates: number; raw_hits: number; baseline_hits: number;
    raw_only_hits: number; baseline_only_hits: number; both_missed: number;
    raw_warmer_misses: number; raw_cooler_misses: number; mean_report_age_min: number | null } };
export function rankCities(rows: CityReport[]) {
  return [...rows].sort((a,b) => (b.hit_rate ?? -1)-(a.hit_rate ?? -1) || b.n-a.n || a.display_name.localeCompare(b.display_name));
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
