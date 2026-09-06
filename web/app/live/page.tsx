"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, ErrorBox, InlineError, Loading } from "@/components/DataState";
import WeatherIcon from "@/components/WeatherIcon";
import { fmtAge, fmtCompactUsd, severityColor } from "@/lib/format";
import { fmtTemp, fmtTempDelta, fmtBandRange, toDisplay, type Unit } from "@/lib/units";
import { fmtTime, fmtCityHour, shortZone } from "@/lib/time";
import type { City, LiveWeather, WeatherEvent } from "@/lib/types";

const STATE_ORDER: Record<string, number> = { INSIDE: 0, BEFORE: 1, AFTER: 2 };

interface Obs { valid_at: string; temp_c: number | null }
interface BandRow { band_id: string; band_lo: number | null; band_hi: number | null; open_low: boolean; open_high: boolean; band_label: string | null }

const WATCH_KEY = "ad4-city-watch";

interface TrendRow {
  city_key: string;
  slope_3_c_per_h: number | null;
  slope_6_c_per_h: number | null;
  direction: string | null;
  rolling_over: boolean | null;
  implied_max_c: number | null;
  typical_climb_left_c: number | null;
  reading_age_min: number | null;
  pct_already_peaked: number | null;
}

type SortKey = "peak" | "rate" | "togo" | "hottest" | "stale" | "name";

/** Each sort answers a different question, so the label is the question. */
const SORTS: Array<{ key: SortKey; label: string; hint: string }> = [
  { key: "peak",    label: "peak window",    hint: "Inside the peak window first, then approaching, then done. What needs watching now." },
  { key: "rate",    label: "moving fastest", hint: "Steepest climb first, by least squares over the last three readings. Where the day is still being decided." },
  { key: "togo",    label: "furthest to go", hint: "Largest gap between the forecast maximum and the running max. Most room left to travel." },
  { key: "hottest", label: "hottest now",    hint: "Highest current reading." },
  { key: "stale",   label: "stalest data",   hint: "Oldest observation first - the cities the desk is flying blind on." },
  { key: "name",    label: "name",           hint: "Alphabetical." },
];

