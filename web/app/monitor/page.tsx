"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { useCityStats } from "@/lib/useCityStats";
import { DataState } from "@/components/DataState";
import { LineChart, Empty } from "@/components/charts";
import { fmtAge, fmtPct, fmtPrice, fmtPp, fmtCompactUsd } from "@/lib/format";
import { fmtTemp, fmtTempDelta, fmtBandRange, type Unit } from "@/lib/units";
import { fmtCityHour, displayTz } from "@/lib/time";
import { findCover } from "@/lib/cover";
import type { CityStats, Opportunity } from "@/lib/types";

/**
 * City Monitor: one city, watched properly.
 *
 * City Watch answers "what are my cities doing" in a line each. This answers
 * the next question, the one you ask right before you actually trade: what is
 * THIS day doing, and has the market noticed yet.
 *
 * The organising idea is that a temperature is not a number, it is a
 * trajectory. 28.4C an hour before peak is a buy after 26.9 / 27.7 / 28.4 and
 * a sell after 29.1 / 28.8 / 28.4, and every panel here exists to make that
 * difference visible:
 *
 *   the trace        every reading of the local day, against the forecast, the
 *                    running max, and where observation alone says the day
 *                    ends up
 *   the read         slope over the last three readings and the last six, and
 *                    how much this city has HISTORICALLY still climbed from
 *                    this hour - measured, per city, from our own archive
 *   the market       what each bucket has cost over the last 48 hours. The
 *                    trade is the gap between that line and the one above it
 *   the ladder       every bucket priced now, with the two-bucket cover
 *                    flagged when the pair qualifies
 *
 * Every panel names the job that fills it when it is empty. A blank chart with
 * no explanation is the failure mode this page was written against.
 */

const KEY = "ad4-monitor-city";

interface Approach {
  city_key: string;
  latest_temp_c: number | null;
  latest_at: string | null;
  latest_local_hour: number | null;
  running_max_c: number | null;
  slope_3_c_per_h: number | null;
  slope_6_c_per_h: number | null;
  direction: string | null;
  rolling_over: boolean | null;
  below_running_max_c: number | null;
  reading_age_min: number | null;
  n_readings: number | null;
  typical_climb_left_c: number | null;
  climb_left_p10_c: number | null;
  climb_left_p90_c: number | null;
  pct_already_peaked: number | null;
  profile_days: number | null;
  implied_max_c: number | null;
  implied_max_low_c: number | null;
  implied_max_high_c: number | null;
}

interface Reading {
  city_key: string;
  valid_at: string;
  temp_c: number;
  local_hour: number;
  source: string | null;
}

interface PriceRow {
  band_id: string;
  band_label: string | null;
  band_lo: number | null;
  band_hi: number | null;
  observed_at: string;
  mid: number | null;
  best_ask: number | null;
}

type LadderRow = Pick<
  Opportunity,
  "band_id" | "band_label" | "band_lo" | "band_hi" | "open_low" | "open_high"
  | "side" | "model_prob" | "market_price" | "edge_net_pp" | "volume_usd" | "tradeable"
>;

const SERIES_COLORS = ["#4f8cff", "#2ecc71", "#ffb020", "#ff6b9d", "#a78bfa", "#22d3ee"];

