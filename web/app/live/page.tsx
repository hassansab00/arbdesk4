"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import WeatherIcon from "@/components/WeatherIcon";
import { severityColor } from "@/lib/format";
import type { City, LiveWeather, WeatherEvent } from "@/lib/types";

const STATE_ORDER: Record<string, number> = { INSIDE: 0, BEFORE: 1, AFTER: 2 };

export default function LiveWeatherPage() {
  const [rows, setRows] = useState<Array<LiveWeather & { city: City | undefined }>>([]);
  const [events, setEvents] = useState<WeatherEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  async function loadWeather() {
    const [{ data: live }, { data: cities }] = await Promise.all([
      supabase.from("live_weather").select("*"),
      supabase.from("cities").select("*"),
    ]);
    const cityByKey = new Map((cities as City[] | null ?? []).map((c) => [c.city_key, c]));
    const merged = ((live as LiveWeather[] | null) ?? []).map((l) => ({ ...l, city: cityByKey.get(l.city_key) }));
    merged.sort((a, b) => (STATE_ORDER[a.peak_window_state ?? "AFTER"] ?? 3) - (STATE_ORDER[b.peak_window_state ?? "AFTER"] ?? 3));
    setRows(merged);
  }

  async function loadEvents() {
    const { data } = await supabase.from("weather_events").select("*").order("detected_at", { ascending: false }).limit(30);
    setEvents((data as WeatherEvent[]) ?? []);
  }

  useEffect(() => {
    loadWeather();
    loadEvents();
    const t = setInterval(loadWeather, 60000);
    const channel = supabase
      .channel("weather-events-feed")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "weather_events" }, () => { loadEvents(); loadWeather(); })
      .subscribe();
    return () => { clearInterval(t); supabase.removeChannel(channel); };
  }, []);

  const detail = rows.find((r) => r.city_key === selected);

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <div>
        <h1 className="mb-1 text-lg font-semibold">Live Weather</h1>
        <p className="mb-4 text-xs text-muted">
          Reading directly from the resolution stations that settle the markets - not a generic
          weather widget. Sorted: inside peak window first, then before, then after.
        </p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          {rows.map((r) => (
            <button
              key={r.city_key}
              onClick={() => setSelected(r.city_key)}
              className={`rounded border bg-panel p-3 text-left text-sm transition ${
                r.peak_window_state === "INSIDE" ? "border-accent peak-pulse" : "border-border"
              } ${r.day_decided ? "opacity-60" : ""}`}
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
                {r.peak_window_state ?? "—"}{r.minutes_to_peak !== null && r.peak_window_state === "BEFORE" ? ` · ${r.minutes_to_peak}m` : ""}
                {r.day_decided && " · DAY DECIDED"}
              </div>
            </button>
          ))}
          {rows.length === 0 && <div className="col-span-full p-8 text-center text-muted">No live_weather rows yet - scripts/live_weather.py has not run against this database.</div>}
        </div>

        {detail && (
          <div className="mt-4 rounded border border-border bg-panel p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-semibold">{detail.city?.display_name ?? detail.city_key} detail</h2>
              <button onClick={() => setSelected(null)} className="text-xs text-muted hover:text-text">close</button>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <Field label="Wind" value={`${detail.wind_speed_kt ?? "—"}kt ${detail.wind_dir_compass ?? ""}`} />
              <Field label="Humidity" value={`${detail.humidity ?? "—"}%`} />
              <Field label="Pressure" value={`${detail.pressure_hpa ?? "—"}hPa`} />
              <Field label="Visibility" value={`${detail.visibility_m ?? "—"}m`} />
            </div>
            {detail.city?.icao && (
              <a
                className="mt-3 inline-block text-xs text-accent hover:underline"
                href={`https://www.weather.gov/wrh/timeseries?site=${detail.city.icao}`}
                target="_blank" rel="noreferrer"
              >
                Resolution source for {detail.city.icao} →
              </a>
            )}
          </div>
        )}
      </div>

      <aside className="rounded border border-border bg-panel p-3">
        <h2 className="mb-2 text-sm font-semibold text-muted">Event feed</h2>
        <div className="space-y-2">
          {events.map((e) => (
            <div key={e.event_id} className={`rounded border-l-4 bg-panel2 p-2 text-xs ${severityColor(e.severity)}`}>
              <div className="flex justify-between"><span className="font-semibold">{e.kind}</span><span className="uppercase text-[10px]">{e.severity}</span></div>
              <div className="text-muted">{e.city_key} · {new Date(e.detected_at).toLocaleTimeString()}</div>
            </div>
          ))}
          {events.length === 0 && <div className="text-muted text-sm">No events yet.</div>}
        </div>
      </aside>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
