# Checks due — written 30 Sep 2026

The old session's reminders were deleted, so **the new session owns these checks**. Run each one at or after its time. Record the result in `docs/PLAN_PROGRESS.md` through a PR and merge it on green. The queries are in `04_VERIFY_QUERIES.sql`; the section names match.

The Supabase project is `jittmxhzgqpifitwupss`. Read browser-facing views as `anon` (`begin; set local role anon; …; rollback;`).

---

## A. Every morning until F1 is accepted (~09:00Z)

Did yesterday's US days reach the hit/miss table? Run **F1-a** and **F1-c**.
- Before F1 is built, expect US days about 24 h late (the regression).
- After F1, record the lag for three consecutive days (°C and °F). That is P4.7's acceptance.

---

## B. 1 Oct, after 05:30Z — the archive night and P1.6 step 3.2 (books)

The archive runs at 02:36Z (n8n clock). On 30 Sep every dataset logged ok with exported = expected = deleted (PLAN_PROGRESS P1.6, 30 Sep 05:30Z check-in).

1. **Every archive dataset** (query **B-1**): each `archive_*` row from 1 Oct is `status = 'ok'`.
   - For each, `detail->>'rows'` (exported) = `detail->'prune'->>'expected_rows'` = `detail->'prune'->>'deleted'`.
   - Where present, `band_days_before` = `band_days_after`.
   - `archive_ladders` logs no `expected_rows`; compare its `rows` with `deleted`.
   - Any mismatch or guard refusal: stop and report, do not re-run.
2. **The books dataset** (`archive_books`) should show:
   - `keep_days` 3;
   - `archived_through` about 28 Sep 02:36Z;
   - its file in `data/archive/books/` after `git pull`.
3. **The held trades.** The 30 Sep note said 5,871 held trades leave on 1 Oct: check `archive_trades`.
   - **The 55 edge marks.** They were not frozen on 30 Sep because `freeze_edge_marks` works only on rows older than 6 h. Confirm they are now in the frozen marks and none is prunable. The query that found them is in the 30 Sep check-in notes (PLAN_PROGRESS P1.6).
4. **The reference rows** (`tools/p16_step32_reference.json`): `book_as_of` at 12:00Z on 25 and 26 Sep, bands whose id starts 0 or 1; 52 and 62 rows, all cited by edges on 29 Sep 20:07Z.
   - How many are no longer in `book_snapshots`? That is how many the prune took.
   - Are those in tonight's books file?
5. **The proof.** Run `python3 tools/p16_step32_proof.py check --sql-dir <scratch>` and execute the SQL it writes.
   - Expect `same = true` for 2026-09-26 and 2026-09-27, with `from_archive` equal to the number the prune took.
   - Any `false`: compare per band, report, and do **not** call step 3.2 done.
6. **Past windows.** Rerun `python3 tools/p16_step32_proof.py past` (cut = tonight's `archived_through` date and file) and its SQL. Expect 34 groups equal and 0 different.
7. **Size** (query **B-7**): `book_snapshots` rows and size, `pg_database_size`, and `storage_pressure()`.
   - The acceptance is under 450 MB.
   - On 30 Sep `storage_pressure()` read 537.6 MB (107.5% of the 500 MB tier) at ~09:00Z.

---

## C. 5 Oct (Monday), after 08:52Z — the first weekly weather-model refit after #288

n8n dispatches `weather_model.yml` Mondays at 08:36Z (`n8n/P6.1_clock.template.json`: `"hours_utc": [8]`, `"weekdays_utc": [1]`).

1. **The run** (GitHub Actions, workflow "Weather Model"):
   - conclusion success;
   - the log fits every active city with no traceback;
   - cape_town's selection lines say `wind_u_mean` and `wind_v_mean` are "not present on every training day".
2. **The rows** (query **C-2**): `derived_weather_model` has rows with `fitted_at` on 5 Oct for the active cities.
3. **City clusters** (query **C-3**): `strategy_params` with `param = 'city_clusters'` has a fit with `as_of >= 2026-10-05`.
   - The 28 Sep 04:58Z fit predates the 29 Sep label repair.
   - The refit is weekly, from the nightly loop.
   - No decision reads it while `strategy_learning` is off.
4. **Record** in audit rows 6 and 7 of PLAN_PROGRESS.

If the run fails:
- Read the traceback.
- Reproduce it with the live rows as done on 30 Sep: pull `FIT_COLUMNS` from `derived_city_day_features` for the active cities and run `fit_city` locally.
- Fix the root cause.
- Do not dispatch by hand without a reason (Rule 7).

---

## D. Any time — things known to be unchecked

- **The 400 on `v_signal_mark`.** It was not re-checked in the edge logs after #285: the `query_logs` tool refused the table name `edge_logs`. `signal_engine`'s own log shows no `earned_weights_error` on the 08:36Z run, which is the direct evidence.
- **Actions minutes.** They were last measured on 28 Sep (2,926 of 3,000 scheduled). Re-measure before adding any step (F1).
- **The S10 shadow record** changes model version at 30 Sep 08:36Z (`389620c0d9` → `f5372ebb05`). Any scoring of S10 shadow rows must split at that version.
