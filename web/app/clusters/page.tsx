"use client";

import { useMemo, useState } from "react";
import { useCityStats } from "@/lib/useCityStats";
import { DataState } from "@/components/DataState";
import StatsNotice from "@/components/StatsNotice";
import { Scatter } from "@/components/charts";
import { fmtAge, fmtCompactUsd, fmtPp, fmtUsd } from "@/lib/format";
import { fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtCityHour, displayTz, tzLabel } from "@/lib/time";
import { heatColor, heatWord, REGION_COLOR } from "@/lib/heat";
import { regionFromLonLat, type Region } from "@/lib/region";
import type { CityStats } from "@/lib/types";

/**
 * City Clusters: what each city IS, not just how much it trades.
 *
 * The page used to plot one thing - 24h volume as a dot size - and colour it
 * by average edge, which made every city look like every other city with a
 * slightly different radius. It also had no answer for the two questions the
 * page is for: is this city hot right now, and is it a city whose weather can
 * be predicted at all.
 *
 * Both now come from v_city_stats (sql/ad4_17_city_stats.sql), which computes
 * each city's own climatological normal and the standard deviation around it.
 * HOTNESS IS IN STANDARD DEVIATIONS, and that is the whole point: +3C is a
 * warm afternoon in Chicago and a once-in-years event in Beirut. Ranking on
 * degrees would just rank cities by how continental their climate is.
 *
 * Four dimensions, each visually distinct:
 *   colour  - hotness against that city's own normal
 *   ring    - region, so geography separates without relying on position
 *   size    - traded volume, the scale of the market
 *   pulse   - the peak window is open right now
 */

type SortKey = "hotness" | "volatility" | "volume" | "edge" | "mae" | "name";

const SORTS: Array<{ key: SortKey; label: string; hint: string }> = [
  { key: "hotness",    label: "Hotness",     hint: "How far today is from this city's own normal, in standard deviations." },
  { key: "volatility", label: "Volatility",  hint: "Standard deviation of the daily maximum around this date. A high figure means the band the day lands in is genuinely uncertain." },
  { key: "volume",     label: "Scale",       hint: "24h traded volume." },
  { key: "edge",       label: "Best edge",   hint: "The largest net edge currently tradeable in this city." },
  { key: "mae",        label: "Predictability", hint: "Measured forecast error at 1 day (MAE). Lower is a city the model knows." },
  { key: "name",       label: "Name",        hint: "Alphabetical." },
];

const REGIONS: Region[] = ["Americas", "Europe/Africa", "West Asia", "East Asia", "Oceania"];

