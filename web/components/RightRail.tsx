"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtAge, severityColor } from "@/lib/format";
import { fmtTemp, type Unit } from "@/lib/units";
import type { LiveWeather, WeatherEvent } from "@/lib/types";

/**
 * A glimpse of the City Monitor: what is moving, what fired, what is stale.
 *
 * WHAT THIS REPLACED AND WHY. Signals lived here through six versions and was
 * wrong in this slot every time. It is a queue you work through, not a thing
 * you glance at - and with every strategy shipping disabled it held one
 * sentence about being switched off, permanently, on every page. The last
 * version also OVERLAID the page, which nobody asked for and which makes a
 * side panel worse than useless: you cannot read the thing you opened it
 * beside.
 *
 * So: no signals, no overlay. A docked column showing live city state -
 * temperature now, which way it is moving and how fast, how far it still has
 * to climb, and the events that fired - with the page reflowing beside it.
 * Collapsing narrows the column; it never floats over anything.
 */

const W_KEY = "ad4-rail-width";
const COLLAPSED_KEY = "ad4-rail-collapsed";
const WATCH_KEY = "ad4-city-watch";

const MIN_W = 240;
const MAX_W = 520;

interface TrendRow {
  city_key: string;
  slope_3_c_per_h: number | null;
  direction: string | null;
  rolling_over: boolean | null;
  implied_max_c: number | null;
  typical_climb_left_c: number | null;
  reading_age_min: number | null;
}

