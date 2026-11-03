import { NextResponse } from "next/server";
import { serviceClient } from "../../../lib/operatorAuth";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Read-only safe projection. The private artifact is never returned. */
export async function GET() {
  const client = serviceClient();
  if (!client) return NextResponse.json({ error: "Forecast reader is not configured." }, { status: 503 });
  try {
    const { data, error } = await client.rpc("shared_weather_snapshot");
    if (error) return NextResponse.json({ error: error.message }, { status: 503 });
    return NextResponse.json(data, { headers: { "Cache-Control": "private, max-age=60" } });
  } catch {
    return NextResponse.json({ error: "Could not reach the frozen forecast reader." }, { status: 503 });
  }
}
