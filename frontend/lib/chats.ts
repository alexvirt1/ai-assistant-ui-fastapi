/**
 * Client for the chat registry: the list in the sidebar, and a thread's
 * stored history.
 *
 * The transcript itself lives in Postgres, in the LangGraph checkpoint, and is
 * replayed by the backend into the shape the runtime restores from. Nothing
 * here caches it - a conversation continued in another tab would go stale, and
 * a stale transcript is worse than a refetch that takes 20ms on localhost.
 */

/** One row in the sidebar. Mirrors ChatSummary in backend/app/chats/routes.py. */
export type ChatSummary = {
  id: string;
  title: string;
  preview: string;
  turnCount: number;
  archived: boolean;
  createdAt: string;
  updatedAt: string;
};

/**
 * A message as assistant-ui's runtime accepts it in `initialMessages`.
 *
 * Hand-written rather than imported: @assistant-ui/react does not re-export
 * CoreMessage from its entrypoint. The runtime fills in the parts this omits
 * (ids, timestamps, argsText for a tool call) when it restores them.
 */
export type RestoredTextPart = { type: "text"; text: string };

export type RestoredToolCallPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  /** Absent when the run was cancelled before the tool answered. */
  result?: unknown;
  isError?: boolean;
};

/**
 * Mirrors CoreMessage's shape: a union per role, not one type with a role
 * field, because each role admits different parts. No "system" case - the
 * backend drops the system prompt when it replays a thread, since it is
 * rebuilt server-side per call and was never part of the conversation.
 */
export type RestoredMessage =
  | { role: "user"; content: RestoredTextPart[] }
  | { role: "assistant"; content: (RestoredTextPart | RestoredToolCallPart)[] };

async function json<T>(response: Response, what: string): Promise<T> {
  if (!response.ok) {
    throw new Error(`${what} failed (${response.status})`);
  }
  return (await response.json()) as T;
}

/**
 * The chat list, newest first, optionally filtered.
 *
 * `signal` matters here rather than being boilerplate: the search box calls
 * this per keystroke, and without cancellation a slow early response can land
 * after a fast later one and repaint the list with results for a prefix the
 * user has already typed past.
 */
export async function listChats(
  query?: string,
  signal?: AbortSignal,
): Promise<ChatSummary[]> {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  const suffix = params.size > 0 ? `?${params}` : "";

  const response = await fetch(`/api/chats${suffix}`, {
    signal,
    headers: { accept: "application/json" },
  });
  return json<ChatSummary[]>(response, "Loading chats");
}

/** A thread's stored transcript, ready to hand to the runtime. */
export async function fetchChatMessages(
  threadId: string,
  signal?: AbortSignal,
): Promise<RestoredMessage[]> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(threadId)}/messages`,
    { signal, headers: { accept: "application/json" } },
  );
  return json<RestoredMessage[]>(response, "Loading history");
}

/**
 * The documents a conversation can search.
 *
 * Shaped to match the store's AttachedDocument, because that is what it
 * populates - the chips a restored conversation shows are drawn from the same
 * rows the backend renders into the system prompt.
 */
export type ChatDocument = {
  id: string;
  name: string;
  sections: number;
  status: "indexing" | "ready" | "failed";
  tier: string;
  message: string;
};

export async function fetchChatDocuments(
  threadId: string,
  signal?: AbortSignal,
): Promise<ChatDocument[]> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(threadId)}/documents`,
    { signal, headers: { accept: "application/json" } },
  );
  return json<ChatDocument[]>(response, "Loading documents");
}

export async function renameChat(
  threadId: string,
  title: string,
): Promise<ChatSummary> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ title }),
  });
  return json<ChatSummary>(response, "Renaming chat");
}

export async function deleteChat(threadId: string): Promise<void> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
  // 404 means it is already gone, which is the state the caller wanted.
  if (!response.ok && response.status !== 404) {
    throw new Error(`Deleting chat failed (${response.status})`);
  }
}
