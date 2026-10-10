/**
 * THE ENSEMBLE FOR ONE CITY'S DAY, AS THE NIGHTLY RECORD READS IT
 * (scripts/ensemble_record.py daily_rows, window 00_23).
 *
 * Open-Meteo's ensemble API returns every member's hourly temperature for a
 * place (ECMWF IFS 0.25: a control and 50 members; GFS 0.25: a control and
 * 30). A member's day is its maximum over the city's own local day, hours
 * 00-23, and a day with fewer than MIN_HOURS hourly values for a member is
 * not a whole day. Pure, so web/tests/ensemble.test.cjs runs it.
 *
 * These are raw model maxima at the model's grid point, not the settlement
 * station's reading: no station correction is applied here. Where the station
 * has already passed a temperature today, a member's day cannot settle below
 * it, so the shares can be floored at the station's maximum so far.
 */

import { type BandBounds, holds, temperatureOrder, venueRead } from "./cityCards";
import type { Unit } from "@/lib/units";

export const ENSEMBLE_MODELS = [
  { key: "ecmwf_ifs025", label: "ECMWF IFS ensemble", meta: "ecmwf_ifs025_ensemble" },
  { key: "gfs025", label: "GFS ensemble", meta: "ncep_gefs025" },
] as const;
export type EnsembleModel = (typeof ENSEMBLE_MODELS)[number]["key"];

/** A day with fewer hourly values than this, for any member, is not whole (the record's rule). */
export const MIN_HOURS = 20;

export interface EnsembleJson {
  hourly?: Record<string, Array<number | null> | string[] | undefined> & { time?: string[] };
}

/** The local calendar date (YYYY-MM-DD) of a UTC instant in an IANA zone. */
export function localDate(utcMs: number, tz: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date(utcMs));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/**
 * Every member's maximum over `day` (local, 00-23) in `tz`, from a response
 * asked with timezone=GMT. Empty when any member has fewer than MIN_HOURS
 * values that day (the record leaves such a day out whole).
 */
export function memberMaxima(js: EnsembleJson | null | undefined, tz: string, day: string): number[] {
  const hourly = js?.hourly ?? {};
  const times = (hourly.time ?? []) as string[];
  const members = Object.entries(hourly)
    .filter(([k]) => k === "temperature_2m" || k.startsWith("temperature_2m_member"))
    .map(([, v]) => (v ?? []) as Array<number | null>);
  if (times.length === 0 || members.length === 0) return [];
  const idx: number[] = [];
  times.forEach((t, i) => {
    if (localDate(Date.parse(`${t}Z`), tz) === day) idx.push(i);
  });
  const out: number[] = [];
  for (const vals of members) {
    const got = idx.map((i) => vals[i]).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    if (got.length < MIN_HOURS) return [];
    out.push(Math.max(...got));
  }
  return out;
}

/** Linear interpolation between closest ranks (numpy's default; the record's percentile). */
export function percentile(sorted: number[], q: number): number {
  if (sorted.length === 1) return sorted[0];
  const pos = ((sorted.length - 1) * q) / 100;
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export interface EnsembleSummary {
  n: number; mean: number; sd: number; min: number; max: number;
  p10: number; p25: number; p50: number; p75: number; p90: number;
}

/** The members' maxima summarised as the record stores them (population sd). */
export function summarise(maxima: number[]): EnsembleSummary | null {
  if (maxima.length === 0) return null;
  const s = maxima.slice().sort((a, b) => a - b);
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  const sd = Math.sqrt(s.reduce((a, b) => a + (b - mean) ** 2, 0) / s.length);
  return {
    n: s.length, mean, sd, min: s[0], max: s[s.length - 1],
    p10: percentile(s, 10), p25: percentile(s, 25), p50: percentile(s, 50),
    p75: percentile(s, 75), p90: percentile(s, 90),
  };
}

/**
 * The share of members whose day the venue would settle in each bucket: the
 * member's maximum (C), floored at the station's maximum so far when there is
 * one, read as the venue reads (whole degree of the market's unit, half away
 * from zero) and placed by the ladder's own half-open edges. Members no
 * bucket holds are counted apart, so the shares never claim more than they
 * cover.
 */
export function bucketShares(maximaC: number[], unit: Unit, bands: BandBounds[], floorC: number | null = null):
  { shares: Map<string, number>; unplaced: number } {
  const shares = new Map<string, number>();
  if (maximaC.length === 0) return { shares, unplaced: 0 };
  const ladder = temperatureOrder(bands);
  let unplaced = 0;
  for (const m of maximaC) {
    const c = floorC !== null && Number.isFinite(floorC) ? Math.max(m, floorC) : m;
    const r = venueRead(unit === "F" ? (c * 9) / 5 + 32 : c);
    const b = ladder.find((x) => holds(x, r));
    if (!b) { unplaced += 1; continue; }
    shares.set(b.band_id, (shares.get(b.band_id) ?? 0) + 1);
  }
  for (const [k, v] of shares) shares.set(k, v / maximaC.length);
  return { shares, unplaced: unplaced / maximaC.length };
}

/**
 * The request the browser sends for one city and model, asked in GMT as the
 * record asks: yesterday, so a city east of UTC has the whole of its today,
 * and four UTC days ahead, so a city west of UTC has the whole of its day
 * after tomorrow (Los Angeles's day +2 ends at 07:00Z on the UTC day +3;
 * scripts/honest_record.py asks four for the same reason; Codex on #362).
 */
export function ensembleUrl(lat: number, lon: number, model: EnsembleModel): string {
  const q = new URLSearchParams({
    latitude: String(lat), longitude: String(lon), hourly: "temperature_2m", models: model,
    timezone: "GMT", past_days: "1", forecast_days: "4",
  });
  return `https://ensemble-api.open-meteo.com/v1/ensemble?${q.toString()}`;
}

/** The run a model's latest answer comes from (Open-Meteo's static meta.json), as ISO UTC. */
export function runTimes(meta: { last_run_initialisation_time?: number; last_run_availability_time?: number } | null):
  { init: string | null; available: string | null } {
  const iso = (v?: number) => (v ? new Date(v * 1000).toISOString() : null);
  return { init: iso(meta?.last_run_initialisation_time), available: iso(meta?.last_run_availability_time) };
}
