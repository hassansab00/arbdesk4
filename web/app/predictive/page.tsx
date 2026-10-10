"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { readAllRows } from "@/lib/readAll";
import { DataState } from "@/components/DataState";
import PredictionHindsight from "@/components/PredictionHindsight";
import PredictionLineup from "@/components/PredictionLineup";
import CityCards from "@/components/CityCards";
import CalibrationStatus from "@/components/CalibrationStatus";
import FocusFilter, { type FocusSet } from "@/components/FocusFilter";
import { selectedKeys, type HindsightRow, type Scope } from "@/lib/focus";
import type { StatusRow } from "@/lib/cityCards";
import { Freshness, FreshnessRow } from "@/components/Provenance";
import { Empty, LineChart, Scatter } from "@/components/charts";
import Convergence3D, { type ConvergencePoint } from "@/components/Convergence3D";
import { fmtInt, fmtPct, fmtPp, fmtPrice, fmtUsd, pnlColor } from "@/lib/format";
import { fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtResolutionDate } from "@/lib/time";
import {
  forwardRows, groupScorecard, pendingDays, largestLeans,
  type ForwardLadderRow, type MarketDay, type PendingDay,
} from "@/lib/predictive";

/**
 * PREDICTIVE - the same question asked in both directions.
 *
 * Forward: what does the desk expect, which bucket does that land in, what is
 * the market charging, and how much of the bankroll would that justify.
 * Backward: when it expected that before, was it right - and did being right
 * pay, which is a different question with a different answer.
 *
 * Every panel reads a view from sql/ad4_31_predictive.sql. Nothing here is a
 * second calculation of something a trading page already computes: two
 * implementations of "edge" drift apart and then nobody knows which is real.
 */

interface ConvRow {
  city_key: string; for_date: string; model: string; lead_days: number;
  forecast_max_c: number; observed_max_c: number | null;
  error_c: number | null; is_past: boolean; is_settled: boolean;
}
/** sql/ad4_85_city_hit_history.sql - the call as it stood BEFORE the day began. */
interface HitRow {
  city_key: string; display_name: string | null; unit: string | null;
  for_date: string; ladder_bands: number; model_bands: number;
  observed_max_c: number | null; forecast_max_c: number | null; error_c: number | null;
  sigma_c: number | null; confidence: number | null; regime_label: string | null;
  /** the centre the call's own pricing run integrated on, and its miss (null before 22 Sep 18:32Z) */
  centre_c: number | null; centre_error_c: number | null;
  winner: string | null;
  model_call: string | null; model_call_prob: number | null; model_prob_on_winner: number | null;
  model_hit: boolean | null;
  /** over the whole settled ladder; an unpriced band counts as zero */
  brier_model: number | null; brier_uniform: number | null;
  called_at: string | null; hours_before_day: number | null;
  /** both sides priced the winner before the day; the market columns are null otherwise */
  head_to_head: boolean;
  bands_scored: number | null;
  market_call: string | null; market_call_price: number | null; market_prob_on_winner: number | null;
  market_hit: boolean | null;
  brier_model_common: number | null; brier_market: number | null; brier_uniform_common: number | null;
}
interface HitSummaryRow {
  city_key: string; display_name: string | null; days: number;
  model_hits: number; model_hit_rate: number | null;
  avg_model_prob_on_winner: number | null;
  brier_model: number | null; brier_uniform: number | null;
  mae_c: number | null; bias_c: number | null; avg_hours_before_day: number | null;
  first_day: string | null; last_day: string | null;
  h2h_days: number; h2h_model_hits: number; market_hits: number;
  market_hit_rate: number | null; brier_model_h2h: number | null; brier_market: number | null;
  days_we_beat_the_market: number; verdict: string;
  /** mae_c and bias_c are the raw forecast input's; these are the priced centre's */
  centre_days: number; centre_mae_c: number | null; centre_bias_c: number | null;
}
interface LadderRow {
  city_key: string; for_date: string; band_id: string; band_index: number | null;
  band_label: string | null; band_lo: number | null; band_hi: number | null;
  open_low: boolean | null; open_high: boolean | null; closed: boolean | null;
  won: boolean | null; model_prob: number | null; forecast_max_c: number | null;
  centre_c: number | null;
  sigma_c: number | null; confidence: number | null; market_price: number | null;
  edge_net_pp: number | null; depth_5c: number | null; tradeable: boolean | null;
  side: string | null;
}
interface ScoreRow {
  city_key: string; model: string; lead_days: number; n_days: number;
  mae_c: number; bias_c: number; error_sd_c: number | null; worst_c: number;
  hit_rate_pct: number | null; within_1c_pct: number | null;
}
interface BankrollRow {
  day: string; day_pnl: number; n_trades: number; n_won: number;
  cumulative_pnl: number; cumulative_trades: number; cumulative_won: number;
  win_rate_pct: number | null;
}
interface ScalingRow {
  edge_bucket: number; edge_band: string; n: number;
  claimed_edge_pp: number; realised_pp: number;
  settled_yes_pct: number; mean_model_prob_pct: number;
}
interface CityRow {
  city_key: string; display_name: string | null; unit: string | null;
  /** the settlement station and the city's clock: what a day and a reading mean here */
  timezone: string | null; icao: string | null; station_name: string | null;
}
/**
 * How many settled outcomes exist, and how many have survived verification.
 *
 * The backward-looking half of this page reads v_prediction_scorecard and
 * v_forecast_convergence, and both now go through a verification gate: an
 * outcome counts only once weather_resolution_evidence independently
 * corroborates the observed maximum for that city-day. That is a good rule
 * and it is not what the panels SAID when it held everything back - they
 * said "nothing has settled", with 2,268 settled days in the archive.
 *
 * An empty panel is allowed. An empty panel giving the wrong reason is not:
 * "let a few days settle" sends you to wait for something that already
 * happened, when the actual blocker is that the evidence capture has never
 * run. These two counts are the difference, and they come from the database
 * rather than from a sentence someone typed.
 */
interface EvidenceHealth {
  raw_forecast_facts: number; verified_forecast_facts: number;
  weather_evidence_rows: number; latest_weather_evidence_at: string | null;
}

const MISSING = (file: string, extra?: string) => (
  <>
    Run <code className="rounded bg-panel2 px-1">{file}</code>
    {extra ? <> {extra}</> : null}
  </>
);

