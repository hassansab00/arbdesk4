import { createHash } from "node:crypto";
import { createClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { exportDataset, PROPRIETARY_EXPORT_DATASETS } from "@/lib/proprietaryExport";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const PAGE_SIZE = 1_000;
const MAX_ROWS = 50_000;
const MAX_DAYS = 31;
const encoder = new TextEncoder();

function configured() {
  return process.env.ARBDESK_PRIVATE_EXPORT_ENABLED === "true"
    && !!process.env.NEXT_PUBLIC_SUPABASE_URL
    && !!process.env.SUPABASE_SERVICE_KEY;
}

function serverClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key || process.env.ARBDESK_PRIVATE_EXPORT_ENABLED !== "true") {
    throw new Error("Private export is not enabled on the server.");
  }
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return !!origin && origin === new URL(request.url).origin;
}

function parseDay(value: unknown, name: string) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${name} must be a calendar date.`);
  }
  const date = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== value) {
    throw new Error(`${name} is not a valid calendar date.`);
  }
  return date;
}

function canonical(value: unknown): string {
  if (value === undefined) return "null";
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
}

function hashRecord(hash: ReturnType<typeof createHash>, record: unknown) {
  const bytes = Buffer.from(canonical(record), "utf8");
  const size = Buffer.alloc(8);
  size.writeBigUInt64BE(BigInt(bytes.byteLength));
  hash.update(size);
  hash.update(bytes);
}

function jsonError(message: string, status: number) {
  return NextResponse.json({ error: message }, { status, headers: { "Cache-Control": "no-store" } });
}

export async function GET() {
  return NextResponse.json({
    enabled: configured(),
    datasets: PROPRIETARY_EXPORT_DATASETS.map(({ key, label, description }) => ({ key, label, description })),
    limits: { max_rows: MAX_ROWS, max_days: MAX_DAYS },
  }, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return jsonError("Cross-origin export rejected.", 403);
  if (!configured()) return jsonError("Private export is not enabled on the server.", 503);
  if (Number(request.headers.get("content-length") || 0) > 4_096) {
    return jsonError("Export request is too large.", 413);
  }

  try {
    const body = await request.json() as { dataset?: unknown; from?: unknown; through?: unknown };
    const dataset = typeof body.dataset === "string" ? exportDataset(body.dataset) : undefined;
    if (!dataset) return jsonError("Unknown export dataset.", 400);

    const from = parseDay(body.from, "From date");
    const through = parseDay(body.through, "Through date");
    if (through < from) return jsonError("Through date must be on or after from date.", 400);
    const exclusiveEnd = new Date(through);
    exclusiveEnd.setUTCDate(exclusiveEnd.getUTCDate() + 1);
    const days = Math.round((exclusiveEnd.valueOf() - from.valueOf()) / 86_400_000);
    if (days > MAX_DAYS) return jsonError(`Choose ${MAX_DAYS} days or fewer per export.`, 400);

    const client = serverClient();
    const counts: Record<string, number> = {};
    let total = 0;
    for (const source of dataset.sources) {
      const result = await client.from(source.relation).select("*", { count: "exact", head: true })
        .gte(source.timeColumn, from.toISOString()).lt(source.timeColumn, exclusiveEnd.toISOString());
      if (result.error) throw result.error;
      counts[source.relation] = result.count ?? 0;
      total += result.count ?? 0;
    }
    if (total > MAX_ROWS) {
      return jsonError(`This range contains ${total.toLocaleString()} rows. Narrow the dates to stay at or below ${MAX_ROWS.toLocaleString()} rows per file.`, 413);
    }

    const generatedAt = new Date().toISOString();
    const fileName = `arbdesk4-${dataset.key}-${body.from}-through-${body.through}.ndjson`;
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const digest = createHash("sha256");
        let exported = 0;
        const header = {
          type: "arbdesk_export_header", version: 1, dataset: dataset.key, generated_at: generatedAt,
          range: { from: body.from, through: body.through, end_exclusive: exclusiveEnd.toISOString() },
          expected_rows: total, source_counts: counts,
          digest: "sha256 over length-prefixed canonical row envelopes",
        };
        controller.enqueue(encoder.encode(`${canonical(header)}\n`));

        try {
          for (const source of dataset.sources) {
            let offset = 0;
            while (offset < counts[source.relation]) {
              if (request.signal.aborted) throw new Error("Export canceled by client.");
              let query = client.from(source.relation).select("*")
                .gte(source.timeColumn, from.toISOString()).lt(source.timeColumn, exclusiveEnd.toISOString())
                .order(source.timeColumn, { ascending: true });
              for (const column of source.orderColumns) query = query.order(column, { ascending: true });
              const result = await query.range(offset, Math.min(offset + PAGE_SIZE, counts[source.relation]) - 1);
              if (result.error) throw result.error;
              const rows = result.data ?? [];
              if (!rows.length && offset < counts[source.relation]) {
                throw new Error(`${source.relation} changed while it was being exported; retry for a consistent file.`);
              }
              for (const row of rows) {
                const envelope = { type: "row", relation: source.relation, data: row };
                hashRecord(digest, envelope);
                controller.enqueue(encoder.encode(`${canonical(envelope)}\n`));
                exported += 1;
              }
              offset += rows.length;
            }
          }
          const footer = {
            type: "arbdesk_export_manifest", version: 1, dataset: dataset.key, generated_at: generatedAt,
            row_count: exported, expected_rows: total, sha256: digest.digest("hex"), complete: exported === total,
          };
          controller.enqueue(encoder.encode(`${canonical(footer)}\n`));
          controller.close();
        } catch (error) {
          controller.error(error);
        }
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Content-Disposition": `attachment; filename="${fileName}"`,
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    return jsonError(error instanceof Error ? error.message : "Export failed.", 400);
  }
}
