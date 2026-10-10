/**
 * ONE CITY SELECTION FOR ALL OF PREDICTIVE (WXPredict build, wave F; Hassan,
 * 6 Oct: "All active cities · Seasonal Focus 10 · Custom selection ... Apply
 * that selection throughout Predictive ... Filtering the visible rows must
 * also update the summary totals").
 *
 * Everything here is pure, so the page and the unit tests
 * (web/tests/focus.test.cjs) run the same code:
 *
 *   selectedKeys     the cities a scope shows, always inside the active roster
 *   summariseByMoment the "Was it right?" totals, from rows, for any selection:
 *                    the same counts v_prediction_hindsight_summary adds up in
 *                    SQL for all cities
 *   cityEvidence     one city's record at one checkpoint, for its card
 *   compareFocus     the Seasonal Focus 10 against the universe and the rest,
 *                    on the rows docs/FOCUS_PREREG.md says count
 *
 * MEMBERSHIP NEVER MOVES A PROBABILITY. These functions choose which rows are
 * shown and added up; nothing here, and nothing that prices, reads the set to
 * change a number (tests/test_focus_set.py, tests/test_city_status.py).
 */

export type Scope = "all" | "focus" | "custom";

/** The cities a scope shows, in roster order, never outside the active roster. */
export function selectedKeys(scope: Scope, active: string[], focus: string[], custom: string[]): string[] {
  if (scope === "all") return [...active];
  const want = new Set(scope === "focus" ? focus : custom);
  return active.filter((k) => want.has(k));
}

/** The moments a call is frozen at, in order; post-peak is apart by design. */
export const MOMENTS: Array<{ key: string; label: string; group: string; afterPeak: boolean }> = [
  { key: "day_ahead", label: "Day ahead", group: "Before the day", afterPeak: false },
  { key: "d1_eve", label: "Evening before", group: "Before the day", afterPeak: false },
  { key: "morning", label: "Morning", group: "Morning", afterPeak: false },
  { key: "noon", label: "Noon", group: "Noon", afterPeak: false },
  { key: "prepeak_2h", label: "2 h before peak", group: "Before the peak", afterPeak: false },
  { key: "prepeak_1h", label: "1 h before peak", group: "Before the peak", afterPeak: false },
  { key: "postpeak_1h", label: "1 h after peak", group: "After the peak", afterPeak: true },
];

export const momentLabel = (k: string) => MOMENTS.find((m) => m.key === k)?.label ?? k;

/** One settled or pending call, as v_prediction_hindsight returns it. */
export type HindsightRow = {
  city_key: string; for_date: string; called_when: string;
  call_order?: number | null; after_peak?: boolean | null;
  predicted_pct: number | string | null; hit: boolean | null;
  forecast_error_c: number | string | null; market_hit: boolean | null;
  engine_version?: string | null; prob_on_winner?: number | string | null;
  /** read by the city popup's last days: the call, the winner and the market's call */
  predicted_band?: string | null; actual_band?: string | null; market_band?: string | null;
  observed_max_c?: number | string | null;
};

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const x = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(x) ? x : null;
};

/**
 * The summary row v_prediction_hindsight_summary makes, from rows. Every
 * filter is the view's (supabase/migrations/20260926090000):
 *   days        hit and predicted_pct both known
 *   hits        a hit with predicted_pct known
 *   claimed_pct mean predicted_pct where hit is known
 *   market_days market_hit known; market_hits; model hits on those days
 *   mae_c, bias_c over forecast_error_c where known
 * The view rounds claimed to 2 places and the errors to 3; these do not, and
 * the page rounds when it prints.
 */
export type MomentSummary = {
  called_when: string; call_order: number; after_peak: boolean;
  days: number; hits: number; claimed_pct: number | null;
  market_days: number; market_hits: number; model_hits_on_market_days: number;
  mae_c: number | null; bias_c: number | null;
  first_day: string | null; last_day: string | null;
};

