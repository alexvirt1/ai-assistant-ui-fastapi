import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Mirrors app/api/chat/route.ts: the browser talks to Next, Next talks to the
// backend. The backend allows any origin, so the browser could call it
// directly - but that would bake its address into the client bundle and make
// the deployment harder to move.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(req: NextRequest, path: string[] | undefined) {
  // Optional catch-all: `path` is undefined for /api/documents itself, which is
  // the upload endpoint. A required [...path] would not match it at all, and
  // Next then treats a multipart POST as a Server Action.
  const suffix = (path ?? []).join("/");
  const url = `${BACKEND}/api/documents${suffix ? `/${suffix}` : ""}`;

  // Upload is multipart and answers are JSON, so the body is streamed through
  // untouched rather than parsed and re-serialised.
  const upstream = await fetch(url, {
    method: req.method,
    headers: passThroughHeaders(req),
    body: req.method === "GET" ? undefined : req.body,
    // Required by undici when streaming a request body.
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  const headers = new Headers(upstream.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

function passThroughHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  // content-type carries the multipart boundary; dropping it breaks uploads.
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("accept", req.headers.get("accept") ?? "application/json");
  return headers;
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}
