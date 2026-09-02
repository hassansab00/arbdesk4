# n8n — import, wire up, and run from the UI

Seven workflows. All of them now have the same three ways in:

| Trigger | When |
|---|---|
| **Schedule Trigger** | the workflow's own cron, unattended |
| **Manual Trigger** | "Execute Workflow" inside n8n, for testing |
| **Webhook Trigger** | the AD4 **Workflows** page, or any HTTP POST |

and the same two ways out:

| Node | What it does |
|---|---|
| **Log run** | calls `log_ingest(job, status, rows, detail)` at the end of *every* execution, whichever trigger started it |
| **Respond** | returns the run summary to the webhook caller, so the UI shows a real result instead of a bare 200 |

Because **Log run** fires on all three paths, the Workflows page's "Last run"
column is the truth for scheduled and manual runs too — not just the ones
started from the browser.

---

## Before you import: read this if P0.2–P0.5 already run in your n8n

`P0.2`, `P0.3`, `P0.4` and `P0.5` **already exist and run** in your n8n — they
are what put the 841 markets and 122k trades in the database. The files here
are reconstructions. Importing them as new workflows and activating them
alongside the originals means **two copies writing the same tables**.

You have two safe options.

### Option A — patch your existing workflows (lowest risk)

Keep your originals, and copy across only what changed. For **P0.3** that is
one node and it matters: it was writing the order book into
`bid_levels`/`ask_levels`, which are `integer` columns on the real schema, so
the ladder never landed. That is why `raw_book` is empty and every fill price
is currently computed from a reconstructed ladder.

1. Open your existing P0.3 in n8n.
2. Find the Code node that builds the `book_snapshots` rows.
3. Replace its code with the `Build snapshots` node's code from
   `n8n/P0.3_book_volume_snapshot.scaffold.json`.
4. Execute once, and check a new row has a non-null `raw_book`.

To also get the webhook + logging on your originals, add the three nodes by
hand — they are small and the parameters are in the files here.

### Option B — cut over to these files (uniform, more setup)

Do it one workflow at a time, and **never leave both active**:

1. Open your existing workflow, copy the Polymarket URL out of its HTTP node.
2. Import the file here as a *new* workflow. Leave it inactive.
3. Paste `supabase_url`, `service_key` and that Polymarket URL into its
   **Config** node.
4. **Execute Workflow** manually. Compare the Summary line and the rows it
   wrote against what the original produces.
5. Only when they match: **deactivate the original**, then activate the new one.

`P1.1`, `P3.1` and `P4.1` do not exist in your n8n yet, so they are a plain
import with no cutover to worry about.

---

## Import

n8n → **Workflows → Import from File**, one file at a time.

| File | Webhook path | Own schedule |
|---|---|---|
| `n8n/P0.2_market_discovery.scaffold.json` | `/webhook/ad4-market-discovery` | as configured |
| `n8n/P0.3_book_volume_snapshot.scaffold.json` | `/webhook/ad4-book-snapshot` | as configured |
| `n8n/P0.4_trade_history.scaffold.json` | `/webhook/ad4-trade-history` | as configured |
| `n8n/P0.5_refresh_rules_text.scaffold.json` | `/webhook/ad4-refresh-rules` | as configured |
| `n8n/P1.1_live_weather_alerts.template.json` | `/webhook/ad4-weather-alert` | event-driven |
| `n8n/P3.1_email_digests.template.json` | `/webhook/ad4-email-digests` | 04:00 / 21:00 UTC |
| `n8n/P4.1_health_watchdog.template.json` | `/webhook/ad4-health-watchdog` | every 6 hours |

## Fill in Config

Every workflow has one **Config** node. Nothing else holds credentials, and no
node uses n8n's credential dropdown — that failed repeatedly earlier in this
project with "Credentials not found".

| Field | Value | Which workflows |
|---|---|---|
| `supabase_url` | `https://YOURPROJECT.supabase.co` | all |
| `service_key` | the **`service_role`** / secret key | all |
| `polymarket_*_url` | see below | P0.2–P0.5 |
| `alert_email` | where alerts go | P1.1, P4.1 |

