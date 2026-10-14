import { NextResponse } from "next/server";
import { requireOperator, serviceClient } from "../../../lib/operatorAuth";
import { webhookHeaders, webhookRefusal } from "../../../lib/n8nWebhook";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * EVERY WRITE THE BROWSER MAKES, BEHIND ONE GATE (plan v2 P1.2).
 *
 * The browser used to call these RPCs itself with the public anon key, so
 * anyone could. Now it sends its Supabase session here; requireOperator checks
 * it against settings.operators, and only then is the RPC called, with the
 * service key. anon and authenticated lose EXECUTE on every name below
 * (20260923130000_writes_need_an_operator.sql). authenticated is included
 * because anyone can create a Supabase account for themselves.
 *
 * Nothing but these names can be called: the route is not a general RPC proxy.
 * They are the writes the site makes (grep of web/, 23 Sep). approve_signal,
 * dismiss_signal, close_position, log_paper_trade and calc_recommendation
 * lose their anon grant too, and nothing on the site calls them.
 */
const WRITE_RPCS = new Set([
  "update_setting",
  "set_strategy_enabled",
  "set_run_scope",
  "queue_backtest",
  "upsert_deployment",
  "set_deployment_status",
]);

const noStore = { "Cache-Control": "no-store" };

function fail(message: string, status: number) {
  return NextResponse.json({ data: null, error: { message } }, { status, headers: noStore });
}

function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return !!origin && origin === new URL(request.url).origin;
}

type Webhooks = Record<string, { url?: string } & Record<string, unknown>>;

async function webhooks(): Promise<Webhooks | null> {
  const client = serviceClient();
  if (!client) return null;
  const { data, error } = await client.from("settings").select("value").eq("key", "n8n_webhooks").maybeSingle();
  if (error) throw new Error("Workflow settings could not be read.");
  const v = data?.value;
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Webhooks) : {};
}

/**
 * GET ?what=workflows returns configuration flags only. In the single-user
 * mode requireOperator deliberately allows requests without a session, so
 * passing that gate must never make saved webhook credentials readable.
 * URLs stay on the server even when sign-in is enabled.
 */
export async function GET(request: Request) {
  const what = new URL(request.url).searchParams.get("what");
  if (what !== "workflows") return fail("Unknown read.", 400);
  let hooks: Webhooks | null;
  try {
    hooks = await webhooks();
  } catch {
    return fail("Workflow settings could not be read.", 503);
  }
  if (!hooks) return fail("Reads of workflow settings are not configured on this server.", 503);
  const configured: Record<string, boolean> = {};
  for (const [job, entry] of Object.entries(hooks)) {
    configured[job] = !!(entry && typeof entry === "object" && typeof entry.url === "string" && entry.url.trim());
  }
  return NextResponse.json(
    { data: { configured, webhooks: null }, error: null },
    { headers: noStore },
  );
}

/**
 * POST { op: "rpc", rpc, params }
 *      { op: "set_webhook", job, url }
 *      { op: "label_backtest", run_id, label }
 *      { op: "fire_workflow", job, body }
 */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return fail("Cross-origin write rejected.", 403);
  const verdict = await requireOperator(request);
  if (!verdict.ok) return fail(verdict.message, verdict.status);
  if (Number(request.headers.get("content-length") || 0) > 65_536) return fail("Request is too large.", 413);

  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return fail("Request body is not JSON.", 400);
  }
  const client = serviceClient();
  if (!client) return fail("Writes are not configured on this server.", 503);

  try {
    if (body.op === "set_webhook") {
      const job = typeof body.job === "string" ? body.job : "";
      const url = typeof body.url === "string" ? body.url.trim() : null;
      if (!/^P\d+\.\d+_[a-z0-9_]+$/i.test(job) || url === null) {
        return fail("A workflow job and URL are required.", 400);
      }
      if (url) {
        try {
          const parsed = new URL(url);
          if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
            return fail("Use an https webhook URL without embedded credentials.", 400);
          }
        } catch {
          return fail("Use a valid https webhook URL.", 400);
        }
      }
      // Merge on the server: a replacement never needs the old secret or
      // the other workflows' URLs in the browser. Preserve their metadata.
      const hooks = await webhooks();
      if (!hooks) return fail("Workflow settings are not configured on this server.", 503);
      const next = { ...hooks, [job]: { ...(hooks[job] ?? {}), url } };
      const result = await client.rpc("update_setting", { p_key: "n8n_webhooks", p_value: next });
      if (result.error || result.data?.ok === false) {
        return fail("The workflow URL could not be saved.", 400);
      }
      // Do not echo the RPC result: its shape must not expose configuration.
      return NextResponse.json({ data: { ok: true }, error: null }, { headers: noStore });
    }

    if (body.op === "rpc") {
      const rpc = String(body.rpc || "");
      if (!WRITE_RPCS.has(rpc)) return fail(`Not a write this route performs: ${rpc || "(none)"}.`, 400);
      const params = (body.params && typeof body.params === "object" ? body.params : {}) as Record<string, unknown>;
      const result = await client.rpc(rpc, params);
      return NextResponse.json(
        { data: result.data ?? null, error: result.error ? { message: result.error.message, hint: result.error.hint } : null },
        { status: result.error ? 400 : 200, headers: noStore },
      );
    }

    if (body.op === "label_backtest") {
      const runId = String(body.run_id || "");
      const label = typeof body.label === "string" ? body.label.slice(0, 200) : "";
      if (!runId) return fail("run_id is required.", 400);
      const { error } = await client.from("backtest_runs").update({ label }).eq("run_id", runId);
      return NextResponse.json({ data: error ? null : { ok: true }, error: error ? { message: error.message } : null },
        { status: error ? 400 : 200, headers: noStore });
    }

    if (body.op === "fire_workflow") {
      const job = String(body.job || "");
      const hooks = await webhooks();
      const url = hooks?.[job]?.url;
      if (!url || typeof url !== "string") return fail(`No webhook URL is saved for ${job || "(none)"}.`, 400);
      if (!/^https:\/\//i.test(url)) return fail(`The saved URL for ${job} is not https.`, 400);
      const extra = (body.body && typeof body.body === "object" ? body.body : {}) as Record<string, unknown>;
      // Server to n8n only, carrying the webhook key (plan v2 P6.4).
      const res = await fetch(url, {
        method: "POST",
        headers: webhookHeaders(),
        body: JSON.stringify({ source: "ad4-ui", ...extra }),
        cache: "no-store",
        signal: AbortSignal.timeout(20_000),
      });
      const refused = webhookRefusal(res.status);
      const text = refused ?? (await res.text());
      return NextResponse.json({ data: { status: res.status, ok: res.ok, text: text.slice(0, 4000) }, error: null },
        { headers: noStore });
    }

    return fail("Unknown operation.", 400);
  } catch (e) {
    return fail(e instanceof Error ? e.message : "Write failed.", 500);
  }
}
