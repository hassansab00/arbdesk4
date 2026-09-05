"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { useCityStats } from "@/lib/useCityStats";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPrice, fmtPp } from "@/lib/format";
import { fmtTemp, fmtTempDelta, fmtBandRange, type Unit } from "@/lib/units";
import { fmtDaysAhead } from "@/lib/time";
import { heatColor, heatWord } from "@/lib/heat";
import type { CityStats, Opportunity } from "@/lib/types";

/** Only the columns this panel reads - a narrowed select does not satisfy
 *  the full Opportunity shape, and pretending otherwise would be a lie tsc
 *  is right to reject. */
type WatchBand = Pick<
  Opportunity,
  "band_id" | "city_key" | "side" | "band_lo" | "band_hi" | "open_low" | "open_high"
  | "band_label" | "unit" | "model_prob" | "market_price" | "edge_net_pp"
  | "resolution_date" | "volume_usd" | "tradeable"
>;

/**
 * City Watch: a standing glance at the handful of cities you actually care
 * about.
 *
 * The rest of the desk is organised by QUESTION - where is the best edge, what
 * is this market's ladder, how hot is everywhere. None of those answer "what
 * are my four cities doing", which is the thing you check between other tasks.
 *
 * One line per watched city, and every figure on it is a decision input:
 *
 *   the band most likely to settle, and what the market charges for it -
 *     that pair IS the trade, compressed to two numbers
 *   the forecast maximum against where the day has already got to
 *   how far today is from that city's own normal
 *   whether there is enough volume for any of it to matter
 *
 * The selection lives in this browser only. It is a personal view of a shared
 * database, and syncing it would mean writing to `settings` from the browser -
 * which the RLS boundary rightly refuses.
 */

const KEY = "ad4-city-watch";
const MAX = 8;

function loadWatch(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.filter((x) => typeof x === "string").slice(0, MAX) : [];
  } catch {
    return [];   // private window, blocked storage - an empty watchlist is fine
  }
}

function saveWatch(keys: string[]) {
  try { localStorage.setItem(KEY, JSON.stringify(keys.slice(0, MAX))); } catch { /* not fatal */ }
}

