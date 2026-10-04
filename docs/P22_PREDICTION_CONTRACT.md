# P2.2: one prediction contract and a version registry (part 1, 4 Oct 2026)

**The source.** The external improvement plan of 4 Oct, item P2.2 ("Unify forecast output and
controlled learning"), reviewed in `docs/EXTERNAL_PLAN_REVIEW_2026-10-04.md`. It asks for:
1. one versioned prediction contract, read by the page and the paper trader, naming:
   - model family, artifact version and serving role;
   - station, target local date and the actual as-of time;
   - input provenance;
   - raw forecast, priced centre and uncertainty;
   - the full bucket probabilities, the predicted top and the fallback state.

   Weather probabilities stay apart from market prices and trading decisions.
2. a refit that makes a candidate instead of silently replacing the incumbent. A registry of
   fitted, shadow, eligible and served versions holds the promotion evidence and a rollback
   target. Promotion is decided per horizon: noon does not authorise the morning.
3. acceptance:
   - the page's calls, the paper decisions and the recorded evaluation name the same forecast
     identity;
   - the page shows main, S10 and challengers apart;
   - learning status separates data capture, fitting, evaluation and serving.

**Part 1, this PR.** The contract and the registry, read-only. Nothing served, priced or traded
changes. The page (part 2) and the paper decisions (part 3) move onto it next.

## Where predictions are recorded today (live, 4 Oct)
| Record | What it holds | Role |
|---|---|---|
| `prediction_checkpoints` | The engine's call at each checkpoint: ladder, top, centre, width, floor, the reading and the forecast run behind it, `model_path`, `forecast_model`, `engine_version` (the commit). | served |
| `s10_shadow_checkpoints` | S10 rd1 and rd3 (`model_version`) at the same checkpoints: ladder, median, q10/q90, inputs. | shadow |
| `variant_shadow_checkpoints` | `da_floor:v1` beside each same-day call, with its inputs (#303). | shadow |
| `current_ladders` | The newest ladder per open city-day, with `priced_from`. | served (current) |
| `band_probabilities` | Every intraday pricing; `forecast_version` / `calibration_version` point to `model_versions`. The day-ahead hit record reads the last one before local midnight. | served |
| `model_versions` | 237 forecast, 24 calibration and 1 cost version. All 262 read `active`, so the table says what existed, not what served. | — |

## The contract: `v_prediction_contract`
One row per recorded call, from the three checkpoint records, in one shape.

| Field (P2.2) | Engine (`prediction_checkpoints`) | S10 (`s10_shadow_checkpoints`) | Variant (`variant_shadow_checkpoints`) |
|---|---|---|---|
| identity | `checkpoint_id` | `checkpoint_id` | `shadow_id` |
| model family | `engine` | `s10` | `engine_variant` |
| artifact version | `priced_from` (new, below); before it, `model_path:forecast_model` | `model_version` | `variant_version` |
| code version | `engine_version` | `contract` | `engine_version` |
| serving role | `served` | `shadow` | `shadow` |
| station | `cities.icao` | `cities.icao` | `cities.icao` |
| target local date, checkpoint | `target_date`, `checkpoint` | same | same |
| as-of time | `decided_at` | `decided_at` | `decided_at` |
| input provenance | forecast run and model, the reading and its source, the floor | `inputs`, the floor | the day-ahead pricing's time and lead, the floor, q |
| raw forecast | `raw_forecast_c` (new, below) | none (S10 has no single raw forecast) | none |
| priced centre | `centre_c` | `median_c` | `day_ahead_centre_c` |
| uncertainty | `sigma_c` | (q90 - q10) / 2.5631, the normal-equivalent width | `day_ahead_sigma_c` |
| full ladder, predicted top | `probs`, `top_band_id`, `top_prob` | same | same |
| fallback state | `block_reason` when `inputs_ok` is false; the pricing path (`model_path`) | none recorded | none recorded |

**Two fields the engine did not keep,** recorded from this PR on (nullable columns, older rows
stay null):
- `priced_from`: the full label the call was priced from. It names the station-correction, MOS
  and width versions behind the centre. The checkpoint row kept only `model_path` and
  `forecast_model`, cut from it.
- `raw_forecast_c`: the forecast maximum before any correction (the engine's `forecast_max_c`).

## The registry: `model_registry` and `v_model_registry`
- **`model_registry`** is append-only events: a family and version moving to a state for a
  horizon, with the evidence, the rollback target and who decided.
- **States**, in the plan's words:
  - `captured`: data recorded, nothing fitted;
  - `fitted`: a candidate exists;
  - `shadow`: recorded beside the served call and scored forward;
  - `eligible`: passed its pre-registered rule;
  - `served`;
  - `retired`.
- **`v_model_registry`** reads the latest state of each family, version and horizon.

**Seeded from what is true on 4 Oct, each row measured:**
| Family | Version | Horizon | State | Evidence |
|---|---|---|---|---|
| engine | the station-corrected forecast path (P3.9 + MOS blend, lead >= 1; the forecast at lead 0) | all checkpoints | served | `prediction_checkpoints` |
| s10 | `rd1:2026-09-25:f5372ebb05` | same day | shadow | `s10_shadow_checkpoints`; S10 beat the served ladder from noon on in P1.1, and serving it is Hassan's decision (P7.6/P7.7) |
| s10 | `rd3:2026-09-25:555719d4a1` | same day | shadow | accepted on the holdout (`docs/CHALLENGER_C_PREREG.md`); forward first look from 25 Oct |
| engine_variant | `da_floor:v1` | same day | shadow | `docs/P11_DA_FLOOR_PREREG.md`; first look about 25 Oct |
| calibration | `temperature:T=1.141` | all checkpoints | fitted | `settings.calibration_map.applies` is false |
| station_width | W2 per-city width | day-ahead | shadow | the switch `station_width_pricing` is off; scored by `P3.9_width_score` (5 dates of the 7 needed, 4 Oct) |
| forecast_postprocess | the P3.4 bias and width cells | per city and lead | served | `v_forecast_postprocess_applied`: 1 cell (1 city, lead 4), applied since its gate passed on 3 Oct |
| trajectory | P3.4 trajectory | same day | fitted | `applied: 0` (gate unmet) |
| weather_model | per-city fits | per city and lead | shadow | `model_promotion`: 0 promoted, 195 shadow, 189 stale (4 Oct) |

## For Hassan: refits that already serve without a candidate step
The plan asks that a refit create a candidate and never silently replace the incumbent. Three
nightly fits replace what serves today:
- **The P3.9 station correction and the P2.9 MOS blend** are refitted every night and served the
  same morning. Rule 11 bounds them (prior, bounds, minimum sample, at most 0.25 °C a night, a
  version on every price), but no candidate or promotion step stands between fit and serving.
- **The P3.4 forecast post-processing** promotes a cell by its own gate. It did so for one cell on
  3 Oct.

Part 1 records these as served, with their own rule as the evidence. Whether each should go
through a candidate and promotion step is your decision, not this PR's.

## Next parts
- **Part 2:** the page reads `v_prediction_contract` and `v_model_registry`. It shows main, S10
  and challengers apart, and the learning status in the four plan words.
- **Part 3:** the paper decisions and the evaluation name the contract's identity
  (`decisions.checkpoint_id` already points at the engine's call; S10's decisions need theirs),
  and a refit writes a `fitted` event instead of serving.