export default function MonitorPage() {
  const stats = useCityStats(60000);
  const [city, setCity] = useState<string | null>(null);

  // The chosen city survives a reload. It is a per-browser preference, not
  // shared state, so it belongs in localStorage and nowhere else.
  useEffect(() => {
    try {
      const v = localStorage.getItem(KEY);
      if (v) setCity(v);
    } catch { /* private window: no preference, no problem */ }
  }, []);
  useEffect(() => {
    if (!city) return;
    try { localStorage.setItem(KEY, city); } catch { /* ignore */ }
  }, [city]);

  const cities = useMemo(
    () => [...stats.rows].sort((a, b) => (b.volume_24h ?? 0) - (a.volume_24h ?? 0)),
    [stats.rows]
  );
  useEffect(() => {
    if (!city && cities.length) setCity(cities[0].city_key);
  }, [cities, city]);

  const c = cities.find((x) => x.city_key === city) ?? null;
  const unit = (c?.unit ?? "C") as Unit;

  const approach = useQuery<Approach[]>(
    () => city
      ? supabase.from("v_city_peak_approach").select("*").eq("city_key", city)
      : Promise.resolve({ data: [] as Approach[], error: null }),
    [city], 60000
  );
  const readings = useQuery<Reading[]>(
    () => city
      ? supabase.from("v_city_today_readings")
          .select("city_key,valid_at,temp_c,local_hour,source")
          .eq("city_key", city).order("valid_at", { ascending: true })
      : Promise.resolve({ data: [] as Reading[], error: null }),
    [city], 60000
  );
  const prices = useQuery<PriceRow[]>(
    () => city
      ? supabase.from("v_band_price_history")
          .select("band_id,band_label,band_lo,band_hi,observed_at,mid,best_ask")
          .eq("city_key", city).order("observed_at", { ascending: true }).limit(4000)
      : Promise.resolve({ data: [] as PriceRow[], error: null }),
    [city], 120000
  );
  const ladder = useQuery<LadderRow[]>(
    () => city
      ? supabase.from("v_opportunities")
          .select("band_id,band_label,band_lo,band_hi,open_low,open_high,side,model_prob,market_price,edge_net_pp,volume_usd,tradeable")
          .eq("city_key", city).eq("side", "YES")
      : Promise.resolve({ data: [] as LadderRow[], error: null }),
    [city], 60000
  );

  const a = approach.data?.[0] ?? null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">City Monitor</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          A temperature is not a number, it is a <b>trajectory</b>. The same 28.4&nbsp;°C an hour
          before peak is a buy after 26.9 / 27.7 / 28.4 and a sell after 29.1 / 28.8 / 28.4 — and
          the level alone cannot tell you which. Everything here is built to show that difference:
          how fast the day is moving, how much this city usually still climbs from this hour, and
          whether the market has repriced for it yet.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">City</span>
        <select
          value={city ?? ""}
          onChange={(e) => setCity(e.target.value)}
          className="rounded border border-border bg-panel px-2 py-1"
        >
          {cities.map((x) => (
            <option key={x.city_key} value={x.city_key}>
              {x.display_name ?? x.city_key}
              {x.volume_24h ? ` — ${fmtCompactUsd(x.volume_24h)} 24h` : ""}
            </option>
          ))}
        </select>
        {c?.timezone && a?.latest_local_hour != null && (
          <span className="font-mono text-[11px] text-muted">
            {fmtCityHour(a.latest_local_hour, c.timezone)} there · {displayTz()} here
          </span>
        )}
        {a?.latest_at && (
          <span className={`font-mono text-[11px] ${(a.reading_age_min ?? 0) > 90 ? "text-bad" : "text-muted"}`}>
            reading {fmtAge(a.latest_at)}
            {(a.reading_age_min ?? 0) > 90 && " — too old to read a slope from"}
          </span>
        )}
      </div>

      {/* ---- the read ------------------------------------------------- */}
      <Section
        title="Where the day is, and which way it is going"
        hint="Slope is least squares over the real timestamps, not last-minus-first: METAR is hourly but SPECIs are not, and uneven spacing breaks the naive form."
      >
        <DataState
          loading={approach.loading}
          error={approach.error}
          isEmpty={!a}
          emptyTitle="No readings for this city today"
          emptyBody={<>Run <b>n8n P1.2 (NWS Monitor)</b> or <b>Actions → Observations</b>. If the view itself is missing, run <code>sql/ad4_26_temp_trend.sql</code>.</>}
        >
          {a && <TheRead a={a} c={c} unit={unit} />}
        </DataState>
      </Section>

      {/* ---- the trace ------------------------------------------------ */}
      <Section
        title="Today's trace"
        hint="Every reading of the local day. The dashed lines are the forecast maximum and where observation alone says the day ends up."
      >
        <DataState
          loading={readings.loading}
          error={readings.error}
          isEmpty={(readings.data?.length ?? 0) < 2}
          emptyTitle="Fewer than two readings today"
          emptyBody="A line needs two points. This is exactly what the observation series from n8n P1.2 exists to provide - the old single-reading fetch could never draw this chart."
        >
          <TempTrace rows={readings.data ?? []} a={a} c={c} unit={unit} />
        </DataState>
      </Section>

      {/* ---- the market ------------------------------------------------ */}
      <Section
        title="What the market has charged"
        hint="Mid price per bucket over the last 48 hours. The trade is the gap between a bucket the day is climbing into and a price that has not moved."
      >
        <DataState
          loading={prices.loading}
          error={prices.error}
          isEmpty={(prices.data?.length ?? 0) < 2}
          emptyTitle="No book history"
          emptyBody={<><b>n8n P0.3 (Book + Volume Snapshot)</b> writes it — without that job nothing on this desk has a price at all.</>}
        >
          <PriceTrace rows={prices.data ?? []} ladder={ladder.data ?? []} />
        </DataState>
      </Section>

      {/* ---- the ladder ------------------------------------------------ */}
      <Section
        title="The ladder now"
        hint="Every bucket, priced. The cover pair is the two most likely ADJACENT buckets when they cost under 70c together including fees."
      >
        <DataState
          loading={ladder.loading}
          error={ladder.error}
          isEmpty={(ladder.data?.length ?? 0) === 0}
          emptyTitle="No priced buckets"
          emptyBody={<>Needs bands from <b>n8n P0.2</b>, a book from <b>P0.3</b>, then <b>Actions → Probabilities</b>.</>}
        >
          <Ladder rows={ladder.data ?? []} a={a} unit={unit} />
        </DataState>
      </Section>
    </div>
  );
}

