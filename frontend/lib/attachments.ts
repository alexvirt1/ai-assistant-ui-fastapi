/**
 * Text attachment support.
 *
 * Deliberately not `SimpleTextAttachmentAdapter` from @assistant-ui/react: that
 * one inlines the whole file with no size limit. An attachment is injected into
 * the message text and then persisted in the Postgres checkpoint, so it keeps
 * competing for the model's context on every later turn of the thread. With
 * OLLAMA_NUM_CTX=8192 and HISTORY_MAX_TOKENS=3000, a ~100 KB file is roughly
 * 25k tokens - it would not merely overflow, it would evict the conversation.
 */

/** Rejected outright: too large to be worth reading into the browser at all. */
export const MAX_FILE_BYTES = 1_000_000;

export type AttachmentLimits = {
  maxChars: number;
  maxBytes: number;
};

/**
 * Limits from the environment, read on the server.
 *
 * Deliberately not `NEXT_PUBLIC_*`: those are inlined into the bundle at build
 * time, so every experiment would need a rebuild. Read server-side and passed
 * down as props, a change takes effect on a frontend restart alone.
 *
 * Set in `frontend/.env.local`:
 *   MAX_ATTACHMENT_CHARS=24000
 *   MAX_ATTACHMENT_BYTES=1000000
 */
export function readAttachmentLimits(): AttachmentLimits {
  return {
    maxChars: positiveIntOr(
      process.env.MAX_ATTACHMENT_CHARS,
      MAX_ATTACHMENT_CHARS,
    ),
    maxBytes: positiveIntOr(process.env.MAX_ATTACHMENT_BYTES, MAX_FILE_BYTES),
  };
}

function positiveIntOr(raw: string | undefined, fallback: number): number {
  const value = Number(raw);
  // Guards against typos silently disabling the cap: "abc", "", "0" and "-5"
  // all fall back rather than becoming an unbounded or nonsensical limit.
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

/**
 * Characters kept from any one attachment.
 *
 * ~24k characters is roughly 6k tokens, about half the backend's
 * HISTORY_MAX_TOKENS budget of 12000 (itself a third of a 32768-token context
 * window). That split is deliberate: a document should be able to occupy a
 * large share of the window while still leaving room for a real conversation
 * about it. Raising this without raising HISTORY_MAX_TOKENS just means the
 * trimmer discards the document sooner.
 */
export const MAX_ATTACHMENT_CHARS = 24000;

/** File dialog filter. Extensions are listed alongside MIME types because
 *  browsers report inconsistent types for .md, .yaml and friends - Chrome
 *  gives .md an empty type, and .json arrives as application/json. */
export const TEXT_ACCEPT = [
  "text/*",
  "application/json",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".xml",
  ".yaml",
  ".yml",
  ".html",
  ".css",
  ".log",
].join(",");

/**
 * Truncate to the character budget, telling the model when content was cut.
 *
 * The marker matters: silently truncated input makes a model answer confidently
 * about a document it only partly saw.
 */
export function truncateForContext(
  text: string,
  maxChars: number = MAX_ATTACHMENT_CHARS,
): string {
  if (text.length <= maxChars) return text;
  const omitted = text.length - maxChars;
  return (
    text.slice(0, maxChars) +
    `\n[...truncated: ${omitted.toLocaleString()} of ` +
    `${text.length.toLocaleString()} characters omitted]`
  );
}

/**
 * Attachment id that works on any origin.
 *
 * `crypto.randomUUID()` is only defined in a **secure context** — HTTPS or
 * localhost. This app is served over plain HTTP on 0.0.0.0:3000, so a browser
 * reaching it by LAN address gets `undefined` and calling it throws, which
 * silently prevented the attachment from ever being added.
 */
export function attachmentId(fileName: string): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid;
  return `${fileName}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export type UploadedDocument = {
  id: string;
  name: string;
  sections: number;
  tier: string;
  message: string;
};

/**
 * Send a file to the document pipeline instead of inlining it.
 *
 * Goes through the Next proxy rather than straight to the backend so its
 * address stays server-side.
 */
export async function uploadDocument(file: File): Promise<UploadedDocument> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch("/api/documents", { method: "POST", body: form });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Upload failed (${response.status}): ${detail.slice(0, 200)}`);
  }

  const data = await response.json();
  return {
    id: data.id,
    name: data.name,
    sections: data.scope?.chunks ?? 0,
    tier: data.scope?.tier ?? "unknown",
    message: data.scope?.message ?? "",
  };
}

/**
 * Start indexing without waiting for it.
 *
 * Embedding a 5 MB document takes about a minute. Blocking send() for that long
 * would freeze the composer, and the search tool indexes on demand anyway - so
 * this is a head start, not a prerequisite. Failures are deliberately ignored.
 */
export function startIndexing(documentId: string): void {
  void fetch(`/api/documents/${documentId}/index`, { method: "POST" }).catch(
    () => undefined,
  );
}

/**
 * The reference the model sees in place of the file's text.
 *
 * Deliberately a plain text part: it travels through the existing chat path
 * with no backend plumbing, and it stays in the thread, so a question three
 * turns later can still reach the document.
 */
export function formatDocumentReference(document: UploadedDocument): string {
  return (
    `<attached-document id="${document.id}" name="${document.name}" ` +
    `sections="${document.sections}">\n` +
    `This document is too large to include here. Its text is NOT in this ` +
    `conversation. Use the search_document tool with id ${document.id} to ` +
    `answer any question about it.\n` +
    `</attached-document>`
  );
}

/**
 * Wrap file text in the delimiter the model sees.
 *
 * Mirrors the shape SimpleTextAttachmentAdapter uses, so the model's prompt
 * looks familiar and the filename is available for it to refer to.
 */
export function formatAttachment(
  name: string,
  text: string,
  maxChars: number = MAX_ATTACHMENT_CHARS,
): string {
  return `<attachment name=${name}>\n${truncateForContext(text, maxChars)}\n</attachment>`;
}
