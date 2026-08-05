"use client";

import type { UploadedDocument } from "./attachments";
import { THREAD_COOKIE } from "./thread";

/**
 * Documents attached to the current conversation.
 *
 * Kept outside React because the attachment adapter is constructed once and
 * lives outside the component tree, but the chat runtime has to send the list
 * on every request. A tiny external store bridges the two without threading a
 * callback through the adapter's constructor.
 *
 * Why send it every turn rather than relying on the attachment message: the
 * reference has to reach the *system prompt*. Left in the conversation it sits
 * in the oldest turn, which is exactly what the backend's history trimmer
 * discards first — measured at 121 messages the reference was gone and the
 * agent could no longer reach a document it had been given.
 */
/**
 * How far along a document is.
 *
 * "indexing" is the honest state, not a formality: embedding a 5 MB file takes
 * about a minute, and until it finishes a question falls through to the tool's
 * lazy index and simply waits. Without this the UI looked idle for that minute.
 *
 * "failed" is not fatal - search_document indexes on demand - so it reads as a
 * warning rather than an error.
 */
export type DocumentStatus = "indexing" | "ready" | "failed";

export type AttachedDocument = {
  id: string;
  name: string;
  sections: number;
  status: DocumentStatus;
  /** Scope tier and message from upload; describes *summarising* cost, which is
   *  not what searching costs, so it is shown as detail rather than a warning. */
  tier: string;
  message: string;
};

let attached: AttachedDocument[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/**
 * Where attached documents survive a page reload.
 *
 * They have to. The conversation lives in Postgres and comes back on reload,
 * but this store was memory-only — so after a refresh the thread still showed
 * an answer citing "[Section 148]" while nothing knew which document that was,
 * and the backend stopped being told a document was attached at all, losing the
 * pinned system-prompt block that makes it searchable.
 *
 * Keyed by thread id so a different conversation never inherits them. The
 * thread cookie is deliberately not httpOnly, so it is readable here.
 */
const STORAGE_KEY = "assistant_attached_documents";

function currentThread(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${THREAD_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]!) : "";
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    if (attached.length === 0) {
      window.localStorage.removeItem(STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ thread: currentThread(), documents: attached }),
    );
  } catch {
    // Full quota or a privacy mode that forbids storage. Losing persistence is
    // a degraded experience; throwing here would break attaching a file.
  }
}

/**
 * Restore documents saved by an earlier page load.
 *
 * Called from an effect rather than at module scope on purpose: reading storage
 * during import would make the first client render disagree with the
 * server-rendered HTML, which is a hydration error. Same reason ThemeToggle
 * guards on mount.
 */
export function hydrateDocuments(): void {
  if (typeof window === "undefined" || attached.length > 0) return;
  let stored: unknown;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    stored = JSON.parse(raw);
  } catch {
    // Corrupt or unreadable: start empty rather than fail to render.
    return;
  }

  const saved = stored as { thread?: string; documents?: AttachedDocument[] };
  // Belongs to a conversation that is no longer the current one, so announcing
  // it would attach a document the thread never saw.
  if (saved?.thread !== currentThread()) return;
  if (!Array.isArray(saved.documents) || saved.documents.length === 0) return;

  attached = saved.documents.filter((d) => d && typeof d.id === "string");
  if (attached.length > 0) emit();
}

export function addDocument(document: UploadedDocument): void {
  if (attached.some((d) => d.id === document.id)) return;
  attached = [
    ...attached,
    {
      id: document.id,
      name: document.name,
      sections: document.sections,
      status: "indexing",
      tier: document.tier,
      message: document.message,
    },
  ];
  persist();
  emit();
}

export function setDocumentStatus(id: string, status: DocumentStatus): void {
  const current = attached.find((d) => d.id === id);
  // No-op guard rather than an unconditional rebuild: useSyncExternalStore
  // compares by reference, so emitting a fresh array for an unchanged status
  // would re-render the whole thread for nothing.
  if (!current || current.status === status) return;
  attached = attached.map((d) => (d.id === id ? { ...d, status } : d));
  persist();
  emit();
}

export function clearDocuments(): void {
  if (attached.length === 0) return;
  attached = [];
  persist();
  emit();
}

/** Stable reference between changes, as useSyncExternalStore requires. */
export function getDocuments(): AttachedDocument[] {
  return attached;
}

/**
 * Empty the in-memory list without touching storage.
 *
 * Exists for tests: it reproduces a page reload, where the module is fresh but
 * localStorage still holds what the previous load wrote. Production code should
 * use clearDocuments(), which also forgets them.
 */
export function clearDocumentsInMemoryOnly(): void {
  attached = [];
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
