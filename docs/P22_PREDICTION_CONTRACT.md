# P2.2: one prediction contract and a version registry (parts 1 and 2, 4 Oct 2026)

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
| station | `station` (new): `cities.icao` as it stood at the decision | the served call's `station` at the same city, date and checkpoint | `station` (new), copied from the served call |
| target local date, checkpoint | `target_date`, `checkpoint` | same | same |
| as-of time | `decided_at` | `decided_at` | `decided_at` |
| input provenance | forecast run and model, the reading and its source, the floor | `inputs`, the floor | the day-ahead pricing's time and lead, the floor, q |
| raw forecast | `raw_forecast_c` (new, below) | none (S10 has no single raw forecast) | none |
| priced centre | `centre_c` | `median_c` | `day_ahead_centre_c` |
| uncertainty | `sigma_c` | (q90 - q10) / 2.5631, the normal-equivalent width | `day_ahead_sigma_c` |
| full ladder, predicted top | `probs`, `top_band_id`, `top_prob` | same | same |
| fallback state | `block_reason` when `inputs_ok` is false; the pricing path (`model_path`) | none recorded | none recorded |

**Fields the records did not keep,** recorded from this PR on (nullable columns, older rows
stay null):
- `station` on the engine's checkpoint and on the variant row: the settlement station as it stood
  when the call was made. `cities` keeps no history, so read through the current row a corrected
  station would rewrite every past call's. For a call from before 4 Oct the contract can only give
  today's station; `station_source` says which (`recorded`, `served_call`, `cities_now`).
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
| s10 | `rd1:2026-09-25:389620c0d9` | same day | retired | added in part 2 (part 1 missed it): S10's first file wrote the shadow rows of 27 Sep 12:36Z - 30 Sep 07:36Z (656, live) until the same fit on the repaired labels replaced it (`docs/PLAN_PROGRESS.md`, check 6) |

## Decided by Hassan, 4 Oct: the nightly refits stay automatic
The plan asks that a refit create a candidate and never silently replace the incumbent. Three
nightly fits replace what serves today:
- **The P3.9 station correction and the P2.9 MOS blend** are refitted every night and served the
  same morning. Rule 11 bounds them (prior, bounds, minimum sample, at most 0.25 °C a night, a
  version on every price), but no candidate or promotion step stands between fit and serving.
- **The P3.4 forecast post-processing** promotes a cell by its own gate. It did so for one cell on
  3 Oct.

**Hassan's decision, 4 Oct: "keep nightly automatic".** The three nightly fits keep serving the
morning after they are fitted, bounded by Rule 11 and their own gates. The registry records them as
served, with their rule and this decision as the evidence.

The candidate step P2.2 asks for still applies to everything else: a new model, a new version of S10,
a variant like `da_floor`, or switching on a fitted map. Those move only on a pre-registered result
and a recorded decision.

## Part 2: the page reads the contract
Migration `20261004200000_the_page_reads_the_contract.sql`. The panel is
`web/components/PredictionLineup.tsx`, on /predictive under "Was it right?".

**Two views the browser reads (as `anon`):**
- `v_learning_status` gives every version's latest registry state in the plan's words:
  - `captured` → data capture;
  - `fitted` → candidate fitting;
  - `shadow` and `eligible` → evaluation;
  - `served` → serving;
  - `retired` → retired.
- `v_prediction_lineup` holds the calls of the last 8 target dates, one row per predictor and
  checkpoint, with the venue's winner once settled.
  - The page reads one date and one checkpoint at a time: at most 144 rows on the live data of
    4 Oct, against 4,197 in the whole window.
  - Each date and checkpoint shows as a line per city, with one column each for the served engine,
    each S10 version and each variant.

**Both views run with the owner's rights.**
- Part 1's `v_prediction_contract` and `v_model_registry` turn from `security_invoker` to the
  owner's rights.
- Read through a page view, a `security_invoker` view checks as `anon`. `anon` can read none of
  the tables under them (CLAUDE.md, 23 Sep).
- The two part-1 views stay granted to the service role alone.

**The engine counts once per checkpoint.**
- It re-captures a checkpoint when its version changes mid-day: 72 second captures in the 8 days
  to 4 Oct.
- The lineup keeps the first capture, the rule `v_checkpoint_calls.first_call` grades by.
- Checked live on 4 Oct, before applying: the lineup's 2,010 graded engine calls equal the 2,010
  first calls of `v_checkpoint_calls` on city, date, checkpoint, called label, winner and hit,
  with `EXCEPT ALL` returning 0 rows in both directions.

**A blinded version shows no past call.**
- `model_registry` gains `blind`. Two versions are under pre-registered forward tests that compute
  and report no score before their first look:
  - rd3 (`docs/CHALLENGER_C_PREREG.md`);
  - `da_floor:v1` (`docs/P11_DA_FLOOR_PREREG.md`).
- A lineup row puts each call beside the day's winner. So a blinded version's call is withheld once
  its day is past or settled, and it never gets a winner, hit or probability on the winner. Its
  calls for today show.
- The first look unblinds it with a new event (`blind = false`).
- Live before applying: rd3 withheld on 71 rows (3 Oct), shown on 175 (4 Oct); `da_floor` shown on
  13 (4 Oct).

**The page.**
- "Where each predictor stands" lists each version with its stage, its evidence and its rollback
  target.
- "Who called what" puts the calls side by side with each column's record on that date and
  checkpoint, and S10's record against the engine on the same city-days.
- A blinded column is labelled as such and never tallied (`web/lib/lineup.ts`,
  `web/tests/lineup.test.cjs`).

## Next part
- **Part 3:** the paper decisions and the evaluation name the contract's identity
  (`decisions.checkpoint_id` already points at the engine's call; S10's decisions need theirs),
  and a refit writes a `fitted` event instead of serving.