export function summariseByMoment(rows: HindsightRow[]): MomentSummary[] {
  const by = new Map<string, HindsightRow[]>();
  for (const r of rows) {
    if (!by.has(r.called_when)) by.set(r.called_when, []);
    by.get(r.called_when)!.push(r);
  }
  const order = (k: string) => {
    const i = MOMENTS.findIndex((m) => m.key === k);
    return i < 0 ? 99 : i;
  };
  const out: MomentSummary[] = [];
  for (const [k, rs] of by) {
    let days = 0, hits = 0, claimedSum = 0, claimedN = 0, mDays = 0, mHits = 0, mModel = 0;
    let errAbs = 0, errSum = 0, errN = 0;
    let first: string | null = null, last: string | null = null;
    let callOrder = Number.POSITIVE_INFINITY, afterPeak = true;
    for (const r of rs) {
      const p = num(r.predicted_pct);
      if (r.hit !== null && p !== null) { days += 1; if (r.hit) hits += 1; }
      if (r.hit !== null && p !== null) { claimedSum += p; claimedN += 1; }
      if (r.market_hit !== null) {
        mDays += 1;
        if (r.market_hit) mHits += 1;
        if (r.hit) mModel += 1;
      }
      const e = num(r.forecast_error_c);
      if (e !== null) { errAbs += Math.abs(e); errSum += e; errN += 1; }
      if (first === null || r.for_date < first) first = r.for_date;
      if (last === null || r.for_date > last) last = r.for_date;
      const co = num(r.call_order);
      if (co !== null && co < callOrder) callOrder = co;
      afterPeak = afterPeak && !!r.after_peak;
    }
    out.push({
      called_when: k, call_order: Number.isFinite(callOrder) ? callOrder : order(k), after_peak: afterPeak,
      days, hits, claimed_pct: claimedN ? claimedSum / claimedN : null,
      market_days: mDays, market_hits: mHits, model_hits_on_market_days: mModel,
      mae_c: errN ? errAbs / errN : null, bias_c: errN ? errSum / errN : null,
      first_day: first, last_day: last,
    });
  }
  return out.sort((a, b) => a.call_order - b.call_order || order(a.called_when) - order(b.called_when));
}

/** Wilson 95% interval of k in n. */
export function wilson(k: number, n: number, z = 1.96): [number, number] | null {
  if (!n) return null;
  const p = k / n;
  const den = 1 + (z * z) / n;
  const c = (p + (z * z) / (2 * n)) / den;
  const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / den;
  return [c - h, c + h];
}

/** One city's record at one checkpoint, over target dates from `since`. */
export type CityEvidence = {
  n: number; hits: number; rate: number | null; interval: [number, number] | null;
  claimed: number | null; gap: number | null;
  mae: number | null; bias: number | null; errN: number;
  marketN: number; marketHits: number; modelHitsOnMarketDays: number;
};

export function cityEvidence(rows: HindsightRow[], city: string, moment: string, since: string): CityEvidence {
  const rs = rows.filter((r) => r.city_key === city && r.called_when === moment && r.for_date >= since);
  const s = summariseByMoment(rs)[0];
  if (!s) {
    return { n: 0, hits: 0, rate: null, interval: null, claimed: null, gap: null, mae: null, bias: null,
             errN: 0, marketN: 0, marketHits: 0, modelHitsOnMarketDays: 0 };
  }
  const rate = s.days ? s.hits / s.days : null;
  const claimed = s.claimed_pct === null ? null : s.claimed_pct / 100;
  return {
    n: s.days, hits: s.hits, rate, interval: wilson(s.hits, s.days),
    claimed, gap: rate !== null && claimed !== null ? rate - claimed : null,
    mae: s.mae_c, bias: s.bias_c,
    errN: rs.filter((r) => num(r.forecast_error_c) !== null).length,
    marketN: s.market_days, marketHits: s.market_hits, modelHitsOnMarketDays: s.model_hits_on_market_days,
  };
}

