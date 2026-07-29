import { cookies } from "next/headers";

import { THREAD_COOKIE } from "@/lib/thread";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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

  const upstream = await fetch("http://127.0.0.1:8000/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: req.headers.get("accept") ?? "text/event-stream",
    },
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