export default function PredictivePage() {
  const citiesQ = useQuery<CityRow[]>(
    // Active only: a retired city keeps every row it ever wrote, so without
    // this filter the desk would go on listing cities it has stopped trading.
    () => supabase.from("cities").select("city_key,display_name,unit,timezone,icao,station_name")
      .eq("status", "active").order("city_key"), []
  );
  const evidenceQ = useQuery<EvidenceHealth[]>(
    () => supabase.from("v_outcome_evidence_health")
      .select("raw_forecast_facts,verified_forecast_facts,weather_evidence_rows,latest_weather_evidence_at"),
    []
  );
  const cities = citiesQ.data ?? [];
  // THE TWO ALL-CITY PANELS ARE SCOPED SERVER-SIDE. citiesQ is active only, so
  // this is the desk's current roster. It goes into the QUERY rather than a
  // filter afterwards, because both panels run against a row cap: filtering
  // after the cap would silently drop active rows to make room for retired
  // ones, and the truncation warning would be measuring the wrong thing.
  const activeKeys = useMemo(() => cities.map((c) => c.city_key), [cities]);
  const [city, setCity] = useState<string>("");

  /* ---------------------------------------- ONE SELECTION FOR THE PAGE (wave F)
     "All active cities · Seasonal Focus 10 · Custom selection" and the moment a
     call was frozen at. Every panel below takes `selected`, and every total is
     added up from the rows it shows. The custom list is remembered in this
     browser only; nothing about it reaches the database. */
  const [scope, setScope] = useState<Scope>("all");
  const [custom, setCustom] = useState<string[]>([]);
  const [moment, setMoment] = useState<string>("day_ahead");
  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("predictive.custom") ?? "[]");
      if (Array.isArray(saved)) setCustom(saved.filter((x) => typeof x === "string"));
    } catch { /* no storage: start empty */ }
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem("predictive.custom", JSON.stringify(custom)); } catch { /* ignore */ }
  }, [custom]);
  const focusQ = useQuery<FocusSet[]>(
    () => supabase.from("focus_sets").select("set_id,label,city_keys,window_from,window_to,evaluate_from,recorded_at")
      .order("recorded_at", { ascending: false }).limit(1),
    []
  );
  const focus = (focusQ.data ?? [])[0] ?? null;
  // The universe the focus is judged against, frozen when the set was recorded.
  const universeQ = useQuery<Array<{ city_keys: string[] }>>(
    () => focus
      ? supabase.from("focus_set_universes").select("city_keys").eq("set_id", focus.set_id).limit(1)
      : Promise.resolve({ data: [] as Array<{ city_keys: string[] }>, error: null }),
    [focus?.set_id ?? ""]
  );
  const universe = (universeQ.data ?? [])[0]?.city_keys ?? null;
  const selected = useMemo(
    () => selectedKeys(scope, activeKeys, focus?.city_keys ?? [], custom),
    [scope, activeKeys, focus, custom]
  );
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  // EVERY FROZEN CALL, read once and shared: "Was it right?", the focus
  // comparison and each card's record come from these rows. A page at a time,
  // with the truncation flag, because a total over a short read looks like an
  // answer (about 4,000 rows on 6 Oct, 48 more a day per moment).
  const HINDSIGHT_MAX = 40000;
  const hindsightQ = useQuery<HindsightRow[]>(
    () => readAllRows<HindsightRow>((from, to) =>
      supabase.from("v_prediction_hindsight")
        .select("city_key,for_date,called_when,call_order,after_peak,called_at,predicted_band,predicted_pct,actual_band,observed_max_c,forecast_max_c,forecast_error_c,hit,market_band,market_hit,outcome_source,scheduled_local,engine_version,prob_on_winner")
        .order("for_date", { ascending: false }).order("city_key").order("called_when")
        .range(from, to), HINDSIGHT_MAX),
    [], 300000, HINDSIGHT_MAX
  );
  // Each active city's status for its local today and tomorrow, at every moment.
  const statusQ = useQuery<StatusRow[]>(
    () => supabase.from("v_city_status")
      .select("city_key,target_date,checkpoint,status,reason,reasons,station_age_h,forecast_run_at,n_models,models_span_c,disagreement_c,settled_days,min_settled_days,rules_version")
      .limit(2000),
    [], 300000, 2000
  );
  /**
   * Why the backward-looking panels are empty, in the numbers themselves.
   *
   * Returns null when there is nothing unusual to explain, so the ordinary
   * "no data yet" copy still applies to an ordinary empty desk.
   */
  /**
   * A one-line banner, shown ABOVE the settled panels rather than instead of
   * them.
   *
   * These panels read v_prediction_scorecard_all and
   * v_forecast_convergence_all, which score every settled outcome in
   * fact_forecast_outcome. The Phase 2A views score only outcomes a row in
   * weather_resolution_evidence independently corroborates, and that table is
   * empty - so they returned nothing at all, and the page showed no settled
   * history despite 2,268 settled comparisons sitting in the archive.
   *
   * Hiding real evidence is the worse failure. Showing it unlabelled would be
   * too, so the count of what is corroborated rides along with it.
   */
  const unverifiedNote = useMemo(() => {
    const h = (evidenceQ.data ?? [])[0];
    if (!h || !h.raw_forecast_facts) return null;
    if (h.verified_forecast_facts >= h.raw_forecast_facts) return null;
    return (
      <p className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[11px] leading-relaxed text-warn">
        <strong className="font-semibold">Measured, not yet independently verified.</strong>{" "}
        {fmtInt(h.verified_forecast_facts)} of {fmtInt(h.raw_forecast_facts)} settled outcomes have
        a corroborating record in{" "}
        <code className="rounded bg-panel2 px-1">weather_resolution_evidence</code>. These numbers
        come from the desk&rsquo;s own observed maximum, which is the same figure it has always
        scored against — treat them as the working record until that capture runs.
      </p>
    );
  }, [evidenceQ.data]);

  const unverified = useMemo(() => {
    const h = (evidenceQ.data ?? [])[0];
    if (!h) return null;
    if (h.verified_forecast_facts > 0) return null;
    if (!h.raw_forecast_facts) return null;
    return (
      <>
        <strong className="text-text">
          {fmtInt(h.raw_forecast_facts)} settled outcomes are in the archive, and none has been
          verified yet.
        </strong>{" "}
        These panels count an outcome only once{" "}
        <code className="rounded bg-panel2 px-1">weather_resolution_evidence</code> independently
        corroborates that day&rsquo;s observed maximum, and that table holds{" "}
        {fmtInt(h.weather_evidence_rows)} rows
        {h.latest_weather_evidence_at ? null : " - the capture has never run"}. Nothing is lost:
        the outcomes are still in{" "}
        <code className="rounded bg-panel2 px-1">fact_forecast_outcome</code>. They appear here the
        moment the evidence lands.
      </>
    );
  }, [evidenceQ.data]);

  // Declared HERE, above the queries, because the convergence and ladder
  // queries are now filtered by it rather than filtered in the browser.
  // The detail panels show a city inside the selection only: a city picked
  // earlier and then filtered out gives way to the first selected one, and an
  // empty selection shows none (Codex on #327).
  const active = (city && selectedSet.has(city) ? city : selected[0]) ?? "";
  /**
   * WHY THESE ARE SPLIT AND FILTERED, and why it was showing one city.
   *
   * This page used to ask for `v_forecast_convergence` with `.limit(20000)`
   * and then filter by city in the browser. That view carries one row per
   * city, per day, per model, per lead: on a real desk it is 55,000 rows at
   * three weeks of history and grows every day. PostgREST does not warn when
   * it truncates - it returns the first 20,000 rows in view order, and that
   * view is ordered by city_key. So the browser received the alphabetically
   * first cities and nothing else, and every other city's funnel and 3D
   * layers were correctly reported as "no data".
   *
   * It looked like a bug in one city. It was the twentieth-thousandth row.
   *
   * The two panels want different slices, so they are now two queries:
   *   convQ    ONE city, every model and lead - the funnel and its ladder
   *   settledQ EVERY city, but only settled days at lead 1 - the scatter
   * Both are bounded by what they draw rather than by a number, and both
   * carry a truncation guard (useQuery's `limit` option) so if either ever
   * does hit its ceiling the page says so instead of quietly showing less.
   */
  const convQ = useQuery<ConvRow[]>(
    // NEWEST DAYS FIRST, and capped at what the server will actually return.
    // One city over 62 days at three models and eight leads is ~1,500 rows -
    // still past Supabase's 1,000-row ceiling - so the order matters: the
    // chart draws the most recent days, and those are the ones that arrive.
    () => supabase.from("v_forecast_convergence_all").select("*")
            .eq("city_key", active).order("for_date", { ascending: false }).limit(1000),
    [active], 300000, 1000
  );
  const settledQ = useQuery<ConvRow[]>(
    // An empty selection asks for nothing: PostgREST refuses `in.()` (Codex on #327).
    () => selected.length === 0 ? Promise.resolve({ data: [] as ConvRow[], error: null }) :
      supabase.from("v_forecast_convergence_all").select("*")
            .eq("is_settled", true).eq("lead_days", 1)
            .in("city_key", selected)
            .order("for_date", { ascending: false }).limit(1000),
    [selected.join(",")], 300000, 1000
  );
  // Forward only: the ladder is drawn for days that have not resolved, and
  // the whole table is one row per band per SIDE per day for every city -
  // 2,112 rows on 22 Sep, past what one request returns, so it is read a page
  // at a time in a fixed order (band_id + side is unique). The columns are
  // the ones LadderRow declares and nothing else.
  const LADDER_MAX = 12000;
  const ladderQ = useQuery<LadderRow[]>(
    () => readAllRows<LadderRow>((from, to) =>
      supabase.from("v_prediction_ladder")
        .select("city_key,for_date,band_id,band_index,band_label,band_lo,band_hi,open_low,open_high,closed,won,model_prob,forecast_max_c,centre_c,sigma_c,confidence,market_price,edge_net_pp,depth_5c,tradeable,side")
        .gte("for_date", new Date().toISOString().slice(0, 10))
        .order("for_date").order("city_key").order("band_id").order("side")
        .range(from, to), LADDER_MAX),
    [], 120000, LADDER_MAX
  );
  // Every bucket edge of the city's markets in the ladder's window (45 days
  // back, 16 ahead). The stored ladder keeps yesterday on (WXPredict build
  // 2.A), so the edges have their own stored copy: the same set, 1,050 on 7 Oct.
  const cityEdgesQ = useQuery<Array<{ edge: number }>>(
    () => supabase.from("v_city_ladder_edges").select("edge")
            .eq("city_key", active).order("edge").limit(1000),
    [active], 120000, 1000
  );
  // ORDERED BEFORE ANY CAP, AND READ A PAGE AT A TIME (handoff F2). This was
  // `.limit(4000)` with no order, rendered `.slice(0, 120)`: 120 of 797 rows in
  // whatever order Postgres returned them, 6 C cities and 2 of 11 US cities
  // (30 Sep, as anon). PostgREST also returns at most 1,000 rows a request
  // however large the limit, so a bigger view would have been cut silently.
  const SCORE_MAX = 4000;
  const scoreQ = useQuery<ScoreRow[]>(
    () => selected.length === 0 ? Promise.resolve({ data: [] as ScoreRow[], error: null }) :
      readAllRows<ScoreRow>((from, to) =>
      supabase.from("v_prediction_scorecard_all")
        .select("city_key,model,lead_days,n_days,mae_c,bias_c,error_sd_c,worst_c,hit_rate_pct,within_1c_pct")
        .in("city_key", selected)
        .order("city_key").order("model").order("lead_days")
        .range(from, to), SCORE_MAX),
    [selected.join(",")], undefined, SCORE_MAX
  );
  const [scoreCity, setScoreCity] = useState<string>("");
  const [allLeans, setAllLeans] = useState(false);
  const bankQ = useQuery<BankrollRow[]>(
    () => supabase.from("v_bankroll_curve").select("*").limit(2000), [], undefined, 2000
  );
  // HIT AND MISS, FOR ONE CITY. Both views are built on fact_band_outcome,
  // which databank.py freezes only after a day has settled against final
  // station authority and never updates - so nothing here can re-score
  // yesterday with today's knowledge.
  const hitQ = useQuery<HitRow[]>(
    () => supabase.from("v_city_hit_history").select("*")
            .eq("city_key", active).order("for_date", { ascending: false }).limit(400),
    [active], 300000, 400
  );
  // THE CITY'S RECENT DAYS NOT IN THE RECORD YET, and why (handoff F1/F2): a
  // day missing from the table is pending - its local day has not ended, the
  // venue has not confirmed the whole ladder, or it is confirmed and waiting
  // for the record - never a miss.
  const recentMarketsQ = useQuery<MarketDay[]>(
    () => supabase.from("markets").select("resolution_date,resolution_verified_at")
            .eq("city_key", active)
            .gte("resolution_date", new Date(Date.now() - 4 * 86400000).toISOString().slice(0, 10))
            .order("resolution_date", { ascending: false }).limit(10),
    [active], 300000
  );
  const hitSumQ = useQuery<HitSummaryRow[]>(
    () => supabase.from("v_city_hit_summary").select("*").limit(200), [], 300000, 200
  );
  const scaleQ = useQuery<ScalingRow[]>(
    () => supabase.from("v_edge_scaling").select("*"), []
  );

  const conv = convQ.data ?? [];
  const unit = (cities.find((c) => c.city_key === active)?.unit ?? "C") as Unit;
  // The forward table lists every city, so each row takes ITS city's unit -
  // not the unit of whichever city is selected further down the page.
  const unitOf = (key: string) => (cities.find((c) => c.city_key === key)?.unit === "F" ? "F" : "C") as Unit;

  const hits = hitQ.data ?? [];
  const activeCity = cities.find((c) => c.city_key === active) ?? null;
  const pending = useMemo<PendingDay[]>(
    () => pendingDays(recentMarketsQ.data ?? [], new Set(hits.map((h) => h.for_date)),
                      activeCity?.timezone ?? null, Date.now()),
    [recentMarketsQ.data, hits, activeCity?.timezone]
  );
  const hitSum = (hitSumQ.data ?? []).find((r) => r.city_key === active) ?? null;

  /* ------------------------------------------------- the convergence funnel */
  const funnel = useMemo<ConvergencePoint[]>(
    () => conv.map((r) => ({
      for_date: r.for_date, lead_days: r.lead_days,
      forecast_max_c: r.forecast_max_c, observed_max_c: r.observed_max_c,
      model: r.model, is_past: r.is_past,
    })),
    [conv]
  );

  // The ladder edges for this city, so the planes are the real buckets rather
  // than a pretty grid.
  const bandEdges = useMemo(() => {
    const edges = new Set<number>();
    for (const r of cityEdgesQ.data ?? []) {
      if (r.edge !== null) edges.add(Number(r.edge));
    }
    return Array.from(edges).sort((a, b) => a - b);
  }, [cityEdgesQ.data]);

  /* --------------------------------------------------------------- forward */
  // Every open city-day (96 on 30 Sep; the table used to render 60 of them).
  // The bucket shown is the published distribution's peak, which is not
  // necessarily the bucket holding the centre (lib/predictive.ts).
  const forward = useMemo(
    () => forwardRows(((ladderQ.data ?? []) as ForwardLadderRow[]).filter((r) => selectedSet.has(r.city_key)),
                      new Date().toISOString().slice(0, 10)),
    [ladderQ.data, selectedSet]
  );
  const forwardCities = useMemo(() => new Set(forward.map((r) => r.city_key)).size, [forward]);

  /* -------------------------------------------------- actual vs predicted */
  const settled = settledQ.data ?? [];
  const scatter = useMemo(
    () => settled
      .filter((r) => r.observed_max_c !== null)
      .map((r) => ({
        x: r.forecast_max_c, y: r.observed_max_c as number,
        label: r.city_key,
        color: Math.abs((r.error_c ?? 0)) <= 1 ? "var(--good, #7ee081)" : "var(--bad, #ff6b8a)",
        hint: `${r.city_key} ${r.for_date} · said ${r.forecast_max_c.toFixed(1)}, got ${(r.observed_max_c as number).toFixed(1)}`,
      })),
    [settled]
  );

  /**
   * The six numbers the scatter is hiding.
   *
   * A cloud of dots cannot be argued with in either direction: it looks
   * roughly diagonal whether the desk is calling days to a quarter of a degree
   * or missing whole buckets. These are computed from the same settled rows
   * the chart draws, so the picture and the numbers can never disagree.
   *
   * ERROR AND BIAS ARE KEPT APART. A forecast that is 1.5 °C hot every day is
   * a correction you can apply; one that is 1.5 °C off in random directions is
   * not, and pooling them into "1.5 °C error" throws away which you have.
   */
  const accuracy = useMemo(() => {
    const rows = settled.filter(
      (r) => r.observed_max_c !== null && r.error_c !== null
    );
    const n = rows.length;
    if (!n) {
      return {
        n: 0, nCities: 0, nCityDays: 0, models: [] as string[], mae: 0, bias: 0, within1: 0, worst: 0, worstWhere: null as string | null,
        hotPct: 50, byCity: [] as Array<{ city: string; bias: number; n: number }>, maxCityBias: 1,
        verdict: "Nothing has settled yet, so there is nothing to score.",
        verdictTone: "text-muted",
      };
    }
    // error_c is signed: observed minus forecast, so positive = hotter than said.
    const errs = rows.map((r) => r.error_c as number);
    const mae = errs.reduce((a, e) => a + Math.abs(e), 0) / n;
    const bias = errs.reduce((a, e) => a + e, 0) / n;
    const within1 = errs.filter((e) => Math.abs(e) <= 1).length / n;
    const hotPct = (errs.filter((e) => e > 0).length / n) * 100;
    let worst = 0;
    let worstWhere: string | null = null;
    for (const r of rows) {
      const a = Math.abs(r.error_c as number);
      if (a > worst) {
        worst = a;
        worstWhere = `${r.city_key} ${r.for_date}`;
      }
    }
    const per = new Map<string, { sum: number; n: number }>();
    for (const r of rows) {
      const cur = per.get(r.city_key) ?? { sum: 0, n: 0 };
      cur.sum += r.error_c as number;
      cur.n += 1;
      per.set(r.city_key, cur);
    }
    const byCity = Array.from(per.entries())
      .map(([city, v]) => ({ city, bias: v.sum / v.n, n: v.n }))
      .sort((a, b) => Math.abs(b.bias) - Math.abs(a.bias));
    const maxCityBias = Math.max(0.5, ...byCity.map((c) => Math.abs(c.bias)));

    // One sentence, ordered by what actually disqualifies the forecast first.
    let verdict: string;
    let verdictTone = "text-muted";
    if (n < 20) {
      verdict = `Only ${n} settled day(s) so far — enough to look at, not enough to conclude from. Treat every number here as provisional until there are a few weeks.`;
      verdictTone = "text-warn";
    } else if (mae > 2) {
      verdict = `Missing by ${mae.toFixed(2)} °C on average is wider than a bucket, so the forecast is not resolving which bucket wins. Sizing off this edge is sizing off noise.`;
      verdictTone = "text-bad";
    } else if (Math.abs(bias) >= 0.5) {
      verdict = `A steady ${Math.abs(bias).toFixed(2)} °C lean ${bias > 0 ? "cool" : "hot"} — days come in ${bias > 0 ? "hotter" : "cooler"} than forecast far more often than not. That is systematic, which means it is correctable: it is exactly what the fitted model absorbs. Until it does, the desk is paying for it every day.`;
      verdictTone = "text-warn";
    } else if (within1 >= 0.6) {
      verdict = `${(within1 * 100).toFixed(0)}% of days land within one bucket of the call, with no meaningful lean either way. This is a forecast worth pricing against.`;
      verdictTone = "text-good";
    } else {
      verdict = `No systematic lean, but only ${(within1 * 100).toFixed(0)}% of days land within a bucket. The direction is honest and the sharpness is not — edges here will be real but small.`;
      verdictTone = "text-muted";
    }
    const nCityDays = new Set(rows.map((r) => `${r.city_key}|${r.for_date}`)).size;
    const models = Array.from(new Set(rows.map((r) => r.model))).sort();
    return { n, nCities: per.size, nCityDays, models, mae, bias, within1, worst, worstWhere, hotPct, byCity, maxCityBias, verdict, verdictTone };
  }, [settled]);

  const errByLead = useMemo(() => {
    const byModel = new Map<string, Map<number, { sum: number; n: number }>>();
    for (const r of scoreQ.data ?? []) {
      if (!byModel.has(r.model)) byModel.set(r.model, new Map());
      const m = byModel.get(r.model)!;
      const cur = m.get(r.lead_days) ?? { sum: 0, n: 0 };
      cur.sum += r.mae_c * r.n_days; cur.n += r.n_days;
      m.set(r.lead_days, cur);
    }
    const colors = ["var(--accent, #4da3ff)", "#f0a35e", "#7ee081", "#c792ea"];
    return Array.from(byModel.entries()).map(([label, m], i) => ({
      label, color: colors[i % colors.length],
      points: Array.from(m.entries())
        .map(([lead, v]) => ({ x: lead, y: v.sum / v.n }))
        .sort((a, b) => a.x - b.x),
    }));
  }, [scoreQ.data]);

  /** Where the forecast stops resolving buckets, read off the same series. */
  const decay = useMemo(() => {
    const all = errByLead.flatMap((s) => s.points);
    if (!all.length) return { text: "" };
    const byLead = new Map<number, number[]>();
    for (const p of all) {
      if (!byLead.has(p.x)) byLead.set(p.x, []);
      byLead.get(p.x)!.push(p.y);
    }
    const avg = Array.from(byLead.entries())
      .map(([lead, ys]) => ({ lead, mae: ys.reduce((a, b) => a + b, 0) / ys.length }))
      .sort((a, b) => a.lead - b.lead);
    const crossed = avg.find((a) => a.mae > 1);
    const best = avg[0];
    if (!crossed) {
      return {
        text: `Every lead day shown still averages under 1 °C — the forecast holds its resolution across the whole window, so there is no lead day at which the desk has to stop trusting it.`,
      };
    }
    if (crossed.lead === best?.lead) {
      return {
        text: `Even at ${crossed.lead} day(s) out the average miss is ${crossed.mae.toFixed(2)} °C — already wider than a bucket. The forecast is not resolving which bucket wins at any lead shown here.`,
      };
    }
    return {
      text: `The average miss crosses one bucket (1 °C) at ${crossed.lead} days out, at ${crossed.mae.toFixed(2)} °C. Inside that the forecast is picking buckets; past it, it is picking a range. Day ${best.lead} is the sharpest at ${best.mae.toFixed(2)} °C.`,
    };
  }, [errByLead]);


  const bank = bankQ.data ?? [];
  const scaling = scaleQ.data ?? [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Predictive</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          The same question in both directions. <strong className="text-text">Forward</strong>: what
          the desk expects, which bucket that lands in, and what the market is charging for it.
          <strong className="text-text"> Backward</strong>: when it expected that before, was it
          right — and separately, did being right pay. Those are different questions with different
          answers, because entry price, fees and fill size all sit between them.
        </p>
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["cities", "fact_forecast_outcome", "weather_forecasts", "bands", "markets", "edges", "band_probabilities", "fact_signal_outcome"]} />
        </div>
        {/* WHETHER THE NUMBERS BELOW ARE CALIBRATED AT ALL. They are not, and
            nothing on this page said so: a map has been fitted every day since
            16 Sep and withheld every time, because the gate wants 30 distinct
            settlement dates and the desk has 8. */}
        <div className="mt-3">
          <CalibrationStatus />
        </div>
      </div>

      {/* ======================================= THE SELECTION, FOR EVERY PANEL == */}
      <FocusFilter scope={scope} setScope={setScope} custom={custom} setCustom={setCustom}
        moment={moment} setMoment={setMoment} cities={cities} focus={focus} shown={selected.length} />

      {/* ================================================ 0. EVERY CITY == */}
      <CityCards onPick={setCity} cities={selected} moment={moment}
        hindsight={hindsightQ.data ?? []} status={statusQ.data ?? []}
        statusError={statusQ.error} scorecard={scoreQ.data ?? []} summary={hitSumQ.data ?? []} />

      {/* ======================================================== 1. FORWARD == */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">What the desk expects</h2>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          One row per city-day still open. The bucket the model puts most weight on, what the
          market charges for that bucket, and the largest tradeable edge anywhere on the ladder.
          A row with no probability has a forecast but no priced market yet. <b>Priced centre</b>{" "}
          and σ are what the ladder was integrated on, after the station correction and the blend;
          <b>raw forecast</b> is the public forecast the engine started from. The <b>most likely
          bucket</b> is the peak of the published distribution, which need not be the bucket the
          centre falls in (a centre held at the day&apos;s observed maximum can sit below the peak).
        </p>
        {forward.length > 0 && (
          <p className="text-[11px] text-muted">
            All {fmtInt(forward.length)} open city-days across {fmtInt(forwardCities)} cities, soonest
            day first, then the largest tradeable edge.
            {forward.some((r) => r.incomplete) ? (
              <> <span className="text-warn">⚠</span> marks a ladder only partly priced or whose
              probabilities do not sum to 1 (±2 pp).</>
            ) : null}
          </p>
        )}
        <DataState
          relation="v_prediction_ladder"
          truncated={ladderQ.truncated}
          loading={ladderQ.loading} error={ladderQ.error} isEmpty={forward.length === 0}
          emptyTitle="No open markets ahead"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "then let n8n P0.2 discover markets and P1.5 forecast them.")}
          onRetry={ladderQ.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">City</th>
                  <th className="px-2 py-1.5 text-left">Resolves</th>
                  <th className="px-2 py-1.5 text-right">Priced centre</th>
                  <th className="px-2 py-1.5 text-right">σ</th>
                  <th className="px-2 py-1.5 text-right text-muted">Raw forecast</th>
                  <th className="px-2 py-1.5 text-left">Most likely bucket</th>
                  <th className="px-2 py-1.5 text-right">Model</th>
                  <th className="px-2 py-1.5 text-right">Market</th>
                  <th className="px-2 py-1.5 text-right">Best edge</th>
                </tr>
              </thead>
              <tbody>
                {forward.map((r) => (
                  <tr key={`${r.city_key}|${r.for_date}`} className="border-t border-border">
                    <td className="px-2 py-1.5">
                      <button className="text-accent hover:underline" onClick={() => setCity(r.city_key)}>
                        {r.city_key}
                      </button>
                    </td>
                    <td className="px-2 py-1.5 text-muted">{fmtResolutionDate(r.for_date)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {r.centre_c === null ? <span className="text-muted">not recorded</span> : fmtTemp(r.centre_c, unitOf(r.city_key), 1)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">
                      {r.sigma_c === null ? "—" : `±${fmtTempDelta(r.sigma_c, unitOf(r.city_key)).replace("+", "")}`}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">
                      {r.forecast_max_c === null ? "—" : fmtTemp(r.forecast_max_c, unitOf(r.city_key), 1)}
                    </td>
                    <td className="px-2 py-1.5">
                      {r.best_band ?? <span className="text-muted">not priced</span>}
                      {r.incomplete ? (
                        <span className="ml-1 text-warn" title={`${r.n_priced} of ${r.n_bands} buckets priced; probabilities sum to ${r.prob_sum === null ? "—" : r.prob_sum.toFixed(3)}`}>⚠</span>
                      ) : null}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtPct(r.best_prob)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtPrice(r.best_price)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${pnlColor(r.top_edge_pp)}`}>
                      {/* edge_net_pp is a FRACTION (0.08 = 8pp), as every other page formats it */}
                      {fmtPp(r.top_edge_pp)}
                      {r.top_edge_band ? <span className="ml-1 text-muted">{r.top_edge_band}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* ==================================================== 1b. HINDSIGHT ==
          The forward panel states a bucket and a confidence and then never
          mentions either again. This grades them. It sits directly under it
          rather than at the bottom of the page because a claim and its
          track record are one thought, and separating them is how a desk
          keeps believing a number nothing has checked. */}
      <PredictionHindsight rows={hindsightQ.data ?? []} loading={hindsightQ.loading} error={hindsightQ.error}
        truncated={hindsightQ.truncated} onRetry={hindsightQ.refresh}
        cities={selected} active={activeKeys} focus={focus} universe={universe}
        moment={moment} setMoment={setMoment} />

      <PredictionLineup cities={selected} />

      {/* ==================================================== 2. THE FUNNEL == */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">How the forecast converged</h2>
          <select
            value={active} onChange={(e) => setCity(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            {cities.filter((c) => selectedSet.has(c.city_key)).map((c) => (
              <option key={c.city_key} value={c.city_key}>
                {c.display_name ?? c.city_key}
              </option>
            ))}
          </select>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Depth is how many days before resolution, height is temperature, width is the
          resolution day. Each ribbon is one model walking from what it said a week out to what it
          said that morning; the green bar is what actually happened. A good model&apos;s ribbons
          narrow toward that bar. A biased one runs parallel to it and never arrives — and those
          two look identical in a table of mean error. The shaded planes are the real bucket
          boundaries, because a 0.6 °C miss across a line loses and a 0.9 °C miss inside one wins.
        </p>
        {/* The planes are their own read (v_city_ladder_edges): a failed one is
            said, not drawn as a funnel without its buckets (Codex on #334). */}
        <DataState
          relation="v_forecast_convergence"
          truncated={convQ.truncated}
          loading={convQ.loading || cityEdgesQ.loading}
          error={convQ.error ?? (cityEdgesQ.error ? `v_city_ladder_edges: ${cityEdgesQ.error}` : null)}
          isEmpty={funnel.length === 0}
          emptyTitle="No forecast series for this city"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "and check v_forecast_coverage — a city with no forward forecast has nothing to draw.")}
          onRetry={() => { convQ.refresh(); cityEdgesQ.refresh(); }}
        >
          <Convergence3D points={funnel} bands={bandEdges} unit={unit} />
        </DataState>
      </section>

      {/* ===================================== HIT AND MISS, ONE CITY AT A TIME
          Mean error in degrees is the wrong unit for this question. On a 1 C
          ladder a 1 C miss is a different bucket, which is a total loss on
          the band that was called - so skill has to be read in buckets,
          against what settled, beside what the market said on the same day. */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Hit and miss, day by day</h2>
          <select
            value={active} onChange={(e) => setCity(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            {cities.filter((c) => selectedSet.has(c.city_key)).map((c) => (
              <option key={c.city_key} value={c.city_key}>
                {c.display_name ?? c.city_key}
              </option>
            ))}
          </select>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Every settled day for this city: the bucket the desk called <b>before the day
          began</b>, and the bucket that actually paid. The call is the last probability the
          desk computed before the city&apos;s local midnight — never one made after the day&apos;s
          high was already on the thermometer. Brier is the multiclass score over the whole
          settled ladder (lower is better; a bucket the desk did not price counts as zero), with a
          uniform guess beside it so &quot;better than nothing&quot; is visible rather than assumed.
          The market is set beside it only on days both sides priced the winning bucket before
          the day, scored on the buckets both priced, each renormalised over that set — scoring
          each side over whatever it happened to price flatters whichever priced less.
        </p>
        {activeCity && (
          <p className="text-[11px] text-muted">
            Settles on{" "}
            <span className="text-text">{activeCity.station_name ?? "its station"}{activeCity.icao ? ` (${activeCity.icao})` : ""}</span>,
            local day on {activeCity.timezone ?? "an unknown clock"}; buckets in °{unit}
            {unit === "F" ? " (2 °F wide)" : " (one whole degree)"}. Temperatures and errors below are in °{unit}; error is priced centre − actual.
          </p>
        )}
        {pending.length > 0 && (
          <div className="rounded border border-border bg-panel2/40 px-3 py-2 text-[11px] leading-relaxed">
            <span className="font-semibold text-text">Not in the record yet — pending, not missed:</span>{" "}
            {pending.map((p, i) => (
              <span key={p.date}>
                {i > 0 ? "; " : ""}
                <span className="text-text">{fmtResolutionDate(p.date)}</span>{" "}
                {p.state === "awaiting_venue"
                  ? "day ended, the venue has not confirmed the whole ladder yet"
                  : "confirmed by the venue, waiting for the record (banked and shown by the next intraday run)"}
              </span>
            ))}
            .
          </div>
        )}
        <DataState
          relation="v_city_hit_history"
          truncated={hitQ.truncated}
          loading={hitQ.loading} error={hitQ.error} isEmpty={hits.length === 0}
          emptyTitle="No settled days for this city yet"
          emptyBody={MISSING("sql/ad4_85_city_hit_history.sql", "and remember a day only appears here once it has settled against final station authority and the desk priced its ladder before the day began.")}
          onRetry={hitQ.refresh}
        >
          <div className="space-y-3">
            {hitSum && (
              <div className="rounded border border-border bg-panel p-3">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="settled days" value={fmtInt(hitSum.days)} />
                  <Stat label="we called it" value={fmtPct(hitSum.model_hit_rate ?? 0)} />
                  <Stat
                    label="our Brier"
                    value={(hitSum.brier_model ?? 0).toFixed(3)}
                    tone={(hitSum.brier_model ?? 1) < (hitSum.brier_uniform ?? 1)
                      ? "var(--c-good)" : "var(--c-bad)"}
                  />
                  <Stat label="uniform guess" value={(hitSum.brier_uniform ?? 0).toFixed(3)} />
                  <Stat label="our p on the winner" value={fmtPct(hitSum.avg_model_prob_on_winner ?? 0)} />
                  <Stat
                    label="called, on average"
                    value={hitSum.avg_hours_before_day == null ? "—" : `${hitSum.avg_hours_before_day.toFixed(1)} h before`}
                  />
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="days the market also priced" value={`${fmtInt(hitSum.h2h_days)} of ${fmtInt(hitSum.days)}`} />
                  {hitSum.h2h_days > 0 ? (
                    <>
                      <Stat
                        label="those days: we / market called it"
                        value={`${fmtInt(hitSum.h2h_model_hits)} / ${fmtInt(hitSum.market_hits)}`}
                        tone={hitSum.h2h_model_hits >= hitSum.market_hits ? "var(--c-good)" : "var(--c-bad)"}
                      />
                      <Stat
                        label="those days: our / market Brier"
                        value={`${(hitSum.brier_model_h2h ?? 0).toFixed(3)} / ${(hitSum.brier_market ?? 0).toFixed(3)}`}
                        tone={(hitSum.brier_model_h2h ?? 1) <= (hitSum.brier_market ?? 1)
                          ? "var(--c-good)" : "var(--c-bad)"}
                      />
                      <Stat
                        label="days we beat the market"
                        value={`${fmtInt(hitSum.days_we_beat_the_market)} of ${fmtInt(hitSum.h2h_days)}`}
                      />
                    </>
                  ) : null}
                  {/* THE PRICED CENTRE'S MISS, NOT THE RAW INPUT'S (audit repair 3): the
                      raw forecast is where the engine started; the centre is what it priced. */}
                  <Stat
                    label="priced centre, mean |error|"
                    value={hitSum.centre_days > 0 && hitSum.centre_mae_c != null
                      ? fmtTempDelta(hitSum.centre_mae_c, unit, 2).replace("+", "") : "not recorded"}
                  />
                  <Stat
                    label="priced centre, bias (centre − actual)"
                    value={hitSum.centre_days > 0 && hitSum.centre_bias_c != null
                      ? fmtTempDelta(hitSum.centre_bias_c, unit, 2) : "not recorded"}
                  />
                </div>
                <p className="mt-2 text-[11px] text-muted">
                  Measured over {hitSum.first_day ?? "—"} → {hitSum.last_day ?? "—"}. The priced centre is
                  recorded on {fmtInt(hitSum.centre_days)} of {fmtInt(hitSum.days)} days (from 23 Sep); the raw
                  forecast input missed by {fmtTempDelta(hitSum.mae_c ?? 0, unit, 2).replace("+", "")} on average (bias{" "}
                  {fmtTempDelta(hitSum.bias_c ?? 0, unit, 2)}) over all {fmtInt(hitSum.days)}.
                </p>
                <p className="mt-3 text-[11px] text-muted">
                  <b>Verdict:</b> {hitSum.verdict}
                </p>
              </div>
            )}

            <div className="overflow-x-auto rounded border border-border bg-panel">
              <table className="w-full text-[11px]">
                <thead className="text-muted">
                  <tr className="border-b border-border">
                    <th className="p-2 text-left">day</th>
                    <th className="p-2 text-right">actual</th>
                    <th className="p-2 text-right">priced centre</th>
                    <th className="p-2 text-right">error</th>
                    <th className="p-2 text-right text-muted">raw forecast</th>
                    <th className="p-2 text-left">paid</th>
                    <th className="p-2 text-left">we called</th>
                    <th className="p-2 text-right">our p on winner</th>
                    <th className="p-2 text-right">called</th>
                    <th className="p-2 text-left">market called</th>
                    <th className="p-2 text-right">its p on winner</th>
                    <th className="p-2 text-right">Brier us / uniform</th>
                    <th className="p-2 text-right">shared bands: us / market</th>
                  </tr>
                </thead>
                <tbody>
                  {hits.map((r) => (
                    <tr key={`${r.city_key}:${r.for_date}`} className="border-b border-border/40">
                      <td className="p-2">{fmtResolutionDate(r.for_date)}</td>
                      <td className="p-2 text-right">{fmtTemp(r.observed_max_c, unit)}</td>
                      <td className="p-2 text-right">
                        {r.centre_c === null ? <span className="text-muted">not recorded</span> : fmtTemp(r.centre_c, unit)}
                      </td>
                      <td className="p-2 text-right" style={{ color: Math.abs(r.centre_error_c ?? 0) > 1 ? "var(--c-bad)" : undefined }}>
                        {/* centre - actual, in the city's unit (it was °C with no unit beside °F readings) */}
                        {r.centre_error_c === null ? "—" : fmtTempDelta(r.centre_error_c, unit)}
                      </td>
                      <td className="p-2 text-right text-muted">{fmtTemp(r.forecast_max_c, unit)}</td>
                      <td className="p-2 font-medium">{r.winner ?? "—"}</td>
                      <td className="p-2" style={{ color: r.model_hit ? "var(--c-good)" : "var(--c-bad)" }}>
                        {r.model_hit ? "✓ " : "✗ "}{r.model_call ?? "—"}
                      </td>
                      <td className="p-2 text-right">{fmtPct(r.model_prob_on_winner ?? 0)}</td>
                      <td className="p-2 text-right text-muted" title={r.called_at ?? undefined}>
                        {r.hours_before_day == null ? "—" : `${r.hours_before_day.toFixed(1)} h before`}
                      </td>
                      {r.head_to_head ? (
                        <>
                          <td className="p-2" style={{ color: r.market_hit ? "var(--c-good)" : "var(--c-bad)" }}>
                            {r.market_hit ? "✓ " : "✗ "}{r.market_call ?? "—"}
                          </td>
                          <td className="p-2 text-right">{fmtPct(r.market_prob_on_winner ?? 0)}</td>
                        </>
                      ) : (
                        <td className="p-2 text-muted" colSpan={2}>not priced by the market before the day</td>
                      )}
                      <td className="p-2 text-right">
                        <span style={{ color: (r.brier_model ?? 1) < (r.brier_uniform ?? 1) ? "var(--c-good)" : "var(--c-bad)" }}>
                          {(r.brier_model ?? 0).toFixed(2)}
                        </span>
                        {" / "}
                        {(r.brier_uniform ?? 0).toFixed(2)}
                      </td>
                      <td className="p-2 text-right">
                        {r.head_to_head ? (
                          <>
                            <span style={{ color: (r.brier_model_common ?? 1) <= (r.brier_market ?? 1) ? "var(--c-good)" : "var(--c-bad)" }}>
                              {(r.brier_model_common ?? 0).toFixed(2)}
                            </span>
                            {" / "}
                            {(r.brier_market ?? 0).toFixed(2)}
                          </>
                        ) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </DataState>
      </section>

      {/* =========================================== 3. ACTUAL AGAINST PREDICTED
          Its own section, full width, because it is the one question that
          decides whether anything else on this page is worth reading. It used
          to be a half-width chart sharing a row with error-by-lead, with two
          sentences and no numbers - a picture of a cloud of dots that could
          not be argued with either way. */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">Actual against predicted</h2>
          <Freshness relation="v_forecast_convergence" />
          <span className="text-[11px] text-muted">
            the only question that decides whether the rest of this page is worth reading
          </span>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Every settled day, one dot per public forecast model (the raw forecasts the engine starts
          from — {accuracy.models.length ? accuracy.models.join(", ") : "nws and Open-Meteo"} — not
          the priced centre): what each said a day out against what the day actually did. On the diagonal is a perfect call; above it the day came in hotter than said, below
          it cooler. Green is within 1 °C, which is roughly one bucket — the resolution the market
          actually pays at, so a dot being green matters more than it being close.{" "}
          <strong className="text-text">Bias and error are read separately</strong>: a forecast that
          is 1.5 °C hot every day is a correction you can apply, and one that is 1.5 °C off in
          random directions is not. The numbers below split them.
        </p>

        <DataState
          relation="v_forecast_convergence"
          truncated={settledQ.truncated}
          // The roster gates the query, so "roster not loaded yet" has to read
          // as loading and not as "nothing has settled" - that exact
          // confusion is what made this page claim an empty archive while
          // 2,268 settled comparisons sat in it.
          loading={settledQ.loading || citiesQ.loading || cities.length === 0}
          error={settledQ.error}
          isEmpty={scatter.length === 0}
          emptyTitle={unverified ? "No verified days yet" : "No settled days yet"}
          emptyBody={unverified ?? "Nothing has settled, so there is nothing to score. This fills in once a market resolves and the day is frozen."}
          onRetry={convQ.refresh}
        >
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <div className="rounded border border-border bg-panel/60 p-3">
              <Scatter
                points={scatter}
                xLabel="forecast °C"
                yLabel="observed °C"
                height={340}
                xTickFormat={(v) => v.toFixed(0)}
                yTickFormat={(v) => v.toFixed(0)}
                // 2,134 dots each carrying a city name is a mat of overlapping
                // words with the data underneath it. Names on hover, and the
                // diagonal drawn, which is the only reference the chart has.
                maxLabels={40}
                zoomable
                diagonal
              />
            </div>

            <div className="space-y-3">
              {/* ---- the six numbers the cloud of dots is hiding ---------- */}
              <div className="grid grid-cols-2 gap-2">
                <Readout
                  label="Forecasts scored"
                  value={fmtInt(accuracy.n)}
                  sub={`${fmtInt(accuracy.nCityDays)} city-days × ${accuracy.models.length} model${accuracy.models.length === 1 ? "" : "s"}, ${accuracy.nCities} cit${accuracy.nCities === 1 ? "y" : "ies"}`}
                />
                <Readout
                  label="Within 1 °C"
                  value={accuracy.n ? `${(accuracy.within1 * 100).toFixed(0)}%` : "—"}
                  sub="one bucket wide"
                  tone={accuracy.within1 >= 0.6 ? "good" : accuracy.within1 >= 0.4 ? "warn" : "bad"}
                />
                <Readout
                  label="Average miss"
                  value={accuracy.n ? `${accuracy.mae.toFixed(2)} °C` : "—"}
                  sub="how far off, ignoring direction"
                  tone={accuracy.mae <= 1 ? "good" : accuracy.mae <= 2 ? "warn" : "bad"}
                />
                <Readout
                  label="Bias"
                  value={accuracy.n ? `${accuracy.bias > 0 ? "+" : ""}${accuracy.bias.toFixed(2)} °C` : "—"}
                  sub={
                    Math.abs(accuracy.bias) < 0.25
                      ? "no systematic lean"
                      : accuracy.bias > 0
                        ? "days run hotter than said"
                        : "days run cooler than said"
                  }
                  tone={Math.abs(accuracy.bias) < 0.25 ? "good" : "warn"}
                />
                <Readout
                  label="Worst miss"
                  value={accuracy.n ? `${accuracy.worst.toFixed(1)} °C` : "—"}
                  sub={accuracy.worstWhere ?? ""}
                  tone={accuracy.worst >= 5 ? "bad" : accuracy.worst >= 3 ? "warn" : "good"}
                />
                <Readout
                  label="Too hot / too cool"
                  value={accuracy.n ? `${accuracy.hotPct.toFixed(0)} / ${(100 - accuracy.hotPct).toFixed(0)}` : "—"}
                  sub="a 50/50 split means no lean"
                  tone={Math.abs(accuracy.hotPct - 50) < 12 ? "good" : "warn"}
                />
              </div>

              {/* ---- what those six numbers mean, in one sentence -------- */}
              <div className="rounded border border-border bg-panel2/40 p-2.5">
                <div className="text-[10px] uppercase tracking-wide text-muted">What this says</div>
                <p className={`mt-1 text-xs leading-relaxed ${accuracy.verdictTone}`}>
                  {accuracy.verdict}
                </p>
              </div>

              {/* ---- and where the misses are concentrated --------------- */}
              {accuracy.byCity.length > 1 && (
                <div className="rounded border border-border bg-panel/60 p-2.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="text-[10px] uppercase tracking-wide text-muted">
                      {allLeans
                        ? `Lean per city, all ${largestLeans(accuracy.byCity, 0).total}`
                        : `The ${Math.min(8, accuracy.byCity.length)} of ${accuracy.byCity.length} cities with the largest lean`}
                      {" "}— bars right of the line ran hotter than forecast
                    </div>
                    {accuracy.byCity.length > 8 && (
                      <button className="text-[10px] text-accent hover:underline" onClick={() => setAllLeans(!allLeans)}>
                        {allLeans ? "largest 8" : `show all ${accuracy.byCity.length}`}
                      </button>
                    )}
                  </div>
                  <div className="mt-1.5 space-y-1">
                    {largestLeans(accuracy.byCity, allLeans ? 0 : 8).shown.map((c) => (
                      <BiasBar key={c.city} city={c.city} bias={c.bias} n={c.n} max={accuracy.maxCityBias} />
                    ))}
                  </div>
                  <p className="mt-1.5 text-[10px] leading-snug text-muted">
                    A city with a consistent lean is a correction waiting to be applied, not noise —
                    it is exactly what the fitted model in <code>derived_weather_model</code> exists
                    to absorb.
                  </p>
                </div>
              )}
            </div>
          </div>
        </DataState>
      </section>

      {/* ---------------------------------------------- error against lead day */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">How fast the forecast decays</h2>
          <Freshness relation="v_prediction_scorecard" />
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          The same error, split by how far ahead the call was made. A model that is sharp tomorrow
          and useless on Friday averages out to &ldquo;fine&rdquo; — this is the only place that
          shows. The practical use is picking the lead day at which to stop trusting it: where the
          line crosses one bucket wide, the forecast has stopped resolving which bucket wins.
        </p>
        {unverifiedNote}
        <DataState
          relation="v_prediction_scorecard"
          loading={scoreQ.loading || citiesQ.loading || cities.length === 0}
          error={scoreQ.error}
          isEmpty={errByLead.length === 0}
          emptyTitle={unverified ? "No verified days yet" : "No scorecard yet"}
          emptyBody={unverified ?? MISSING("sql/ad4_31_predictive.sql", "and let a few days settle so there is something to score.")}
          onRetry={scoreQ.refresh}
        >
          <div className="rounded border border-border bg-panel/60 p-3">
            <LineChart
              series={errByLead}
              height={260}
              yLabel="average miss °C"
              xTickFormat={(v) => `${v}d`}
              yTickFormat={(v) => v.toFixed(1)}
            />
            <p className="mt-2 text-[11px] leading-relaxed text-muted">
              {decay.text}
            </p>
          </div>
        </DataState>
      </section>

      {/* ------------------------------------------------ the scorecard table */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Raw forecast accuracy, per city, per model, per lead</h2>
          <select
            value={selectedSet.has(scoreCity) ? scoreCity : ""} onChange={(e) => setScoreCity(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            <option value="">All {fmtInt(selected.length)} selected cities</option>
            {cities.filter((c) => selectedSet.has(c.city_key)).map((c) => (
              <option key={c.city_key} value={c.city_key}>{c.display_name ?? c.city_key}</option>
            ))}
          </select>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Each public forecast model at each lead, against the day&apos;s observed maximum, in °C for
          every city. <strong className="text-text">Bias</strong> is kept separate from error on
          purpose: a model 1.5 °C hot every single day is fixable, one that is 1.5 °C off in random
          directions is not. <strong className="text-text">Same whole °C</strong> is whether the
          forecast and the observed maximum share a whole degree Celsius. That is a Celsius
          city&apos;s bucket, but <em>not</em> a Fahrenheit city&apos;s: those settle on 2 °F buckets,
          so read the settlement-bucket record in <b>Hit and miss</b> above. A city needs at least 5
          settled days per model and lead to get a row.
        </p>
        <DataState
          relation="v_prediction_scorecard"
          truncated={scoreQ.truncated}
          loading={scoreQ.loading || citiesQ.loading || cities.length === 0}
          error={scoreQ.error} isEmpty={(scoreQ.data ?? []).length === 0}
          emptyTitle={unverified ? "No verified days yet" : "Nothing scored yet"}
          emptyBody={unverified ?? "Needs at least 5 settled days per city, model and lead."}
          onRetry={scoreQ.refresh}
        >
          {(() => {
            const sCity = selectedSet.has(scoreCity) ? scoreCity : "";
            const sc = groupScorecard(scoreQ.data ?? [], selected, sCity);
            return (
              <div className="space-y-2">
                <p className="text-[11px] text-muted">
                  {sCity
                    ? `${fmtInt(sc.shownRows)} rows for ${sCity}.`
                    : `All ${fmtInt(sc.totalRows)} rows: ${fmtInt(sc.citiesWithRows)} of the ${fmtInt(selected.length)} selected cities, by city, then model, then lead.`}
                  {sc.missing.length > 0 ? (
                    <> No row yet (fewer than 5 settled days per model and lead):{" "}
                      <span className="text-text">{sc.missing.join(", ")}</span>.</>
                  ) : null}
                </p>
                <div className="max-h-[36rem] overflow-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-panel2 text-muted">
                      <tr>
                        <th className="px-2 py-1.5 text-left">City</th>
                        <th className="px-2 py-1.5 text-left">Model</th>
                        <th className="px-2 py-1.5 text-right">Lead</th>
                        <th className="px-2 py-1.5 text-right">Days</th>
                        <th className="px-2 py-1.5 text-right">MAE °C</th>
                        <th className="px-2 py-1.5 text-right">Bias °C</th>
                        <th className="px-2 py-1.5 text-right">Worst °C</th>
                        <th className="px-2 py-1.5 text-right">Same whole °C</th>
                        <th className="px-2 py-1.5 text-right">Within 1 °C</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sc.groups.flatMap((g) => g.rows.map((r, i) => (
                        <tr key={`${r.city_key}|${r.model}|${r.lead_days}`}
                            className={i === 0 ? "border-t-2 border-border" : "border-t border-border/40"}>
                          <td className="px-2 py-1.5">
                            {i === 0 ? (
                              <span>
                                {r.city_key}
                                <span className="ml-1 text-muted">{unitOf(r.city_key) === "F" ? "°F city" : ""}</span>
                              </span>
                            ) : null}
                          </td>
                          <td className="px-2 py-1.5 text-muted">{r.model}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{r.lead_days}d</td>
                          <td className="px-2 py-1.5 text-right tabular-nums text-muted">{fmtInt(r.n_days)}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.mae_c).toFixed(2)}</td>
                          <td className={`px-2 py-1.5 text-right tabular-nums ${Math.abs(r.bias_c) > 0.5 ? "text-warn" : "text-muted"}`}>
                            {r.bias_c > 0 ? "+" : ""}{Number(r.bias_c).toFixed(2)}
                          </td>
                          <td className="px-2 py-1.5 text-right tabular-nums text-muted">{Number(r.worst_c).toFixed(1)}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">
                            {r.hit_rate_pct === null ? "—" : `${Number(r.hit_rate_pct).toFixed(0)}%`}
                          </td>
                          <td className="px-2 py-1.5 text-right tabular-nums text-muted">
                            {r.within_1c_pct === null ? "—" : `${Number(r.within_1c_pct).toFixed(0)}%`}
                          </td>
                        </tr>
                      )))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })()}
        </DataState>
      </section>

      {/* ============================================ 4. DID BEING RIGHT PAY == */}
      <section className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Bankroll</h2>
          <p className="text-xs leading-relaxed text-muted">
            Realised P&amp;L, cumulative, from filled paper trades only — a proposal that never
            filled cost nothing and proved nothing. The win rate beside it is running, not final.
          </p>
          <DataState
          relation="v_bankroll_curve"
            loading={bankQ.loading} error={bankQ.error} isEmpty={bank.length === 0}
            emptyTitle="No filled trades yet"
            emptyBody="Every strategy ships disabled, and the paper desk has filled nothing yet."
            onRetry={bankQ.refresh}
          >
            <>
              <LineChart
                height={240} yLabel="cumulative $" zeroLine
                series={[{
                  label: "net P&L", color: "var(--accent, #4da3ff)", fill: true,
                  points: bank.map((r, i) => ({ x: i, y: r.cumulative_pnl })),
                }]}
                xTickFormat={(i) => bank[Math.round(i)]?.day?.slice(5) ?? ""}
                yTickFormat={(v) => fmtUsd(v)}
              />
              <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                <Stat label="Net" value={fmtUsd(bank[bank.length - 1]?.cumulative_pnl ?? 0)}
                      tone={pnlColor(bank[bank.length - 1]?.cumulative_pnl)} />
                <Stat label="Trades" value={fmtInt(bank[bank.length - 1]?.cumulative_trades ?? 0)} />
                <Stat label="Win rate"
                      value={bank[bank.length - 1]?.win_rate_pct == null ? "—"
                             : `${bank[bank.length - 1].win_rate_pct!.toFixed(0)}%`} />
              </div>
            </>
          </DataState>
        </div>

        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Can it scale?</h2>
          <p className="text-xs leading-relaxed text-muted">
            Claimed edge against what that edge actually returned. If realised tracks claimed, size
            can go up. If realised is flat whatever was claimed, the edge estimate is noise — and
            sizing up multiplies noise, not profit.
          </p>
          <DataState
          relation="v_edge_scaling"
            loading={scaleQ.loading} error={scaleQ.error} isEmpty={scaling.length === 0}
            emptyTitle="Nothing to compare yet"
            emptyBody="Needs settled bands with both a claimed edge and a market price — Actions → Data Bank writes them."
            onRetry={scaleQ.refresh}
          >
            <>
              <LineChart
                height={240} yLabel="points" zeroLine
                series={[
                  { label: "claimed", color: "var(--muted, #8a93a6)", dashed: true,
                    points: scaling.map((r) => ({ x: r.edge_bucket, y: r.claimed_edge_pp })) },
                  { label: "realised", color: "var(--accent, #4da3ff)", fill: true,
                    points: scaling.map((r) => ({ x: r.edge_bucket, y: r.realised_pp })) },
                ]}
                xTickFormat={(i) => scaling.find((s) => s.edge_bucket === Math.round(i))?.edge_band ?? ""}
                yTickFormat={(v) => v.toFixed(0)}
              />
              <div className="mt-2 space-y-1 text-[11px] text-muted">
                {scaling.map((r) => (
                  <div key={r.edge_bucket} className="flex justify-between tabular-nums">
                    <span>{r.edge_band} · {fmtInt(r.n)} bands</span>
                    <span>
                      claimed {r.claimed_edge_pp.toFixed(1)} → realised{" "}
                      <span className={pnlColor(r.realised_pp)}>{r.realised_pp.toFixed(1)}</span>
                    </span>
                  </div>
                ))}
              </div>
            </>
          </DataState>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded border border-border bg-panel2 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`tabular-nums ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

/** One number with what it means under it. Colour is a verdict, not decoration. */
function Readout({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "warn" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div className="rounded border border-border bg-panel/60 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-0.5 text-base font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {sub ? <div className="mt-0.5 text-[10px] leading-snug text-muted">{sub}</div> : null}
    </div>
  );
}

/**
 * A city's lean, drawn from a centre line so direction is visible without
 * reading a sign. Right of the line = days ran hotter than forecast.
 */
function BiasBar({ city, bias, n, max }: { city: string; bias: number; n: number; max: number }) {
  const frac = Math.min(1, Math.abs(bias) / max);
  const pct = frac * 50;
  const hot = bias > 0;
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="w-16 shrink-0 truncate font-mono text-muted" title={`${city} · ${n} day(s)`}>
        {city}
      </span>
      <span className="relative h-2.5 flex-1 rounded-sm bg-panel2">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <span
          className={`absolute inset-y-0 rounded-sm ${hot ? "bg-bad/70" : "bg-accent/70"}`}
          style={hot ? { left: "50%", width: `${pct}%` } : { right: "50%", width: `${pct}%` }}
        />
      </span>
      <span className={`w-14 shrink-0 text-right tabular-nums ${Math.abs(bias) >= 0.5 ? "text-warn" : "text-muted"}`}>
        {bias > 0 ? "+" : ""}
        {bias.toFixed(2)}°
      </span>
    </div>
  );
}