export default function CityWatch() {
  const [watch, setWatch] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setWatch(loadWatch());
    setHydrated(true);
    // Live Weather writes the same key. Without this, starring a city there
    // leaves this panel showing a stale list until the next reload - two views
    // of one list disagreeing, which is worse than not sharing it at all.
    const sync = (e: StorageEvent) => { if (!e.key || e.key === KEY) setWatch(loadWatch()); };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  // Resilient: works from `cities` + `live_weather` when v_city_stats is
  // absent, so the selector is never empty just because a migration is pending.
  const statsQ = useCityStats(120000);
  const oppQ = useQuery<WatchBand[]>(
    () =>
      supabase
        .from("v_opportunities")
        .select("band_id,city_key,side,band_lo,band_hi,open_low,open_high,band_label,unit,model_prob,market_price,edge_net_pp,resolution_date,volume_usd,tradeable")
        .eq("side", "YES")
        .limit(2000),
    [],
    60000
  );

  const stats = statsQ.rows;
  const byCity = useMemo(() => new Map(stats.map((s) => [s.city_key, s])), [stats]);

  // The band the model thinks is most likely, per city, on the nearest day.
  const topBand = useMemo(() => {
    const best = new Map<string, WatchBand>();
    for (const o of oppQ.data ?? []) {
      if (o.model_prob === null) continue;
      const cur = best.get(o.city_key);
      // nearest settlement date wins; within a date, highest model probability
      if (!cur || o.resolution_date < cur.resolution_date ||
          (o.resolution_date === cur.resolution_date && (o.model_prob ?? 0) > (cur.model_prob ?? 0))) {
        best.set(o.city_key, o);
      }
    }
    return best;
  }, [oppQ.data]);

  const allRows = watch.map((k) => byCity.get(k)).filter(Boolean) as CityStats[];
  const VISIBLE = 5;
  const rows = showAll ? allRows : allRows.slice(0, VISIBLE);
  const available = useMemo(
    () => [...stats].sort((a, b) => (a.display_name ?? a.city_key).localeCompare(b.display_name ?? b.city_key)),
    [stats]
  );

  function toggle(k: string) {
    setWatch((w) => {
      const next = w.includes(k) ? w.filter((x) => x !== k) : w.length >= MAX ? w : [...w, k];
      saveWatch(next);
      return next;
    });
  }

  if (!hydrated) return null;   // avoid a hydration mismatch on localStorage

  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-border">
      <div className="flex shrink-0 items-center justify-between px-3 py-2">
        <div>
          <span className="text-sm font-semibold">City Watch</span>
          <span className="ml-2 text-[11px] text-muted">
            {watch.length ? `${watch.length} watched` : "pick your cities"}
          </span>
        </div>
        <button className="text-xs text-muted hover:text-text" onClick={() => setEditing((e) => !e)}>
          {editing ? "done" : "edit"}
        </button>
      </div>

      {editing && (
        <div className="border-b border-border bg-panel2 px-2 pb-2">
          <p className="px-1 pb-1.5 text-[10px] leading-relaxed text-muted">
            Up to {MAX}. Saved in this browser only — a personal view, not a database setting.
          </p>
          {available.length === 0 ? (
            <p className="px-1 text-[11px] text-muted">
              {statsQ.loading
                ? "Loading cities…"
                : statsQ.error
                ? `Could not read cities: ${statsQ.error}`
                : "The cities table is empty — that is Phase 0 data."}
            </p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {available.map((c) => {
                const on = watch.includes(c.city_key);
                return (
                  <button
                    key={c.city_key}
                    onClick={() => toggle(c.city_key)}
                    disabled={!on && watch.length >= MAX}
                    className={`rounded border px-1.5 py-0.5 text-[10px] transition disabled:opacity-30 ${
                      on ? "border-accent bg-accent/10 text-accent" : "border-border text-muted hover:text-text"
                    }`}
                  >
                    {c.display_name ?? c.city_key}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
        {allRows.length === 0 && !editing && (
          <div className="rounded border border-dashed border-border p-3 text-[11px] leading-relaxed text-muted">
            <div className="font-semibold text-text">Nothing watched yet</div>
            Pick a few cities and this becomes a standing glance at them — the band most likely to
            settle, what it costs, the forecast against where the day has got to, and whether there
            is volume behind it.
            <button
              onClick={() => setEditing(true)}
              className="mt-1.5 block rounded border border-accent px-2 py-0.5 text-[10px] text-accent hover:bg-accent/10"
            >
              Choose cities
            </button>
          </div>
        )}

        {rows.map((c) => {
          const unit = (c.unit ?? "C") as Unit;
          const top = topBand.get(c.city_key);
          const band = top
            ? top.band_label ?? fmtBandRange(top.band_lo, top.band_hi, unit, top.open_low, top.open_high)
            : null;
          const edge = top?.edge_net_pp ?? null;
          return (
            <div key={c.city_key} className="rounded border border-border bg-panel2 p-2 text-[11px]">
              {/* city + how unusual today is there */}
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate font-semibold text-text">{c.display_name ?? c.city_key}</span>
                <span className="shrink-0 font-mono" style={{ color: heatColor(c.hotness_sigma) }} title={heatWord(c.hotness_sigma)}>
                  {c.anomaly_c === null ? "—" : `${fmtTempDelta(c.anomaly_c, unit)}`}
                  {c.hotness_sigma !== null && (
                    <span className="ml-1 opacity-70">{c.hotness_sigma > 0 ? "+" : ""}{c.hotness_sigma.toFixed(1)}σ</span>
                  )}
                </span>
              </div>

              {/* the trade, compressed: most likely band and what it costs */}
              {top ? (
                <div className="mt-1 flex items-baseline justify-between gap-2 rounded bg-panel px-1.5 py-1">
                  <span className="truncate">
                    <span className="text-muted">most likely </span>
                    <span className="font-mono text-accent">{band}</span>
                  </span>
                  <span className="shrink-0 font-mono">
                    {fmtPct(top.model_prob, 0)}
                    <span className="mx-1 text-muted">vs</span>
                    {fmtPrice(top.market_price)}
                    {edge !== null && (
                      <span className={`ml-1 ${edge > 0 ? "text-good" : "text-muted"}`}>{fmtPp(edge)}</span>
                    )}
                  </span>
                </div>
              ) : (
                <div className="mt-1 rounded bg-panel px-1.5 py-1 text-muted">no priced bands</div>
              )}

              {/* the weather that decides it */}
              <div className="mt-1 grid grid-cols-3 gap-1 font-mono text-[10px]">
                <span title="Latest observation">
                  <span className="text-muted">now </span>{fmtTemp(c.now_c, unit)}
                </span>
                <span title="Highest reading so far today">
                  <span className="text-muted">max </span>{fmtTemp(c.running_max_c, unit)}
                </span>
                <span title={c.forecast_model ? `forecast model: ${c.forecast_model}` : "no forecast for today"}>
                  <span className="text-muted">fcst </span>
                  <span className={c.forecast_max_c === null ? "text-muted" : "text-text"}>
                    {fmtTemp(c.forecast_max_c, unit)}
                  </span>
                </span>
              </div>

              <div className="mt-1 flex items-center justify-between text-[10px] text-muted">
                <span>
                  {fmtCompactUsd(c.volume_24h)} 24h
                  {top ? ` · settles ${fmtDaysAhead(top.resolution_date)}` : ""}
                </span>
                <span className="flex items-center gap-1.5">
                  {c.peak_window_state === "INSIDE" && <span className="text-accent">peak open</span>}
                  {c.day_decided && <span>decided</span>}
                  {/* An hours-old reading beside a live-looking price is how a
                      stale number gets traded on. Say it in colour. */}
                  <span
                    className={
                      !c.observed_at ? "text-bad"
                      : Date.now() - new Date(c.observed_at).getTime() > 3 * 3600_000 ? "text-bad"
                      : Date.now() - new Date(c.observed_at).getTime() > 90 * 60_000 ? "text-warn"
                      : "text-muted"
                    }
                    title={
                      "Age of the newest observation for this city. Anything over about 90 minutes " +
                      "means the running max may already have moved; hours means the weather feed is not running."
                    }
                  >
                    {fmtAge(c.observed_at)}
                  </span>
                </span>
              </div>
            </div>
          );
        })}

        {allRows.length > VISIBLE && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="w-full rounded border border-border py-1.5 text-[10px] text-muted hover:border-accent hover:text-accent"
          >
            {showAll ? "Show fewer" : `Show ${allRows.length - VISIBLE} more`}
          </button>
        )}
      </div>
    </div>
  );
}
