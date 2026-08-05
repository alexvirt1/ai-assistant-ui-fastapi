import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Same reasoning as app/api/documents: the browser talks to Next, Next talks
// to the backend, so the backend's address never reaches the client bundle.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(req: NextRequest, path: string[] | undefined) {
  const suffix = (path ?? []).join("/");
  // The search string carries ?q= for the sidebar's filter; dropping it would
  // silently turn every search into "list everything".
  const url = `${BACKEND}/api/chats${suffix ? `/${suffix}` : ""}${req.nextUrl.search}`;

  const upstream = await fetch(url, {
    method: req.method,
    headers: passThroughHeaders(req),
    body: req.method === "GET" || req.method === "DELETE" ? undefined : req.body,
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  const headers = new Headers(upstream.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");

  // 204 (a successful delete) must not carry a body, and undici rejects one.
  const body = upstream.status === 204 ? null : upstream.body;

  return new Response(body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

function passThroughHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("accept", req.headers.get("accept") ?? "application/json");

  // Inert today - there is no login, and the backend attributes every request
  // to the single configured user. Forwarded anyway so that when
  // current_user_id starts reading a session, the credential is already
  // arriving instead of being silently dropped at the proxy.
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const authorization = req.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);

  return headers;
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}
