"use client";

/**
 * Hybrid mode on the client: which model answers this chat, whether its
 * documents may go to the cloud model, and the one-shot "escalate this turn"
 * flag set when the user approves an escalation.
 *
 * Kept in a small external store, like documentStore, and read by the request
 * body through getters (see requestFields): the edge runtime applies new
 * options only after a render, so a flag set just before sending a message
 * would otherwise go out one request late.
 */

export type HybridMode = "local" | "auto" | "cloud";

export type CloudStatus = {
  configured: boolean;
  model: { name: string; label: string; model: string } | null;
  budget_usd: number;
  month_spent_usd: number | null;
};

type State = { mode: HybridMode; allowDocuments: boolean; escalateNext: boolean };

let state: State = { mode: "local", allowDocuments: false, escalateNext: false };
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getHybrid(): State {
  return state;
}

export function subscribeHybrid(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setHybrid(update: Partial<State>): void {
  state = { ...state, ...update };
  emit();
}

/** Reset for a chat being opened or started. */
export function resetHybrid(mode: HybridMode = "local", allowDocuments = false): void {
  setHybrid({ mode, allowDocuments, escalateNext: false });
}

/**
 * Fields merged into every chat request. Getters, so the values are read when
 * the request is built, not when the runtime was last rendered.
 */
export const requestFields = {
  get mode() {
    return state.mode;
  },
  get allowCloudDocuments() {
    return state.allowDocuments;
  },
  get escalate() {
    return state.escalateNext;
  },
};

export async function fetchCloudStatus(signal?: AbortSignal): Promise<CloudStatus | null> {
  try {
    const response = await fetch("/api/cloud", { signal });
    return response.ok ? ((await response.json()) as CloudStatus) : null;
  } catch {
    return null;
  }
}

/** A chat's stored mode; null for a chat the backend does not know yet. */
export async function fetchChatHybrid(
  threadId: string,
): Promise<{ mode: HybridMode; allowDocuments: boolean } | null> {
  try {
    const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}`);
    if (!response.ok) return null;
    const chat = (await response.json()) as { mode?: HybridMode; allowCloudDocuments?: boolean };
    return { mode: chat.mode ?? "local", allowDocuments: Boolean(chat.allowCloudDocuments) };
  } catch {
    return null;
  }
}

/** Store a chat's settings now; a chat not saved yet gets them with its first turn. */
export async function saveChatHybrid(
  threadId: string,
  settings: { mode?: HybridMode; allowDocuments?: boolean },
): Promise<void> {
  try {
    await fetch(`/api/chats/${encodeURIComponent(threadId)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...(settings.mode ? { mode: settings.mode } : {}),
        ...(settings.allowDocuments !== undefined
          ? { allowCloudDocuments: settings.allowDocuments }
          : {}),
      }),
    });
  } catch {
    // Sent again with the next turn anyway.
  }
}

type TranscriptMessage = { role: string; content: readonly { type: string; text?: string }[] };

/** The conversation as plain text, for pasting into claude.ai. */
export function transcriptForClaude(messages: readonly TranscriptMessage[]): string {
  const lines = messages
    .map((m) => {
      const text = m.content
        .filter((part) => part.type === "text" && part.text)
        .map((part) => part.text)
        .join("\n")
        .trim();
      if (!text) return "";
      return `${m.role === "user" ? "User" : "Assistant (local model)"}: ${text}`;
    })
    .filter(Boolean);
  return [
    "Here is a conversation I had with my local assistant. Please continue it and answer my last message.",
    "",
    ...lines,
  ].join("\n\n");
}

export const CLAUDE_NEW_CHAT_URL = "https://claude.ai/new";

export function formatUsd(value: number): string {
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}
