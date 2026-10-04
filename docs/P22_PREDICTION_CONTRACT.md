# P2.2: one prediction contract and a version registry (parts 1 to 3, 4 Oct 2026)

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
| station_width | W2 per-city width | day-ahead | shadow | the switch `station_width_pricing` is off; scored by `P3.9_width_score` (5 dates of the 7 needed, 4 Oct). Part 3 retires this row once a nightly width version is registered (live, at its first run): from then on each nightly width fit is its own version (review of #306). |
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
- `v_prediction_lineup` holds the calls from 7 target dates ago on, one row per predictor and
  checkpoint, with the venue's winner once settled.
  - The page reads one date and one checkpoint at a time: at most 144 rows on the live data of
    4 Oct, against 4,197 in the whole window.
  - It offers tomorrow to 7 days back (UTC dates). Tomorrow is the evening-before call's date, and
    a city ahead of UTC starts its day first: at 17:59Z on 4 Oct the view held 31 evening-before
    calls for 5 Oct (review of #305).
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
  the city's local day is over or its winner is banked, and it never gets a winner, hit or
  probability on the winner. While the city's day runs, its calls show.
- Past means past on the city's clock: `target_date` is its local date, and a city ahead of UTC ends
  its day hours before UTC does (the review of #305; the first draft compared with UTC's date).
- The first look unblinds it with a new event (`blind = false`).
- Live before applying, 17:47Z:
  - rd3 on 3 Oct: 61 of 61 calls withheld.
  - rd3 on 4 Oct: 75 of 194 withheld. 45 of the 75 are in cities whose 4 Oct had already ended with
    no winner banked yet; the UTC rule of the first draft would have shown them.
  - `da_floor` on 4 Oct: 22 shown.

**The page.**
- "Where each predictor stands" lists each version with its stage, its evidence and its rollback
  target.
- "Who called what" puts the calls side by side with each column's record on that date and
  checkpoint, and S10's record against the engine on the same city-days.
- A blinded column is labelled as such and never tallied (`web/lib/lineup.ts`,
  `web/tests/lineup.test.cjs`).

## Part 3: decisions name their call, and no refit serves unrecorded
Migration `20261004210000_decisions_name_their_call.sql`. The plan's acceptance: the page's calls,
the paper decisions and the recorded evaluation name the same forecast identity, and a refit does
not silently replace the incumbent.

### 1. Each decision names the call it acted on
**The gap, live on 4 Oct.**
- The engine's six strategies write one `decisions` row per city-day, and `checkpoint_id` names
  the engine's checkpoint.
- S11 and S12 decide on that call's ladder, so for them it is the right call.
- S10's three strategies decide on S10's own ladder (`s10_shadow_checkpoints`). Yet all 1,833 of
  their rows with a checkpoint named the engine's call, and none named the S10 row or the S10
  version they acted on.

**From this PR on, the tick writes the call.**
- `decisions.prediction_id` and `decisions.prediction_source` name it as the contract does: its
  `prediction_id` and `recorded_in`. The two are set together, or both left null.
- S11 and S12 name the engine checkpoint.
- S10 names the stored S10 row and acts on that row's ladder:
  - `scripts/s10_shadow.py` reads the rows back after its write.
  - The write ignores a duplicate key, so a recomputed ladder was never stored. Until now S10
    decided on it anyway: 30 of its decisions acted on a ladder no row holds.
  - If the read-back fails, the decision keeps the computed ladder and names no call. The tick
    counts it as `unrecorded` and logs `attention`, as it does for a lost `da_floor` capture.
  - If the write fails, S10 has no ladder and decides `NONE`.

**`v_decision_prediction` resolves every decision, the older ones too, and says how (`link`).**
Counts are from the live dry run on 4 Oct (33,497 decisions):

| `link` | Meaning | Live, 4 Oct |
|---|---|---|
| `recorded` | `prediction_id` written by the tick | from this PR on |
| `checkpoint` | S11 and S12: the engine's checkpoint is their call | `s11_ladder` 1,844 |
| `same_tick` | an S10 decision before this PR: the rd1 row written in the same tick (within 60 s; the measured gaps run to 38.7 s, and none lies between 60 s and 10 min) | `s10_winner` 1,470 |
| `not_recorded` | an S10 decision on a ladder no stored row holds (the engine's second captures, 25 Sep - 1 Oct) | `s10_winner` 30 |
| `no_call` | decided without a ladder (S10 has no evening-before call), or without a checkpoint (27 Sep) | `s10_winner` 344, `s11_ladder` 78 |
| `signal_path` | s1-s9: they decided on the old signal path, which is not a recorded call | `s1` 2,133 |

**The evaluation names the same identity.**
- The engine's grading (`fact_checkpoint_outcome`) is keyed by `checkpoint_id`, its
  `prediction_id`.
- S10's forward scoring (`tools/fec_s10_forward.py`) names city, date, checkpoint and both model
  versions. That key is unique in `s10_shadow_checkpoints`, so it names one row; the
  pre-registered tool is left unchanged.
- A paper order carries its `decision_id` (`engine_orders`), and through it the call.
- **The archive keeps the link.** `archive_observations` exports `decisions` from a fixed column list
  and then prunes at 30 days. The list now holds every column of the table: `decision_id` (the key,
  until now used only to page) and the two new ones. A test holds the list to the table, as the
  migrations declare it (review of #306). The first decisions are due for the archive about 25 Oct.

### 2. The nightly refits are recorded as they serve
Hassan's decision stands: they serve automatically. What changes is that a refit is no longer
silent. `record_model_versions()` appends a `model_registry` event whenever a version's state
changes.

**Served means priced.** Whether a version serves depends on several things:
- which forward rows are fresh;
- which city-days a fit rewrote (the engine reads every fresh row, whatever its version);
- each call's lead;
- the switches. The MOS blend and the width apply only inside the station correction's branch.

The engine records the answer on every price it makes. The label names the correction version,
then `+station-mos:` when the blend applied and `+station-width:` when the width priced
(`forecast_provenance`). So the function reads what priced, rather than re-deriving what could
have:

| State | When |
|---|---|
| served | priced in the last 36 h (`band_probabilities` -> `model_versions.label`, and the tick's `priced_from`), no newer version of the family first priced after its last price, its switches on (the MOS blend and the width also need the correction's), and forward rows younger than the switch's `max_age_hours`, as `probability_engine` reads them. The width rides on the correction's rows; a MOS row counts only over a fresh correction row of its city-day made by its `p39_version`. Two versions pricing side by side are both served. |
| retired | at once when a switch goes off or its forward rows expire; or superseded (a newer version first priced after its last price); or not priced for 36 h. Versions retiring in one run are written in the order they first priced, so the newer fit gets the later `event_id`. |
| fitted (the width: shadow) | the newest fit in the forward rows that has not priced, recorded once with the reason: the switch is off, the correction is off, or it has not priced yet. An older fit that never priced is retired once a newer fit supersedes it, so each family keeps one candidate, not one a night. A row registered by hand is left alone, except part 1's `W2 per-city width`, retired once a nightly width version is registered. |

**Calibration** and new **S10 / variant** versions are recorded as before:
- calibration: `settings.calibration_map`, served if `applies`, else fitted;
- S10 and variants: a version first written in the last two days is recorded once as shadow.

**Each served event names its rollback,** the version served before it.

**How this answered the review of #306:** each round found the function reading a proxy for
serving.
- First the coefficient tables, which may never price.
- Then the forward rows, where several versions are fresh at once and horizons apply.
- Then a horizon taken from the switch, which the prices can't confirm.

The price labels are the engine's own record of what served.

- **One horizon, "as priced".** A price label names the versions, not the lead, so which leads a
  nightly version served can't be told from its prices. The switch's lead setting goes in the
  evidence. Per-horizon promotion is for candidates, which move by a decision; the nightly fits serve
  by Hassan's rule, wherever the engine prices with them (review of #306).
- **It runs in the database.** pg_cron calls it hourly at :50, which costs no Actions minutes
  (Rule 7). It also runs once in the migration.
- **Live dry run, 4 Oct 19:30Z, rolled back:**
  - the correction `station-correction:2026-10-04:da319e41d6` is served, priced 1,574 times from
    08:36Z to 17:36Z;
  - MOS `station-mos:2026-10-04:0656cd4985` is served, priced 1,541 times;
  - the width `station-width:2026-10-04:d8fd4f2746` is shadow: its switch is off, and it never
    priced;
  - the 3 Oct versions priced until 04:37Z and were then superseded, so they get no event;
  - a second run appended nothing.

**On the page:** the learning status lists the standing versions and the newest retired version of
each family, and counts the rest, since every nightly fit now retires the one before it.
`v_learning_status` has three columns appended:
- `event_id`: the events of one run share a time, so the newest is chosen by time, then by `event_id`;
- `newest_retired`: marks that row;
- `retired_in_family`: counts the family's retired versions.

The page asks for the standing rows and the marked ones only. A row cap therefore never drops a
standing version, and the count covers rows the page never fetched. The lineup looks up its own
versions' states separately, so an older retired S10 version still reads as retired there (review
of #306).

### Not done here
- The P3.4 forecast post-processing promotes per cell, and the trajectory and the per-city weather
  models fit per cell or per city. They keep their family rows from part 1; a version per cell
  would be 195 rows.
- The replay (`engine_replay_live.py`) still matches S10 by key and version. It could use the
  decisions' `prediction_id` from now on.
