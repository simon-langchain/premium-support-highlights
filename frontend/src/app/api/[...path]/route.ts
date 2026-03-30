/**
 * Catch-all proxy: forwards any /api/* request to the FastAPI backend.
 * More specific route handlers (e.g. accounts/[accountId]/summary) take
 * priority over this catch-all in Next.js App Router.
 */
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

// BACKEND_URL: set to the LSD deployment URL in production (e.g. Vercel env var).
// Falls back to localhost for local development with start.sh.
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

async function forward(req: NextRequest, segments: string[]): Promise<Response> {
  const url = `${BACKEND}/api/${segments.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (key !== "host" && key !== "connection" && key !== "transfer-encoding") {
      headers.set(key, value);
    }
  });
  // LSD requires a LangSmith API key. Injected server-side so it is never
  // exposed to the browser. Set LANGSMITH_API_KEY in Vercel environment variables.
  if (process.env.LANGSMITH_API_KEY) {
    headers.set("x-api-key", process.env.LANGSMITH_API_KEY);
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.arrayBuffer() : undefined;

  return fetch(url, {
    method: req.method,
    headers,
    body: body ? Buffer.from(body) : undefined,
  });
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return forward(req, path);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return forward(req, path);
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return forward(req, path);
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return forward(req, path);
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return forward(req, path);
}