### What `polymarket_*_url` is, and where to find yours

It is the Polymarket API endpoint that workflow calls to *read* data — the
markets list, an order book, the trade tape, a market's rules text. It is not
something this repo can tell you, and that is deliberate: the four P0.x
workflows were built directly in n8n and never captured here, so the repo has
never seen the URLs they use. Guessing one and writing it down as fact would
be worse than leaving it blank.

**Your existing workflows already have them.** To read one out:

1. n8n → open your working **P0.3** (or P0.2 / P0.4 / P0.5).
2. Click its HTTP Request node — the one fetching *from Polymarket*, not the
   ones pointing at `supabase.co`.
3. Copy the **URL** field.

The only Polymarket URL this repo contains at all is
`https://gamma-api.polymarket.com/markets`, in `scripts/settlement.py`, and
that one is itself flagged unverified. Do not treat it as the answer for the
other three.

**If you take Option A above, you never need this.** Patching the
`Build snapshots` code node inside your existing P0.3 leaves its fetch — and
its URL — untouched.

> **Fixed here:** these four fetch nodes used to send `apikey` and
> `Authorization: Bearer <service_key>` headers to the Polymarket URL —
> copy-pasted from the Supabase nodes. That put your Supabase service key in a
> request to a third party. They now send only `Accept: application/json`.
> Worth checking the same thing in your own workflows.

### The scaffolds are not drop-in replacements

`Fetch books` and `Fetch trades` do a single static GET. They do **not**
iterate the bands loaded above them or substitute each band's `token_yes` into
the request — which a real book or trade fetch has to do, because Polymarket
serves one book per token. Your existing P0.3/P0.4 already handle that; these
files reconstruct the row *shape*, not the fetch. The sticky note on each
canvas says so too.

That is the strongest reason to prefer **Option A**.

> The service key belongs **only** here and in the GitHub Actions secret. Never
> in a `NEXT_PUBLIC_*` variable — that compiles it into the browser bundle.
>
> Never re-export a filled-in workflow into this repo. Use
> `python scripts/sanitise_n8n_export.py <export.json> n8n/` — it strips Config
> values and refuses to write if a secret survives.

## Activate

Toggle **Active** (top right) on each workflow.

This matters for the UI: **an inactive workflow only answers its
`/webhook-test/` URL**, and only for one call after you press "Listen for test
event". The `/webhook/` URL the Workflows page uses needs the workflow Active.

## Wire the webhooks to the UI

1. Run `sql/ad4_14_workflows.sql` in Supabase (once). It adds
   `settings.n8n_webhooks`, the `v_workflow_runs` view, and makes
   `update_setting`'s whitelist data-driven so the page can save the URLs.
2. Open each workflow's **Webhook Trigger** node and copy its **Production URL**.
3. In AD4 → **Workflows**, click **set URL** on that row, paste, **Save**.
4. The **Run now** button turns on.

### CORS

Each Webhook Trigger ships with `allowedOrigins: *` so the browser can call it.
Narrow it to your Vercel domain once that is settled: open the node → Options →
Allowed Origins.

### Is this safe?

`settings` is anon-readable, so anything holding your publishable key can read
these URLs and POST to them. n8n webhook paths are unguessable and every
workflow is idempotent, so the realistic worst case is someone burning n8n
executions — not corrupting data. If that trade is not one you want, leave
`n8n_webhooks` empty: the Workflows page degrades to a read-only status board
and says so, and the schedules still populate it.

## P3.1 takes an argument

The Email Digests webhook accepts a body:

```json
{"digest": "morning"}   // default
{"digest": "eod"}
```

which is why that row has two buttons.

## Verifying a run

Either read the Summary node in n8n, or:

```sql
select job, status, "rows", trigger, summary, logged_at
from v_workflow_runs
order by logged_at desc;
```

`trigger` is `manual`, `trigger` (schedule) or `webhook`, so you can tell which
path fired it. The full history is in `ingest_log`.
