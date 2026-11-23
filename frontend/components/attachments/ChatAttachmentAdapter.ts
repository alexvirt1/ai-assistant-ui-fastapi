"use client";

import {
  CompositeAttachmentAdapter,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";

import { clearAttachmentNotice, showAttachmentNotice } from "@/lib/attachmentNotice";

const UNSUPPORTED = "No matching adapter";

/**
 * The paperclip: text files and reference photos, picked by the file's type.
 *
 * Over the plain composite it adds one thing, a reason the user can see when a
 * file is refused (see lib/attachmentNotice.ts). A phone's picker offers any
 * file whatever `accept` says, and before this a photo landed in the text
 * adapter and was sent as a garbled "document".
 */
export class ChatAttachmentAdapter extends CompositeAttachmentAdapter {
  constructor(adapters: AttachmentAdapter[]) {
    super(adapters);
  }

  async add(state: { file: File }): Promise<PendingAttachment> {
    try {
      const attachment = await super.add(state);
      clearAttachmentNotice();
      return attachment;
    } catch (error) {
      throw report(error, state.file.name);
    }
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    try {
      return await super.send(attachment);
    } catch (error) {
      throw report(error, attachment.name);
    }
  }
}

function report(error: unknown, name: string): Error {
  const raw = error instanceof Error ? error.message : String(error);
  const message = raw.includes(UNSUPPORTED)
    ? `"${name}" can't be attached. Attach a text file (.txt, .md, .csv, .json…) ` +
      `or a PNG, JPEG, GIF or WebP photo.`
    : raw;
  showAttachmentNotice(message);
  return new Error(message);
}
