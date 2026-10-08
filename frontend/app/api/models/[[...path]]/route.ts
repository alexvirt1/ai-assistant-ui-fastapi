import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Same reasoning as app/api/chats: the browser talks to Next, Next talks to
// the backend, so the backend's address never reaches the client bundle.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(req: NextRequest, path: string[] | undefined) {
  const suffix = (path ?? []).join("/");
  const url = `${BACKEND}/api/models${suffix ? `/${suffix}` : ""}`;

  const headers = new Headers({
    accept: req.headers.get("accept") ?? "application/json",
  });
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const upstream = await fetch(url, {
    method: req.method,
    headers,
    // Bodies here are a few bytes of JSON, so read rather than streamed.
    body: req.method === "GET" ? undefined : await req.text(),
  });

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}
