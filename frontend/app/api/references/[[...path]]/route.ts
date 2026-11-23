import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Reference photos: POST uploads one (multipart: file, threadId), GET streams
// the stored PNG, DELETE removes it for good. Same proxy shape as
// app/api/documents, so the backend's address stays out of the bundle.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(req: NextRequest, path: string[] | undefined) {
  // Optional catch-all: `path` is undefined for /api/references itself, the
  // upload endpoint (a required [...path] would not match it).
  const suffix = (path ?? []).map(encodeURIComponent).join("/");
  const url = `${BACKEND}/api/references${suffix ? `/${suffix}` : ""}`;

  const headers = new Headers();
  // content-type carries the multipart boundary; dropping it breaks uploads.
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("accept", req.headers.get("accept") ?? "*/*");
  // Inert until there is a login; forwarded so it arrives when there is.
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const authorization = req.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);

  const upstream = await fetch(url, {
    method: req.method,
    headers,
    // Streamed through untouched rather than parsed and re-serialised.
    body: req.method === "POST" ? req.body : undefined,
    duplex: "half",
  } as RequestInit & { duplex: "half" });

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

type Context = { params: Promise<{ path?: string[] }> };

export async function GET(req: NextRequest, { params }: Context) {
  return proxy(req, (await params).path);
}

export async function POST(req: NextRequest, { params }: Context) {
  return proxy(req, (await params).path);
}

export async function DELETE(req: NextRequest, { params }: Context) {
  return proxy(req, (await params).path);
}
