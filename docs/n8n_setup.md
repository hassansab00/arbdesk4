# n8n setup

7 workflow files. Import them, fill in 2 boxes, press play.

---

## What each one does

| File | What it does | How often |
|---|---|---|
| `P0.2_market_discovery` | Finds new Polymarket weather markets. Writes `markets` + `bands`. | every 6h |
| `P0.3_book_volume_snapshot` | Saves the order book for every band. This is where all your prices come from. | every 1h |
| `P0.4_trade_history` | Saves recent trades. This is where all your volume numbers come from. | every 6h |
| `P0.5_refresh_rules_text` | Checks if Polymarket changed a market's rules. | daily |
| `P1.1_live_weather_alerts` | Emails you when the weather spikes. | when it happens |
| `P3.1_email_digests` | Morning brief + end of day report. | 04:00 and 21:00 UTC |
| `P4.1_health_watchdog` | Emails you if something broke. Silent when fine. | every 6h |

---

## STOP — read this first

**You already have P0.2, P0.3, P0.4 and P0.5 running in n8n.** That's where
your 841 markets and 122,000 trades came from.

The files here are new versions of those same four. If you import them and
turn them on while the old ones are still on, **both will write to the same
tables at the same time.** That is the thing that breaks your data.

So: import them, but leave them **OFF**. Test each one. Only then turn the
old one off and the new one on. Step 5 below.

P1.1, P3.1 and P4.1 are new. Nothing to clash with. Just import and go.

---

## Step 1 — Run the SQL

In Supabase, SQL Editor, paste and run **`sql/ad4_14_workflows.sql`**.

This adds the bits the UI needs to show and run the workflows.

---

## Step 2 — Import

In n8n: **Workflows → Import from File**. One file at a time. All 7.

---

## Step 3 — Fill in Config

Every workflow has a box called **Config**. Open it. Fill in:

| Box | What goes in it |
|---|---|
| `supabase_url` | `https://YOURPROJECT.supabase.co` |
| `service_key` | your Supabase **secret** key (the `service_role` one) |
| `alert_email` | your email — only on P1.1 and P4.1 |

That's it. Nothing else needs touching.

**Do not put the secret key anywhere in Vercel.** It goes here and in GitHub
Actions only.

### About the Polymarket web address

P0.2–P0.5 each have one more box: a Polymarket web address. I've already
filled in a sensible default, so try it as-is first.

**I could not test these.** The machine that built these files is not allowed
to reach polymarket.com, so I couldn't check the addresses are right. They're
educated guesses.

If a workflow fails on the Polymarket step, get the real address from the
workflow you already have:

1. Open your old P0.3 in n8n.
2. Click the box that fetches from Polymarket (not the ones that say
   `supabase.co`).
3. Copy what's in the **URL** field.
4. Paste it into the new workflow's Config.

Three of them need a placeholder in the address:
- P0.3 needs `{token}` where the token id goes
- P0.4 needs `{market}` where the market id goes
- P0.5 needs `{condition}` where the condition id goes

The workflow swaps the real value in for you.

**Nothing gets written if the fetch fails.** Each workflow checks it got
usable data before it writes anything, and stops with a message telling you
what went wrong. A wrong address costs you a failed run, not bad data.

---

## Step 4 — Test each one

Open the workflow. Press **Execute Workflow** (the button at the bottom).

Watch the last box, **Summary**. It tells you what happened in one line:

```
AD4 P0.3: 5457 book snapshots written from 5863 bands, 406 failed. LIVE=5100 WIDE=357
AD4 P0.4: 1204 trades ($48,300) written from 210 markets, 3 unmatched tokens.
AD4 P4.1: all clear.
```

If it goes red, click the red box and read the message. It says what to fix.

---

## Step 5 — Swap the old ones out (P0.2–P0.5 only)

Do these **one at a time**. Don't do all four at once.

1. Run the new one by hand (step 4). Check the Summary looks sane.
2. Compare it to what your old one reports.
3. Happy? Open the **old** workflow, switch **Active** to **off**.
4. Open the **new** one, switch **Active** to **on**.
5. Wait for one scheduled run. Check it worked.
6. Then do the next one.

If anything looks wrong: turn the new one off, turn the old one back on.
Nothing is lost.

For P1.1, P3.1, P4.1 — just switch **Active** on. No old version to worry
about.

---

## Step 6 — Wire up the Run buttons in AD4

This lets you run any workflow from the AD4 website instead of opening n8n.

For each workflow:

1. In n8n, click the **Webhook Trigger** box.
2. Copy the **Production URL**.
3. In AD4, go to the **Workflows** page.
4. Find that workflow's row, click **set URL**, paste, **Save**.

The **Run now** button turns on.

**The workflow must be Active for this to work.** An inactive workflow
ignores its Production URL. That's an n8n rule, not a bug.

Don't want to do this? Skip it. The Workflows page still shows you when each
one last ran and whether it worked.

---

## What changed from your old workflows

Two real bugs, both found by checking against your live database:

**P0.3 was throwing away the order book.** It wrote the price ladder into
`bid_levels` / `ask_levels`. Those columns hold a plain number — a count —
not a ladder. So the ladder went nowhere. That's why `raw_book` is empty and
every price in Goals and Calculator is currently an estimate rather than the
real book. Fixed: it now writes `raw_book` properly.

**P0.4 was writing the wrong date column.** It wrote `observed_at`. The real
column is `traded_at`. That's why every band showed $0 volume for so long,
with 122,000 trades sitting right there. Fixed.

Also fixed:
- All four were sending your **Supabase secret key to Polymarket** in the
  request headers. Copy-paste mistake from the Supabase boxes next to them.
  They now send nothing.
- P0.3 and P0.4 only ever fetched **one** thing per run. They now fetch one
  per band / per market, which is what they need to do.
- P4.1 was reading its own results wrong. It cried wolf on every single run
  and never noticed real failures. Fixed.
- P0.5 marked a market as "rules changed" the first time it ever saw it.
  Fixed — first time isn't a change.

---

## Checking it worked

In Supabase, SQL Editor:

```sql
select job, status, "rows", trigger, summary, logged_at
from v_workflow_runs
order by logged_at desc;
```

Every workflow writes a row here when it finishes — whether you ran it by
hand, from AD4, or it ran on its own schedule. `trigger` tells you which.

To check P0.3 specifically fixed the book problem:

```sql
select * from ad4_reconcile_report();
```

Look at the `raw_book coverage` line. It should stop saying `0 of N`.
