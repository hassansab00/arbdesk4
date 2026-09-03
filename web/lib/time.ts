/**
 * Time display.
 *
 * Two clocks matter on this desk and the UI was showing neither of them well.
 *
 *   YOUR clock decides when you are at the screen. Every timestamp - when a
 *   snapshot was taken, when a signal fired, when a workflow last ran - is
 *   something you read against your own day. Those were all printed in UTC.
 *
 *   The CITY's clock decides the weather. A daily maximum happens in the
 *   afternoon LOCAL to the city; peak windows, resolution dates and the
 *   forecast day boundary are all city-local facts. Rendering Chicago's 15:00
 *   peak in any other zone destroys the only thing the number was saying.
 *
 * So: timestamps in your zone, weather clock in the city's, and where the two
 * meet - "the peak is at 15:00 there, which is 23:00 here" - show both.
 *
 * Your zone comes from the browser (Intl), which is already Beirut on a
 * machine set to Beirut, and is right for anyone else without configuration.
 * It can be overridden and the choice is remembered per browser.
 */

const TZ_KEY = "ad4-display-tz";

/** The viewer's timezone: an explicit override, else whatever the browser says. */
export function displayTz(): string {
  try {
    const saved = localStorage.getItem(TZ_KEY);
    if (saved) return saved;
  } catch {
    /* private window or blocked site data - fall through to the browser's */
  }
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function setDisplayTz(tz: string | null): void {
  try {
    if (tz) localStorage.setItem(TZ_KEY, tz);
    else localStorage.removeItem(TZ_KEY);
  } catch {
    /* not fatal - the session just falls back to the browser's zone */
  }
}

/** "GMT+3" style label for a zone, for putting next to a time. */
export function tzLabel(tz: string, at: Date = new Date()): string {
  try {
    return (
      new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" })
        .formatToParts(at)
        .find((p) => p.type === "timeZoneName")?.value ?? tz
    );
  } catch {
    return tz;
  }
}

/** Short zone name for a city, e.g. "CDT". Falls back to the offset. */
export function shortZone(tz: string | null | undefined, at: Date = new Date()): string {
  if (!tz) return "";
  return tzLabel(tz, at);
}

function fmt(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat("en-GB", { timeZone: tz, ...opts }).format(d);
  } catch {
    return d.toISOString().slice(0, 16).replace("T", " ");
  }
}

/** "14:32" in the viewer's zone. */
export function fmtTime(iso: string | null | undefined, tz = displayTz()): string {
  return fmt(iso, tz, { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** "3 Sep 14:32" in the viewer's zone. */
export function fmtDateTime(iso: string | null | undefined, tz = displayTz()): string {
  return fmt(iso, tz, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** "Wed 3 Sep" in the viewer's zone. */
export function fmtDate(iso: string | null | undefined, tz = displayTz()): string {
  return fmt(iso, tz, { weekday: "short", day: "numeric", month: "short" });
}

/** A resolution date (a plain YYYY-MM-DD, already city-local) spelled out.
 *  Deliberately NOT timezone-converted: it is a calendar date in the city,
 *  not an instant, and shifting it by an offset changes which day it names. */
export function fmtResolutionDate(ymd: string | null | undefined): string {
  if (!ymd) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return ymd;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  // Assembled from parts rather than taken whole: en-GB renders September as
  // "Sept", and a four-letter month among eleven three-letter ones makes every
  // date column on the desk jump a pixel. Ordering stays day-then-month.
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month").slice(0, 3)}`;
}

/** How many days from today (in the viewer's zone) a resolution date is.
 *  0 = today, 1 = tomorrow. This is what "1/2 days ahead" means on the board. */
export function daysAhead(ymd: string | null | undefined, tz = displayTz()): number | null {
  if (!ymd) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return null;
  const target = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  const todayStr = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
  const t = /^(\d{4})-(\d{2})-(\d{2})/.exec(todayStr);
  if (!t) return null;
  return Math.round((target - Date.UTC(+t[1], +t[2] - 1, +t[3])) / 86400000);
}

/** "today", "tomorrow", "in 3 days", "yesterday" - what a trader actually says. */
export function fmtDaysAhead(ymd: string | null | undefined, tz = displayTz()): string {
  const n = daysAhead(ymd, tz);
  if (n === null) return "";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

/** An hour-of-day in the CITY's zone rendered as the viewer's wall clock:
 *  "15:00 CDT · 23:00 your time". Peak windows are the case this exists for. */
export function fmtCityHour(
  hourLocal: number | null | undefined,
  cityTz: string | null | undefined,
  viewerTz = displayTz()
): string {
  if (hourLocal === null || hourLocal === undefined || Number.isNaN(hourLocal)) return "—";
  const h = Math.floor(hourLocal);
  const mm = Math.round((hourLocal - h) * 60);
  const there = `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  if (!cityTz) return there;

  // Build today's instant at that local hour in the city, then read it back
  // in the viewer's zone. Intl both ways, so DST is handled on both sides.
  try {
    const now = new Date();
    const ymd = new Intl.DateTimeFormat("en-CA", {
      timeZone: cityTz, year: "numeric", month: "2-digit", day: "2-digit",
    }).format(now);
    const guess = new Date(`${ymd}T${there}:00Z`);
    const asCity = new Intl.DateTimeFormat("en-US", {
      timeZone: cityTz, hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(guess);
    const [gh, gm] = asCity.split(":").map(Number);
    const driftMin = (gh * 60 + gm) - (h * 60 + mm);
    const instant = new Date(guess.getTime() - driftMin * 60000);
    const here = new Intl.DateTimeFormat("en-GB", {
      timeZone: viewerTz, hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(instant);
    if (here === there) return `${there} ${shortZone(cityTz)}`;
    return `${there} ${shortZone(cityTz)} · ${here} your time`;
  } catch {
    return there;
  }
}