/** A deterministic generator, so a bootstrap gives the same interval twice. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type GroupScore = {
  n: number; hits: number; rate: number | null; interval: [number, number] | null;
  probOnWinner: number | null; marketN: number; marketHits: number; marketRate: number | null;
};

function score(rs: HindsightRow[]): GroupScore {
  const settled = rs.filter((r) => r.hit !== null);
  const hits = settled.filter((r) => r.hit).length;
  const pw = settled.map((r) => num(r.prob_on_winner)).filter((x): x is number => x !== null);
  const m = settled.filter((r) => r.market_hit !== null);
  const mh = m.filter((r) => r.market_hit).length;
  return {
    n: settled.length, hits, rate: settled.length ? hits / settled.length : null,
    interval: wilson(hits, settled.length),
    probOnWinner: pw.length ? pw.reduce((a, b) => a + b, 0) / pw.length : null,
    marketN: m.length, marketHits: mh, marketRate: m.length ? mh / m.length : null,
  };
}

export type FocusComparison = {
  moment: string; from: string; to: string; dates: number; keys: number;
  focus: GroupScore; universe: GroupScore; rest: GroupScore;
  /** focus minus the group, per date then averaged, with a 90% date-resampled interval */
  vsUniverse: { mean: number | null; interval: [number, number] | null };
  vsRest: { mean: number | null; interval: [number, number] | null };
  versions: number;
};

const key = (r: HindsightRow) => `${r.for_date}|${r.called_when}|${r.engine_version ?? ""}`;

function perDateDiff(a: HindsightRow[], b: HindsightRow[]): Map<string, number> {
  const rate = (rs: HindsightRow[]) => {
    const m = new Map<string, { h: number; n: number }>();
    for (const r of rs) {
      if (r.hit === null) continue;
      const cur = m.get(r.for_date) ?? { h: 0, n: 0 };
      cur.n += 1;
      if (r.hit) cur.h += 1;
      m.set(r.for_date, cur);
    }
    return m;
  };
  const ra = rate(a), rb = rate(b);
  const out = new Map<string, number>();
  for (const [d, x] of ra) {
    const y = rb.get(d);
    if (y && x.n && y.n) out.set(d, x.h / x.n - y.h / y.n);
  }
  return out;
}

function bootMean(diffs: number[], n = 1000, seed = 11): [number, number] | null {
  if (diffs.length < 2) return null;
  const rnd = mulberry32(seed);
  const means: number[] = [];
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = 0; j < diffs.length; j++) s += diffs[Math.floor(rnd() * diffs.length)];
    means.push(s / diffs.length);
  }
  means.sort((x, y) => x - y);
  const q = (p: number) => means[Math.min(means.length - 1, Math.max(0, Math.floor(p * (means.length - 1))))];
  return [q(0.05), q(0.95)];
}

/**
 * The focus set against the universe and against the rest, as
 * docs/FOCUS_PREREG.md fixes it: settled calls on target dates from `from`
 * through `to` (the registered window; nothing after it is scored), at one
 * checkpoint, in the universe recorded at registration (focus_set_universes,
 * never today's roster); a row counts only where BOTH groups have rows for its date,
 * checkpoint and predictor version; the difference is taken per date and
 * averaged, with a 90% interval from whole dates resampled (seed 11, 1,000
 * draws). This is the page's running view; the formal reading on 1 Dec is
 * tools/fec_same_day.cluster_boot over the whole window.
 */
export function compareFocus(rows: HindsightRow[], focus: string[], universe: string[],
                             moment: string, from: string, to: string): FocusComparison {
  const f = new Set(focus), u = new Set(universe);
  const at = rows.filter((r) => r.called_when === moment && r.for_date >= from && r.for_date <= to
    && r.hit !== null && u.has(r.city_key));
  const inF = at.filter((r) => f.has(r.city_key));
  const inRest = at.filter((r) => !f.has(r.city_key));
  const fKeys = new Set(inF.map(key)), rKeys = new Set(inRest.map(key));
  const both = new Set([...fKeys].filter((k) => rKeys.has(k)));
  const keep = (rs: HindsightRow[]) => rs.filter((r) => both.has(key(r)));
  const F = keep(inF), R = keep(inRest), U = keep(at);
  const dU = perDateDiff(F, U), dR = perDateDiff(F, R);
  const mean = (m: Map<string, number>) => {
    const v = [...m.values()];
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  return {
    moment, from, to, dates: new Set(U.map((r) => r.for_date)).size, keys: both.size,
    focus: score(F), universe: score(U), rest: score(R),
    vsUniverse: { mean: mean(dU), interval: bootMean([...dU.values()]) },
    vsRest: { mean: mean(dR), interval: bootMean([...dR.values()]) },
    versions: new Set(U.map((r) => r.engine_version ?? "")).size,
  };
}
