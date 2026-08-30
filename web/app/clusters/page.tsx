"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { fmtCompactUsd } from "@/lib/format";
import { regionFromLonLat, utcOffsetHours, type Region } from "@/lib/region";
import type { City } from "@/lib/types";

interface ClusterCity extends City {
  volume24h: number;
  nTrades24h: number;
  netEdge: number | null;
  peakWindowState: string | null;
  peakHourLocal: number | null;
  windowWidthH: number | null;
}

const REGIONS: Region[] = ["Americas", "Europe/Africa", "West Asia", "East Asia", "Oceania"];

export default function ClustersPage() {
  const [now, setNow] = useState(new Date());
  const [regionFilter, setRegionFilter] = useState<Region | "ALL">("ALL");

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  const cityQ = useQuery<City[]>(() => supabase.from("cities").select("*").eq("status", "active"), [], 60000);
  // 24h rolling traded volume, not the calendar-day total: a city that was
  // busy overnight is liquid now, and `trade_date` would hide that.
  const volQ = useQuery<Array<{ city_key: string; volume_usd: number; n_trades: number }>>(
    () => supabase.from("v_city_volume").select("city_key,volume_usd,n_trades"), [], 60000
  );
  const edgeQ = useQuery<Array<{ city_key: string; edge_net_pp: number }>>(
    () => supabase.from("v_opportunities").select("city_key,edge_net_pp").eq("tradeable", true), [], 60000
  );
  const liveQ = useQuery<Array<{ city_key: string; peak_window_state: string | null }>>(
    () => supabase.from("live_weather").select("city_key,peak_window_state"), [], 60000
  );
  const peakQ = useQuery<Array<Record<string, unknown>>>(
    () => supabase.from("derived_weather_peak").select("*").eq("month", new Date().getMonth() + 1), []
  );

  const cities: ClusterCity[] = useMemo(() => {
    const base = cityQ.data ?? [];
    const volByCity = new Map((volQ.data ?? []).map((r) => [r.city_key, r]));
    const edgeByCity = new Map<string, number[]>();
    for (const r of edgeQ.data ?? []) {
      const arr = edgeByCity.get(r.city_key) ?? [];
      arr.push(r.edge_net_pp);
      edgeByCity.set(r.city_key, arr);
    }
    const liveByCity = new Map((liveQ.data ?? []).map((r) => [r.city_key, r.peak_window_state]));
    const peakByCity = new Map((peakQ.data ?? []).map((r) => [r.city_key as string, r]));
    return base.map((c) => {
      const edges = edgeByCity.get(c.city_key) ?? [];
      const peak = peakByCity.get(c.city_key) as any;
      const vol = volByCity.get(c.city_key);
      return {
        ...c,
        volume24h: vol?.volume_usd ?? 0,
        nTrades24h: vol?.n_trades ?? 0,
        netEdge: edges.length ? edges.reduce((s, v) => s + v, 0) / edges.length : null,
        peakWindowState: liveByCity.get(c.city_key) ?? null,
        peakHourLocal: peak?.peak_hour_local ?? peak?.peak_hour ?? null,
        windowWidthH: peak?.window_width_h ?? null,
      };
    });
  }, [cityQ.data, volQ.data, edgeQ.data, liveQ.data, peakQ.data]);

  const filtered = useMemo(
    () => cities.filter((c) => regionFilter === "ALL" || regionFromLonLat(c.longitude, c.latitude) === regionFilter),
    [cities, regionFilter]
  );

  const maxVolume = Math.max(1, ...cities.map((c) => c.volume24h));

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">City Clusters</h1>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        Node size = 24h traded volume, colour = current net edge, pulse = peak window currently open.
        Volume is the rolling 24h figure from <code>trades_observed</code> (via{" "}
        <code>v_city_volume</code>), so a city that traded heavily overnight still reads as liquid.
        Region is derived from longitude — there is no region column in the schema; see{" "}
        <code>lib/region.ts</code>.
      </p>
      <InlineError message={volQ.error ?? edgeQ.error ?? liveQ.error ?? peakQ.error} />

      <div className="flex flex-wrap gap-2 text-xs">
        <FilterChip active={regionFilter === "ALL"} onClick={() => setRegionFilter("ALL")} label="All regions" />
        {REGIONS.map((r) => <FilterChip key={r} active={regionFilter === r} onClick={() => setRegionFilter(r)} label={r} />)}
      </div>

      <DataState
        loading={cityQ.loading}
        error={cityQ.error}
        isEmpty={cities.length === 0}
        emptyTitle="No active cities"
        emptyBody={
          <>
            <code>cities</code> has no rows with <code>status = &apos;active&apos;</code>. The city
            universe is Phase 0 data — load it before anything else here will render.
          </>
        }
        onRetry={cityQ.refresh}
      >
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
                <title>{`${c.display_name ?? c.city_key} · 24h vol ${fmtCompactUsd(c.volume24h)} (${c.nTrades24h} trades) · avg net edge ${c.netEdge?.toFixed(3) ?? "—"}`}</title>
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

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Traded volume by city (24h)</h2>
        {cities.some((c) => c.volume24h > 0) ? (
          <div className="space-y-1 rounded border border-border bg-panel p-3">
            {[...cities]
              .sort((a, b) => b.volume24h - a.volume24h)
              .slice(0, 20)
              .map((c) => (
                <div key={c.city_key} className="flex items-center gap-2 text-[11px]">
                  <span className="w-28 shrink-0 truncate text-muted">{c.display_name ?? c.city_key}</span>
                  <div className="h-2 flex-1 rounded bg-panel2">
                    <div
                      className="h-2 rounded bg-accent/70"
                      style={{ width: `${Math.max(1, (c.volume24h / maxVolume) * 100)}%` }}
                    />
                  </div>
                  <span className="w-16 shrink-0 text-right font-mono">{fmtCompactUsd(c.volume24h)}</span>
                  <span className="w-14 shrink-0 text-right font-mono text-muted">{c.nTrades24h} tr</span>
                </div>
              ))}
          </div>
        ) : (
          <div className="rounded border border-dashed border-border p-4 text-center text-xs text-muted">
            No traded volume in the last 24h. <code>trades_observed</code> is empty or the trade
            ingest has not run — this is where thin-market warnings across the rest of the platform
            come from, so it is worth populating.
          </div>
        )}
      </section>
      </DataState>
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
