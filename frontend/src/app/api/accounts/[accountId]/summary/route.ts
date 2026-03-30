import { NextRequest, NextResponse } from "next/server";

// Allow up to 5 minutes for the LLM to generate the summary
export const maxDuration = 300;

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ accountId: string }> }
) {
  const { accountId } = await params;
  const body = await req.text();

  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (process.env.LANGSMITH_API_KEY) {
    headers["x-api-key"] = process.env.LANGSMITH_API_KEY;
  }

  const res = await fetch(
    `${backendUrl}/api/accounts/${accountId}/summary`,
    {
      method: "POST",
      headers,
      body,
      signal: AbortSignal.timeout(300_000),
    }
  );

  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({ detail: "Unknown error" }));
    return NextResponse.json(data, { status: res.status });
  }

  // The backend streams SSE keepalive pings while the LLM generates, then
  // sends a single `result` event. Parse the stream here and return plain JSON
  // to the browser — avoids streaming compatibility issues in Next.js dev mode.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = done ? "" : (chunks.pop() ?? "");

    for (const chunk of chunks) {
      let eventType = "";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) eventType = line.slice(7).trim();
        if (line.startsWith("data: ")) data = line.slice(6).trim();
      }
      if (eventType === "result" && data) {
        return NextResponse.json(JSON.parse(data));
      }
      if (eventType === "error" && data) {
        return NextResponse.json(JSON.parse(data), { status: 500 });
      }
    }

    if (done) break;
  }

  return NextResponse.json({ detail: "Stream ended without result" }, { status: 500 });
}
