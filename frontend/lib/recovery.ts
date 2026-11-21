/**
 * Recovering a turn whose stream dropped before it finished.
 *
 * The backend runs a turn to the end even when the browser stops listening,
 * and stores the result. Seen live: a browser extension cut the chat stream
 * while images were still being generated, leaving cards that said
 * "Generating image…" forever, while the images and the reply were already
 * saved. So when a turn ends unfinished on screen, the stored transcript is
 * polled until the backend has finished it, and the chat is reloaded from it.
 */

import type { RestoredMessage } from "./chats";

/** The parts of a live assistant-ui message this looks at. */
export type LiveMessage = {
  role: string;
  status?: { type: string; reason?: string };
  content: readonly { type: string; result?: unknown }[];
};

/**
 * Whether a turn that just stopped running ended unfinished: a tool call
 * still waiting for its result, or the run failing. A run the user cancelled
 * is not recovered - that is what they asked for.
 */
export function endedUnfinished(message: LiveMessage | undefined): boolean {
  if (!message || message.role !== "assistant") return false;
  if (message.status?.type === "incomplete" && message.status.reason === "cancelled") {
    return false;
  }
  const pendingTool = message.content.some(
    (part) => part.type === "tool-call" && part.result === undefined,
  );
  const failed = message.status?.type === "incomplete" && message.status.reason === "error";
  return pendingTool || failed;
}

/**
 * Whether the stored transcript holds a finished last turn: an assistant
 * message whose tool calls all have results and which has its reply text.
 */
export function turnSettled(messages: RestoredMessage[]): boolean {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return false;
  const toolsDone = last.content.every(
    (part) => part.type !== "tool-call" || part.result !== undefined,
  );
  const hasText = last.content.some((part) => part.type === "text" && part.text.trim());
  return toolsDone && hasText;
}

export const RECOVERY_POLL_MS = 5000;
export const RECOVERY_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Poll the stored transcript until the last turn has settled, or give up.
 * Resolves with the transcript to show (the latest one fetched, settled or
 * not), or null if it was aborted or never could be fetched.
 */
export async function waitForSettledTurn(
  fetchMessages: () => Promise<RestoredMessage[]>,
  signal: AbortSignal,
  { pollMs = RECOVERY_POLL_MS, timeoutMs = RECOVERY_TIMEOUT_MS } = {},
): Promise<RestoredMessage[] | null> {
  const deadline = Date.now() + timeoutMs;
  let latest: RestoredMessage[] | null = null;
  while (!signal.aborted) {
    try {
      latest = await fetchMessages();
      if (turnSettled(latest)) return latest;
    } catch {
      // A failed poll is retried; the backend may be restarting.
    }
    if (Date.now() >= deadline) return latest;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, pollMs);
      signal.addEventListener("abort", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
  return null;
}