/* ------------------------------------------------------------------ read -- */

function TheRead({ a, c, unit }: { a: Approach; c: CityStats | null; unit: Unit }) {
  const dir = a.direction ?? "unknown";
  const climbing = dir.startsWith("climbing");
  const falling = dir.startsWith("falling");
  const color = a.rolling_over ? "text-warn" : climbing ? "text-good" : falling ? "text-bad" : "text-muted";

  // The forecast and the observation-implied maximum are two independent
  // estimates of the same number. Showing them apart is the point: when they
  // disagree, one of them is about to be wrong and that is tradeable.
  const gap =
    c?.forecast_max_c != null && a.implied_max_c != null ? a.implied_max_c - c.forecast_max_c : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <Figure label="Now" value={fmtTemp(a.latest_temp_c, unit)} big />
        <Figure label="Max so far" value={fmtTemp(a.running_max_c, unit)} />
        <Figure label="Forecast" value={fmtTemp(c?.forecast_max_c ?? null, unit)} />
        <Figure
          label="Implied by observation"
          value={fmtTemp(a.implied_max_c, unit)}
          sub={
            a.implied_max_low_c != null && a.implied_max_high_c != null
              ? `${fmtTemp(a.implied_max_low_c, unit)} – ${fmtTemp(a.implied_max_high_c, unit)}`
              : undefined
          }
        />
      </div>

      <div className={`flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px] ${color}`}>
        <span className="uppercase tracking-wide">{a.rolling_over ? "rolling over" : dir}</span>
        {a.slope_3_c_per_h != null && (
          <span title="Least squares over the last three readings.">
            <span className="text-muted">3-reading </span>
            {fmtTempDelta(a.slope_3_c_per_h, unit)}/h
          </span>
        )}
        {a.slope_6_c_per_h != null && (
          <span title="Least squares over the last six. Disagreement with the short slope is the turn.">
            <span className="text-muted">6-reading </span>
            {fmtTempDelta(a.slope_6_c_per_h, unit)}/h
          </span>
        )}
        {a.below_running_max_c != null && a.below_running_max_c > 0.05 && (
          <span className="text-muted">
            {fmtTempDelta(a.below_running_max_c, unit).replace("+", "")} below today&apos;s high
          </span>
        )}
        <span className="text-muted">{a.n_readings ?? 0} readings today</span>
      </div>

      {a.rolling_over && (
        <div className="rounded bg-warn/10 px-2 py-1 text-[11px] text-warn">
          <b>The afternoon has turned.</b> The last three readings are falling while the
          six-reading average is still rising — the peak is behind, and any bucket above{" "}
          {fmtTemp(a.running_max_c, unit)} now needs a new maximum this day is not making.
        </div>
      )}

      {a.typical_climb_left_c != null ? (
        <div className="text-[11px] text-muted">
          At this hour this city has historically climbed a further{" "}
          <b className="text-text">{fmtTempDelta(a.typical_climb_left_c, unit)}</b> before peaking
          {a.climb_left_p10_c != null && a.climb_left_p90_c != null && (
            <> ({fmtTempDelta(a.climb_left_p10_c, unit)} to {fmtTempDelta(a.climb_left_p90_c, unit)})</>
          )}
          {a.profile_days != null && <>, measured over {a.profile_days} day(s)</>}.
          {a.pct_already_peaked != null && a.pct_already_peaked > 20 && (
            <>
              {" "}On <b className="text-warn">{a.pct_already_peaked.toFixed(0)}%</b> of them the
              day was already over by now.
            </>
          )}
        </div>
      ) : (
        <div className="text-[11px] text-muted">
          No climb profile for this hour yet — it needs 20+ well-observed days per city-hour.
          Backfill with <b>Actions → Observations</b>, then re-run <code>sql/ad4_26</code>.
        </div>
      )}

      {gap !== null && Math.abs(gap) >= 0.5 && (
        <div className="rounded bg-accent/10 px-2 py-1 text-[11px] text-accent">
          Observation implies <b>{fmtTempDelta(gap, unit)}</b> against the forecast. Two independent
          estimates of the same number disagree by more than half a degree — one of them is about to
          be wrong.
        </div>
      )}
    </div>
  );
}

