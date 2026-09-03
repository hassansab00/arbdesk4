/**
 * Temperature display.
 *
 * Every temperature in the database is CELSIUS - temp_c, forecast_max_c,
 * band_lo/band_hi after conversion, sigma_c, mae_c. That is the right storage
 * decision and it does not change.
 *
 * What was wrong is that the UI printed those numbers with a hardcoded "°C"
 * on every page, for every city. A US market settles on a Fahrenheit reading
 * published by the NWS; showing it as 28.3°C means doing arithmetic in your
 * head against a market quoted in whole degrees F, on every glance. The band
 * edges are the worst of it: a band is [70, 72)°F, and 21.1-22.2°C is the
 * same interval rendered unrecognisable.
 *
 * `cities.unit` has held 'C' or 'F' per city the whole time. These helpers
 * are the only place a temperature becomes a string, so the unit is honoured
 * once rather than remembered eleven times.
 */

export type Unit = "C" | "F";

export function cToF(c: number): number {
  return c * 9 / 5 + 32;
}

export function fToC(f: number): number {
  return (f - 32) * 5 / 9;
}

/** A Celsius value as a NUMBER in the city's unit - for axes, bars, maths. */
export function toDisplay(c: number, unit: Unit | null | undefined): number {
  return unit === "F" ? cToF(c) : c;
}

/** A Celsius DIFFERENCE in the city's unit. Offsets do not apply to a delta:
 *  a 1°C change is 1.8°F, not 33.8°F. Getting this wrong turns every sigma,
 *  every MAE and every hourly change into nonsense. */
export function deltaToDisplay(c: number, unit: Unit | null | undefined): number {
  return unit === "F" ? c * 9 / 5 : c;
}

export function unitSuffix(unit: Unit | null | undefined): string {
  return unit === "F" ? "°F" : "°C";
}

/** An absolute temperature, in the city's unit. */
export function fmtTemp(
  c: number | null | undefined,
  unit: Unit | null | undefined,
  digits = 1
): string {
  if (c === null || c === undefined || Number.isNaN(c)) return "—";
  return `${toDisplay(c, unit).toFixed(digits)}${unitSuffix(unit)}`;
}

/** A temperature CHANGE, signed, in the city's unit. */
export function fmtTempDelta(
  c: number | null | undefined,
  unit: Unit | null | undefined,
  digits = 1
): string {
  if (c === null || c === undefined || Number.isNaN(c)) return "—";
  const v = deltaToDisplay(c, unit);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}${unitSuffix(unit)}`;
}

/** A band edge. Whole degrees for F markets, which quote in whole degrees;
 *  one decimal for C markets, which quote in halves. */
export function fmtBandEdge(c: number | null | undefined, unit: Unit | null | undefined): string {
  if (c === null || c === undefined || Number.isNaN(c)) return "";
  const v = toDisplay(c, unit);
  return unit === "F" ? String(Math.round(v)) : v.toFixed(1);
}

/** "70–72°F", "≤ 68°F", "≥ 78°F" - the interval as the market states it. */
export function fmtBandRange(
  lo: number | null | undefined,
  hi: number | null | undefined,
  unit: Unit | null | undefined,
  openLow?: boolean | null,
  openHigh?: boolean | null
): string {
  const s = unitSuffix(unit);
  if (openLow || lo === null || lo === undefined) return `≤ ${fmtBandEdge(hi, unit)}${s}`;
  if (openHigh || hi === null || hi === undefined) return `≥ ${fmtBandEdge(lo, unit)}${s}`;
  return `${fmtBandEdge(lo, unit)}–${fmtBandEdge(hi, unit)}${s}`;
}
