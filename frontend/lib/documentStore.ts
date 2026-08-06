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

/**
 * A file on its way to the document pipeline.
 *
 * Deliberately separate from the attached list rather than a fourth
 * DocumentStatus: getDocuments() is what the runtime sends to the backend as
 * the documents the model may search, and a file still in flight has no id to
 * search by. Keeping it out of that list is what stops the model being told
 * about a document the backend has not stored yet.
 */
export type PendingUpload = {
  /** The composer attachment's id - the document id does not exist yet. */
  id: string;
  name: string;
};

let attached: AttachedDocument[] = [];
let uploading: PendingUpload[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/**
 * Replace the list wholesale - what a conversation has attached, as the
 * backend knows it.
 *
 * This is where the list comes from now. It used to be mirrored into
 * localStorage so a reload could restore it, but the association lives in
 * Postgres as of chat_thread_documents, and a browser-side copy of an
 * authoritative server list is one more thing that can silently disagree with
 * it. A reload now restores documents the same way it restores the
 * conversation: by asking the backend.
 */
export function setDocuments(documents: AttachedDocument[]): void {
  // Reference equality is the store's change signal, so an unconditional
  // assignment would re-render the thread on every chat switch that has no
  // documents at either end - the common case.
  if (attached.length === 0 && documents.length === 0) return;
  attached = documents;
  emit();
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
  emit();
}

export function setDocumentStatus(id: string, status: DocumentStatus): void {
  const current = attached.find((d) => d.id === id);
  // No-op guard rather than an unconditional rebuild: useSyncExternalStore
  // compares by reference, so emitting a fresh array for an unchanged status
  // would re-render the whole thread for nothing.
  if (!current || current.status === status) return;
  attached = attached.map((d) => (d.id === id ? { ...d, status } : d));
  emit();
}

/**
 * Announce that a file is being uploaded.
 *
 * The upload is the one step of the attachment path with nothing to show for
 * itself: the composer holds the message until every attachment has been sent,
 * so between pressing send and the backend answering there is no chip, no
 * message and no running indicator anywhere on screen. This is that missing
 * acknowledgement, and it is the store's job because the adapter that does the
 * uploading lives outside the component tree.
 */
export function startUpload(id: string, name: string): void {
  if (uploading.some((u) => u.id === id)) return;
  uploading = [...uploading, { id, name }];
  emit();
}

/** Upload settled - succeeded, failed, or fell back to inline text. */
export function finishUpload(id: string): void {
  if (!uploading.some((u) => u.id === id)) return;
  uploading = uploading.filter((u) => u.id !== id);
  emit();
}

/** Stable reference between changes, as useSyncExternalStore requires. */
export function getPendingUploads(): PendingUpload[] {
  return uploading;
}

export function clearDocuments(): void {
  if (attached.length === 0 && uploading.length === 0) return;
  attached = [];
  uploading = [];
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
