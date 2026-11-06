import { NextResponse } from "next/server";
import { serviceClient } from "../../../lib/operatorAuth";
import { projectTemperatureResearch } from "../../../lib/temperatureResearch";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const client = serviceClient();
  if (!client) return NextResponse.json({ error: "Research reader is not configured." }, { status: 503 });
  try {
    const { data, error } = await client.rpc("temperature_snapshot");
    if (error) return NextResponse.json({ error: "Research status is temporarily unavailable." }, { status: 503 });
    return NextResponse.json(projectTemperatureResearch(data), { headers: { "Cache-Control": "private, max-age=60" } });
  } catch {
    return NextResponse.json({ error: "Could not read the temperature research status." }, { status: 503 });
  }
}
