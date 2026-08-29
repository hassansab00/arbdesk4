// The schema has no explicit "region" column, so region is derived from
// longitude bands - a reasonable geographic approximation of the spec's
// five groups (Americas / Europe+Africa / West Asia / East Asia / Oceania),
// not an authoritative mapping. Swap for a real `cities.region` column if
// one gets added later.
export type Region = "Americas" | "Europe/Africa" | "West Asia" | "East Asia" | "Oceania";

export function regionFromLonLat(lon: number | null, lat: number | null): Region {
  if (lon === null) return "Americas";
  if (lon < -30) return "Americas";
  if (lon < 25) return "Europe/Africa";
  if (lon < 60) return "West Asia";
  if (lon < 135) return "East Asia";
  return "Oceania";
}

// Local UTC offset in hours for an IANA timezone, right now - computed
// with Intl (no library) rather than a hardcoded table.
export function utcOffsetHours(timeZone: string | null, at: Date = new Date()): number {
  if (!timeZone) return 0;
  try {
    const dtf = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "shortOffset" });
    const part = dtf.formatToParts(at).find((p) => p.type === "timeZoneName")?.value ?? "GMT+0";
    const m = part.match(/GMT([+-]\d+)(?::(\d+))?/);
    if (!m) return 0;
    const hours = parseInt(m[1], 10);
    const minutes = m[2] ? parseInt(m[2], 10) / 60 : 0;
    return hours >= 0 ? hours + minutes : hours - minutes;
  } catch {
    return 0;
  }
}