export default function LiveWeatherPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("peak");
  const [query, setQuery] = useState("");
  const [watchedOnly, setWatchedOnly] = useState(false);
  // The SAME list City Watch uses. Two separate watchlists in one app is two
  // things to maintain and one of them is always stale.
  const [watched, setWatched] = useState<string[]>([]);
  useEffect(() => {
    try {
      const v = JSON.parse(localStorage.getItem(WATCH_KEY) || "[]");
      if (Array.isArray(v)) setWatched(v.filter((x) => typeof x === "string"));
    } catch { /* private window: no watchlist, no problem */ }
  }, []);
  function toggleWatch(k: string) {
    setWatched((w) => {
      const next = w.includes(k) ? w.filter((x) => x !== k) : [...w, k];
      try { localStorage.setItem(WATCH_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      // City Watch reads on mount; tell it now rather than on next reload.
      try { window.dispatchEvent(new StorageEvent("storage", { key: WATCH_KEY })); } catch { /* ignore */ }
      return next;
    });
  }
  // Bands the running max has just crossed, and cities whose running max
  // just ticked up: both drive a transient card state (§8.4).
  const [flashing, setFlashing] = useState<Record<string, number>>({});
  const prevMax = useRef<Record<string, number>>({});

  const live = useQuery<LiveWeather[]>(() => supabase.from("live_weather").select("*"), [], 60000);
  // Which way each city is moving and how fast (sql/ad4_26). live_weather has
  // temp_change_1h, which is one difference between two readings; this is a
  // least-squares slope over the last three, plus where the day is heading on
  // observation alone. That is the difference between a thermometer and
  // monitoring.
  const trend = useQuery<TrendRow[]>(
    () => supabase.from("v_city_peak_approach")
      .select("city_key,slope_3_c_per_h,slope_6_c_per_h,direction,rolling_over,implied_max_c,typical_climb_left_c,reading_age_min,pct_already_peaked"),
    [], 60000
  );
  const cities = useQuery<City[]>(() => supabase.from("cities").select("*"), []);
  const events = useQuery<WeatherEvent[]>(
    () => supabase.from("weather_events").select("*").order("detected_at", { ascending: false }).limit(50),
    []
  );
  // Today's forecast maximum, per city. The card needs it beside the running
  // max: "23.1 now, 28.4 forecast, 24.6 so far" is a position; "23.1 now" is
  // a thermometer. Every model that has an opinion on today is fetched, and
  // the shortest lead time wins - that is the freshest view of the same day.
  const forecasts = useQuery<Array<{ city_key: string; forecast_max_c: number | null; lead_days: number | null; model: string | null; run_at: string | null }>>(
    () =>
      supabase
        .from("weather_forecasts")
        .select("city_key,forecast_max_c,lead_days,model,run_at,for_date")
        .eq("for_date", new Date().toISOString().slice(0, 10))
        .order("run_at", { ascending: false })
        .limit(1000),
    [],
    5 * 60000
  );

  // Supabase Realtime: new weather_events push straight in, no polling.
  const [realtimeStatus, setRealtimeStatus] = useState<string>("connecting");
  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;
    try {
      channel = supabase
        .channel("weather-events-feed")
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "weather_events" }, () => {
          events.refresh();
          live.refresh();
        })
        .subscribe((status) => setRealtimeStatus(status));
    } catch (e) {
      setRealtimeStatus(e instanceof Error ? e.message : "unavailable");
    }
    return () => {
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Flash a card when its running max moves up.
  useEffect(() => {
    const rows = live.data ?? [];
    const next: Record<string, number> = {};
    for (const r of rows) {
      const prev = prevMax.current[r.city_key];
      if (prev !== undefined && r.running_max_c !== null && r.running_max_c > prev + 1e-9) {
        next[r.city_key] = Date.now();
      }
      if (r.running_max_c !== null) prevMax.current[r.city_key] = r.running_max_c;
    }
    if (Object.keys(next).length) {
      setFlashing((f) => ({ ...f, ...next }));
      const t = setTimeout(() => setFlashing({}), 3000);
      return () => clearTimeout(t);
    }
  }, [live.data]);

  const cityByKey = useMemo(
    () => new Map((cities.data ?? []).map((c) => [c.city_key, c])),
    [cities.data]
  );

  // Shortest lead time per city; ties broken by the newest run.
  const forecastByCity = useMemo(() => {
    const m = new Map<string, { forecast_max_c: number | null; model: string | null; run_at: string | null }>();
    for (const f of forecasts.data ?? []) {
      if (f.forecast_max_c === null) continue;
      const cur = m.get(f.city_key);
      if (!cur) m.set(f.city_key, f);
    }
    return m;
  }, [forecasts.data]);

  const trendByCity = useMemo(
    () => new Map((trend.data ?? []).map((t) => [t.city_key, t])),
    [trend.data]
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    let merged = (live.data ?? []).map((l) => ({
      ...l,
      city: cityByKey.get(l.city_key),
      // NOT `trend` - LiveWeather already has one (RISING/FALLING/FLAT,
      // a single 1h difference). This is the least-squares slope.
      approach: trendByCity.get(l.city_key) ?? null,
    }));

    if (watchedOnly && watched.length) merged = merged.filter((r) => watched.includes(r.city_key));
    if (q) {
      merged = merged.filter(
        (r) =>
          r.city_key.toLowerCase().includes(q) ||
          (r.city?.display_name ?? "").toLowerCase().includes(q)
      );
    }

    // A city with no value for the sort key SINKS, always - it must never
    // float to the top because null happened to compare as less than a number.
    const hi = (v: number | null | undefined) => (v == null ? -Infinity : v);
    const lo = (v: number | null | undefined) => (v == null ? Infinity : v);
    const gap = (r: (typeof merged)[number]) => {
      const f = forecastByCity.get(r.city_key)?.forecast_max_c;
      return f != null && r.running_max_c != null ? f - r.running_max_c : null;
    };

    const cmp: Record<SortKey, (a: (typeof merged)[number], b: (typeof merged)[number]) => number> = {
      peak: (a, b) =>
        (STATE_ORDER[a.peak_window_state ?? "AFTER"] ?? 3) -
        (STATE_ORDER[b.peak_window_state ?? "AFTER"] ?? 3),
      rate: (a, b) => hi(b.approach?.slope_3_c_per_h) - hi(a.approach?.slope_3_c_per_h),
      togo: (a, b) => hi(gap(b)) - hi(gap(a)),
      hottest: (a, b) => hi(b.temp_c) - hi(a.temp_c),
      stale: (a, b) =>
        lo(a.observed_at ? new Date(a.observed_at).getTime() : null) -
        lo(b.observed_at ? new Date(b.observed_at).getTime() : null),
      name: (a, b) =>
        (a.city?.display_name ?? a.city_key).localeCompare(b.city?.display_name ?? b.city_key),
    };

    // Watched cities lead every sort. You chose them; they should not be
    // twenty rows down because the sort disagrees.
    const w = new Set(watched);
    return merged.sort((a, b) => {
      const wa = w.has(a.city_key) ? 0 : 1;
      const wb = w.has(b.city_key) ? 0 : 1;
      return wa !== wb ? wa - wb : cmp[sortKey](a, b);
    });
  }, [live.data, cityByKey, trendByCity, sortKey, query, watchedOnly, watched, forecastByCity]);

  // A BAND_CROSS event in the last hour puts a red border on that city.
  const bandCrossCities = useMemo(() => {
    const cutoff = Date.now() - 3600 * 1000;
    return new Set(
      (events.data ?? [])
        .filter((e) => e.kind === "BAND_CROSS" && new Date(e.detected_at).getTime() > cutoff)
        .map((e) => e.city_key)
    );
  }, [events.data]);

  const detail = rows.find((r) => r.city_key === selected);

  // HOW OLD IS THE FEED, as one number.
  //
  // The age was on every card in 10px grey, which is the right place for a
  // per-city figure and the wrong place for "nothing here has updated since
  // Thursday". A page whose every card is stale reads as a page that is
  // working, and that is the single thing this section got wrong.
  const feed = useMemo(() => {
    const seen = (live.data ?? [])
      .map((r) => (r.observed_at ? new Date(r.observed_at).getTime() : null))
      .filter((v): v is number => v !== null);
    if (!seen.length) return null;
    const newest = Math.max(...seen);
    const ageMin = (Date.now() - newest) / 60000;
    const models = (live.data ?? []).filter((r) => r.source_kind === "model").length;
    const stations = (live.data ?? []).filter((r) => r.source_kind === "station").length;
    return {
      newest, ageMin, models, stations,
      cities: seen.length,
      // Two hours is P1.2's own cadence, so anything past three is a job that
      // did not run rather than a job between runs.
      level: ageMin > 720 ? "bad" : ageMin > 180 ? "warn" : "ok",
    };
  }, [live.data]);

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <div>
        <h1 className="mb-1 text-lg font-semibold">Live Weather</h1>
        <p className="mb-3 max-w-3xl text-xs leading-relaxed text-muted">
          Reading directly from the resolution stations that settle the markets — not a generic
          weather widget. Sorted: inside peak window first, then before, then after. A pulsing border
          means the peak window is open, a flash means the running max just moved, a dimmed card
          means the day is decided, and a red border means a band was crossed in the last hour.
        </p>

        {feed && (
          <div
            className={`mb-3 rounded border px-3 py-2 text-xs leading-relaxed ${
              feed.level === "bad"
                ? "border-bad/50 bg-bad/10 text-bad"
                : feed.level === "warn"
                ? "border-warn/50 bg-warn/10 text-warn"
                : "border-border bg-panel2 text-muted"
            }`}
          >
            <b>
              Newest reading anywhere: {fmtAge(new Date(feed.newest).toISOString())}
            </b>{" "}
            across {feed.cities} cities.
            {feed.level !== "ok" && (
              <>
                {" "}
                Nothing is updating this feed. n8n <b>P1.2</b> writes the US cities and{" "}
                <b>P1.5</b> writes every city — check both are imported, Active, and that their
                Config holds the <b>service_role</b> key, not the anon key. Actions → <i>Live
                Weather Monitor</i> is the manual fallback.
              </>
            )}
            {(feed.models > 0 || feed.stations > 0) && (
              <>
                {" "}
                <span className="opacity-80">
                  {feed.stations} from a station, {feed.models} interpolated by a model.
                </span>
              </>
            )}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a city"
            className="w-40 rounded border border-border bg-panel px-2 py-1 placeholder:text-muted"
          />
          <span className="text-muted">Sort</span>
          {SORTS.map((o) => (
            <button
              key={o.key}
              title={o.hint}
              onClick={() => setSortKey(o.key)}
              className={`rounded border px-2 py-1 ${
                sortKey === o.key
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-muted hover:text-text"
              }`}
            >
              {o.label}
            </button>
          ))}
          <button
            onClick={() => setWatchedOnly((v) => !v)}
            disabled={watched.length === 0}
            title={watched.length === 0 ? "Star a city first" : "Show only the cities you starred"}
            className={`rounded border px-2 py-1 ${
              watchedOnly ? "border-accent bg-accent/10 text-accent" : "border-border text-muted hover:text-text"
            } ${watched.length === 0 ? "opacity-40" : ""}`}
          >
            ★ watched{watched.length ? ` (${watched.length})` : ""}
          </button>
          <span className="ml-auto font-mono text-[11px] text-muted">
            {rows.length} of {live.data?.length ?? 0}
            {trend.error && <span className="ml-2 text-warn">no trend data — run sql/ad4_26</span>}
          </span>
        </div>

        <DataState
          loading={live.loading || cities.loading}
          error={live.error ?? cities.error}
          isEmpty={rows.length === 0}
          emptyTitle="No live weather yet"
          emptyBody={
            <>
              <code>live_weather</code> is empty — <code>scripts/live_weather.py</code> has not run
              against this database. Trigger GitHub Actions → <b>Live Weather</b> (it also runs on a
              schedule). Each run upserts one row per active city.
            </>
          }
          onRetry={() => { live.refresh(); cities.refresh(); }}
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
            {rows.map((r) => {
              const inside = r.peak_window_state === "INSIDE";
              const crossed = bandCrossCities.has(r.city_key);
              const flash = flashing[r.city_key] !== undefined;
              const unit = (r.city?.unit ?? "C") as Unit;
              const cityTz = r.city?.timezone ?? null;
              const fc = forecastByCity.get(r.city_key);
              // How much of the forecast day is still ahead of the running max.
              const toGo =
                fc?.forecast_max_c != null && r.running_max_c != null
                  ? fc.forecast_max_c - r.running_max_c
                  : null;
              return (
                <button
                  key={r.city_key}
                  onClick={() => setSelected(r.city_key === selected ? null : r.city_key)}
                  className={[
                    "rounded border bg-panel p-3 text-left text-sm transition",
                    crossed ? "border-bad" : inside ? "border-accent peak-pulse" : "border-border",
                    r.day_decided ? "opacity-60" : "",
                    flash ? "wx-flash-card" : "",
                    r.city_key === selected ? "ring-1 ring-accent" : "",
                  ].join(" ")}
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 font-semibold">
                      {/* A span, not a button: this card is already a button and
                          nesting one inside it is invalid HTML that swallows
                          the click in some browsers. */}
                      <span
                        role="button"
                        tabIndex={0}
                        title={watched.includes(r.city_key) ? "Stop watching" : "Watch this city"}
                        onClick={(e) => { e.stopPropagation(); toggleWatch(r.city_key); }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault(); e.stopPropagation(); toggleWatch(r.city_key);
                          }
                        }}
                        className={`cursor-pointer text-sm leading-none ${
                          watched.includes(r.city_key) ? "text-accent" : "text-border hover:text-muted"
                        }`}
                      >
                        ★
                      </span>
                      {r.city?.display_name ?? r.city_key}
                    </span>
                    <WeatherIcon condition={r.sky_condition} size={28} />
                  </div>
                  <div className="mt-1 flex items-baseline gap-2 font-mono text-lg">
                    <span>{fmtTemp(r.temp_c, unit)}</span>
                    {r.temp_change_1h !== null && Math.abs(r.temp_change_1h) > 0.05 && (
                      <span className={`text-xs ${r.temp_change_1h > 0 ? "text-good" : "text-bad"}`}>
                        {r.temp_change_1h > 0 ? "▲" : "▼"} {fmtTempDelta(r.temp_change_1h, unit)}/h
                      </span>
                    )}
                  </div>

                  {/* The three numbers that make this a position rather than a
                      thermometer: where the day is forecast to end up, where it
                      has got to, and how much of that is still to come. */}
                  <div className="mt-1.5 grid grid-cols-3 gap-1 border-t border-border pt-1.5 font-mono text-[11px]">
                    <div>
                      <div className="text-[9px] uppercase tracking-wide text-muted">Forecast</div>
                      <span className={fc ? "text-text" : "text-muted"}>
                        {fc ? fmtTemp(fc.forecast_max_c, unit) : "—"}
                      </span>
                    </div>
                    <div>
                      <div className="text-[9px] uppercase tracking-wide text-muted">Max so far</div>
                      {fmtTemp(r.running_max_c, unit)}
                    </div>
                    <div>
                      <div className="text-[9px] uppercase tracking-wide text-muted">To go</div>
                      <span className={toGo !== null && toGo > 0 ? "text-warn" : "text-muted"}>
                        {toGo === null ? "—" : toGo <= 0 ? "reached" : fmtTempDelta(toGo, unit)}
                      </span>
                    </div>
                  </div>

                  {/* MONITORING, not a thermometer: which way it is going, how
                      fast, and where that lands. temp_change_1h above is one
                      difference between two readings; this is the slope. */}
                  {r.approach && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-border pt-1.5 font-mono text-[10px]">
                      <span
                        className={
                          r.approach.rolling_over ? "text-warn"
                          : (r.approach.direction ?? "").startsWith("climbing") ? "text-good"
                          : (r.approach.direction ?? "").startsWith("falling") ? "text-bad"
                          : "text-muted"
                        }
                        title="Least squares over the last three readings, on their real timestamps."
                      >
                        {r.approach.rolling_over ? "ROLLED OVER" : (r.approach.direction ?? "—")}
                        {r.approach.slope_3_c_per_h != null &&
                          ` ${fmtTempDelta(r.approach.slope_3_c_per_h, unit)}/h`}
                      </span>
                      {r.approach.implied_max_c != null && (
                        <span
                          className="text-muted"
                          title="This reading plus how much this city has historically still climbed from this local hour. An estimate from observation alone, to hold the forecast against."
                        >
                          heading for <b className="text-accent">{fmtTemp(r.approach.implied_max_c, unit)}</b>
                        </span>
                      )}
                      {(r.approach.pct_already_peaked ?? 0) > 60 && (
                        <span className="text-muted" title="Share of past days on which the maximum was already behind by this hour.">
                          {Math.round(r.approach.pct_already_peaked!)}% done by now
                        </span>
                      )}
                    </div>
                  )}

                  <div className="mt-1.5 text-[10px] leading-relaxed text-muted">
                    <div>
                      max at{" "}
                      {r.running_max_at
                        ? `${fmtTime(r.running_max_at, cityTz ?? undefined)} ${shortZone(cityTz)}`
                        : "—"}
                      {" · peak "}
                      {r.peak_window_state ?? "—"}
                      {r.minutes_to_peak !== null && r.peak_window_state === "BEFORE"
                        ? ` in ${r.minutes_to_peak}m`
                        : ""}
                    </div>
                    <div>
                      obs {fmtAge(r.observed_at)}
                      {/* A model interpolation is an opinion about a
                          coordinate; a station reading is an instrument at the
                          ICAO the market settles on. Only one of them is
                          evidence, so the card must not present them alike. */}
                      {r.source_kind === "model" && (
                        <span
                          className="ml-1 rounded bg-warn/15 px-1 text-warn"
                          title={`Modelled by ${r.source ?? "a forecast model"} — interpolated to this coordinate, not measured at the station the market settles on.`}
                        >
                          model
                        </span>
                      )}
                      {r.day_decided && <span className="ml-1 text-muted">· DAY DECIDED</span>}
                      {crossed && <span className="ml-1 text-bad">· BAND CROSS</span>}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </DataState>

        {detail && <CityDetail row={detail} onClose={() => setSelected(null)} />}
      </div>

      <aside className="rounded border border-border bg-panel p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted">Event feed</h2>
          <span
            className={`text-[10px] ${realtimeStatus === "SUBSCRIBED" ? "text-good" : "text-muted"}`}
            title="Supabase Realtime subscription on weather_events. Requires the table to be in the supabase_realtime publication — sql/ad4_live_weather.sql adds it."
          >
            {realtimeStatus === "SUBSCRIBED" ? "live" : realtimeStatus.toLowerCase()}
          </span>
        </div>
        <DataState
          relation="weather_events"
          loading={events.loading}
          error={events.error}
          isEmpty={(events.data ?? []).length === 0}
          emptyTitle="No events yet"
          emptyBody={
            <>
              <code>weather_events</code> fills as <code>scripts/live_weather.py</code> detects
              spikes, drops, band crosses and day-decided transitions. Thresholds live in{" "}
              <code>settings.weather_alerts</code> and are provisional.
            </>
          }
          onRetry={events.refresh}
          compact
        >
          <div className="space-y-2">
            {(events.data ?? []).map((e) => (
              <div key={e.event_id} className={`rounded border-l-4 bg-panel2 p-2 text-xs ${severityColor(e.severity)}`}>
                <div className="flex justify-between">
                  <span className="font-semibold">{e.kind}</span>
                  <span className="text-[10px] uppercase">{e.severity}</span>
                </div>
                <div className="text-muted">
                  {e.city_key} · {fmtAge(e.detected_at)}
                  {e.temp_c !== null && ` · ${fmtTemp(e.temp_c, cityByKey.get(e.city_key)?.unit)}`}
                  {e.change_c !== null && ` · ${fmtTempDelta(e.change_c, cityByKey.get(e.city_key)?.unit)}`}
                </div>
              </div>
            ))}
          </div>
        </DataState>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail view: live temperature chart with band overlay and peak-window
// shading (§8.4). The chart is inline SVG - no chart library, so nothing
// extra ships to the browser and it themes with the rest of the desk.
// ---------------------------------------------------------------------------
function CityDetail({ row, onClose }: { row: LiveWeather & { city: City | undefined }; onClose: () => void }) {
  const since = useMemo(() => new Date(Date.now() - 24 * 3600 * 1000).toISOString(), []);
  // The market settles in this city's unit. Every temperature below follows it.
  const dUnit = (row.city?.unit ?? "C") as Unit;

  const obs = useQuery<Obs[]>(
    () =>
      supabase
        .from("weather_observations")
        .select("valid_at,temp_c")
        .eq("city_key", row.city_key)
        .gte("valid_at", since)
        .order("valid_at", { ascending: true })
        .limit(500),
    [row.city_key, since],
    120000
  );

  const bands = useQuery<BandRow[]>(
    () =>
      supabase
        .from("v_opportunities")
        .select("band_id,band_lo,band_hi,open_low,open_high,band_label")
        .eq("city_key", row.city_key)
        .eq("side", "YES"),
    [row.city_key]
  );

  const peak = useQuery<Array<{ peak_hour_local: number | null; window_width_h: number | null }>>(
    () =>
      supabase
        .from("derived_weather_peak")
        .select("peak_hour_local,window_width_h")
        .eq("city_key", row.city_key)
        .eq("month", new Date().getMonth() + 1)
        .limit(1),
    [row.city_key]
  );

  const volume = useQuery<Array<{ volume_usd: number; n_trades: number }>>(
    () => supabase.from("v_city_volume").select("volume_usd,n_trades").eq("city_key", row.city_key).limit(1),
    [row.city_key],
    60000
  );

  const series = (obs.data ?? []).filter((o) => o.temp_c !== null) as Array<{ valid_at: string; temp_c: number }>;
  const bandRows = (bands.data ?? []).filter((b) => b.band_lo !== null || b.band_hi !== null);

  return (
    <div className="mt-4 rounded border border-border bg-panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold">{row.city?.display_name ?? row.city_key} detail</h2>
        <button onClick={onClose} className="text-xs text-muted hover:text-text">close</button>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:grid-cols-6">
        <Field label="Now" value={fmtTemp(row.temp_c, dUnit)} />
        <Field label="Running max" value={fmtTemp(row.running_max_c, dUnit)} />
        <Field label="Δ 1h" value={fmtTempDelta(row.temp_change_1h, dUnit)} />
        <Field label="Wind" value={`${row.wind_speed_kt ?? "—"}kt ${row.wind_dir_compass ?? ""}`} />
        <Field label="Humidity" value={`${row.humidity ?? "—"}%`} />
        <Field label="Pressure" value={`${row.pressure_hpa ?? "—"}hPa`} />
        <Field label="Visibility" value={`${row.visibility_m ?? "—"}m`} />
        <Field label="Sky" value={row.sky_condition ?? "—"} />
        {/* The peak hour is a CITY-local fact - it is when the sun is highest
            there - so it is shown on that clock, with your own beside it. */}
        <Field
          label="Peak window"
          value={
            peak.data?.[0]?.peak_hour_local != null
              ? fmtCityHour(peak.data[0].peak_hour_local, row.city?.timezone)
              : (row.peak_window_state ?? "—")
          }
          wide
        />
        <Field label="Day decided" value={row.day_decided ? "yes" : "no"} />
        <Field
          label="Market vol 24h"
          value={volume.data?.[0] ? fmtCompactUsd(volume.data[0].volume_usd) : "—"}
          hint="Traded volume across this city's bands. A city with no volume has no market to trade, whatever the weather does."
        />
        <Field label="Last obs" value={fmtAge(row.observed_at)} />
        <Field
          label="Source"
          value={
            row.source_kind === "model"
              ? `${row.source ?? "model"} · interpolated, not measured`
              : row.source_kind === "station"
              ? `${row.source ?? "station"} · instrument reading`
              : "—"
          }
        />
      </div>

      <h3 className="mb-1 mt-4 text-xs font-semibold text-muted">
        Last 24h — temperature, band overlay, peak window
      </h3>
      {obs.loading ? (
        <Loading compact />
      ) : obs.error ? (
        <ErrorBox message={obs.error} onRetry={obs.refresh} compact />
      ) : series.length < 2 ? (
        <div className="rounded border border-dashed border-border p-4 text-center text-xs text-muted">
          Not enough observations in the last 24h to draw a chart ({series.length} point
          {series.length === 1 ? "" : "s"}). <code>weather_observations</code> is filled by the
          observations ingest and by each live-weather poll.
        </div>
      ) : (
        <TempChart
          series={series}
          bands={bandRows}
          runningMax={row.running_max_c}
          peakHourLocal={peak.data?.[0]?.peak_hour_local ?? null}
          windowWidthH={peak.data?.[0]?.window_width_h ?? null}
          timezone={row.city?.timezone ?? null}
          chartUnit={dUnit}
        />
      )}
      <InlineError message={bands.error ?? peak.error ?? volume.error} />

      {row.city?.icao && (
        <a
          className="mt-3 inline-block text-xs text-accent hover:underline"
          href={`https://www.weather.gov/wrh/timeseries?site=${row.city.icao}`}
          target="_blank"
          rel="noreferrer"
        >
          Resolution source for {row.city.icao} →
        </a>
      )}
    </div>
  );
}

function TempChart({
  series, bands, runningMax, peakHourLocal, windowWidthH, timezone, chartUnit,
}: {
  series: Array<{ valid_at: string; temp_c: number }>;
  bands: BandRow[];
  runningMax: number | null;
  peakHourLocal: number | null;
  windowWidthH: number | null;
  timezone: string | null;
  // The plot stays in Celsius - every value on it is Celsius, and converting
  // the geometry would be pointless work. Only the LABELS convert.
  chartUnit: Unit;
}) {
  const W = 720, H = 240, PAD_L = 34, PAD_R = 8, PAD_T = 8, PAD_B = 20;
  const t0 = new Date(series[0].valid_at).getTime();
  const t1 = new Date(series[series.length - 1].valid_at).getTime();
  const span = Math.max(1, t1 - t0);

  const temps = series.map((s) => s.temp_c);
  const bandEdges = bands.flatMap((b) => [b.band_lo, b.band_hi]).filter((v): v is number => v !== null);
  const lo = Math.min(...temps, ...(bandEdges.length ? bandEdges : temps)) - 1;
  const hi = Math.max(...temps, ...(bandEdges.length ? bandEdges : temps), runningMax ?? -Infinity) + 1;
  const range = Math.max(0.1, hi - lo);

  const x = (t: number) => PAD_L + ((t - t0) / span) * (W - PAD_L - PAD_R);
  const y = (v: number) => PAD_T + (1 - (v - lo) / range) * (H - PAD_T - PAD_B);

  const path = series.map((s, i) => `${i === 0 ? "M" : "L"}${x(new Date(s.valid_at).getTime()).toFixed(1)},${y(s.temp_c).toFixed(1)}`).join(" ");

  // Peak-window shading, in UTC, for each calendar day the chart covers.
  const shades: Array<{ x1: number; x2: number }> = [];
  if (peakHourLocal !== null && windowWidthH !== null) {
    let offset = 0;
    try {
      if (timezone) {
        const part = new Intl.DateTimeFormat("en-US", { timeZone: timezone, timeZoneName: "shortOffset" })
          .formatToParts(new Date())
          .find((p) => p.type === "timeZoneName")?.value ?? "GMT+0";
        const m = part.match(/GMT([+-]\d+)(?::(\d+))?/);
        if (m) {
          const h = parseInt(m[1], 10);
          const mm = m[2] ? parseInt(m[2], 10) / 60 : 0;
          offset = h >= 0 ? h + mm : h - mm;
        }
      }
    } catch { offset = 0; }
    const startUtcHour = peakHourLocal - windowWidthH / 2 - offset;
    for (let d = -1; d <= 1; d++) {
      const day = new Date(t0);
      day.setUTCHours(0, 0, 0, 0);
      const s = day.getTime() + (startUtcHour + d * 24) * 3600 * 1000;
      const e = s + windowWidthH * 3600 * 1000;
      if (e < t0 || s > t1) continue;
      shades.push({ x1: x(Math.max(s, t0)), x2: x(Math.min(e, t1)) });
    }
  }

  const yTicks = 4;

  return (
    <div className="overflow-x-auto rounded border border-border bg-panel2 p-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 480 }}>
        {/* peak window shading, behind everything */}
        {shades.map((s, i) => (
          <rect key={i} x={s.x1} y={PAD_T} width={Math.max(1, s.x2 - s.x1)} height={H - PAD_T - PAD_B} fill="#4f8cff" opacity={0.1} />
        ))}

        {/* band overlay: alternating stripes with labels on the right */}
        {bands.map((b, i) => {
          const top = b.band_hi ?? hi;
          const bottom = b.band_lo ?? lo;
          const yTop = y(Math.min(top, hi));
          const yBot = y(Math.max(bottom, lo));
          if (!Number.isFinite(yTop) || !Number.isFinite(yBot)) return null;
          return (
            <g key={b.band_id}>
              <rect
                x={PAD_L} y={Math.min(yTop, yBot)} width={W - PAD_L - PAD_R}
                height={Math.max(1, Math.abs(yBot - yTop))}
                fill={i % 2 === 0 ? "#ffffff" : "#000000"} opacity={0.03}
              />
              <line x1={PAD_L} x2={W - PAD_R} y1={yTop} y2={yTop} stroke="#232a38" strokeDasharray="3 3" />
              <text x={W - PAD_R - 2} y={yTop + 9} textAnchor="end" fontSize="8" fill="#8a93a6">
                {b.band_label ?? fmtBandRange(b.band_lo, b.band_hi, chartUnit, b.open_low, b.open_high)}
              </text>
            </g>
          );
        })}

        {/* y axis */}
        {[...Array(yTicks + 1)].map((_, i) => {
          const v = lo + (range * i) / yTicks;
          return (
            <g key={i}>
              <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="#1a2030" />
              <text x={PAD_L - 4} y={y(v) + 3} textAnchor="end" fontSize="9" fill="#8a93a6">
                {toDisplay(v, chartUnit).toFixed(0)}
              </text>
            </g>
          );
        })}

        {/* running max */}
        {runningMax !== null && (
          <>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(runningMax)} y2={y(runningMax)} stroke="#ffb020" strokeWidth={1} />
            <text x={PAD_L + 3} y={y(runningMax) - 3} fontSize="9" fill="#ffb020">
              running max {fmtTemp(runningMax, chartUnit)}
            </text>
          </>
        )}

        {/* the temperature trace */}
        <path d={path} fill="none" stroke="#4f8cff" strokeWidth={1.6} />
        <circle cx={x(t1)} cy={y(series[series.length - 1].temp_c)} r={2.5} fill="#4f8cff" />

        {/* x axis: hour labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const t = t0 + f * span;
          return (
            <text key={f} x={x(t)} y={H - 6} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"} fontSize="9" fill="#8a93a6">
              {new Date(t).toISOString().slice(11, 16)}Z
            </text>
          );
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-3 text-[10px] text-muted">
        <span><span className="mr-1 inline-block h-2 w-3 align-middle" style={{ background: "#4f8cff", opacity: 0.25 }} />peak window</span>
        <span><span className="mr-1 inline-block h-0.5 w-3 align-middle" style={{ background: "#ffb020" }} />running max</span>
        <span>dashed lines = band edges</span>
        {bands.length === 0 && <span className="text-warn">no bands loaded for this city — band overlay is empty</span>}
      </div>
    </div>
  );
}

function Field({ label, value, hint, wide }: { label: string; value: string; hint?: string; wide?: boolean }) {
  return (
    // `wide` is for values that carry two clocks ("15:00 CDT · 23:00 your
    // time") and would otherwise wrap mid-phrase in a one-column cell.
    <div title={hint} className={wide ? "col-span-2" : undefined}>
      <div className="text-muted">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
