import { NextResponse } from "next/server";
import { requireOperator, serviceClient } from "../../../lib/operatorAuth";

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
  const { data } = await client.from("settings").select("value").eq("key", "n8n_webhooks").maybeSingle();
  const v = data?.value;
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Webhooks) : {};
}

/**
 * GET ?what=workflows - which n8n jobs have a webhook URL, and the URLs
 * themselves to whoever passes requireOperator (everyone who can reach the
 * site while sign-in is off; only an operator when it is on).
 */
export async function GET(request: Request) {
  const what = new URL(request.url).searchParams.get("what");
  if (what !== "workflows") return fail("Unknown read.", 400);
  const hooks = await webhooks();
  if (!hooks) return fail("Reads of workflow settings are not configured on this server.", 503);
  const configured: Record<string, boolean> = {};
  for (const [job, entry] of Object.entries(hooks)) {
    configured[job] = !!(entry && typeof entry === "object" && typeof entry.url === "string" && entry.url.trim());
  }
  const verdict = await requireOperator(request);
  return NextResponse.json(
    { data: { configured, webhooks: verdict?.ok ? hooks : null }, error: null },
    { headers: noStore },
  );
}

/**
 * POST { op: "rpc", rpc, params }
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
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "ad4-ui", ...extra }),
        cache: "no-store",
        signal: AbortSignal.timeout(20_000),
      });
      const text = await res.text();
      return NextResponse.json({ data: { status: res.status, ok: res.ok, text: text.slice(0, 4000) }, error: null },
        { headers: noStore });
    }

    return fail("Unknown operation.", 400);
  } catch (e) {
    return fail(e instanceof Error ? e.message : "Write failed.", 500);
  }
}
