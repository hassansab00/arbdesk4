"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { LineChart, Empty } from "@/components/charts";
import { fmtPct, fmtPp } from "@/lib/format";

/**
 * What the desk has learned about itself.
 *
 * Analytics showed five things and three of them needed settled paper trades,
 * so on a desk with no enabled strategies it was four-fifths empty. Meanwhile
 * seven views full of answers sat unread: whether the model's probabilities are
 * honest, whether claimed edge becomes money, whether the forward model beats
 * the forecast it post-processes, how wrong the CLOUD forecast is, what
 * actually moves a day, and how much of one is left at any hour.
 *
 * None of these need a single trade to have been placed. They are what the
 * archive can answer on its own, which is the point of keeping it.
 */

interface Calib { bucket: string; predicted_mid: number | null; n: number; predicted: number | null; observed: number | null; gap: number | null; market_avg: number | null; model_mae: number | null; market_mae: number | null }
interface EdgeReal { edge_bucket: string; ord: number; n: number; claimed_pp: number | null; realised_pp: number | null; hit_rate: number | null; avg_price: number | null; volume_usd: number | null }
interface FwdSkill { city_key: string; lead_days: number; n_days: number; model_mae_c: number | null; model_bias_c: number | null; nws_mae_c: number | null; persistence_mae_c: number | null; beat_nws: boolean | null; beat_persistence: boolean | null }
interface CondSkill { city_key: string; n_days: number; cloud_mae_oktas: number | null; cloud_bias_oktas: number | null; dryness_mae_c: number | null; dryness_bias_c: number | null; max_mae_c: number | null }
interface Effect { variable: string; bucket: string; n: number; avg_climb_c: number | null; avg_day_change_c: number | null }
interface Persist { city_key: string; n_days: number; persistence_mae_c: number | null; delta_sd_c: number | null; mean_drift_c: number | null; biggest_swing_c: number | null }
interface Climb { city_key: string; local_hour: number; n_days: number; typical_climb_left_c: number | null; climb_left_p10_c: number | null; climb_left_p90_c: number | null; pct_already_peaked: number | null }

/** Every panel here reads one view. A missing view is a named SQL file, not a
 *  red box - so the page degrades one section at a time. */
function Panel({
  title, hint, sql, q, isEmpty, emptyBody, children,
}: {
  title: string; hint: string; sql: string;
  q: { loading: boolean; error: string | null; refresh: () => void };
  isEmpty: boolean; emptyBody: React.ReactNode; children: React.ReactNode;
}) {
  const missing = q.error && /does not exist|schema cache|could not find/i.test(q.error);
  return (
    <section className="rounded border border-border bg-panel">
      <div className="border-b border-border px-3 py-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-muted">{hint}</p>
      </div>
      <div className="p-3">
        {missing ? (
          <p className="text-xs text-muted">
            Not installed — run <code>{sql}</code>.
          </p>
        ) : (
          <DataState
            loading={q.loading} error={q.error} isEmpty={isEmpty}
            emptyTitle="Nothing measured yet" emptyBody={emptyBody}
            onRetry={q.refresh} compact
          >
            {children}
          </DataState>
        )}
      </div>
    </section>
  );
}

