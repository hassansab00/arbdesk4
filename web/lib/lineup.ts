/**
 * WHO CALLED WHAT, AND WHERE EACH PREDICTOR STANDS (P2.2 part 2, 4 Oct).
 *
 * The external plan's P2.2 asks the page to show the main engine, S10 and the
 * challengers apart, and the learning status in four words: data capture,
 * candidate fitting, evaluation, serving. Both come from the database
 * (20261004200000_the_page_reads_the_contract.sql):
 *
 *   v_learning_status    each predictor version's latest registry state
 *   v_prediction_lineup  every recorded call of the last 8 target dates, one
 *                        row per predictor and checkpoint, with the venue's
 *                        winner once settled
 *
 * A BLINDED VERSION SHOWS NO PAST CALL. rd3 and da_floor are under
 * pre-registered forward tests that compute and report no score before their
 * first look. The view withholds a blinded version's call once its day is past
 * or settled, and never gives it a winner; these functions keep it that way
 * and never count it in a tally.
 */

export interface LineupRow {
  city_key: string; target_date: string; checkpoint: string;
  model_family: string; version: string; serving_role: string;
  as_of: string; station: string | null;
  blind: boolean; withheld: boolean;
  top_band_id: string | null; top_label: string | null; top_prob: number | string | null;
  priced_centre_c: number | string | null; uncertainty_c: number | string | null;
  winner_band_id: string | null; winner_label: string | null;
  hit: boolean | null; prob_on_winner: number | string | null;
}

export interface StatusRow {
  family: string; version: string; horizon: string; state: string; stage: string;
  blind: boolean; decided_at: string; decided_by: string; evidence: string;
  rollback_to: string | null; note: string | null;
}

/** The plan's words, in the order a version moves through them. */
export const STAGES = ["serving", "evaluation", "candidate fitting", "data capture", "retired"];

export const FAMILY: Record<string, string> = {
  engine: "Served engine",
  s10: "S10 remaining-day",
  engine_variant: "Engine variant",
  calibration: "Calibration map",
  station_width: "Station width",
  forecast_postprocess: "Forecast post-processing",
  trajectory: "Trajectory",
  weather_model: "Per-city weather models",
};

/** The checkpoints the lineup can show, earliest first. */
export const CHECKPOINTS = ["d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h"];

const FAMILY_ORDER = ["engine", "s10", "engine_variant"];

export const num = (v: unknown): number | null => {
  const x = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(x) ? x : null;
};

/** "rd1 f5372eb" from "rd1:2026-09-25:f5372ebb05"; "da_floor v1" from "da_floor:v1". */
export function shortVersion(v: string): string {
  const parts = v.split(":");
  if (parts.length === 3) return `${parts[0]} ${parts[2].slice(0, 7)}`;
  return parts.join(" ");
}

/** Every version, serving first and retired last; within a stage by family, then version. */
export function learningStatus(rows: StatusRow[]): StatusRow[] {
  const stage = (s: string) => {
    const i = STAGES.indexOf(s);
    return i < 0 ? STAGES.length : i;
  };
  const fam = (f: string) => {
    const i = FAMILY_ORDER.indexOf(f);
    return i < 0 ? FAMILY_ORDER.length : i;
  };
  return [...rows].sort((a, b) =>
    stage(a.stage) - stage(b.stage) || fam(a.family) - fam(b.family)
    || a.family.localeCompare(b.family) || a.version.localeCompare(b.version));
}

/** The target dates the lineup holds: today (UTC, the database's clock) and the 7 before it. */
export function lineupDates(now: Date, days = 8): string[] {
  const base = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Array.from({ length: days }, (_, i) => new Date(base - i * 86_400_000).toISOString().slice(0, 10));
}

export interface Column {
  /** "engine", or family|version for everything else */
  key: string;
  family: string;
  /** null for the engine: its version is recorded per call (the tooltip names it) */
  version: string | null;
  title: string;
  blind: boolean;
  /** the registry's latest state for this version, when it has one */
  state: string | null;
}

export interface Tally {
  /** calls with a settled winner */
  graded: number;
  hits: number;
  /** city-days this column and the engine both called and both graded */
  common: number;
  hitsOnCommon: number;
  engineHitsOnCommon: number;
}

export interface Line {
  city_key: string;
  winner_label: string | null;
  cells: Record<string, LineupRow>;
}

export interface Lineup {
  columns: Column[];
  lines: Line[];
  tallies: Record<string, Tally>;
}

const colKey = (r: { model_family: string; version: string }) =>
  r.model_family === "engine" ? "engine" : `${r.model_family}|${r.version}`;

/**
 * One date and checkpoint's calls as a table: a line per city, a column per
 * predictor, and each column's record on the cities it was graded on. A
 * blinded column is never tallied, whatever its rows carry.
 */
export function lineupTable(rows: LineupRow[], status: StatusRow[] = []): Lineup {
  const state = new Map(status.map((s) => [`${s.family}|${s.version}`, s]));
  const served = status.find((s) => s.family === "engine" && s.state === "served");

  const cols = new Map<string, Column>();
  for (const r of rows) {
    const key = colKey(r);
    if (cols.has(key)) {
      if (r.blind) cols.get(key)!.blind = true;
      continue;
    }
    const reg = r.model_family === "engine" ? served : state.get(key);
    cols.set(key, {
      key,
      family: r.model_family,
      version: r.model_family === "engine" ? null : r.version,
      title: r.model_family === "engine"
        ? FAMILY.engine
        : `${FAMILY[r.model_family] ?? r.model_family} ${shortVersion(r.version)}`,
      blind: r.blind || Boolean(reg?.blind),
      state: reg?.state ?? null,
    });
  }
  const fam = (f: string) => {
    const i = FAMILY_ORDER.indexOf(f);
    return i < 0 ? FAMILY_ORDER.length : i;
  };
  const columns = [...cols.values()].sort((a, b) =>
    fam(a.family) - fam(b.family) || (a.version ?? "").localeCompare(b.version ?? ""));

  const byCity = new Map<string, Line>();
  for (const r of rows) {
    let line = byCity.get(r.city_key);
    if (!line) {
      line = { city_key: r.city_key, winner_label: null, cells: {} };
      byCity.set(r.city_key, line);
    }
    line.cells[colKey(r)] = r;
    if (!r.blind && r.winner_label) line.winner_label = r.winner_label;
  }
  const lines = [...byCity.values()].sort((a, b) => a.city_key.localeCompare(b.city_key));

  const tallies: Record<string, Tally> = {};
  for (const c of columns) {
    const t: Tally = { graded: 0, hits: 0, common: 0, hitsOnCommon: 0, engineHitsOnCommon: 0 };
    if (!c.blind) {
      for (const line of lines) {
        const cell = line.cells[c.key];
        if (!cell || cell.blind || cell.hit === null) continue;
        t.graded += 1;
        if (cell.hit) t.hits += 1;
        const eng = line.cells.engine;
        if (c.key !== "engine" && eng && eng.hit !== null) {
          t.common += 1;
          if (cell.hit) t.hitsOnCommon += 1;
          if (eng.hit) t.engineHitsOnCommon += 1;
        }
      }
    }
    tallies[c.key] = t;
  }
  return { columns, lines, tallies };
}
