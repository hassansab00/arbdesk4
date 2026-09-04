"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import Brand from "@/components/Brand";
import { displayTz, setDisplayTz, tzLabel } from "@/lib/time";
import { fmtAge } from "@/lib/format";

/**
 * The masthead: what the desk is, and whether it is actually alive.
 *
 * The right-hand side answers the question every stale page raises and none
 * of them answered: IS DATA ARRIVING? Books, weather and forecasts each have
 * a job that writes them, and each goes quiet in its own way - a workflow
 * paused, an Action failing, a swap half-done. Reading "Chicago only" or an
 * empty event feed and having to guess which of those it was is the thing
 * that made the whole desk feel broken.
 *
 * Freshness thresholds are the cadence of the job that writes each table,
 * doubled: late enough to mean something, early enough to catch a stall
 * before a trading day is lost.
 */
type Feed = { label: string; at: string | null; staleAfterMin: number; what: string };

/** What the desk is looking at right now, for the line under the wordmark. */
interface Scope { cities: number; markets: number; days: number; edges: number }

export default function Header() {
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [tz, setTz] = useState<string>("UTC");
  const [picking, setPicking] = useState(false);
  const [scope, setScope] = useState<Scope | null>(null);

  useEffect(() => {
    setTz(displayTz());
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    async function load() {
      const newest = async (table: string, col: string) => {
        try {
          const { data } = await supabase.from(table).select(col).order(col, { ascending: false }).limit(1);
          const row = (data as Array<Record<string, string>> | null)?.[0];
          return row ? row[col] ?? null : null;
        } catch {
          return null;
        }
      };
      const [books, weather, forecasts] = await Promise.all([
        newest("book_snapshots", "observed_at"),
        newest("live_weather", "updated_at"),
        newest("weather_forecasts", "run_at"),
      ]);
      // The masthead line: how much market this desk is actually covering.
      try {
        const today = new Date().toISOString().slice(0, 10);
        const [{ count: cityCount }, mkt, { count: edgeCount }] = await Promise.all([
          supabase.from("cities").select("city_key", { count: "exact", head: true }),
          supabase.from("markets").select("market_id,resolution_date").gte("resolution_date", today).limit(2000),
          supabase.from("v_opportunities").select("edge_id", { count: "exact", head: true }).eq("tradeable", true),
        ]);
        const dates = new Set((mkt.data ?? []).map((m: { resolution_date: string }) => m.resolution_date));
        setScope({
          cities: cityCount ?? 0,
          markets: (mkt.data ?? []).length,
          days: dates.size,
          edges: edgeCount ?? 0,
        });
      } catch {
        setScope(null);
      }

      setFeeds([
        { label: "Books", at: books, staleAfterMin: 120,
          what: "order books from Polymarket — P0.3, hourly" },
        { label: "Weather", at: weather, staleAfterMin: 60,
          what: "current readings — Live Weather Action (15 min) and P1.2 (2h)" },
        { label: "Forecast", at: forecasts, staleAfterMin: 12 * 60,
          what: "forecast runs — Open-Meteo Action and P1.3" },
      ]);
    }
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  const stale = (f: Feed) =>
    !f.at || Date.now() - new Date(f.at).getTime() > f.staleAfterMin * 60_000;
  const down = feeds?.filter(stale) ?? [];

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-base px-3 py-2 sm:px-4">
      <Brand
        subtitle={
          scope
            ? `Polymarket daily-temperature markets · ${scope.cities} cities · ` +
              `${scope.markets} live over ${scope.days} settlement day${scope.days === 1 ? "" : "s"} · ` +
              `${scope.edges} tradeable edge${scope.edges === 1 ? "" : "s"}`
            : null
        }
      />

      <div className="flex items-center gap-4">
        {feeds && (
          <div className="hidden items-center gap-3 md:flex">
            {feeds.map((f) => (
              <div key={f.label} className="flex items-center gap-1.5" title={`${f.what}\nlast write: ${f.at ?? "never"}`}>
                <span
                  className={`inline-block h-1.5 w-1.5 rounded-full ${
                    stale(f) ? (f.at ? "bg-warn" : "bg-bad") : "bg-good"
                  }`}
                />
                <span className="font-mono text-[11px] text-muted">
                  {f.label} <span className={stale(f) ? "text-warn" : "text-text"}>{fmtAge(f.at)}</span>
                </span>
              </div>
            ))}
          </div>
        )}

        {feeds && down.length > 0 && (
          <span className="rounded border border-warn/50 bg-warn/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-warn md:hidden">
            {down.length} feed{down.length > 1 ? "s" : ""} stale
          </span>
        )}

        <div className="relative">
          <button
            onClick={() => setPicking((p) => !p)}
            className="rounded border border-border px-2 py-1 font-mono text-xs text-text hover:border-accent"
            title="Every timestamp on the desk is shown in this timezone. Weather clocks stay local to their city."
          >
            {now
              ? new Intl.DateTimeFormat("en-GB", {
                  timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
                }).format(now)
              : "--:--:--"}
            <span className="ml-1.5 text-muted">{tzLabel(tz)}</span>
          </button>
          {picking && (
            <div className="absolute right-0 z-30 mt-1 w-64 rounded border border-border bg-panel p-2 text-xs shadow-lg">
              <div className="mb-1 text-muted">Show all timestamps in</div>
              <select
                className="w-full rounded border border-border bg-panel2 px-1.5 py-1 text-text"
                value={tz}
                onChange={(e) => { setDisplayTz(e.target.value); setTz(e.target.value); setPicking(false); }}
              >
                {["Asia/Beirut", "UTC", "America/New_York", "America/Chicago",
                  "America/Denver", "America/Los_Angeles", "Europe/London",
                  "Europe/Paris", "Asia/Dubai", "Asia/Tokyo", "Australia/Sydney",
                ].map((z) => <option key={z} value={z}>{z}</option>)}
              </select>
              <p className="mt-2 leading-relaxed text-muted">
                Peak windows and resolution dates stay on the <b>city&apos;s</b> clock —
                a daily maximum happens in that city&apos;s afternoon, wherever you are.
              </p>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
