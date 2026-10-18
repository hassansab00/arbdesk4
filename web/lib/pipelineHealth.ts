export interface FeedRun {
  job: string; status: string | null; logged_at: string | null; rows: number | null;
  detail?: Record<string, unknown> | null;
}

/** A tick is evidence for the station feed only when its observation step reports. */
export function observationFeed(run: FeedRun): FeedRun | null {
  if (run.job !== "tick") return run;
  const observation = run.detail?.observations as Record<string, unknown> | undefined;
  if (!observation) return null;
  const problems = [observation.stale, observation.silent, observation.iem_stale]
    .some(value => Array.isArray(value) && value.length > 0);
  const valid = !observation.error && !problems
    && Number(observation.rows) > 0 && Number(observation.cities) > 0;
  return {...run, rows: Number(observation.rows) || 0, status: valid ? "ok" : "attention"};
}

export function feedState(feed: {jobs: string[]; mode: "either" | "both"; staleAfterH: number},
                          latest: Map<string, FeedRun>, now = Date.now()) {
  const rows = feed.jobs.map(job => latest.get(job)).filter((r): r is FeedRun => !!r);
  const row = rows.reduce<FeedRun | undefined>((pick, value) => {
    if (!pick) return value;
    const newer = Date.parse(value.logged_at ?? "") > Date.parse(pick.logged_at ?? "");
    return (feed.mode === "either" ? newer : !newer) ? value : pick;
  }, undefined);
  const complete = feed.mode === "either" ? rows.length > 0 : rows.length === feed.jobs.length;
  const ageH = row?.logged_at ? (now - Date.parse(row.logged_at)) / 3600000 : null;
  const healthy = feed.mode === "either" ? row?.status === "ok" : rows.every(r => r.status === "ok");
  return {row, ageH, status: !row || !complete ? "never" : ageH === null || !Number.isFinite(ageH)
    || ageH < 0 || ageH > feed.staleAfterH ? "stale" : healthy ? "ok" : "attention"};
}
