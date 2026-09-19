"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import type { CityStats } from "@/lib/types";

/**
 * City rows, from v_city_stats where it exists and from the base tables where
 * it does not.
 *
 * This exists because of a mistake worth naming. Three surfaces - City
 * Clusters, Analytics and City Watch - were written against v_city_stats,
 * which sql/ad4_17_city_stats.sql creates. On a database where that file has
 * not been run, PostgREST answers "relation does not exist" and all three went
 * red or blank. The user could not tell a missing SQL file from a broken page,
 * and the pages showed NOTHING even though `cities`, `live_weather`,
 * `weather_forecasts` and `v_city_volume` were all sitting there full of the
 * data those pages mostly wanted.
 *
 * A page must not require a migration to render. The view is an optimisation -
 * it computes the climatological normal in Postgres instead of pulling every
 * observation into the browser - and everything else it returns is a join over
 * tables that have existed since Phase 0. So: try the view, and if it is not
 * there, do the join client-side and leave the climate fields null. Hotness
 * then reads "—" with one line saying which file supplies it, and every other
 * figure on the page works.
 */

export type StatsMode = "view" | "fallback" | "empty";
export type FallbackReason = "missing" | "timeout" | "other" | null;

export interface CityStatsResult {
  rows: CityStats[];
  mode: StatsMode;
  /** Why the view was not used, so the UI can name the right fix. */
  reason: FallbackReason;
  /** The real Postgres message when the view was unusable, for the UI to show. */
  viewError: string | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

// Any failure of the view falls back, not just a missing one. The view timed
// out in production - "canceling statement due to statement timeout" - and
// because that message is not "does not exist", the fallback did not fire and
// the page showed a red box instead of the temperatures it could have shown
// from the base tables. The reason the view failed is worth REPORTING, but it
// is never a reason to render nothing.
const MISSING = /does not exist|could not find|schema cache|not found/i;
const TIMEOUT = /timeout|canceling statement|too long/i;

export function useCityStats(pollMs = 60000): CityStatsResult {
  const [rows, setRows] = useState<CityStats[]>([]);
  const [mode, setMode] = useState<StatsMode>("empty");
  const [viewError, setViewError] = useState<string | null>(null);
  const [reason, setReason] = useState<FallbackReason>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const { data, error: e } = await supabase.from("v_city_stats").select("*");
        if (!e && data) {
          if (!cancelled) {
            setRows(data as CityStats[]);
            setMode(data.length ? "view" : "empty");
            setViewError(null);
            setReason(null);
            setError(null);
          }
          return;
        }
        // The view is missing (or unreadable). Build the same shape from the
        // tables, minus the climate columns only it can compute.
        const emsg = e?.message ?? "v_city_stats unavailable";
        if (!cancelled) {
          setViewError(emsg);
          setReason(MISSING.test(emsg) ? "missing" : TIMEOUT.test(emsg) ? "timeout" : "other");
        }

        const today = new Date().toISOString().slice(0, 10);
        const [cities, live, fc, vol] = await Promise.all([
          supabase.from("cities").select("city_key,display_name,icao,timezone,unit,latitude,longitude"),
          supabase.from("live_weather").select("city_key,temp_c,running_max_c,peak_window_state,day_decided,observed_at"),
          // Shortest lead first, THEN newest run. Ordering by run_at alone
          // picks a seven-day-lead guess issued a week ago over this
          // morning's one-day forecast, because the previous-runs archive
          // stores both and the old row is a perfectly valid "newest run for
          // that lead". Same bug the view had.
          supabase.from("weather_forecasts")
            .select("city_key,forecast_max_c,model,run_at,for_date,lead_days")
            .eq("for_date", today)
            .order("lead_days", { ascending: true, nullsFirst: false })
            .order("run_at", { ascending: false }).limit(2000),
          supabase.from("v_city_volume").select("city_key,volume_usd,n_trades"),
        ]);
        if (cities.error) throw cities.error;

        // AND SAY SO IF THE SERVER CUT IT. This path is the fallback, so it
        // cannot use useQuery's truncation flag, and PostgREST's ceiling is
        // 1,000 rows however large a .limit() asks for. 54 cities x 3 models
        // x 8 leads is about 1,300 forecast rows for one day, so the cut is
        // reachable - and a page that quietly shows the alphabetically first
        // cities is worse than one that errors.
        if ((fc.data ?? []).length >= 2000 || (fc.data ?? []).length === 1000) {
          if (!cancelled) {
            setViewError((prev) => [prev, "Forecast rows were cut short by the "
              + "server's 1,000-row ceiling, so some cities may be missing a "
              + "forecast here. The city stats view does not have this limit - "
              + "fix it and this fallback goes away."].filter(Boolean).join(" "));
          }
        }

        const liveBy = new Map((live.data ?? []).map((r: Record<string, unknown>) => [r.city_key as string, r]));
        const volBy = new Map((vol.data ?? []).map((r: Record<string, unknown>) => [r.city_key as string, r]));
        const fcBy = new Map<string, Record<string, unknown>>();
        for (const f of (fc.data ?? []) as Array<Record<string, unknown>>) {
          if (!fcBy.has(f.city_key as string)) fcBy.set(f.city_key as string, f);
        }

        const merged: CityStats[] = ((cities.data ?? []) as Array<Record<string, unknown>>).map((c) => {
          const l = liveBy.get(c.city_key as string) ?? {};
          const f = fcBy.get(c.city_key as string) ?? {};
          const v = volBy.get(c.city_key as string) ?? {};
          return {
            city_key: c.city_key as string,
            display_name: (c.display_name as string) ?? null,
            icao: (c.icao as string) ?? null,
            timezone: (c.timezone as string) ?? null,
            unit: ((c.unit as string) ?? "C") as "C" | "F",
            latitude: (c.latitude as number) ?? null,
            longitude: (c.longitude as number) ?? null,
            // Only the view can compute these - it needs a full pass over
            // weather_observations. Null, and the UI says why.
            baseline: null, baseline_days: null, normal_max_c: null,
            volatility_c: null, anomaly_c: null, hotness_sigma: null,
            mae_c: null, bias_c: null, skill_days: null,
            model_spread_c: null, n_models: null, sigma_multiplier: null,
            depth_5c: null, live_bands: null,
            n_tradeable: null, best_edge_pp: null, avg_edge_pp: null,
            peak_hour_local: null, window_width_h: null,
            // ...but everything below is on tables that always exist.
            now_c: (l.temp_c as number) ?? null,
            running_max_c: (l.running_max_c as number) ?? null,
            forecast_max_c: (f.forecast_max_c as number) ?? null,
            forecast_model: (f.model as string) ?? null,
            forecast_lead_days: (f.lead_days as number) ?? null,
            forecast_at: (f.run_at as string) ?? null,
            volume_24h: (v.volume_usd as number) ?? 0,
            n_trades_24h: (v.n_trades as number) ?? 0,
            peak_window_state: (l.peak_window_state as string) ?? null,
            day_decided: (l.day_decided as boolean) ?? null,
            observed_at: (l.observed_at as string) ?? null,
          };
        });

        if (!cancelled) {
          setRows(merged);
          setMode(merged.length ? "fallback" : "empty");
          setError(null);
        }
      } catch (e) {
        const msg = e && typeof e === "object" && "message" in e
          ? String((e as { message: unknown }).message) : String(e);
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    const t = pollMs > 0 ? setInterval(load, pollMs) : null;
    return () => { cancelled = true; if (t) clearInterval(t); };
  }, [pollMs, tick]);

  return { rows, mode, reason, viewError, loading, error, refresh: () => setTick((n) => n + 1) };
}
