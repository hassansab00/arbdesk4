// Which part of the world a city is in.
//
// This was derived from longitude alone, with `if (lon === null) return
// "Americas"` as the fallback - so every city whose coordinates were never
// filled in was filed under the Americas, and Ankara and Moscow sat in the
// same group as Chicago. A default that looks like an answer is worse than
// an admitted gap.
//
// The IANA timezone is the authority now, because it is a real geographic
// statement ("Europe/Istanbul") rather than an inference, and cities.timezone
// is populated for every city the desk trades - it is what every local-day
// calculation in the schema already relies on. Longitude is the fallback, and
// "Unknown" is a real answer when there is neither.
export type Region =
  | "Americas"
  | "Europe/Africa"
  | "West Asia"
  | "East Asia"
  | "Oceania"
  | "Unknown";

// Longitude ranges are only reached when a city has no timezone at all.
function fromLongitude(lon: number | null | undefined): Region {
  if (lon === null || lon === undefined || !Number.isFinite(lon)) return "Unknown";
  if (lon < -30) return "Americas";
  if (lon < 25) return "Europe/Africa";
  if (lon < 60) return "West Asia";
  if (lon < 135) return "East Asia";
  return "Oceania";
}

// Asia spans three of our five groups, so the zone's own city decides. Listed
// rather than inferred: "Asia/Nicosia" is West and "Asia/Seoul" is East, and
// no rule over the string gets both right.
const WEST_ASIA = new Set([
  "dubai", "qatar", "riyadh", "kuwait", "baghdad", "tehran", "muscat",
  "bahrain", "amman", "beirut", "damascus", "jerusalem", "tel_aviv", "gaza",
  "nicosia", "famagusta", "yerevan", "baku", "tbilisi", "aden", "kabul",
  "karachi", "tashkent", "ashgabat", "dushanbe", "bishkek", "almaty",
  "aqtau", "aqtobe", "atyrau", "oral", "qostanay", "qyzylorda", "samarkand",
  "kolkata", "calcutta", "colombo", "kathmandu", "dhaka", "thimphu",
]);

export function regionFromCity(city: {
  timezone?: string | null;
  longitude?: number | null;
  latitude?: number | null;
}): Region {
  const tz = (city.timezone ?? "").trim();
  if (tz) {
    const [area, ...rest] = tz.split("/");
    const zone = (rest[rest.length - 1] ?? "").toLowerCase();
    switch (area) {
      case "America":
      case "US":
      case "Canada":
      case "Brazil":
      case "Mexico":
      case "Chile":
      case "Cuba":
      case "Jamaica":
        return "Americas";
      case "Europe":
      case "Africa":
      case "Atlantic":
      case "Arctic":
      case "GB":
      case "Eire":
      case "Portugal":
      case "Poland":
      case "Turkey":
        return "Europe/Africa";
      case "Australia":
      case "Pacific":
      case "NZ":
        return "Oceania";
      case "Asia":
        return WEST_ASIA.has(zone) ? "West Asia" : "East Asia";
      case "Indian":
        return "West Asia";
      default:
        break;                      // UTC, Etc/*, or something unrecognised
    }
  }
  return fromLongitude(city.longitude);
}

/** Kept for callers that genuinely only have coordinates. */
export function regionFromLonLat(lon: number | null, lat: number | null): Region {
  return fromLongitude(lon);
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
