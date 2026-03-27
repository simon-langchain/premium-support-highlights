/**
 * Catch-all proxy: forwards any /api/* request to the FastAPI backend.
 * More specific route handlers (e.g. accounts/[accountId]/summary) take
 * priority over this catch-all in Next.js App Router.
 */
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND = "http://localhost:8000";

async function forward(req: NextRequest, segments: string[]): Promise<Response> {
  const url = `${BACKEND}/api/${segments.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (key !== "host" && key !== "connection" && key !== "transfer-encoding") {
      headers.set(key, value);
    }
  });

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
