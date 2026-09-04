/**
 * Colour and language for how far a day is from a city's own normal.
 *
 * The scale is in STANDARD DEVIATIONS, not degrees, because that is the only
 * form comparable across cities: +3C is a warm afternoon in Chicago (1.4 sd)
 * and a once-in-years event in Beirut (7 sd). A page that ranks cities against
 * each other on degrees is ranking them on how continental their climate is.
 */
export function heatColor(sigma: number | null | undefined): string {
  if (sigma === null || sigma === undefined || Number.isNaN(sigma)) return "var(--c-muted)";
  if (sigma >= 2) return "#ff3b30";       // exceptional heat
  if (sigma >= 1) return "#ff8c42";
  if (sigma >= 0.3) return "#ffb020";
  if (sigma > -0.3) return "#8a93a6";     // ordinary for the date
  if (sigma > -1) return "#5aa9e6";
  if (sigma > -2) return "#4f8cff";
  return "#7b61ff";                        // exceptional cold
}

export function heatWord(sigma: number | null | undefined): string {
  if (sigma === null || sigma === undefined || Number.isNaN(sigma)) return "no baseline";
  if (sigma >= 2) return "exceptionally hot";
  if (sigma >= 1) return "hot for the date";
  if (sigma >= 0.3) return "mild-warm";
  if (sigma > -0.3) return "normal";
  if (sigma > -1) return "mild-cool";
  if (sigma > -2) return "cold for the date";
  return "exceptionally cold";
}

/** Five regions, each with its own hue, so a map or a legend separates them
 *  at a glance instead of relying on position. */
export const REGION_COLOR: Record<string, string> = {
  "Americas": "#4f8cff",
  "Europe/Africa": "#2ecc71",
  "West Asia": "#ffb020",
  "East Asia": "#ff6b9d",
  "Oceania": "#a78bfa",
  // A city with neither a timezone nor coordinates. Grey on purpose: it must
  // not look like one of the five real groups.
  "Unknown": "#6b7280",
};
