export interface ExportSource {
  relation: string;
  timeColumn: string;
  orderColumns: string[];
}

export interface ExportDataset {
  key: string;
  label: string;
  description: string;
  sources: ExportSource[];
}

export const PROPRIETARY_EXPORT_DATASETS: ExportDataset[] = [
  {
    key: "research_bundle",
    label: "Proprietary research bundle",
    description: "Immutable research captures, corrections, quality flags, and integrity manifests.",
    sources: [
      { relation: "research_captures", timeColumn: "captured_at", orderColumns: ["capture_id"] },
      { relation: "proprietary_data_corrections", timeColumn: "recorded_at", orderColumns: ["correction_id"] },
      { relation: "proprietary_data_quality_flags", timeColumn: "detected_at", orderColumns: ["flag_id"] },
      { relation: "proprietary_data_manifests", timeColumn: "captured_at", orderColumns: ["manifest_id"] },
    ],
  },
  {
    key: "probabilities",
    label: "Predictive probabilities",
    description: "Raw and calibrated band probabilities with model and input references.",
    sources: [{ relation: "band_probabilities", timeColumn: "computed_at", orderColumns: ["prob_id"] }],
  },
  {
    key: "signals",
    label: "Strategy signals",
    description: "Strategy decisions and system alerts retained by the desk.",
    sources: [{ relation: "signals", timeColumn: "fired_at", orderColumns: ["signal_id"] }],
  },
  {
    key: "settled_facts",
    label: "Settled research facts",
    description: "Forecast, band, and signal outcomes used for calibration and validation.",
    sources: [
      { relation: "fact_forecast_outcome", timeColumn: "captured_at", orderColumns: ["city_key", "for_date", "model", "lead_days"] },
      { relation: "fact_band_outcome", timeColumn: "captured_at", orderColumns: ["band_id"] },
      { relation: "fact_signal_outcome", timeColumn: "captured_at", orderColumns: ["signal_id"] },
    ],
  },
  {
    key: "outcome_evidence",
    label: "Resolution evidence",
    description: "Raw authoritative weather and venue payloads, hashes, parser versions, and collection attempts.",
    sources: [
      { relation: "weather_resolution_evidence", timeColumn: "captured_at", orderColumns: ["evidence_id"] },
      { relation: "weather_resolution_attempts", timeColumn: "captured_at", orderColumns: ["attempt_id"] },
      { relation: "paper_resolution_evidence", timeColumn: "captured_at", orderColumns: ["proof_id"] },
    ],
  },
  {
    key: "forecasts",
    label: "Forecast archive",
    description: "Captured provider and proprietary forecasts.",
    sources: [{ relation: "weather_forecasts", timeColumn: "run_at", orderColumns: ["forecast_id"] }],
  },
  {
    key: "observations",
    label: "Weather observations",
    description: "Station observations retained as source evidence.",
    sources: [{ relation: "weather_observations", timeColumn: "valid_at", orderColumns: ["obs_id"] }],
  },
  {
    key: "market_evidence",
    label: "Market books and trades",
    description: "Captured order books and observed market trades.",
    sources: [
      { relation: "book_snapshots", timeColumn: "observed_at", orderColumns: ["snapshot_id"] },
      { relation: "trades_observed", timeColumn: "ingested_at", orderColumns: ["trade_id"] },
    ],
  },
];

export function exportDataset(key: string): ExportDataset | undefined {
  return PROPRIETARY_EXPORT_DATASETS.find((dataset) => dataset.key === key);
}
