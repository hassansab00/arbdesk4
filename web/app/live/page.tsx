"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, ErrorBox, InlineError, Loading } from "@/components/DataState";
import WeatherIcon from "@/components/WeatherIcon";
import { fmtAge, fmtCompactUsd, severityColor } from "@/lib/format";
import type { City, LiveWeather, WeatherEvent } from "@/lib/types";

const STATE_ORDER: Record<string, number> = { INSIDE: 0, BEFORE: 1, AFTER: 2 };

interface Obs { valid_at: string; temp_c: number | null }
interface BandRow { band_id: string; band_lo: number | null; band_hi: number | null; open_low: boolean; open_high: boolean; band_label: string | null }

export default function LiveWeatherPage() {
  const [selected, setSelected] = useState<string | null>(null);
  // Bands the running max has just crossed, and cities whose running max
  // just ticked up: both drive a transient card state (§8.4).
  const [flashing, setFlashing] = useState<Record<string, number>>({});
  const prevMax = useRef<Record<string, number>>({});

  const live = useQuery<LiveWeather[]>(() => supabase.from("live_weather").select("*"), [], 60000);
  const cities = useQuery<City[]>(() => supabase.from("cities").select("*"), []);
  const events = useQuery<WeatherEvent[]>(
    () => supabase.from("weather_events").select("*").order("detected_at", { ascending: false }).limit(50),
    []
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

  const rows = useMemo(() => {
    const merged = (live.data ?? []).map((l) => ({ ...l, city: cityByKey.get(l.city_key) }));
    merged.sort(
      (a, b) => (STATE_ORDER[a.peak_window_state ?? "AFTER"] ?? 3) - (STATE_ORDER[b.peak_window_state ?? "AFTER"] ?? 3)
    );
    return merged;
  }, [live.data, cityByKey]);

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
                    <span className="font-semibold">{r.city?.display_name ?? r.city_key}</span>
                    <WeatherIcon condition={r.sky_condition} size={28} />
                  </div>
                  <div className="mt-1 font-mono text-lg">
                    {r.temp_c?.toFixed(1) ?? "—"}°C
                    {r.trend === "RISING" && <span className="ml-1 text-good">↑</span>}
                    {r.trend === "FALLING" && <span className="ml-1 text-bad">↓</span>}
                  </div>
                  <div className="text-xs text-muted">
                    max {r.running_max_c?.toFixed(1) ?? "—"}°C
                    {r.running_max_at ? ` @ ${new Date(r.running_max_at).toISOString().slice(11, 16)}Z` : ""}
                  </div>
                  <div className="mt-1 text-[10px] text-muted">
                    {r.peak_window_state ?? "—"}
                    {r.minutes_to_peak !== null && r.peak_window_state === "BEFORE" ? ` · ${r.minutes_to_peak}m` : ""}
                    {r.day_decided && " · DAY DECIDED"}
                    {crossed && <span className="ml-1 text-bad">· BAND CROSS</span>}
                  </div>
                  <div className="mt-1 text-[10px] text-muted">obs {fmtAge(r.observed_at)}</div>
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
                  {e.temp_c !== null && ` · ${e.temp_c.toFixed(1)}°C`}
                  {e.change_c !== null && ` · Δ${e.change_c.toFixed(1)}°C`}
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
        <Field label="Now" value={`${row.temp_c?.toFixed(1) ?? "—"}°C`} />
        <Field label="Running max" value={`${row.running_max_c?.toFixed(1) ?? "—"}°C`} />
        <Field label="Δ 1h" value={row.temp_change_1h !== null ? `${row.temp_change_1h.toFixed(1)}°C` : "—"} />
        <Field label="Wind" value={`${row.wind_speed_kt ?? "—"}kt ${row.wind_dir_compass ?? ""}`} />
        <Field label="Humidity" value={`${row.humidity ?? "—"}%`} />
        <Field label="Pressure" value={`${row.pressure_hpa ?? "—"}hPa`} />
        <Field label="Visibility" value={`${row.visibility_m ?? "—"}m`} />
        <Field label="Sky" value={row.sky_condition ?? "—"} />
        <Field label="Peak window" value={row.peak_window_state ?? "—"} />
        <Field label="Day decided" value={row.day_decided ? "yes" : "no"} />
        <Field
          label="Market vol 24h"
          value={volume.data?.[0] ? fmtCompactUsd(volume.data[0].volume_usd) : "—"}
          hint="Traded volume across this city's bands. A city with no volume has no market to trade, whatever the weather does."
        />
        <Field label="Last obs" value={fmtAge(row.observed_at)} />
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
  series, bands, runningMax, peakHourLocal, windowWidthH, timezone,
}: {
  series: Array<{ valid_at: string; temp_c: number }>;
  bands: BandRow[];
  runningMax: number | null;
  peakHourLocal: number | null;
  windowWidthH: number | null;
  timezone: string | null;
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
                {b.band_label ?? `${b.band_lo}-${b.band_hi}`}
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
              <text x={PAD_L - 4} y={y(v) + 3} textAnchor="end" fontSize="9" fill="#8a93a6">{v.toFixed(0)}</text>
            </g>
          );
        })}

        {/* running max */}
        {runningMax !== null && (
          <>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(runningMax)} y2={y(runningMax)} stroke="#ffb020" strokeWidth={1} />
            <text x={PAD_L + 3} y={y(runningMax) - 3} fontSize="9" fill="#ffb020">running max {runningMax.toFixed(1)}°C</text>
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

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className="text-muted">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