function Figure({ label, value, sub, big }: { label: string; value: string; sub?: string; big?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`font-mono ${big ? "text-2xl" : "text-lg"}`}>{value}</div>
      {sub && <div className="font-mono text-[10px] text-muted">{sub}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------- trace -- */

function TempTrace({
  rows, a, c, unit,
}: { rows: Reading[]; a: Approach | null; c: CityStats | null; unit: Unit }) {
  const toUnit = (v: number) => (unit === "F" ? v * 9 / 5 + 32 : v);
  const pts = rows.map((r) => ({ x: r.local_hour, y: toUnit(r.temp_c) }));
  const x0 = Math.min(...pts.map((p) => p.x));
  const x1 = Math.max(...pts.map((p) => p.x));

  // A horizontal reference is a two-point series across the same x range -
  // cheaper and more honest than a second chart component.
  const flat = (v: number | null | undefined, label: string, color: string) =>
    v == null ? [] : [{ label, color, dashed: true, points: [{ x: x0, y: toUnit(v) }, { x: x1, y: toUnit(v) }] }];

  const series = [
    { label: "observed", color: "#4f8cff", points: pts, fill: true },
    ...flat(c?.forecast_max_c, "forecast max", "#ffb020"),
    ...flat(a?.implied_max_c, "implied max", "#2ecc71"),
  ];

  return (
    <div>
      <LineChart
        series={series}
        height={220}
        yLabel={unit === "F" ? "°F" : "°C"}
        xTickFormat={(h) => `${String(Math.floor(h)).padStart(2, "0")}:00`}
        yTickFormat={(v) => v.toFixed(1)}
      />
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted">
        <Legend color="#4f8cff" label="observed, local day" />
        {c?.forecast_max_c != null && <Legend color="#ffb020" label="forecast maximum" />}
        {a?.implied_max_c != null && <Legend color="#2ecc71" label="implied by observation alone" />}
        <span>
          {rows.length} reading(s)
          {rows.some((r) => r.source === "NWS") && " · NWS series"}
        </span>
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-[2px] w-4" style={{ background: color }} />
      {label}
    </span>
  );
}

/* ---------------------------------------------------------------- prices -- */

function PriceTrace({ rows, ladder }: { rows: PriceRow[]; ladder: LadderRow[] }) {
  // Six lines is the most a reader can follow. Ranked by model probability
  // where we have it, so the buckets in play are the ones drawn.
  const rank = new Map(ladder.map((l) => [l.band_id, l.model_prob ?? 0]));
  const byBand = new Map<string, PriceRow[]>();
  for (const r of rows) {
    if (r.mid == null) continue;
    const list = byBand.get(r.band_id) ?? [];
    list.push(r);
    byBand.set(r.band_id, list);
  }
  const chosen = [...byBand.entries()]
    .sort((x, y) => (rank.get(y[0]) ?? 0) - (rank.get(x[0]) ?? 0))
    .slice(0, 6);

  if (!chosen.length) return <Empty height={200}>No bucket has a mid price in the last 48 hours.</Empty>;

  const series = chosen.map(([band_id, list], i) => ({
    label: list[0].band_label ?? band_id.slice(0, 6),
    color: SERIES_COLORS[i % SERIES_COLORS.length],
    points: list.map((r) => ({ x: new Date(r.observed_at).getTime(), y: r.mid as number })),
  }));

  return (
    <div>
      <LineChart
        series={series}
        height={200}
        yLabel="mid"
        xTickFormat={(t) => new Date(t).toISOString().slice(11, 16)}
        yTickFormat={(v) => `${(v * 100).toFixed(0)}c`}
      />
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted">
        {series.map((s) => <Legend key={s.label} color={s.color} label={s.label} />)}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- ladder -- */

function Ladder({ rows, a, unit }: { rows: LadderRow[]; a: Approach | null; unit: Unit }) {
  const sorted = useMemo(
    () => [...rows].sort((x, y) => (x.band_lo ?? -999) - (y.band_lo ?? -999)),
    [rows]
  );

  // The cover pair, from web/lib/cover.ts - the same arithmetic the signal
  // engine runs, held to it by tests/test_strategies.py. A desk that sees
  // "cover pair live" here and gets no signal has learned to distrust both.
  const cover = useMemo(() => findCover(rows), [rows]);

  return (
    <div className="space-y-2">
      {cover && (
        <div className={`rounded px-2 py-1.5 text-[11px] ${cover.qualifies ? "bg-good/10 text-good" : "bg-panel2 text-muted"}`}>
          {cover.qualifies ? (
            <>
              <b>Cover pair live.</b> {cover.labels[0]} + {cover.labels[1]} cost{" "}
              <b>{fmtPrice(cover.total)}</b> together including{" "}
              {fmtPrice(cover.fee)} of fee, and exactly one of them settles at $1.00 —{" "}
              <b>{cover.returnPct?.toFixed(1)}%</b> if the day lands anywhere in the pair. The model
              makes that {fmtPct(cover.prob, 0)}.
            </>
          ) : (
            <>
              <b>No cover pair.</b> The two most likely buckets{" "}
              {cover.blockedBy === "not_adjacent"
                ? "are not adjacent — a gap between them is not a cover, it is two bets"
                : cover.blockedBy === "too_expensive"
                  ? `cost ${fmtPrice(cover.total)} together, over the 70c cap`
                  : `cost ${fmtPrice(cover.total)} against a model probability of ${fmtPct(cover.prob, 0)} — paying more than it is worth`}
              .
            </>
          )}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-muted">
            <tr>
              <th className="p-2 text-left">Bucket</th>
              <th className="p-2 text-right">Model P</th>
              <th className="p-2 text-right">Price</th>
              <th className="p-2 text-right">Edge</th>
              <th className="p-2 text-right">Vol 24h</th>
              <th className="p-2 text-left">Where the day is</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const inRunning = contains(r, a?.running_max_c, unit);
              const inImplied = contains(r, a?.implied_max_c, unit);
              return (
                <tr
                  key={r.band_id}
                  className={`border-t border-border ${cover?.ids.has(r.band_id) && cover.qualifies ? "bg-good/5" : ""}`}
                >
                  <td className="p-2 font-mono">
                    {r.band_label ?? fmtBandRange(r.band_lo, r.band_hi, unit, r.open_low, r.open_high)}
                  </td>
                  <td className="p-2 text-right font-mono">{fmtPct(r.model_prob, 0)}</td>
                  <td className="p-2 text-right font-mono">{fmtPrice(r.market_price)}</td>
                  <td className={`p-2 text-right font-mono ${(r.edge_net_pp ?? 0) > 0 ? "text-good" : "text-muted"}`}>
                    {fmtPp(r.edge_net_pp)}
                  </td>
                  <td className="p-2 text-right font-mono text-muted">{fmtCompactUsd(r.volume_usd ?? 0)}</td>
                  <td className="p-2 text-[10px]">
                    {inRunning && <span className="mr-2 text-warn">max so far</span>}
                    {inImplied && <span className="text-good">heading here</span>}
                    {!r.tradeable && <span className="ml-2 text-muted">blocked</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Bands are labelled in the city's unit; the archive is Celsius. */
function contains(r: LadderRow, celsius: number | null | undefined, unit: Unit): boolean {
  if (celsius == null) return false;
  const v = unit === "F" ? celsius * 9 / 5 + 32 : celsius;
  if (r.open_low) return r.band_hi != null && v < r.band_hi;
  if (r.open_high) return r.band_lo != null && v >= r.band_lo;
  if (r.band_lo == null || r.band_hi == null) return false;
  return r.band_lo <= v && v < r.band_hi;
}

/* --------------------------------------------------------------- chrome -- */

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-border bg-panel">
      <div className="border-b border-border px-3 py-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {hint && <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{hint}</p>}
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}
