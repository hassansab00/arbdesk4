"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { LineChart, Empty } from "@/components/charts";
import { fmtPct, fmtPp } from "@/lib/format";
import { fmtResolutionDate } from "@/lib/time";

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
/**
 * A panel that is EMPTY still has to be worth reading.
 *
 * "Nothing measured yet" on seven panels in a row reads as a broken page. It
 * is not: most of these answer questions that need settled outcomes, and a
 * desk three days old has none. So an empty panel now says what IS known -
 * how many rows exist, how far off the answer is, and what will produce it -
 * and shows a progress bar toward the sample size at which the number starts
 * to mean anything. A honest "12 of 300, first settlement in 9 hours" is a
 * different message from "nothing", and only one of them is actionable.
 */
const MIN_FOR_A_CURVE = 300;   // matches components/DataBank.tsx - one threshold, not two

function Panel({
  title, hint, sql, q, isEmpty, emptyBody, progress, aside, children, explain,
}: {
  title: string; hint: string; sql: string;
  q: { loading: boolean; error: string | null; refresh: () => void };
  isEmpty: boolean; emptyBody: React.ReactNode;
  progress?: { have: number; need: number; label: string; note?: React.ReactNode };
  aside?: React.ReactNode;
  children: React.ReactNode;
  /**
   * The panel in plain words, with a worked example.
   *
   * `hint` says what the chart IS in one sentence, which is enough for
   * someone who already knows the concept and no help at all to anyone else.
   * These two panels measure the two things that decide whether the desk is
   * real - are the probabilities honest, and does claimed edge turn into
   * money - and both were being skipped because nobody could tell what they
   * were looking at. A worked example with actual numbers in it is the
   * difference.
   */
  explain?: React.ReactNode;
}) {
  const missing = q.error && /does not exist|schema cache|could not find/i.test(q.error);
  return (
    <section className="rounded border border-border bg-panel">
      <div className="border-b border-border px-3 py-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-muted">{hint}</p>
        {explain ? (
          <details className="group mt-1.5">
            <summary className="cursor-pointer list-none text-[11px] text-accent hover:underline">
              <span className="group-open:hidden">What am I looking at? →</span>
              <span className="hidden group-open:inline">Hide the explanation ↑</span>
            </summary>
            <div className="mt-1.5 max-w-3xl space-y-2 rounded border border-border bg-panel2/50 px-2.5 py-2 text-[11px] leading-relaxed text-muted">
              {explain}
            </div>
          </details>
        ) : null}
      </div>
      {aside}
      <div className="p-3">
        {missing ? (
          <p className="text-xs text-muted">
            Not installed — run <code>{sql}</code>.
          </p>
        ) : (
          <DataState
            loading={q.loading} error={q.error} isEmpty={isEmpty}
            emptyTitle="Nothing measured yet" emptyBody={
              <>
                {emptyBody}
                {progress && <Progress {...progress} />}
              </>
            }
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
  // WHAT IS KNOWN WHILE THE ANSWERS ARE STILL COMING. Every panel below that
  // needs settled outcomes can at least say how many exist, how many are on
  // their way, and when the next one lands.
  /**
   * COUNTED BY THE DATABASE, not by fetching rows and calling .length.
   *
   * This asked for 5,000 rows of fact_band_outcome and counted what came
   * back. PostgREST caps a response at 1,000 rows by default, so the answer
   * was always "1,000" the moment the table passed a thousand rows - which is
   * how the progress bar came to read "1,000 of 300" while the panel beside
   * it said "Nothing measured yet". Two numbers from the same page
   * contradicting each other is worse than either being missing.
   *
   * head + count asks Postgres for the count and transfers no rows at all.
   */
  const banked = useQuery<{ count: number }>(
    async () => {
      const { count, error } = await supabase
        .from("v_verified_fact_band_outcome")
        .select("*", { count: "exact", head: true });
      return { data: { count: count ?? 0 }, error };
    },
    [], 300000
  );
  const pending = useQuery<Array<{ resolution_date: string }>>(
    () => supabase.from("v_opportunities").select("resolution_date").eq("side", "YES").limit(4000), [], 300000, 4000
  );
  const settling = useMemo(() => {
    const days = (pending.data ?? []).map((r) => r.resolution_date).filter(Boolean).sort();
    const bands = days.length;
    const next = days[0] ?? null;
    return { bands, next };
  }, [pending.data]);
  const nBanked = banked.data?.count ?? 0;

  /** Shared by the two panels that wait on settled outcomes. */
  const settledProgress = {
    have: nBanked,
    need: MIN_FOR_A_CURVE,
    label: "settled bands banked",
    note: (
      <>
        {settling.bands > 0 ? (
          <>
            {settling.bands.toLocaleString()} band{settling.bands === 1 ? " is" : "s are"} priced on
            the board right now
            {settling.next ? <>, the first settling <b>{fmtResolutionDate(settling.next)}</b></> : ""}.
            Each settled band becomes one row here.
          </>
        ) : (
          <>Nothing is priced on the board yet, so nothing is on its way to settling.</>
        )}
      </>
    ),
  };

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
        aside={<CalibrationKey />}
        explain={
          <>
            <p>
              <strong className="text-text">The question.</strong> The desk does not predict
              temperatures, it prices <em>probabilities</em>: &ldquo;this band has a 30% chance&rdquo;.
              A probability is only worth anything if it is <em>honest</em> — if the things it
              calls 30% happen about 30% of the time. That is what calibration means, and it is a
              completely different question from being accurate.
            </p>
            <p>
              <strong className="text-text">How to read it.</strong> Every band the desk has ever
              priced is put in a bucket by what the model claimed, then we count how many of them
              actually happened. Take the bucket where the model said <b>30%</b>. If 30 out of
              those 100 bands won, the dot sits on the diagonal and the model is honest at 30%. If
              only <b>18</b> won, the dot sits below the line: the model says 30% and means 18%,
              so it is <b>overconfident</b> and every edge computed from it is overstated. If{" "}
              <b>42</b> won, it is underconfident and the desk is leaving money on the table.
            </p>
            <p>
              <strong className="text-text">Why it is not the same as being accurate.</strong> A
              model can have an excellent temperature error and still be badly calibrated — right
              about the middle and far too sure of itself about the spread. That failure is
              invisible in every error metric and shows up here and nowhere else. When it appears,{" "}
              <code>scripts/calibration.py</code> fits a correction and the desk stops overstating
              its edge; <code>sql/ad4_45</code> does the same for the width of the distribution.
            </p>
            <p>
              <strong className="text-text">What to do about it.</strong> On the diagonal: nothing,
              trade the edges as computed. Consistently below: the desk is over-betting and every
              position should be smaller until the correction lands. Consistently above: it is
              under-betting. A curve built on fewer than {MIN_FOR_A_CURVE} settled outcomes is
              noise, so it is not drawn at all rather than drawn faintly.
            </p>
          </>
        }
        emptyBody={<>Needs venue-verified bands. The settlement collector first requires Gamma and CLOB to agree on the complete ladder; the <b>Databank</b> action then freezes the result. Unverified history stays archived but cannot train this curve. Under {MIN_FOR_A_CURVE} outcomes the curve is noise dressed as evidence, so it is not drawn.</>}
        progress={settledProgress}
      >
        <CalibrationChart rows={calib.data ?? []} />
      </Panel>

      {/* ---- 2. does edge become money ------------------------------- */}
      <Panel
        title="Does claimed edge become money?"
        hint="Edge is a prediction about profit. This is the only place it is checked against what actually happened — grouped by how much edge was claimed, so a desk that is right about small edges and wrong about large ones can see it."
        sql="sql/ad4_18_databank.sql + Actions → Databank"
        q={edge} isEmpty={(edge.data?.length ?? 0) === 0}
        explain={
          <>
            <p>
              <strong className="text-text">What &ldquo;edge&rdquo; is.</strong> When the market
              prices a band at <b>30c</b> and the desk&apos;s model says it has a <b>38%</b> chance,
              the desk is claiming <b>8 percentage points of edge</b>: it believes it is buying
              something worth 38c for 30c. That number is a <em>prediction about profit</em>, and
              like any prediction it can be wrong.
            </p>
            <p>
              <strong className="text-text">What this table checks.</strong> Every settled band is
              grouped by how much edge was claimed on it, and then we ask what those bands
              actually returned. <b>Claimed</b> is what the desk said it was getting.{" "}
              <b>Realised</b> is what it got. Buy a hundred bands at 30c claiming 8 pp of edge:
              if 38 of them win, you paid $30 and collected $38, and realised matches claimed. If
              only 31 win, you collected $31 — the claim was 8 pp and the reality was 1 pp, and{" "}
              <b>seven points of the edge were imaginary</b>.
            </p>
            <p>
              <strong className="text-text">Why it is split by size.</strong> The interesting
              failure is not being wrong everywhere — it is being right about small edges and
              wrong about big ones. A 2 pp claim that realises 2 pp and a 20 pp claim that
              realises −4 pp average out to something that looks fine, and they mean opposite
              things: a huge claimed edge is usually the model misunderstanding a market rather
              than beating it. Split by bucket, that shows immediately.
            </p>
            <p>
              <strong className="text-text">What to do about it.</strong> Realised tracking
              claimed down the column: the model is real, trade it. Realised falling away in the
              large-edge rows only: cap the size the desk will take on a big claimed edge, because
              those are the ones it is wrong about. Realised negative across the board: the edge
              is not there and the fee and slippage model is eating it — check the fill
              assumptions on Goals before the model.
            </p>
          </>
        }
        emptyBody={<>Needs settled bands. Same source as calibration above.</>}
        progress={settledProgress}
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
        <PersistenceTable rows={persist.data ?? []} />
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

/**
 * A number with its own magnitude drawn beside it.
 *
 * These panels were tables of digits, and a table of digits is read one cell
 * at a time - which is a fair complaint about a page technically full of
 * correct numbers. A bar scaled to the column's own maximum turns each table
 * into a ranking you can see, without changing what any figure means.
 */
function Bar({ v, max, tone = "accent", suffix = "" }: {
  v: number | null | undefined; max: number; tone?: "accent" | "good" | "warn" | "bad"; suffix?: string;
}) {
  if (v == null || !Number.isFinite(v)) return <span className="text-muted">—</span>;
  const pct = max > 0 ? Math.min(100, (Math.abs(v) / max) * 100) : 0;
  const bg = { accent: "bg-accent/60", good: "bg-good/60", warn: "bg-warn/60", bad: "bg-bad/60" }[tone];
  return (
    <span className="flex items-center justify-end gap-1.5">
      <span className="relative h-1.5 w-14 shrink-0 overflow-hidden rounded bg-panel2">
        <span className={`absolute inset-y-0 left-0 ${bg}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular-nums">{v.toFixed(2)}{suffix}</span>
    </span>
  );
}

/**
 * A signed value drawn from a centre line, so "warming 0.9" and "cooling 0.9"
 * lean in opposite directions instead of being two numbers with a minus sign
 * between them.
 */
function Diverging({ v, max, suffix = "" }: { v: number | null | undefined; max: number; suffix?: string }) {
  if (v == null || !Number.isFinite(v)) return <span className="text-muted">—</span>;
  const pct = max > 0 ? Math.min(50, (Math.abs(v) / max) * 50) : 0;
  return (
    <span className="flex items-center gap-1.5">
      <span className="relative h-1.5 w-16 shrink-0 rounded bg-panel2">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <span
          className={`absolute inset-y-0 ${v >= 0 ? "left-1/2 bg-good/60" : "bg-bad/60"}`}
          style={v >= 0 ? { width: `${pct}%` } : { right: "50%", width: `${pct}%` }}
        />
      </span>
      <span className={`tabular-nums ${v >= 0 ? "text-good" : "text-bad"}`}>
        {v >= 0 ? "+" : ""}{v.toFixed(2)}{suffix}
      </span>
    </span>
  );
}

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
          {/* Bars scaled to THIS variable's own range, so the shape of the
              effect - monotonic, flat, or one outlier bucket - is visible.
              A coefficient that only holds because of one bucket is the kind
              of thing a column of numbers hides and a bar chart cannot. */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="p-1 text-left">Bucket</th>
                  <th className="p-1 text-right">Days</th>
                  <th className="p-1 text-right">Climbed from morning</th>
                  <th className="p-1 text-left">Change on yesterday</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => {
                  const maxClimb = Math.max(...list.map((x) => x.avg_climb_c ?? 0), 0.1);
                  const maxChange = Math.max(...list.map((x) => Math.abs(x.avg_day_change_c ?? 0)), 0.1);
                  return (
                    <tr key={r.bucket} className="border-t border-border">
                      <td className="p-1">{r.bucket}</td>
                      <td className={`p-1 text-right font-mono tabular-nums ${r.n < 20 ? "text-warn" : "text-muted"}`}
                          title={r.n < 20 ? "Under 20 days - too thin to read as an effect." : ""}>
                        {r.n}
                      </td>
                      <td className="p-1 text-right font-mono">
                        <Bar v={r.avg_climb_c} max={maxClimb} tone="good" suffix="°C" />
                      </td>
                      <td className="p-1 font-mono">
                        <Diverging v={r.avg_day_change_c} max={maxChange} suffix="°C" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}


/**
 * How far off the answer is, in the units of the thing that produces it.
 *
 * The bar is not decoration: the whole reason these panels are empty is that
 * a statistical answer needs a sample, and "how big is the sample so far"
 * is the only actionable fact available before it arrives.
 */
function Progress({ have, need, label, note }: { have: number; need: number; label: string; note?: React.ReactNode }) {
  const pct = need > 0 ? Math.min(100, (have / need) * 100) : 0;
  return (
    <div className="mt-2 max-w-md">
      <div className="flex items-baseline justify-between font-mono text-[10px]">
        <span className="text-muted">{label}</span>
        <span className={have >= need ? "text-good" : "text-muted"}>
          {have.toLocaleString()} of {need.toLocaleString()}
        </span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded bg-panel2">
        <div className={`h-full ${have >= need ? "bg-good" : "bg-accent/70"}`} style={{ width: `${pct}%` }} />
      </div>
      {note && <div className="mt-1 text-[10px] leading-relaxed text-muted">{note}</div>}
    </div>
  );
}

/**
 * What a calibration curve is, drawn rather than described.
 *
 * "Is a 30% actually a 30%" is the single most important question about this
 * desk and the hardest to picture, and the panel above it was a title over an
 * empty box. This is the reference: the diagonal is honesty, and the two ways
 * of being wrong bend away from it in opposite directions and cost money in
 * opposite ways.
 */
function CalibrationKey() {
  const W = 300, H = 150, PAD = 26;
  const X = (p: number) => PAD + p * (W - PAD - 8);
  const Y = (p: number) => H - PAD - p * (H - PAD - 10);
  // Clamped to [0, 1]: an observed frequency cannot be negative or over 100%,
  // and an unclamped curve drew itself outside the axes - which made the
  // reference diagram wrong in the one way a reference diagram must not be.
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const curve = (f: (p: number) => number) =>
    Array.from({ length: 41 }, (_, i) => i / 40).map((p) => `${X(p)},${Y(clamp(f(p)))}`).join(" ");
  // Overconfident: extreme claims happen LESS often than claimed, so the curve
  // is flatter than the diagonal and pulled toward the middle.
  const over = (p: number) => 0.5 + (p - 0.5) * 0.55;
  // Underconfident: hedged claims happen MORE often than claimed, so the curve
  // is steeper and saturates at both ends.
  const under = (p: number) => 0.5 + (p - 0.5) * 1.8;
  return (
    <div className="flex flex-wrap items-start gap-4 border-b border-border bg-panel2/40 px-3 py-2">
      <svg viewBox={`0 0 ${W} ${H}`} width={260} className="shrink-0" role="img" aria-label="Calibration reference">
        <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--c-good)" strokeWidth={1.5} strokeDasharray="4 3" />
        <polyline points={curve(over)} fill="none" stroke="var(--c-bad)" strokeWidth={1.5} />
        <polyline points={curve(under)} fill="none" stroke="var(--c-warn)" strokeWidth={1.5} />
        <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(0)} stroke="var(--c-border)" />
        <line x1={X(0)} y1={Y(0)} x2={X(0)} y2={Y(1)} stroke="var(--c-border)" />
        <text x={X(0.5)} y={H - 6} textAnchor="middle" fontSize="9" fill="var(--c-muted)">what the model claimed</text>
        <text x={9} y={Y(0.5)} fontSize="9" fill="var(--c-muted)" transform={`rotate(-90 9 ${Y(0.5)})`} textAnchor="middle">
          how often it happened
        </text>
      </svg>
      <div className="min-w-[220px] flex-1 space-y-1 text-[11px] leading-relaxed">
        <div><span className="text-good">▬ ▬</span> <b>Calibrated.</b> Bands priced at 30% settle YES 30% of the time. Nothing to fix.</div>
        <div><span className="text-bad">▬</span> <b>Overconfident.</b> The curve is flatter than the diagonal: when the model says 80% it happens 65% of the time. This is the expensive one — it makes the desk buy favourites that are not favourites, and every edge computed from those probabilities is overstated.</div>
        <div><span className="text-warn">▬</span> <b>Underconfident.</b> Steeper than the diagonal: the model hedges toward 50% and is right more often than it claims. Costs opportunity rather than money — real edges get filtered out as too small.</div>
        <div className="text-muted">The fix for either is sigma: overconfidence means the model&apos;s spread is too narrow for how wrong its forecasts actually are.</div>
      </div>
    </div>
  );
}


/**
 * The bar every forecast has to clear, drawn as a ranking.
 *
 * Persistence MAE decides whether a city is worth forecasting at all - a high
 * number means yesterday is a poor guide and there is room to add value, a low
 * one means nothing you can do beats doing nothing. It was the third column of
 * a six-column table of digits, which is precisely the wrong shape for a
 * question whose answer is an ordering.
 */
function PersistenceTable({ rows }: { rows: Persist[] }) {
  const sorted = [...rows].sort((a, b) => (b.persistence_mae_c ?? 0) - (a.persistence_mae_c ?? 0));
  const maxMae = Math.max(...sorted.map((r) => r.persistence_mae_c ?? 0), 0.1);
  const maxSwing = Math.max(...sorted.map((r) => r.biggest_swing_c ?? 0), 0.1);
  const maxDrift = Math.max(...sorted.map((r) => Math.abs(r.mean_drift_c ?? 0)), 0.1);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted">
          <tr>
            <th className="p-1 text-left">City</th>
            <th className="p-1 text-right">Days</th>
            <th className="p-1 text-right" title="How wrong yesterday's maximum is as a forecast for today. A HIGH number is a beatable city - there is room for a forecast to add value. A low one means nothing you can do adds much.">
              Persistence MAE
            </th>
            <th className="p-1 text-right" title="Standard deviation of the day-to-day change. High means the band the day lands in is genuinely uncertain.">
              Day-to-day σ
            </th>
            <th className="p-1 text-left" title="Mean day-to-day change over the archive: a seasonal drift, warming or cooling.">
              Drift
            </th>
            <th className="p-1 text-right" title="The largest single-day move in the archive.">Biggest swing</th>
            <th className="p-1 text-left">Worth forecasting?</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.city_key} className="border-t border-border">
              <td className="p-1">{r.city_key}</td>
              <td className="p-1 text-right font-mono tabular-nums text-muted">{r.n_days}</td>
              <td className="p-1 text-right font-mono">
                <Bar v={r.persistence_mae_c} max={maxMae}
                     tone={(r.persistence_mae_c ?? 0) > maxMae * 0.6 ? "good" : "accent"} suffix="°C" />
              </td>
              <td className="p-1 text-right font-mono tabular-nums text-muted">
                {r.delta_sd_c?.toFixed(2) ?? "—"}
              </td>
              <td className="p-1 font-mono"><Diverging v={r.mean_drift_c} max={maxDrift} suffix="°C" /></td>
              <td className="p-1 text-right font-mono">
                <Bar v={r.biggest_swing_c} max={maxSwing} tone="warn" suffix="°C" />
              </td>
              <td className="p-1 text-[11px]">
                {r.persistence_mae_c == null ? (
                  <span className="text-muted">—</span>
                ) : r.persistence_mae_c > 2.5 ? (
                  <span className="text-good">yes — yesterday is a poor guide here</span>
                ) : r.persistence_mae_c > 1.5 ? (
                  <span className="text-muted">some room</span>
                ) : (
                  <span className="text-warn">barely — yesterday is already close</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
