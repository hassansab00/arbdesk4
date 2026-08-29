"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { regionFromLonLat, utcOffsetHours, type Region } from "@/lib/region";
import type { City } from "@/lib/types";

interface ClusterCity extends City {
  volume24h: number;
  netEdge: number | null;
  peakWindowState: string | null;
  peakHourLocal: number | null;
  windowWidthH: number | null;
}

const REGIONS: Region[] = ["Americas", "Europe/Africa", "West Asia", "East Asia", "Oceania"];

export default function ClustersPage() {
  const [cities, setCities] = useState<ClusterCity[]>([]);
  const [now, setNow] = useState(new Date());
  const [regionFilter, setRegionFilter] = useState<Region | "ALL">("ALL");

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    async function load() {
      const { data: cityRows } = await supabase.from("cities").select("*").eq("status", "active");
      const base = (cityRows as City[]) ?? [];

      const today = new Date().toISOString().slice(0, 10);
      const { data: volRows } = await supabase.from("derived_city_day_volume").select("city_key,volume_usd").eq("trade_date", today);
      const volByCity = new Map((volRows ?? []).map((r: any) => [r.city_key, r.volume_usd as number]));

      const { data: edgeRows } = await supabase.from("v_opportunities").select("city_key,edge_net_pp").eq("tradeable", true);
      const edgeByCity = new Map<string, number[]>();
      for (const r of edgeRows ?? []) {
        const arr = edgeByCity.get((r as any).city_key) ?? [];
        arr.push((r as any).edge_net_pp);
        edgeByCity.set((r as any).city_key, arr);
      }

      const { data: liveRows } = await supabase.from("live_weather").select("city_key,peak_window_state");
      const liveByCity = new Map((liveRows ?? []).map((r: any) => [r.city_key, r.peak_window_state]));

      const month = new Date().getMonth() + 1;
      const { data: peakRows } = await supabase.from("derived_weather_peak").select("*").eq("month", month);
      const peakByCity = new Map((peakRows ?? []).map((r: any) => [r.city_key, r]));

      const merged: ClusterCity[] = base.map((c) => {
        const edges = edgeByCity.get(c.city_key) ?? [];
        const peak = peakByCity.get(c.city_key);
        return {
          ...c,
          volume24h: volByCity.get(c.city_key) ?? 0,
          netEdge: edges.length ? edges.reduce((s, v) => s + v, 0) / edges.length : null,
          peakWindowState: liveByCity.get(c.city_key) ?? null,
          peakHourLocal: peak?.peak_hour_local ?? peak?.peak_hour ?? null,
          windowWidthH: peak?.window_width_h ?? null,
        };
      });
      setCities(merged);
    }
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const filtered = useMemo(
    () => cities.filter((c) => regionFilter === "ALL" || regionFromLonLat(c.longitude, c.latitude) === regionFilter),
    [cities, regionFilter]
  );

  const maxVolume = Math.max(1, ...cities.map((c) => c.volume24h));

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">City Clusters</h1>
      <p className="text-xs text-muted">
        Node size = 24h volume, colour = current net edge, pulse = peak window currently open.
        Region is derived from longitude (no explicit region column in the schema) - see lib/region.ts.
      </p>

      <div className="flex flex-wrap gap-2 text-xs">
        <FilterChip active={regionFilter === "ALL"} onClick={() => setRegionFilter("ALL")} label="All regions" />
        {REGIONS.map((r) => <FilterChip key={r} active={regionFilter === r} onClick={() => setRegionFilter(r)} label={r} />)}
      </div>

      <div className="rounded border border-border bg-panel p-2">
        <svg viewBox="0 0 360 180" className="w-full" style={{ maxHeight: 420 }}>
          <rect x={0} y={0} width={360} height={180} fill="#0e1219" />
          {[...Array(6)].map((_, i) => <line key={i} x1={0} y1={(i * 180) / 6} x2={360} y2={(i * 180) / 6} stroke="#1a2030" />)}
          {[...Array(12)].map((_, i) => <line key={i} x1={(i * 360) / 12} y1={0} x2={(i * 360) / 12} y2={180} stroke="#1a2030" />)}
          {filtered.map((c) => {
            if (c.longitude === null || c.latitude === null) return null;
            const x = ((c.longitude + 180) / 360) * 360;
            const y = ((90 - c.latitude) / 180) * 180;
            const r = 2 + 6 * Math.sqrt(c.volume24h / maxVolume);
            const color = c.netEdge === null ? "#8a93a6" : c.netEdge > 0 ? "#2ecc71" : "#ff5c5c";
            const pulsing = c.peakWindowState === "INSIDE";
            return (
              <g key={c.city_key}>
                {pulsing && <circle cx={x} cy={y} r={r + 3} fill="none" stroke={color} strokeWidth={0.5} opacity={0.6} className="peak-pulse" />}
                <circle cx={x} cy={y} r={r} fill={color} opacity={0.85} />
                <title>{c.display_name ?? c.city_key} · vol {Math.round(c.volume24h)} · edge {c.netEdge?.toFixed(3) ?? "—"}</title>
              </g>
            );
          })}
        </svg>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">24h UTC timeline - peak windows vs. now</h2>
        <div className="max-h-96 overflow-y-auto rounded border border-border bg-panel p-2">
          <div className="relative mb-1 h-4 text-[10px] text-muted">
            {[...Array(25)].map((_, h) => (
              <span key={h} style={{ position: "absolute", left: `${(h / 24) * 100}%` }}>{h}</span>
            ))}
          </div>
          <div className="relative">
            <div
              className="absolute top-0 bottom-0 w-px bg-accent z-10"
              style={{ left: `${((now.getUTCHours() + now.getUTCMinutes() / 60) / 24) * 100}%` }}
            />
            {filtered.map((c) => {
              const offset = utcOffsetHours(c.timezone);
              if (c.peakHourLocal === null || c.windowWidthH === null) {
                return (
                  <div key={c.city_key} className="flex items-center gap-2 py-0.5 text-[10px]">
                    <span className="w-24 shrink-0 truncate text-muted">{c.display_name ?? c.city_key}</span>
                    <span className="text-muted">no measured window this month</span>
                  </div>
                );
              }
              const startLocal = c.peakHourLocal - c.windowWidthH / 2;
              const startUtc = ((startLocal - offset) % 24 + 24) % 24;
              const widthPct = Math.min(100, (c.windowWidthH / 24) * 100);
              return (
                <div key={c.city_key} className="flex items-center gap-2 py-0.5">
                  <span className="w-24 shrink-0 truncate text-[10px] text-muted">{c.display_name ?? c.city_key}</span>
                  <div className="relative h-2 flex-1 rounded bg-panel2">
                    <div
                      className="absolute h-2 rounded bg-accent/60"
                      style={{ left: `${(startUtc / 24) * 100}%`, width: `${widthPct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}

function FilterChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button onClick={onClick} className={`rounded-full border px-3 py-1 ${active ? "border-accent text-accent" : "border-border text-muted"}`}>
      {label}
    </button>
  );
}
