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

/**
 * Characters kept from any one attachment. Matches the backend's
 * REST_TOOL_MAX_CHARS default so tool output and attachments cost the model a
 * comparable amount of context (~1500 tokens).
 */
export const MAX_ATTACHMENT_CHARS = 6000;

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

/**
 * Wrap file text in the delimiter the model sees.
 *
 * Mirrors the shape SimpleTextAttachmentAdapter uses, so the model's prompt
 * looks familiar and the filename is available for it to refer to.
 */
export function formatAttachment(name: string, text: string): string {
  return `<attachment name=${name}>\n${truncateForContext(text)}\n</attachment>`;
}
