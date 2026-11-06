type ObjectRow = Record<string, unknown>;
const object = (x: unknown): ObjectRow => x && typeof x === "object" && !Array.isArray(x) ? x as ObjectRow : {};
const count = (x: unknown) => typeof x === "number" && Number.isFinite(x) && x >= 0 ? Math.floor(x) : 0;
const text = (x: unknown) => typeof x === "string" ? x : null;
const labels: Record<string, string> = {
  missing_received_weather: "No fresh weather receipt",
  missing_as_of_rules_receipt: "Rules receipt missing",
  incomplete_received_local_weather_day: "Incomplete hourly day",
  unknown_or_mismatched_observation_contract: "Contract or station mismatch",
  missing_or_mismatched_station_weather_location: "Weather location mismatch",
  bounded_verification: "Verification limit",
  deadline: "Run budget reached",
};

/** Explicit public summary: no packets, coefficients, source rows or error payloads. */
export function projectTemperatureResearch(input: unknown) {
  const data = object(input);
  if (!Array.isArray(data.models) || typeof data.calls !== "number" || typeof data.scored !== "number") throw new Error("Invalid research summary");
  const models = data.models.map(object).sort((a, b) => String(b.registered_at).localeCompare(String(a.registered_at)));
  const model = models[0];
  if (model && (!['unfitted_reference','fitted_candidate'].includes(String(model.state)) || !/^[a-f0-9]{64}$/.test(String(model.sha256)))) throw new Error("Invalid research model");
  const status = object(data.status), detail = object(status.detail);
  const learning = object(data.learning), learned = object(learning.detail);
  const skips = new Map<string, number>();
  for (const [key, value] of Object.entries(object(detail.skipped))) {
    const label = labels[key] ?? "Input validation failed";
    skips.set(label, (skips.get(label) ?? 0) + count(value));
  }
  return {
    model: model ? { state: String(model.state), version: String(model.sha256).slice(0,12),
      registered_at: text(model.registered_at), evaluation_first: text(model.evaluation_first),
      evaluation_last: text(model.evaluation_last), cities: Array.isArray(model.cohort) ? model.cohort.length : 0 } : null,
    calls: count(data.calls), scored: count(data.scored),
    capture: { updated_at: text(status.updated_at), current_model: !!model && detail.model_sha256 === model.sha256,
      error: detail.state === 'error' || typeof detail.error === 'string',
      due: count(detail.due), new: count(detail.new),
      skipped: [...skips].map(([label, n]) => ({ label, count: n })),
      blocked: [...skips].reduce((sum, [label,n]) => sum + (label === 'Verification limit' ? 0 : n), 0) },
    learning: { updated_at: text(learning.updated_at), dates: count(learned.dates), rows: count(learned.eligible_rows),
      cities_per_date: count(learned.cities_per_date_min), staged: learned.state === 'fitted_unregistered_candidate' },
  };
}
export type TemperatureResearchSummary = ReturnType<typeof projectTemperatureResearch>;

export function temperatureResearchPhase(data: TemperatureResearchSummary, now = Date.now()) {
  if (!data.model) return { label: "Not registered", warning: false };
  if (data.capture.error) return { label: "Capture needs attention", warning: true };
  const receipt = Date.parse(data.capture.updated_at ?? "");
  if (!Number.isFinite(receipt)) return { label: "Awaiting first capture run", warning: false };
  if (!data.capture.current_model) return { label: "Awaiting current model run", warning: false };
  if (now - receipt > 2 * 3600_000) return { label: "Capture report overdue", warning: true };
  if (data.capture.blocked) return { label: "Capture inputs blocked", warning: true };
  if (!data.calls) return { label: "Waiting for qualified captures", warning: false };
  return { label: data.model.state === 'unfitted_reference' ? "Collecting training evidence" : "Fitted candidate registered", warning: false };
}
