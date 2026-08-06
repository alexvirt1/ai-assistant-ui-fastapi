"use client";

import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";

import {
  MAX_ATTACHMENT_CHARS,
  MAX_FILE_BYTES,
  TEXT_ACCEPT,
  attachmentId,
  formatAttachment,
  formatDocumentReference,
  startIndexing,
  uploadDocument,
} from "@/lib/attachments";
import {
  addDocument,
  finishUpload,
  setDocumentStatus,
  startUpload,
} from "@/lib/documentStore";

/**
 * Routes an attached text file by size.
 *
 * Small files are inlined as an ordinary text part, which the chat route's
 * convert_to_langchain_messages already handles. (It does *not* handle file
 * parts - those are parsed and silently dropped - so text is the only working
 * shape today.)
 *
 * Files too large for the context window go to the document pipeline instead,
 * and the model receives a reference it can search with the search_document
 * tool. Inlining a 5 MB file would not work anyway: it is persisted into the
 * LangGraph checkpoint and then discarded by the history trimmer, so the model
 * would see almost none of it while the thread carried all of it forever.
 *
 * Limits are constructor options rather than module constants so they can come
 * from the environment; see readAttachmentLimits() in lib/attachments.ts.
 */
export class TextAttachmentAdapter implements AttachmentAdapter {
  accept = TEXT_ACCEPT;

  private readonly maxChars: number;
  private readonly maxBytes: number;

  constructor(
    options: { maxChars?: number; maxBytes?: number } = {},
  ) {
    this.maxChars = options.maxChars ?? MAX_ATTACHMENT_CHARS;
    this.maxBytes = options.maxBytes ?? MAX_FILE_BYTES;
  }

  async add({ file }: { file: File }): Promise<PendingAttachment> {
    if (file.size > this.maxBytes) {
      // Thrown before the file is read: rejecting a 50 MB file is much better
      // than loading it into memory and then truncating away 99% of it.
      throw new Error(
        `"${file.name}" is ${(file.size / 1_000_000).toFixed(1)} MB. ` +
          `Attachments are limited to ${(this.maxBytes / 1_000_000).toFixed(1)} MB.`,
      );
    }

    return {
      id: attachmentId(file.name),
      type: "document",
      name: file.name,
      contentType: file.type || "text/plain",
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    const text = await attachment.file.text();
    const complete = (body: string): CompleteAttachment => ({
      ...attachment,
      status: { type: "complete" },
      content: [{ type: "text", text: body }],
    });

    // Small enough to read directly: inline the whole thing, no truncation.
    if (text.length <= this.maxChars) {
      return complete(formatAttachment(attachment.name, text, this.maxChars));
    }

    // Too large for the context window. Hand it to the document pipeline and
    // send the model a reference instead of the text.
    //
    // Announced before the fetch, not after it: the composer appends the
    // message only once every attachment has been sent, so the seconds the
    // backend spends storing and chunking a multi-megabyte file are seconds in
    // which the UI shows nothing happening at all. Cleared in `finally` so the
    // fallback path below takes the chip away too.
    startUpload(attachment.id, attachment.name);
    try {
      const document = await uploadDocument(attachment.file);
      // Registered first, and before indexing finishes: every later request
      // carries it to the system prompt (the reference below only reaches the
      // model on this turn), and the chip needs to exist to show "indexing".
      addDocument(document);
      // Deliberately not awaited - embedding takes about a minute per 5 MB and
      // send() must not block on it. The chip reports when it lands.
      void startIndexing(document.id).then((ok) =>
        setDocumentStatus(document.id, ok ? "ready" : "failed"),
      );
      return complete(formatDocumentReference(document));
    } catch (error) {
      // Falling back to a truncated inline copy is worse than the document
      // path but far better than dropping the attachment: the user still gets
      // an answer about the opening of their file, and the marker says what
      // happened.
      const reason = error instanceof Error ? error.message : String(error);
      return complete(
        `${formatAttachment(attachment.name, text, this.maxChars)}\n` +
          `[Note: this document could not be uploaded for full-text search ` +
          `(${reason}); only its opening is shown above.]`,
      );
    } finally {
      finishUpload(attachment.id);
    }
  }

  async remove(): Promise<void> {
    // Nothing to clean up: the file never leaves the browser until send().
  }
}
