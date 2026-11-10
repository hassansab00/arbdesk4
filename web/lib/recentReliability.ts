export type RecentHit = {
  city_key: string; for_date: string; model_call: string | null; market_call: string | null;
  model_hit: boolean | null; market_hit: boolean | null; head_to_head: boolean;
};

/** The same frozen day-ahead call and matched market snapshot shown in Was it right?.
 * Pending outcomes are never losses; the newest date can be partly settled. */
export function recentReliability(rows: RecentHit[], selected: string[], maxDates = 7) {
  const wanted = new Set(selected);
  const paired = rows.filter(r => wanted.has(r.city_key) && r.head_to_head
    && r.model_hit !== null && r.market_hit !== null && r.model_call && r.market_call);
  const dates = [...new Set(paired.map(r => r.for_date))].sort().reverse().slice(0, maxDates);
  const days = new Set(dates);
  const seen = new Set<string>();
  let n = 0, modelHits = 0, marketHits = 0, disagreements = 0, modelDisagreementHits = 0, marketDisagreementHits = 0;
  for (const r of paired) {
    if (!days.has(r.for_date)) continue;
    const key = `${r.city_key}|${r.for_date}`;
    if (seen.has(key)) continue;
    seen.add(key);
    n++;
    if (r.model_hit) modelHits++;
    if (r.market_hit) marketHits++;
    if (r.model_call !== r.market_call) {
      disagreements++;
      if (r.model_hit) modelDisagreementHits++;
      if (r.market_hit) marketDisagreementHits++;
    }
  }
  return { dates, n, modelHits, marketHits, disagreements, modelDisagreementHits, marketDisagreementHits };
}
