"use client";

import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";

import {
  MAX_FILE_BYTES,
  TEXT_ACCEPT,
  attachmentId,
  formatAttachment,
} from "@/lib/attachments";

/**
 * Reads a text file in the browser and sends its contents as an ordinary text
 * part.
 *
 * That part shape is why this needs no backend change: the chat route's
 * convert_to_langchain_messages already handles text parts. (It does *not*
 * handle file parts - those are parsed and silently dropped - so emitting text
 * is both the simplest and the only working option today.)
 */
export class TextAttachmentAdapter implements AttachmentAdapter {
  accept = TEXT_ACCEPT;

  async add({ file }: { file: File }): Promise<PendingAttachment> {
    if (file.size > MAX_FILE_BYTES) {
      // Thrown before the file is read: rejecting a 50 MB file is much better
      // than loading it into memory and then truncating away 99% of it.
      throw new Error(
        `"${file.name}" is ${(file.size / 1_000_000).toFixed(1)} MB. ` +
          `Attachments are limited to ${MAX_FILE_BYTES / 1_000_000} MB.`,
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

    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "text",
          text: formatAttachment(attachment.name, text),
        },
      ],
    };
  }

  async remove(): Promise<void> {
    // Nothing to clean up: the file never leaves the browser until send().
  }
}
