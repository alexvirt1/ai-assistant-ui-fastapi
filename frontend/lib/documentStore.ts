"use client";

import type { UploadedDocument } from "./attachments";

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
export type AttachedDocument = {
  id: string;
  name: string;
  sections: number;
};

let attached: AttachedDocument[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function addDocument(document: UploadedDocument): void {
  if (attached.some((d) => d.id === document.id)) return;
  attached = [
    ...attached,
    { id: document.id, name: document.name, sections: document.sections },
  ];
  emit();
}

export function clearDocuments(): void {
  if (attached.length === 0) return;
  attached = [];
  emit();
}

/** Stable reference between changes, as useSyncExternalStore requires. */
export function getDocuments(): AttachedDocument[] {
  return attached;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
