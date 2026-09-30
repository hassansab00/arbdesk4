# Start here — moving to a new session (30 Sep 2026)

This folder is everything a new Claude Code session needs to take over ArbDesk 4 without this conversation. It is in the repository (`docs/handoff-2026-09-30/`, from PR #289), so a new session on `hassansab00/arbdesk4` already has it. The same files are attached for download.

## The files

| File | What it is | Who reads it |
|---|---|---|
| `00_START_HERE.md` | This page: how to start, and the prompt to paste | Hassan |
| `01_HANDOFF.md` | The state on 30 Sep. Your three questions answered with measured numbers (running in circles; why the prediction trails the market; why US cities are missing), what is live, what waits on you, the rules and traps, where things are | Both |
| `02_FIX_SPECS.md` | The work, fully specified: F1 (US days in hit/miss), F2 (all cities in the hit-rate table), F3 (improving the prediction). Root cause, evidence, design, files and lines, tests, acceptance | The new session |
| `03_CHECKS_DUE.md` | The dated checks: every morning until F1 is accepted; 1 Oct (archive and books proof); 5 Oct (weekly weather-model refit) | The new session |
| `04_VERIFY_QUERIES.sql` | Every query behind every number, labelled, to re-check them or prove a fix | The new session |

## How to start the new session

1. **Wait for PR #289 to merge.** It adds this folder to `main`. If it is not merged when you start, tell the new session to merge it first on green CI, or attach these files to your first message.
2. Open a **new Claude Code session on `hassansab00/arbdesk4`**. It needs the same environment as this one: the Supabase, GitHub, n8n and Vercel connectors, and the repo's secrets. `CLAUDE.md` loads by itself; it holds your standing rules.
3. **Paste the prompt below as your first message.** If you want the files in the conversation too, attach all five.
4. Expect the first reply to be a short plan built from these files, then work on F1 and F2. If it starts anything that is not in `02_FIX_SPECS.md` or `03_CHECKS_DUE.md`, stop it and point it back here.

## The prompt to paste

```text
You are taking over ArbDesk 4 (repo hassansab00/arbdesk4, Supabase project
jittmxhzgqpifitwupss). The previous session ended on 30 Sep 2026 ~09:00Z.

Read, in this order, before doing anything else:
  1. CLAUDE.md (my standing rules - they override everything)
  2. docs/handoff-2026-09-30/00_START_HERE.md
  3. docs/handoff-2026-09-30/01_HANDOFF.md
  4. docs/handoff-2026-09-30/02_FIX_SPECS.md
  5. docs/handoff-2026-09-30/03_CHECKS_DUE.md
  6. docs/handoff-2026-09-30/04_VERIFY_QUERIES.sql
If PR #289 is still open, check its CI and merge it (squash) when green first.

Then work ONLY on these, in this order, and nothing else unless I ask:
  F1. US city-days in /predictive's "Hit and miss" table the same day
      (a regression since 25 Sep). Measure first, as 02_FIX_SPECS.md says:
      when the venue resolves US markets, and how long a US-only sweep plus a
      databank pass take. Then build it inside pipeline_intraday (no new
      schedule), test it, open a PR, merge on green, and check it on the next
      real runs.
  F2. Show every city in "Hit rate, per city, per lead" (today it shows 8 of
      48). Web checks: tsc, test:routes, next build.
  The dated checks in 03_CHECKS_DUE.md, at their times.
  F3 only after F1 and F2 are accepted.

How I want you to work:
  - Re-check every number in the handoff against the live database before
    you rely on it; say where each number you state comes from. If you have
    not measured something, say "not measured".
  - Do not invent tasks, studies or refactors. If you find something else,
    tell me in one line and keep going with F1/F2.
  - Both test suites before every push (pytest and the database contracts).
  - After each piece of work, tell me plainly what changed, what you
    verified on a real run, and what is still not verified.
  - A note that says "X still does not work" is an open bug, not a footnote.
    Fix it or tell me.

Start by giving me a short plan (5-10 lines) for F1 and F2 from these files,
then begin F1's measurement.
```

## What is still waiting on you

These are in `01_HANDOFF.md` §4. The new session will not build them unless you say so.
1. **One retry on HTTP 401** in `common.rest()`. Six refusals since 29 Sep 12:00Z failed three tick runs.
2. **The edge gate's anchor:** keep it on the raw forecast minus bias, or move it to the ladder's peak. The numbers are in audit row 3 of PLAN_PROGRESS.
3. **Getting under 450 MB:** move closed markets to the repository (P1.6 step 3.4).
4. **The verdict ledger's size** (`resolution_verdicts`, same notes).

## What was already closed out in the old session

- **PRs #284–#288 merged:** audit repairs 4–7 and the 30 Sep check-in. #289 is this handoff.
- **Repair 5 checked** on the 30 Sep 08:36Z run: `signal_engine` ok with no weights error; `paper_exits` counts add up.
- **S10's refit is live** from 30 Sep 08:36Z (`rd1:2026-09-25:f5372ebb05`).
- **Every scheduled reminder the old session had is deleted,** so it cannot act beside the new session.
