"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Globe from "@/components/Globe";
import { useCityStats } from "@/lib/useCityStats";
import StatsNotice from "@/components/StatsNotice";
import { DataState } from "@/components/DataState";
import { heatColor, heatWord, REGION_COLOR } from "@/lib/heat";
import { regionFromCity } from "@/lib/region";
import { fmtCompactUsd, fmtPp } from "@/lib/format";
import type { CityStats } from "@/lib/types";

/**
 * The desk, seen from orbit.
 *
 * This is the same 37 cities the Clusters page ranks, arranged the one way
 * that carries information a table cannot: geographically, under real
 * daylight. The desk trades the daily maximum, and the maximum is made in the
 * hours around each city's solar afternoon - a band that sweeps west around
 * the planet all day. Which cities are inside that band right now is the
 * single most useful geographic fact at any moment, and it is a column of
 * numbers on every other page.
 *
 * The second thing it shows without computing anything: correlation. Cities
 * under one weather system sit together and go hot together. "Ten positions
 * across correlated cities is not ten bets" is a sentence in a risk document
 * until you watch a whole continent turn the same colour.
 */
export default function GlobePage() {
  const router = useRouter();
  const stats = useCityStats(60000);

  const rows = stats.rows ?? [];
  const withCoords = rows.filter((c) => c.latitude != null && c.longitude != null);
  const missing = rows.length - withCoords.length;

  // Where the action is: cities inside their measured peak window, hottest
  // against their own normal first. This is the globe's own list, not a
  // second ranking of the Clusters page.
  const inWindow = useMemo(
    () =>
      rows
        .filter((c) => c.peak_window_state === "INSIDE" && !c.day_decided)
        .sort((a, b) => (b.hotness_sigma ?? -99) - (a.hotness_sigma ?? -99)),
    [rows]
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Globe</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Every city the desk covers, under real daylight. The lit side is daytime <b>now</b>; the
          maxima this desk trades are made in the few hours around each city&apos;s solar afternoon,
          and that band sweeps west all day. A pulsing ring is a city inside its measured peak
          window right now. Colour is hotness against that city&apos;s own normal, in standard
          deviations — the only form comparable between Chicago and Beirut. Drag to rotate; click a
          city to open its monitor.
        </p>
      </div>

      <StatsNotice mode={stats.mode} reason={stats.reason} viewError={stats.viewError} />

      <DataState
        loading={stats.loading}
        error={stats.viewError}
        isEmpty={withCoords.length === 0}
        emptyTitle="No cities with coordinates"
        emptyBody={
          <>
            The globe needs <code className="rounded bg-panel2 px-1">cities.latitude</code> and{" "}
            <code className="rounded bg-panel2 px-1">cities.longitude</code>. Run{" "}
            <code className="rounded bg-panel2 px-1">sql/ad4_00_preflight.sql</code>, which seeds
            them.
          </>
        }
        onRetry={stats.refresh}
      >
        <>
          <Globe
            cities={withCoords}
            height={520}
            onPick={(k) => router.push(`/monitor?city=${encodeURIComponent(k)}`)}
          />

          {/* ---- legend: every channel on the globe, named ------------- */}
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted">
            <span className="flex items-center gap-1.5">
              colour
              {[-2, -1, 0, 1, 2].map((s) => (
                <span
                  key={s}
                  className="inline-block h-2.5 w-4 rounded-sm"
                  style={{ background: heatColor(s) }}
                  title={`${s > 0 ? "+" : ""}${s}σ — ${heatWord(s)}`}
                />
              ))}
              <span>hotness vs its own normal</span>
            </span>
            <span className="flex items-center gap-1.5">
              ring
              {/* "Unknown" is not a place - it is a city with no timezone and no
                  coordinates, which cannot be drawn here at all. The Clusters
                  page leaves it out of its chips for the same reason. */}
              {(Object.entries(REGION_COLOR) as Array<[string, string]>)
                .filter(([r]) => r !== "Unknown")
                .map(([r, c]) => (
                <span key={r} className="inline-flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-full border" style={{ borderColor: c }} />
                  <span>{r}</span>
                </span>
                ))}
            </span>
            <span>size · 24h traded volume</span>
            <span>pulse · peak window open now</span>
            <span className="text-warn">the warm band · local solar 12:00–17:00, where maxima are made</span>
            {missing > 0 && (
              <span className="text-warn">
                {missing} city/cities have no coordinates and cannot be placed
              </span>
            )}
          </div>

          {/* ---- what is happening on the lit side --------------------- */}
          <section className="mt-4">
            <div className="flex flex-wrap items-baseline gap-2">
              <h2 className="text-sm font-semibold">In the window right now</h2>
              <span className="text-xs text-muted">
                {inWindow.length === 0
                  ? "no city is inside its peak window — the desk is between afternoons"
                  : `${inWindow.length} ${inWindow.length === 1 ? "city is" : "cities are"} making today's maximum, hottest against its own normal first`}
              </span>
              <Link href="/clusters" className="ml-auto text-xs text-accent hover:underline">
                rank them all →
              </Link>
            </div>
            {inWindow.length > 0 && (
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {inWindow.slice(0, 8).map((c) => (
                  <button
                    key={c.city_key}
                    onClick={() => router.push(`/monitor?city=${encodeURIComponent(c.city_key)}`)}
                    className="rounded border border-border bg-panel p-2 text-left hover:border-accent"
                  >
                    <div className="flex items-baseline gap-1.5">
                      <span
                        className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border"
                        style={{
                          background: c.hotness_sigma == null ? "#5b6472" : heatColor(c.hotness_sigma),
                          borderColor: REGION_COLOR[regionFromCity(c)] ?? "#8ab4ff",
                        }}
                      />
                      <span className="truncate text-xs font-semibold">{c.display_name ?? c.city_key}</span>
                      <span className="ml-auto font-mono text-[10px] text-muted">
                        {c.now_c == null ? "—" : `${c.now_c.toFixed(1)}°`}
                      </span>
                    </div>
                    <div className="mt-0.5 font-mono text-[10px] text-muted">
                      {c.hotness_sigma == null ? (
                        "no baseline"
                      ) : (
                        <span className={c.hotness_sigma > 1 ? "text-warn" : ""}>
                          {c.hotness_sigma > 0 ? "+" : ""}
                          {c.hotness_sigma.toFixed(1)}σ · {heatWord(c.hotness_sigma)}
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-[10px] text-muted">
                      {c.best_edge_pp != null ? `best edge ${fmtPp(c.best_edge_pp)}` : "no edge priced"}
                      {c.volume_24h ? ` · ${fmtCompactUsd(c.volume_24h)}` : ""}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          <p className="mt-3 max-w-3xl text-[10px] leading-relaxed text-muted">
            The lighting uses the standard low-precision solar model — declination from the day of
            year, hour angle from UTC, no equation of time — so the terminator is right to within a
            few minutes. It is orientation, not evidence: every peak window on this page comes from{" "}
            <code>derived_weather_peak</code>, which is measured per city and month from this
            desk&apos;s own archive, and nothing here is computed from the lighting.
          </p>
        </>
      </DataState>
    </div>
  );
}