export default function RightRail() {
  const [collapsed, setCollapsed] = useState(false);
  const [width, setWidth] = useState(300);
  const [drag, setDrag] = useState(false);
  const [watched, setWatched] = useState<string[]>([]);
  const col = useRef<HTMLElement | null>(null);

  useEffect(() => {
    try {
      const w = Number(localStorage.getItem(W_KEY));
      if (Number.isFinite(w) && w >= MIN_W && w <= MAX_W) setWidth(w);
      setCollapsed(localStorage.getItem(COLLAPSED_KEY) === "1");
      const v = JSON.parse(localStorage.getItem(WATCH_KEY) || "[]");
      if (Array.isArray(v)) setWatched(v.filter((x) => typeof x === "string"));
    } catch { /* private window */ }
  }, []);

  useEffect(() => {
    if (!drag) return;
    const move = (e: MouseEvent) =>
      setWidth(Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - e.clientX)));
    const up = () => {
      setDrag(false);
      try { localStorage.setItem(W_KEY, String(width)); } catch { /* ignore */ }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.userSelect = "";
    };
  }, [drag, width]);

  const liveQ = useQuery<LiveWeather[]>(
    () => supabase.from("live_weather").select("*"), [], 60000
  );
  const trendQ = useQuery<TrendRow[]>(
    () => supabase.from("v_city_peak_approach")
      .select("city_key,slope_3_c_per_h,direction,rolling_over,implied_max_c,typical_climb_left_c,reading_age_min"),
    [], 60000
  );
  const eventsQ = useQuery<WeatherEvent[]>(
    () => supabase.from("weather_events").select("*")
      .order("detected_at", { ascending: false }).limit(12),
    [], 60000
  );
  const citiesQ = useQuery<Array<{ city_key: string; display_name: string | null; unit: string | null }>>(
    () => supabase.from("cities").select("city_key,display_name,unit"), []
  );

  const trendBy = useMemo(() => {
    const m = new Map<string, TrendRow>();
    for (const t of trendQ.data ?? []) m.set(t.city_key, t);
    return m;
  }, [trendQ.data]);

  const unitBy = useMemo(() => {
    const m = new Map<string, Unit>();
    for (const c of citiesQ.data ?? []) m.set(c.city_key, (c.unit ?? "C") as Unit);
    return m;
  }, [citiesQ.data]);

  /**
   * MOVING FIRST, not alphabetical. A glance column's whole job is to put the
   * thing that changed at the top; sorted by name it is a directory.
   * Starred cities win, then steepest climb, then staleness.
   */
  const rows = useMemo(() => {
    const live = liveQ.data ?? [];
    const star = new Set(watched);
    const scored = live.map((r) => {
      const t = trendBy.get(r.city_key);
      return { r, t, rate: Math.abs(t?.slope_3_c_per_h ?? 0) };
    });
    scored.sort((a, b) => {
      const sa = star.has(a.r.city_key) ? 1 : 0;
      const sb = star.has(b.r.city_key) ? 1 : 0;
      if (sa !== sb) return sb - sa;
      if (a.r.peak_window_state === "INSIDE" && b.r.peak_window_state !== "INSIDE") return -1;
      if (b.r.peak_window_state === "INSIDE" && a.r.peak_window_state !== "INSIDE") return 1;
      return b.rate - a.rate;
    });
    return star.size ? scored.filter((s) => star.has(s.r.city_key)) : scored.slice(0, 12);
  }, [liveQ.data, trendBy, watched]);

  const newest = useMemo(() => {
    const t = (liveQ.data ?? [])
      .map((r) => (r.observed_at ? new Date(r.observed_at).getTime() : 0))
      .filter(Boolean);
    return t.length ? Math.max(...t) : null;
  }, [liveQ.data]);

  function toggle() {
    setCollapsed((c) => {
      try { localStorage.setItem(COLLAPSED_KEY, c ? "0" : "1"); } catch { /* ignore */ }
      return !c;
    });
  }

  if (collapsed) {
    return (
      <button
        onClick={toggle}
        title="Show the city monitor"
        className="flex w-7 shrink-0 items-center justify-center border-l border-border bg-panel text-[10px] uppercase tracking-widest text-muted hover:text-text"
      >
        <span style={{ writingMode: "vertical-rl" }}>City monitor</span>
      </button>
    );
  }

  return (
    <>
      <div
        onMouseDown={() => setDrag(true)}
        title="Drag to resize"
        className={`hidden w-1.5 shrink-0 cursor-col-resize bg-border/40 hover:bg-accent/60 md:block ${drag ? "bg-accent" : ""}`}
      />
      {/* A real column. The page reflows beside it - it never floats over the
          thing you opened it to read. */}
      <aside
        ref={col}
        className="hidden min-h-0 shrink-0 flex-col border-l border-border bg-panel md:flex"
        style={{ width }}
      >
        <div className="flex shrink-0 items-baseline gap-2 border-b border-border px-2.5 py-1.5">
          <Link href="/monitor" className="text-xs font-semibold hover:text-accent">
            City monitor
          </Link>
          <span className="text-[10px] text-muted">
            {newest ? fmtAge(new Date(newest).toISOString()) : "no readings"}
          </span>
          <span className="flex-1" />
          <button onClick={toggle} className="text-[10px] text-muted hover:text-text">hide</button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {liveQ.error ? (
            <p className="px-2.5 py-3 text-[11px] leading-relaxed text-bad">{liveQ.error}</p>
          ) : rows.length === 0 ? (
            <p className="px-2.5 py-3 text-[11px] leading-relaxed text-muted">
              No live readings yet. n8n <b>P1.2</b> writes the US cities and <b>P1.5</b> writes
              every city.
            </p>
          ) : (
            rows.map(({ r, t }) => {
              const unit = unitBy.get(r.city_key) ?? "C";
              const rate = t?.slope_3_c_per_h ?? null;
              const arrow = rate === null ? "" : rate > 0.15 ? "▲" : rate < -0.15 ? "▼" : "→";
              const tone = rate === null ? "text-muted"
                : rate > 0.15 ? "text-good" : rate < -0.15 ? "text-bad" : "text-muted";
              const toGo = t?.typical_climb_left_c ?? null;
              return (
                <Link
                  key={r.city_key}
                  href={`/monitor?city=${encodeURIComponent(r.city_key)}`}
                  className={`block border-b border-border px-2.5 py-1.5 hover:bg-panel2 ${
                    r.peak_window_state === "INSIDE" ? "border-l-2 border-l-accent" : ""
                  } ${r.day_decided ? "opacity-60" : ""}`}
                >
                  <div className="flex items-baseline gap-1.5">
                    <span className="truncate text-xs">{r.city_key}</span>
                    <span className="flex-1" />
                    <span className="text-xs tabular-nums">{fmtTemp(r.temp_c, unit, 1)}</span>
                    <span className={`text-[10px] tabular-nums ${tone}`}>
                      {arrow}
                      {rate === null ? "" : ` ${Math.abs(rate).toFixed(1)}/h`}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-baseline gap-2 text-[10px] text-muted">
                    <span>max {fmtTemp(r.running_max_c, unit, 1)}</span>
                    {toGo !== null && toGo > 0.1 && !r.day_decided && (
                      <span className="text-warn">+{toGo.toFixed(1)} to go</span>
                    )}
                    {r.day_decided && <span>decided</span>}
                    {r.source_kind === "model" && (
                      <span className="rounded bg-warn/15 px-1 text-warn" title="Interpolated by a model, not measured at the station">
                        model
                      </span>
                    )}
                    <span className="flex-1" />
                    <span>{fmtAge(r.observed_at)}</span>
                  </div>
                </Link>
              );
            })
          )}
        </div>

        <div className="shrink-0 border-t border-border">
          <div className="px-2.5 py-1 text-[10px] uppercase tracking-widest text-muted">
            Events
          </div>
          <div className="max-h-40 overflow-y-auto">
            {(eventsQ.data ?? []).length === 0 ? (
              <p className="px-2.5 pb-2 text-[11px] text-muted">Nothing fired yet.</p>
            ) : (
              (eventsQ.data ?? []).map((e) => (
                <div key={e.event_id} className="border-t border-border px-2.5 py-1 text-[10px]">
                  <span className={severityColor(e.severity)}>{e.kind}</span>{" "}
                  <span className="text-text">{e.city_key}</span>{" "}
                  <span className="text-muted">{fmtAge(e.detected_at)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
