import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Generated images: GET streams the PNG, DELETE removes it for good. Same
// proxy as app/api/chats, so the backend's address stays out of the bundle.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(req: NextRequest, path: string[] | undefined) {
  const suffix = (path ?? []).map(encodeURIComponent).join("/");
  const url = `${BACKEND}/api/images${suffix ? `/${suffix}` : ""}`;

  const headers = new Headers();
  headers.set("accept", req.headers.get("accept") ?? "*/*");
  // Inert until there is a login; forwarded so it arrives when there is.
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const authorization = req.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);

  const upstream = await fetch(url, { method: req.method, headers });

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  // 204 (a successful delete) must not carry a body, and undici rejects one.
  const body = upstream.status === 204 ? null : upstream.body;
  return new Response(body, {
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

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await params;
  return proxy(req, path);
}