export default function ClustersPage() {
  const [regionFilter, setRegionFilter] = useState<Region | "ALL">("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("hotness");
  const [selected, setSelected] = useState<string | null>(null);

  const q = useCityStats(60000);
  const all = q.rows;

  const cities = useMemo(() => {
    const filtered =
      regionFilter === "ALL"
        ? all
        : all.filter((c) => regionFromLonLat(c.longitude, c.latitude) === regionFilter);
    const val = (c: CityStats): number => {
      switch (sortKey) {
        case "hotness": return c.hotness_sigma ?? -Infinity;
        case "volatility": return c.volatility_c ?? -Infinity;
        case "volume": return c.volume_24h ?? 0;
        case "edge": return c.best_edge_pp ?? -Infinity;
        case "mae": return c.mae_c === null ? Infinity : c.mae_c;   // low is good
        default: return 0;
      }
    };
    return [...filtered].sort((a, b) =>
      sortKey === "name"
        ? (a.display_name ?? a.city_key).localeCompare(b.display_name ?? b.city_key)
        : sortKey === "mae"
        ? val(a) - val(b)
        : val(b) - val(a)
    );
  }, [all, regionFilter, sortKey]);

  const maxVolume = Math.max(1, ...all.map((c) => c.volume_24h ?? 0));
  const withBaseline = all.filter((c) => c.hotness_sigma !== null).length;
  const detail = cities.find((c) => c.city_key === selected);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">City Clusters</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          <b>Colour is hotness against each city&apos;s own normal, in standard deviations</b> — the
          only form comparable across cities. +3&nbsp;°C is a warm afternoon in Chicago and a
          once-in-years event in Beirut, so degrees alone would rank cities by how continental their
          climate is. The ring is the region, size is 24h traded volume, and a pulsing ring means the
          peak window is open there right now. Baseline is every year&apos;s observations within ten
          days of today&apos;s date; a city without enough history shows grey and says so.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip active={regionFilter === "ALL"} onClick={() => setRegionFilter("ALL")} label="All regions" />
        {REGIONS.map((r) => (
          <Chip
            key={r}
            active={regionFilter === r}
            onClick={() => setRegionFilter(r)}
            label={r}
            dot={REGION_COLOR[r]}
          />
        ))}
        <span className="ml-auto flex items-center gap-1">
          <span className="text-muted">sort by</span>
          {SORTS.map((s) => (
            <button
              key={s.key}
              title={s.hint}
              onClick={() => setSortKey(s.key)}
              className={`rounded border px-2 py-0.5 ${
                sortKey === s.key ? "border-accent text-accent" : "border-border text-muted hover:text-text"
              }`}
            >
              {s.label}
            </button>
          ))}
        </span>
      </div>

      <DataState
        loading={q.loading}
        error={q.error}
        isEmpty={all.length === 0}
        emptyTitle="No cities"
        emptyBody={
          <>
            <code>cities</code> is empty. That is Phase 0 data and nothing on this page can render
            without it — load the city universe first.
          </>
        }
        onRetry={q.refresh}
      >
        <StatsNotice mode={q.mode} reason={q.reason} viewError={q.viewError} />

        {/* ---- the heat scale, stated once ------------------------------- */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-border bg-panel px-3 py-2 text-[10px]">
          <span className="text-muted">Today vs normal:</span>
          {[
            [-2.5, "≤ −2σ"], [-1.5, "−2 to −1σ"], [-0.6, "−1 to −0.3σ"],
            [0, "normal"], [0.6, "+0.3 to +1σ"], [1.5, "+1 to +2σ"], [2.5, "≥ +2σ"],
          ].map(([s, label]) => (
            <span key={label as string} className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: heatColor(s as number) }} />
              <span className="text-muted">{label as string}</span>
            </span>
          ))}
          <span className="ml-auto text-muted">
            {withBaseline} of {all.length} cities have enough history for a baseline
          </span>
        </div>

        {/* ---- the map --------------------------------------------------- */}
        <div className="rounded border border-border bg-panel p-2">
          <svg viewBox="0 0 360 190" className="w-full" style={{ maxHeight: 440 }}>
            <rect x={0} y={0} width={360} height={180} fill="#0e1219" />
            {[...Array(6)].map((_, i) => (
              <line key={`h${i}`} x1={0} y1={(i * 180) / 6} x2={360} y2={(i * 180) / 6} stroke="#1a2030" />
            ))}
            {[...Array(12)].map((_, i) => (
              <line key={`v${i}`} x1={(i * 360) / 12} y1={0} x2={(i * 360) / 12} y2={180} stroke="#1a2030" />
            ))}
            <line x1={0} y1={90} x2={360} y2={90} stroke="#232a38" strokeDasharray="2 3" />

            {cities.map((c) => {
              if (c.longitude === null || c.latitude === null) return null;
              const x = ((c.longitude + 180) / 360) * 360;
              const y = ((90 - c.latitude) / 180) * 180;
              const r = 2.5 + 5.5 * Math.sqrt((c.volume_24h ?? 0) / maxVolume);
              const region = regionFromLonLat(c.longitude, c.latitude);
              const on = c.peak_window_state === "INSIDE";
              return (
                <g key={c.city_key} onClick={() => setSelected(c.city_key === selected ? null : c.city_key)} style={{ cursor: "pointer" }}>
                  {on && (
                    <circle cx={x} cy={y} r={r + 3.5} fill="none" stroke={heatColor(c.hotness_sigma)} strokeWidth={0.6} className="peak-pulse" />
                  )}
                  <circle cx={x} cy={y} r={r + 1.4} fill="none" stroke={REGION_COLOR[region]} strokeWidth={0.9} opacity={0.85} />
                  <circle
                    cx={x} cy={y} r={r}
                    fill={heatColor(c.hotness_sigma)}
                    opacity={c.hotness_sigma === null ? 0.35 : 0.9}
                    stroke={c.city_key === selected ? "#fff" : "none"}
                    strokeWidth={0.8}
                  />
                  <title>
                    {`${c.display_name ?? c.city_key} · ${heatWord(c.hotness_sigma)}` +
                     (c.anomaly_c !== null ? ` (${fmtTempDelta(c.anomaly_c, c.unit as Unit)} vs normal)` : "") +
                     ` · ${fmtCompactUsd(c.volume_24h)} traded 24h · ${region}`}
                  </title>
                </g>
              );
            })}
          </svg>
        </div>

        {detail && <Detail c={detail} onClose={() => setSelected(null)} />}

        {/* ---- the two axes that actually decide a city ------------------ */}
        <section className="rounded border border-border bg-panel p-3">
          <h2 className="text-sm font-semibold">Predictability against scale</h2>
          <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
            The two things that decide whether a city is worth trading, on one plot. Left is a city
            the model knows; right is a city that trades. <b>The bottom-right corner is where the
            money is</b> — low forecast error and real volume. A city high on the left is
            unpredictable and illiquid, and no edge shown there should be believed.
          </p>
          <Scatter
            height={280}
            logX
            xLabel="24h traded volume (log)"
            yLabel="Forecast MAE °C at 1 day — lower is better"
            xTickFormat={(v) => fmtCompactUsd(v)}
            yTickFormat={(v) => v.toFixed(1)}
            points={cities
              .filter((c) => c.mae_c !== null && (c.volume_24h ?? 0) > 0)
              .map((c) => ({
                x: c.volume_24h ?? 0,
                y: c.mae_c as number,
                label: c.city_key,
                color: heatColor(c.hotness_sigma),
                r: 3.5 + 4 * Math.sqrt((c.volume_24h ?? 0) / maxVolume),
                hint:
                  `${c.display_name ?? c.city_key}: MAE ${c.mae_c?.toFixed(2)}°C over ${c.skill_days ?? 0} days, ` +
                  `${fmtCompactUsd(c.volume_24h)} traded, ${heatWord(c.hotness_sigma)}`,
              }))}
          />
        </section>

        {/* ---- the table: every number, sortable ------------------------- */}
        <section className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-[11px] uppercase tracking-wide text-muted">
              <tr>
                <th className="p-2 text-left">City</th>
                <th className="p-2 text-left">Region</th>
                <th className="p-2 text-right" title="Today's forecast maximum, or the running max if no forecast.">Today</th>
                <th className="p-2 text-right" title="This city's climatological normal for this date.">Normal</th>
                <th className="p-2 text-right" title="Degrees from normal, and the same figure in standard deviations.">Hotness</th>
                <th className="p-2 text-right" title="Standard deviation of the daily maximum around this date. High means the band the day lands in is genuinely uncertain.">Volatility</th>
                <th className="p-2 text-right" title="Measured forecast error at 1 day.">MAE</th>
                <th className="p-2 text-right" title="Peak window on the city's clock, and on yours.">Peak</th>
                <th className="p-2 text-right">Vol 24h</th>
                <th className="p-2 text-right" title="Book depth fillable inside 5c.">Depth</th>
                <th className="p-2 text-right" title="Best net edge currently tradeable here.">Best edge</th>
              </tr>
            </thead>
            <tbody>
              {cities.map((c) => {
                const unit = c.unit as Unit;
                const region = regionFromLonLat(c.longitude, c.latitude);
                return (
                  <tr
                    key={c.city_key}
                    onClick={() => setSelected(c.city_key === selected ? null : c.city_key)}
                    className={`cursor-pointer border-t border-border hover:bg-panel2 ${
                      c.city_key === selected ? "bg-panel2" : ""
                    }`}
                  >
                    <td className="p-2">
                      <span className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: heatColor(c.hotness_sigma) }} />
                      {c.display_name ?? c.city_key}
                      {c.peak_window_state === "INSIDE" && (
                        <span className="ml-1.5 text-[9px] uppercase tracking-wide text-accent">peak</span>
                      )}
                    </td>
                    <td className="p-2 text-[11px]" style={{ color: REGION_COLOR[region] }}>{region}</td>
                    <td className="p-2 text-right font-mono">{fmtTemp(c.forecast_max_c ?? c.running_max_c, unit)}</td>
                    <td className="p-2 text-right font-mono text-muted">{fmtTemp(c.normal_max_c, unit)}</td>
                    <td className="p-2 text-right font-mono" style={{ color: heatColor(c.hotness_sigma) }}>
                      {c.anomaly_c === null ? (
                        <span className="text-muted" title={`Baseline: ${c.baseline ?? "none"}`}>—</span>
                      ) : (
                        <>
                          {fmtTempDelta(c.anomaly_c, unit)}
                          <span className="ml-1 text-[10px] opacity-80">
                            {c.hotness_sigma !== null ? `${c.hotness_sigma > 0 ? "+" : ""}${c.hotness_sigma.toFixed(1)}σ` : ""}
                          </span>
                        </>
                      )}
                    </td>
                    <td className="p-2 text-right font-mono text-muted">
                      {c.volatility_c === null ? "—" : `±${fmtTempDelta(c.volatility_c, unit).replace("+", "")}`}
                    </td>
                    <td className={`p-2 text-right font-mono ${(c.mae_c ?? 0) > 2 ? "text-warn" : "text-muted"}`}>
                      {c.mae_c === null ? "—" : c.mae_c.toFixed(2)}
                    </td>
                    <td className="p-2 text-right font-mono text-[11px] text-muted">
                      {c.peak_hour_local === null ? "—" : fmtCityHour(c.peak_hour_local, c.timezone)}
                    </td>
                    <td className="p-2 text-right font-mono">{fmtCompactUsd(c.volume_24h)}</td>
                    <td className="p-2 text-right font-mono text-muted">{c.depth_5c === null ? "—" : fmtUsd(c.depth_5c)}</td>
                    <td className={`p-2 text-right font-mono ${(c.best_edge_pp ?? 0) > 0 ? "text-good" : "text-muted"}`}>
                      {c.best_edge_pp === null ? "—" : fmtPp(c.best_edge_pp)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <p className="text-[11px] text-muted">
          Peak windows are shown on the city&apos;s clock with yours beside them — a daily maximum
          happens in that city&apos;s afternoon wherever you are reading this. Your clock is{" "}
          <b>{tzLabel(displayTz())}</b>; change it in the masthead.
        </p>
      </DataState>
    </div>
  );
}

function Detail({ c, onClose }: { c: CityStats; onClose: () => void }) {
  const unit = c.unit as Unit;
  return (
    <div className="rounded border border-accent/40 bg-panel p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">
          {c.display_name ?? c.city_key}{" "}
          <span className="font-mono text-xs text-muted">{c.icao}</span>{" "}
          <span className="text-xs" style={{ color: heatColor(c.hotness_sigma) }}>· {heatWord(c.hotness_sigma)}</span>
        </h2>
        <button onClick={onClose} className="text-xs text-muted hover:text-text">close</button>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4 lg:grid-cols-6">
        <F label="Now" v={fmtTemp(c.now_c, unit)} />
        <F label="Max so far" v={fmtTemp(c.running_max_c, unit)} />
        <F label="Forecast" v={fmtTemp(c.forecast_max_c, unit)} hint={c.forecast_model ? `model: ${c.forecast_model}` : undefined} />
        <F label="Normal for the date" v={fmtTemp(c.normal_max_c, unit)} hint={`${c.baseline ?? "no"} baseline from ${c.baseline_days ?? 0} days`} />
        <F label="Volatility (1σ)" v={c.volatility_c === null ? "—" : `±${fmtTempDelta(c.volatility_c, unit).replace("+", "")}`}
           hint="Standard deviation of the daily maximum around this date. This is what decides how many bands a day could plausibly land in." />
        <F label="Forecast MAE" v={c.mae_c === null ? "—" : `${c.mae_c.toFixed(2)}°C`} hint={`measured over ${c.skill_days ?? 0} days at 1-day lead`} />
        <F label="Bias" v={c.bias_c === null ? "—" : `${c.bias_c > 0 ? "+" : ""}${c.bias_c.toFixed(2)}°C`} hint="Systematic error the engine already corrects for." />
        <F label="Models disagree by" v={c.model_spread_c === null ? "—" : `${c.model_spread_c.toFixed(2)}°C`}
           hint={c.n_models ? `${c.n_models} models; sigma widened ×${c.sigma_multiplier ?? 1}` : "only one forecast model — run P1.3 for a second"} />
        <F label="Peak window" v={c.peak_hour_local === null ? "—" : fmtCityHour(c.peak_hour_local, c.timezone)} wide />
        <F label="Vol 24h" v={`${fmtCompactUsd(c.volume_24h)} · ${c.n_trades_24h ?? 0} trades`} />
        <F label="Depth 5¢" v={c.depth_5c === null ? "—" : fmtUsd(c.depth_5c)} />
        <F label="Tradeable now" v={`${c.n_tradeable ?? 0} bands`} hint={c.best_edge_pp !== null ? `best ${fmtPp(c.best_edge_pp)}` : undefined} />
        <F label="Last observation" v={fmtAge(c.observed_at)} />
      </div>
    </div>
  );
}

function F({ label, v, hint, wide }: { label: string; v: string; hint?: string; wide?: boolean }) {
  return (
    <div title={hint} className={wide ? "col-span-2" : undefined}>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-mono">{v}</div>
    </div>
  );
}

function Chip({ active, onClick, label, dot }: { active: boolean; onClick: () => void; label: string; dot?: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 ${
        active ? "border-accent text-accent" : "border-border text-muted hover:text-text"
      }`}
    >
      {dot && <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} />}
      {label}
    </button>
  );
}
