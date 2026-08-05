import { cookies } from "next/headers";

import { THREAD_COOKIE } from "@/lib/thread";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Shared with the documents and chats proxies rather than hardcoded here, so
// moving the backend is one env var instead of four edits.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function POST(req: Request) {
  const body = await req.json();
  const cookieStore = await cookies();

  let threadId =
    body.threadId ??
    body.id ??
    cookieStore.get(THREAD_COOKIE)?.value;

  if (!threadId) {
    threadId = crypto.randomUUID();
  }

  const headersOut = new Headers({
    "Content-Type": "application/json",
    Accept: req.headers.get("accept") ?? "text/event-stream",
  });
  // Inert until there is a login to carry: forwarded now so that adding one is
  // a backend change rather than also a hunt for where the credential was
  // being dropped. See app/api/chats/[[...path]]/route.ts.
  const cookie = req.headers.get("cookie");
  if (cookie) headersOut.set("cookie", cookie);
  const authorization = req.headers.get("authorization");
  if (authorization) headersOut.set("authorization", authorization);

  const upstream = await fetch(`${BACKEND}/api/chat`, {
    method: "POST",
    headers: headersOut,
    body: JSON.stringify({
      ...body,
      threadId,
    }),
  });

  const headers = new Headers(upstream.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.set("Cache-Control", "no-cache, no-transform");

  const response = new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });

  response.headers.append(
    "Set-Cookie",
    `${THREAD_COOKIE}=${threadId}; Path=/; SameSite=Lax; Max-Age=31536000`
  );

  return response;
}