export default function ModelAnalytics() {
  const [city, setCity] = useState<string>("");

  const calib = useQuery<Calib[]>(() => supabase.from("v_calibration").select("*"), [], 300000);
  const edge = useQuery<EdgeReal[]>(() => supabase.from("v_edge_realisation").select("*").order("ord"), [], 300000);
  const fwd = useQuery<FwdSkill[]>(() => supabase.from("v_model_forecast_skill").select("*"), [], 300000);
  const cond = useQuery<CondSkill[]>(() => supabase.from("v_condition_skill").select("*"), [], 300000);
  const effects = useQuery<Effect[]>(() => supabase.from("v_weather_effects").select("*"), [], 300000);
  const persist = useQuery<Persist[]>(() => supabase.from("v_persistence_skill").select("*"), [], 300000);
  const climb = useQuery<Climb[]>(() => supabase.from("v_city_climb_profile").select("*"), [], 300000);

  const climbCities = useMemo(
    () => Array.from(new Set((climb.data ?? []).map((c) => c.city_key))).sort(),
    [climb.data]
  );
  const climbCity = city || climbCities[0] || "";
  const climbRows = useMemo(
    () => (climb.data ?? []).filter((c) => c.city_key === climbCity).sort((a, b) => a.local_hour - b.local_hour),
    [climb.data, climbCity]
  );

  return (
    <div className="space-y-4">
      {/* ---- 1. is the model honest ---------------------------------- */}
      <Panel
        title="Calibration — is a 30% actually a 30%?"
        hint="Every band the desk priced, grouped by what the model claimed, against how often it happened. A model can have a wonderful error and still be systematically overconfident, and that shows up here and nowhere else."
        sql="sql/ad4_18_databank.sql + Actions → Databank"
        q={calib} isEmpty={(calib.data?.length ?? 0) === 0}
        emptyBody={<>Needs settled bands in <code>fact_band_outcome</code>. The <b>Databank</b> action fills it daily once markets start resolving.</>}
      >
        <CalibrationChart rows={calib.data ?? []} />
      </Panel>

      {/* ---- 2. does edge become money ------------------------------- */}
      <Panel
        title="Does claimed edge become money?"
        hint="Edge is a prediction about profit. This is the only place it is checked against what actually happened — grouped by how much edge was claimed, so a desk that is right about small edges and wrong about large ones can see it."
        sql="sql/ad4_18_databank.sql + Actions → Databank"
        q={edge} isEmpty={(edge.data?.length ?? 0) === 0}
        emptyBody={<>Needs settled bands. Same source as calibration above.</>}
      >
        <Table
          head={["Claimed edge", "n", "Claimed", "Realised", "Hit rate", "Avg price"]}
          rows={(edge.data ?? []).map((r) => [
            r.edge_bucket, String(r.n), fmtPp(r.claimed_pp),
            <span key="r" className={(r.realised_pp ?? 0) >= 0 ? "text-good" : "text-bad"}>{fmtPp(r.realised_pp)}</span>,
            fmtPct(r.hit_rate, 0), r.avg_price != null ? `${(r.avg_price * 100).toFixed(0)}c` : "—",
          ])}
        />
      </Panel>

      {/* ---- 3. is our own forecast worth anything ------------------- */}
      <Panel
        title="AD4's forward model against NWS and persistence"
        hint="Per LEAD DAY, because a model that is sharp today and useless on Friday looks fine averaged together. Persistence — yesterday's maximum, unchanged — is the bar; NWS is the forecast this model post-processes."
        sql="sql/ad4_25_model_forecast.sql + Actions → Model Forecast"
        q={fwd} isEmpty={(fwd.data?.length ?? 0) === 0}
        emptyBody={<>Needs forward predictions scored against outcomes. Run <b>Weather Model</b>, then <b>Model Forecast</b>, and wait for those days to happen.</>}
      >
        <Table
          head={["City", "Lead", "Days", "AD4", "NWS", "Persistence", "Verdict"]}
          rows={(fwd.data ?? [])
            .sort((a, b) => a.city_key.localeCompare(b.city_key) || a.lead_days - b.lead_days)
            .map((r) => [
              r.city_key, `${r.lead_days}d`, String(r.n_days),
              <b key="m" className={r.beat_nws ? "text-good" : ""}>{r.model_mae_c?.toFixed(2) ?? "—"}</b>,
              r.nws_mae_c?.toFixed(2) ?? "—",
              r.persistence_mae_c?.toFixed(2) ?? "—",
              <span key="v" className={r.beat_persistence ? "text-good" : "text-warn"}>
                {r.beat_persistence ? (r.beat_nws ? "beats both" : "beats persistence") : "loses to persistence"}
              </span>,
            ])}
        />
      </Panel>

      {/* ---- 4. the error entering through a side door --------------- */}
      <Panel
        title="How wrong is the CLOUD forecast?"
        hint="The desk measures temperature error and has never measured this. If cloud is worth about −1 °C per okta, a cloud forecast two oktas out is a 2 °C error entering the model through a side door — invisible to every temperature-based skill metric."
        sql="sql/ad4_24_nws_gridpoint.sql + n8n P1.4"
        q={cond} isEmpty={(cond.data?.length ?? 0) === 0}
        emptyBody={<>Needs forecast conditions to compare against observed ones. Run <b>n8n P1.4</b> and wait for those days to happen.</>}
      >
        <Table
          head={["City", "Days", "Cloud MAE", "Cloud bias", "Dryness MAE", "Dryness bias", "Max MAE"]}
          rows={(cond.data ?? []).map((r) => [
            r.city_key, String(r.n_days),
            r.cloud_mae_oktas != null ? `${r.cloud_mae_oktas.toFixed(1)} oktas` : "—",
            <Bias key="cb" v={r.cloud_bias_oktas} unit=" oktas" />,
            r.dryness_mae_c != null ? `${r.dryness_mae_c.toFixed(1)}°C` : "—",
            <Bias key="db" v={r.dryness_bias_c} unit="°C" />,
            r.max_mae_c != null ? `${r.max_mae_c.toFixed(2)}°C` : "—",
          ])}
        />
      </Panel>

      {/* ---- 5. what actually moves a day --------------------------- */}
      <Panel
        title="What actually moves a day"
        hint="Measured on this desk's own archive, in plain buckets: how much further the afternoon climbed from its morning reading under each condition. This is where the model's coefficients come from, before any fitting."
        sql="sql/ad4_21_weather_features.sql"
        q={effects} isEmpty={(effects.data?.length ?? 0) === 0}
        emptyBody={<>Needs observation history with dewpoint and cloud. Run <b>Actions → Observations</b>.</>}
      >
        <EffectsPanel rows={effects.data ?? []} />
      </Panel>

      {/* ---- 6. the bar every forecast has to clear ----------------- */}
      <Panel
        title="Persistence — the bar, per city"
        hint="Yesterday's maximum, unchanged. It is not a strawman: in a stable air mass it is very hard to beat, and a city with a small day-to-day swing is a city where no forecast can add much. Sorted by how beatable each city is."
        sql="sql/ad4_21_weather_features.sql"
        q={persist} isEmpty={(persist.data?.length ?? 0) === 0}
        emptyBody={<>Needs observation history. Run <b>Actions → Observations</b>.</>}
      >
        <Table
          head={["City", "Days", "Persistence MAE", "Day-to-day σ", "Drift", "Biggest swing"]}
          rows={(persist.data ?? [])
            .sort((a, b) => (b.persistence_mae_c ?? 0) - (a.persistence_mae_c ?? 0))
            .map((r) => [
              r.city_key, String(r.n_days),
              <b key="p">{r.persistence_mae_c?.toFixed(2) ?? "—"}°C</b>,
              r.delta_sd_c?.toFixed(2) ?? "—",
              <Bias key="d" v={r.mean_drift_c} unit="°C" />,
              r.biggest_swing_c != null ? `${r.biggest_swing_c.toFixed(1)}°C` : "—",
            ])}
        />
      </Panel>

      {/* ---- 7. how much of the day is left ------------------------- */}
      <Panel
        title="How much climb is left, by hour"
        hint="Measured per city and local hour: how much further the day still climbed from here, historically. This is what turns a rate of change into a decision — +0.9 °C/h at 13:00 is ordinary in one city and remarkable in another."
        sql="sql/ad4_26_temp_trend.sql"
        q={climb} isEmpty={climbRows.length === 0}
        emptyBody={<>Needs 20+ well-observed days per city-hour. Backfill with <b>Actions → Observations</b>.</>}
      >
        <div className="space-y-2">
          <select
            value={climbCity}
            onChange={(e) => setCity(e.target.value)}
            className="rounded border border-border bg-panel px-2 py-1 text-xs"
          >
            {climbCities.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <ClimbChart rows={climbRows} />
        </div>
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------- pieces -- */

function Bias({ v, unit }: { v: number | null; unit: string }) {
  if (v == null) return <span className="text-muted">—</span>;
  // Bias has a sign and the sign is the finding: a forecast that is
  // consistently high is correctable, one that is merely noisy is not.
  return (
    <span className={Math.abs(v) < 0.2 ? "text-muted" : v > 0 ? "text-warn" : "text-accent"}>
      {v > 0 ? "+" : ""}{v.toFixed(2)}{unit}
    </span>
  );
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-[10px] uppercase tracking-wide text-muted">
          <tr>{head.map((h, i) => <th key={h} className={`p-1.5 ${i === 0 ? "text-left" : "text-right"}`}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {r.map((c, j) => (
                <td key={j} className={`p-1.5 ${j === 0 ? "" : "text-right font-mono"}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CalibrationChart({ rows }: { rows: Calib[] }) {
  const pts = rows
    .filter((r) => r.predicted != null && r.observed != null)
    .map((r) => ({ x: (r.predicted as number) * 100, y: (r.observed as number) * 100 }))
    .sort((a, b) => a.x - b.x);
  if (pts.length < 2) return <Empty height={160}>Not enough settled bands to draw a calibration curve.</Empty>;

  // The diagonal is what perfect calibration looks like. Distance from it is
  // the whole reading, so it is drawn rather than described.
  const lo = Math.min(0, ...pts.map((p) => p.x));
  const hi = Math.max(100, ...pts.map((p) => p.x));
  return (
    <div>
      <LineChart
        height={200}
        yLabel="% that happened"
        xTickFormat={(v) => `${v.toFixed(0)}%`}
        yTickFormat={(v) => `${v.toFixed(0)}%`}
        series={[
          { label: "perfect", color: "#8a93a6", dashed: true, points: [{ x: lo, y: lo }, { x: hi, y: hi }] },
          { label: "the model", color: "#4f8cff", points: pts },
        ]}
      />
      <p className="mt-1 text-[11px] text-muted">
        Above the dashed line the model is <b>under</b>-confident; below it, over-confident. A
        consistent gap is correctable — <code>scripts/calibration.py</code> fits it.
      </p>
    </div>
  );
}

function ClimbChart({ rows }: { rows: Climb[] }) {
  if (rows.length < 2) return <Empty height={160}>Not enough hours measured for this city.</Empty>;
  return (
    <div>
      <LineChart
        height={190}
        yLabel="°C still to climb"
        xTickFormat={(h) => `${String(Math.round(h)).padStart(2, "0")}:00`}
        series={[
          { label: "p90", color: "#2ecc71", dashed: true, points: rows.map((r) => ({ x: r.local_hour, y: r.climb_left_p90_c ?? 0 })) },
          { label: "typical", color: "#4f8cff", fill: true, points: rows.map((r) => ({ x: r.local_hour, y: r.typical_climb_left_c ?? 0 })) },
          { label: "p10", color: "#ffb020", dashed: true, points: rows.map((r) => ({ x: r.local_hour, y: r.climb_left_p10_c ?? 0 })) },
        ]}
      />
      <p className="mt-1 text-[11px] text-muted">
        Where the blue line reaches zero is when this city&apos;s day is normally over. The band
        between p10 and p90 is how much that varies — a wide band means the hour tells you less
        than it looks.
      </p>
    </div>
  );
}

function EffectsPanel({ rows }: { rows: Effect[] }) {
  const byVar = new Map<string, Effect[]>();
  for (const r of rows) {
    const list = byVar.get(r.variable) ?? [];
    list.push(r);
    byVar.set(r.variable, list);
  }
  const span = (list: Effect[]) => {
    const vals = list.map((r) => r.avg_climb_c).filter((v): v is number => v != null);
    return vals.length ? Math.max(...vals) - Math.min(...vals) : 0;
  };
  // Ordered by how much the variable actually SEPARATES days. The one with the
  // widest spread is the one worth trading on, and that is not obvious from an
  // alphabetical list.
  const vars = [...byVar.entries()].sort((a, b) => span(b[1]) - span(a[1]));

  return (
    <div className="space-y-3">
      {vars.map(([name, list]) => (
        <div key={name}>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="text-xs font-semibold">{name.replace(/_/g, " ")}</span>
            <span className="font-mono text-[10px] text-muted">
              spreads the climb by {span(list).toFixed(1)}°C
            </span>
          </div>
          <Table
            head={["Bucket", "Days", "Climbed from morning", "Change on yesterday"]}
            rows={list.map((r) => [
              r.bucket, String(r.n),
              <b key="c">{r.avg_climb_c != null ? `+${r.avg_climb_c.toFixed(1)}°C` : "—"}</b>,
              <Bias key="d" v={r.avg_day_change_c} unit="°C" />,
            ])}
          />
        </div>
      ))}
    </div>
  );
}
