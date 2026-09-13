"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Globe, { type CityBar } from "@/components/Globe";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { useCityStats } from "@/lib/useCityStats";
import StatsNotice from "@/components/StatsNotice";
import { DataState } from "@/components/DataState";
import { FreshnessRow } from "@/components/Provenance";
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
interface GlobeOpp {
  city_key: string;
  model_prob: number | null;
  edge_net_pp: number | null;
  market_price: number | null;
  band_label: string | null;
  tradeable: boolean | null;
  fillable_usd_5c: number | null;
  resolution_date: string;
}

export default function GlobePage() {
  const router = useRouter();
  const stats = useCityStats(60000);

  /**
   * WHAT THE TOWERS STAND ON. The globe used to carry one channel - hotness -
   * and every other question sent you to another page. These are the four a
   * trader actually asks of a city at a glance, and they come from the board
   * rather than from anything computed here, so they cannot disagree with it.
   */
  const oppQ = useQuery<GlobeOpp[]>(
    () => supabase.from("v_opportunities")
            .select("city_key,model_prob,edge_net_pp,market_price,band_label,tradeable,fillable_usd_5c,resolution_date")
            .gte("resolution_date", new Date().toISOString().slice(0, 10))
            .limit(4000),
    [], 60000, 4000
  );

  // The best live band per city: highest net edge among the ones the desk
  // would actually take. That single band is the entry the tower describes.
  const bestByCity = useMemo(() => {
    const m = new Map<string, GlobeOpp>();
    for (const o of oppQ.data ?? []) {
      if (!o.tradeable) continue;
      const cur = m.get(o.city_key);
      if (!cur || (o.edge_net_pp ?? -99) > (cur.edge_net_pp ?? -99)) m.set(o.city_key, o);
    }
    return m;
  }, [oppQ.data]);

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

  const [picked, setPicked] = useState<string | null>(null);
  const pickedCity = rows.find((c) => c.city_key === picked) ?? null;
  const pickedOpp = picked ? bestByCity.get(picked) ?? null : null;

  /**
   * The four bars, per city. Each is normalised to 0..1 for HEIGHT and keeps
   * its real figure for the readout - a normalised bar cannot be read as a
   * number and a raw number cannot be compared between cities, so both.
   *
   * The ceilings are stated rather than derived from the data on screen: a
   * scale that rescales itself makes a quiet day look like a good one.
   *   ENTRY  net edge, 0-15 pp        - is there anything here
   *   WIN    the model's probability  - how likely that entry is to pay
   *   SKILL  1 - mae/5 C              - whether this city's forecast is worth
   *                                     trusting at all, which gates the two
   *                                     above rather than adding to them
   *   CLIMB  forecast - running max   - how much of the day is still to come;
   *                                     zero means the number is already in
   */
  const bars = useMemo(() => {
    const m = new Map<string, CityBar[]>();
    /**
     * NOT EVERY CITY GETS A TOWER, and that restraint is the feature.
     *
     * Thirty-seven towers is 148 bars on one sphere; where two cities sit
     * close together they overlap into a smear that says nothing, and a
     * channel that is always on stops meaning anything even where it is
     * readable. So a tower is drawn for the ten cities with the best live
     * entry - which makes the tower itself a signal, "this is one worth
     * looking at" - and the Globe adds one for whatever is under the pointer,
     * so the channel is still discoverable by pointing at anything.
     */
    const TOWERS = 10;
    const ranked = rows
      .map((c) => ({ key: c.city_key, edge: bestByCity.get(c.city_key)?.edge_net_pp ?? -99 }))
      .filter((r) => r.edge > 0)
      .sort((a, b) => b.edge - a.edge)
      .slice(0, TOWERS);
    const show = new Set(ranked.map((r) => r.key));

    for (const c of rows) {
      const o = bestByCity.get(c.city_key);
      const edge = o?.edge_net_pp ?? null;
      const prob = o?.model_prob ?? null;
      const mae = c.mae_c ?? null;
      const climb =
        c.forecast_max_c != null && c.running_max_c != null
          ? Math.max(0, c.forecast_max_c - c.running_max_c)
          : null;
      m.set(c.city_key, [
        { key: "edge", label: "entry", color: "#7ee081",
          frac: !show.has(c.city_key) ? 0 : edge == null ? 0 : Math.min(1, Math.max(0, edge) / 15),
          text: edge == null ? "no live band" : `${edge > 0 ? "+" : ""}${edge.toFixed(1)} pp` },
        { key: "win", label: "win", color: "#4da3ff",
          frac: !show.has(c.city_key) ? 0 : prob == null ? 0 : Math.min(1, Math.max(0, prob)),
          text: prob == null ? "—" : `${(prob * 100).toFixed(0)}%` },
        { key: "skill", label: "skill", color: "#c792ea",
          frac: !show.has(c.city_key) ? 0 : mae == null ? 0 : Math.min(1, Math.max(0, 1 - mae / 5)),
          text: mae == null ? "unmeasured" : `${mae.toFixed(2)} °C err` },
        { key: "climb", label: "climb", color: "#ffb020",
          // frac 0 on a city outside the top ten: the Globe draws nothing
          // unless it is hovered, and the readout still shows every figure.
          frac: !show.has(c.city_key) || climb == null ? 0 : Math.min(1, climb / 8),
          text: climb == null ? "—" : `${climb.toFixed(1)} °C to go` },
      ]);
    }
    return m;
  }, [rows, bestByCity]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Globe</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Every city the desk covers, under real daylight. The lit side is daytime <b>now</b>; the
          maxima this desk trades are made in the few hours around each city&apos;s solar afternoon,
          and that band sweeps west all day. A pulsing ring is a city inside its measured peak
          window right now. Colour is hotness against that city&apos;s own normal, in standard
          deviations — the only form comparable between Chicago and Beirut. Drag to turn it — it follows your hand — scroll or use +/− to zoom, and click a
          city to open it underneath without leaving the globe. Each city carries a small tower:
          entry, win, skill, climb.
        </p>
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["cities", "derived_weather_peak"]} />
        </div>
      </div>

      <StatsNotice mode={stats.mode} reason={stats.reason} viewError={stats.viewError} />

      <DataState
        loading={stats.loading}
        error={stats.viewError}
        truncated={oppQ.truncated}
        relation="v_opportunities"
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
            bars={bars}
            selected={picked}
            // NOT a redirect any more. Clicking a city used to leave the page
            // entirely, so the one thing the globe is for - seeing a city IN
            // CONTEXT of the others - was lost the moment you asked about one.
            // It opens underneath instead, and the monitor is still one click
            // from there for anyone who wants the full page.
            onPick={(k) => setPicked((cur) => (cur === k ? null : k))}
          />

          {pickedCity && (
            <CityPanel
              city={pickedCity}
              opp={pickedOpp}
              bars={bars.get(pickedCity.city_key) ?? []}
              onClose={() => setPicked(null)}
              onOpenMonitor={() =>
                router.push(`/monitor?city=${encodeURIComponent(pickedCity.city_key)}`)}
            />
          )}

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

          {/* ---- can this city be timed at all? ------------------------
              The globe shows WHERE the peak band is. This says whether the
              desk actually knows when each city peaks, or is still assuming
              15:00 - which is the difference between s7 being a strategy and
              s7 being a guess. */}
          <PeakCoverage />

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


interface PeakRow {
  city_key: string;
  display_name: string | null;
  peak_hour_local: number | null;
  window_width_h: number | null;
  n_days: number | null;
  months_measured: number;
  verdict: string;
}

/**
 * Whether the desk knows when each city peaks.
 *
 * derived_weather_peak had no writer at all until sql/ad4_37 - every consumer
 * of the peak hour, including strategy s7's entire trigger, was silently on a
 * fallback. This is the check that it is being written, and the honest answer
 * for cities where it cannot be: a six-hour window is not a measurement
 * failure, it is a city whose peak genuinely moves, and timing it is not a
 * strategy there.
 */
function PeakCoverage() {
  const q = useQuery<PeakRow[]>(() => supabase.from("v_peak_hour_coverage").select("*"), [], 300000);
  const [open, setOpen] = useState(false);
  const rows = q.data ?? [];
  const measured = rows.filter((r) => r.peak_hour_local != null);
  const wide = measured.filter((r) => (r.window_width_h ?? 0) > 4);
  const missing = rows.length - measured.length;

  if (q.error && /does not exist|schema cache|could not find/i.test(q.error)) {
    return (
      <div className="mt-4 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[11px] leading-relaxed text-warn">
        <b>The measured peak hour is not installed.</b> Run{" "}
        <code>sql/ad4_37_peak_hour.sql</code>. Until then every peak window on this desk — including
        strategy <b>s7</b>&apos;s entire trigger — falls back to an assumed 15:00.
      </div>
    );
  }

  return (
    <section className="mt-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-semibold">Can these cities be timed?</h2>
        <span className="text-xs text-muted">
          <b className={measured.length ? "text-good" : "text-warn"}>{measured.length}</b> of{" "}
          {rows.length} have a measured peak hour for this month
          {wide.length > 0 && (
            <span className="text-warn"> · {wide.length} peak too loosely to time</span>
          )}
          {missing > 0 && <span className="text-muted"> · {missing} still on the assumed 15:00</span>}
        </span>
        <button onClick={() => setOpen((o) => !o)} className="ml-auto text-xs text-accent hover:underline">
          {open ? "hide" : "show each city"}
        </button>
      </div>
      <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-muted">
        Measured from this desk&apos;s own archive, per city and calendar month. The <b>window</b> is
        how much the peak moves: a city with a 1.5-hour window can be timed to the hour, and one with
        six hours cannot be timed at all — which is a real answer, not a gap.
      </p>
      {open && (
        <div className="mt-2 overflow-x-auto rounded border border-border">
          <table className="w-full text-xs">
            <thead className="bg-panel2 text-muted">
              <tr>
                <th className="px-2 py-1.5 text-left">City</th>
                <th className="px-2 py-1.5 text-right">Peak hour (local)</th>
                <th className="px-2 py-1.5 text-right">Window</th>
                <th className="px-2 py-1.5 text-right">Days measured</th>
                <th className="px-2 py-1.5 text-right">Months on file</th>
                <th className="px-2 py-1.5 text-left">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.city_key} className="border-t border-border">
                  <td className="px-2 py-1.5">{r.display_name ?? r.city_key}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">
                    {r.peak_hour_local == null
                      ? <span className="text-warn">assumed 15:00</span>
                      : `${String(Math.floor(r.peak_hour_local)).padStart(2, "0")}:${String(Math.round((r.peak_hour_local % 1) * 60)).padStart(2, "0")}`}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                    (r.window_width_h ?? 0) > 4 ? "text-warn" : "text-muted"
                  }`}>
                    {r.window_width_h == null ? "—" : `±${(r.window_width_h / 2).toFixed(1)}h`}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">{r.n_days ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">{r.months_measured}</td>
                  <td className="px-2 py-1.5 text-[11px] text-muted">{r.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ==========================================================================
   THE CITY, UNDER THE GLOBE.
 
   Clicking a dot used to navigate to /monitor, which threw away the only
   thing the globe is for: seeing one city in the context of the other
   thirty-six. This opens beneath it instead - same page, globe still on
   screen with the city ringed - and the full monitor stays one click away.
 
   Everything here is already on the row or on the board. Nothing is
   recomputed: a second implementation of "edge" drifts from the first and
   then nobody knows which number is real.
   ========================================================================== */
function CityPanel({
  city,
  opp,
  bars,
  onClose,
  onOpenMonitor,
}: {
  city: CityStats;
  opp: GlobeOpp | null;
  bars: CityBar[];
  onClose: () => void;
  onOpenMonitor: () => void;
}) {
  const climbing = city.forecast_max_c != null && city.running_max_c != null
    ? city.forecast_max_c - city.running_max_c
    : null;

  // The one sentence. Six numbers and the reader still has to know the desk's
  // rules to turn them into a decision, which is the gap this closes.
  let verdict = "Nothing tradeable on the board for this city right now.";
  let tone = "text-muted";
  if (opp) {
    const edge = opp.edge_net_pp ?? 0;
    const trustworthy = city.mae_c != null && city.mae_c <= 2.5;
    if (edge >= 5 && trustworthy) {
      verdict = `${opp.band_label ?? "a band"} is the entry: ${edge.toFixed(1)} pp of net edge at ${opp.market_price != null ? opp.market_price.toFixed(2) : "—"}, on a city whose forecast has been good to ${city.mae_c!.toFixed(2)} °C.`;
      tone = "text-good";
    } else if (edge >= 5) {
      verdict = `${opp.band_label ?? "a band"} shows ${edge.toFixed(1)} pp — but this city's forecast error is ${city.mae_c == null ? "unmeasured" : `${city.mae_c.toFixed(2)} °C`}, which is wide enough to be the whole edge. Size accordingly.`;
      tone = "text-warn";
    } else {
      verdict = `Best live band is ${opp.band_label ?? "—"} at ${edge.toFixed(1)} pp. Thin, and not what the desk is for.`;
      tone = "text-muted";
    }
  }

  const F = ({ label, value, hint }: { label: string; value: string; hint?: string }) => (
    <div className="rounded border border-border bg-panel2/60 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-mono text-sm tabular-nums">{value}</div>
      {hint ? <div className="text-[10px] text-muted">{hint}</div> : null}
    </div>
  );

  return (
    <section className="mt-3 rounded border border-accent/40 bg-panel/70 p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="inline-block h-3 w-3 shrink-0 rounded-full border"
              style={{
                background: city.hotness_sigma == null ? "#5b6472" : heatColor(city.hotness_sigma),
                borderColor: REGION_COLOR[regionFromCity(city)] ?? "#8ab4ff",
              }} />
        <h2 className="text-sm font-semibold">{city.display_name ?? city.city_key}</h2>
        <span className="text-[11px] text-muted">
          {regionFromCity(city)}
          {city.timezone ? ` · ${city.timezone}` : ""}
          {city.peak_hour_local != null ? ` · peaks about ${city.peak_hour_local}:00 local` : ""}
        </span>
        <button onClick={onOpenMonitor} className="ml-auto text-xs text-accent hover:underline">
          full monitor →
        </button>
        <button onClick={onClose} className="text-xs text-muted hover:text-text">close</button>
      </div>

      <p className={`mt-1.5 text-xs leading-relaxed ${tone}`}>{verdict}</p>

      {/* The same four channels the tower draws, as figures. The tower is for
          comparing cities; this is for reading one. */}
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {bars.map((b) => (
          <div key={b.key} className="rounded border border-border bg-panel2/60 px-2 py-1.5">
            <div className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-[1px]" style={{ background: b.color }} />
              <span className="text-[10px] uppercase tracking-wide text-muted">{b.label}</span>
            </div>
            <div className="font-mono text-sm tabular-nums">{b.text}</div>
            <div className="mt-1 h-1 rounded bg-panel2">
              <div className="h-1 rounded" style={{ width: `${Math.round(b.frac * 100)}%`, background: b.color }} />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <F label="now" value={city.now_c == null ? "—" : `${city.now_c.toFixed(1)}°`} />
        <F label="running max" value={city.running_max_c == null ? "—" : `${city.running_max_c.toFixed(1)}°`} />
        <F label="forecast"
           value={city.forecast_max_c == null ? "—" : `${city.forecast_max_c.toFixed(1)}°`}
           hint={city.forecast_model
             ? `${city.forecast_model}${city.forecast_lead_days != null ? ` · ${city.forecast_lead_days}d lead` : ""}`
             : undefined} />
        <F label="still to climb"
           value={climbing == null ? "—" : `${climbing > 0 ? "+" : ""}${climbing.toFixed(1)}°`}
           hint={city.day_decided ? "day already decided" : city.peak_window_state ?? undefined} />
        <F label="vs its normal"
           value={city.hotness_sigma == null ? "—" : `${city.hotness_sigma > 0 ? "+" : ""}${city.hotness_sigma.toFixed(1)}σ`}
           hint={city.hotness_sigma == null ? "no baseline yet" : heatWord(city.hotness_sigma)} />
        <F label="24h volume"
           value={fmtCompactUsd(city.volume_24h ?? 0)}
           hint={`${city.n_tradeable ?? 0} tradeable of ${city.live_bands ?? 0}`} />
      </div>

      {opp ? (
        <div className="mt-2 rounded border border-border bg-panel2/40 px-2 py-1.5 text-[11px]">
          <span className="text-muted">best live band </span>
          <span className="font-mono">{opp.band_label ?? "—"}</span>
          <span className="text-muted"> · model </span>
          <span className="font-mono">{opp.model_prob == null ? "—" : `${(opp.model_prob * 100).toFixed(0)}%`}</span>
          <span className="text-muted"> · market </span>
          <span className="font-mono">{opp.market_price == null ? "—" : opp.market_price.toFixed(2)}</span>
          <span className="text-muted"> · edge </span>
          <span className="font-mono">{fmtPp(opp.edge_net_pp)}</span>
          <span className="text-muted"> · fillable at 5c </span>
          <span className="font-mono">{fmtCompactUsd(opp.fillable_usd_5c ?? 0)}</span>
        </div>
      ) : null}

      {city.forecast_suspect ? (
        <p className="mt-1.5 text-[11px] text-warn">
          This city&apos;s forecast is flagged suspect — it sits more than 4 °C above anything the
          city has actually done in three days, which is the shape of a stale long-lead row. Check
          <code className="mx-1 rounded bg-panel2 px-1">v_forecast_audit</code> before trading it.
        </p>
      ) : null}
    </section>
  );
}
