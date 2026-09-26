# Inputs of the first S10 replay (plan v2 P7.3), 26 Sep 2026

Running `scripts/backtest/replay_checkpoints.py` on these files, together with
`data/training/previous_runs/` (P2.9), reproduces
`docs/S10_REPLAY_2026-09-26.md` byte for byte (checked 26 Sep).

| file | what | from |
|---|---|---|
| `replay_inputs.json.gz` | the 1,561 venue-confirmed city-days (24 Aug-25 Sep), their 17,171 canonical bands, `derived_weather_peak`, the cities' q layer (unused), 15,347 book mids from 06:00 to 21:00 local | `tools/p73_replay_inputs.sql`, run 26 Sep 23:20Z |
| `obs_utc.csv.gz` | station readings `city_key, utc, temp_c`: the IEM rows of `data/archive/observations`, plus `weather_observations` source `IEM` from 28 Jul 2026; 551,689 readings, 22 Jul 2025 - 26 Sep 2026, one per city and minute | built 26 Sep for P7.2 |
| `labels_whole.json.gz` | `derived_city_day_features` (city, date, max_c, ...), training labels only, **whole days only**: the 43 rows computed before their local day ended were removed (`common.day_had_ended`); 22,484 rows up to 25 Sep | read 26 Sep 18:56Z, filtered 23:00Z |
| `units.json`, `tz.json` | each city's unit and IANA zone, from `cities` | 26 Sep |

The venue's winners come only from `replay_inputs`; the station labels only
train the model.
