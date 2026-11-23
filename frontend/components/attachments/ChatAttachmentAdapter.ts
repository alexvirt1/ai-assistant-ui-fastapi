"use client";

import {
  CompositeAttachmentAdapter,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";

import { clearAttachmentNotice, showAttachmentNotice } from "@/lib/attachmentNotice";

const UNSUPPORTED = "No matching adapter";

/** A repeated send while the first is uploading: not the user's error to show. */
export const ALREADY_SENDING = "This attachment is already being sent.";

/**
 * The paperclip: text files and reference photos, picked by the file's type.
 *
 * Over the plain composite it adds one thing, a reason the user can see when a
 * file is refused (see lib/attachmentNotice.ts). A phone's picker offers any
 * file whatever `accept` says, and before this a photo landed in the text
 * adapter and was sent as a garbled "document".
 */
export class ChatAttachmentAdapter extends CompositeAttachmentAdapter {
  /** Attachments being uploaded now, by id. */
  private readonly sending = new Set<string>();

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

  /**
   * Upload one attachment, once.
   *
   * assistant-ui's composer awaits the uploads before it clears itself, and
   * Send stays enabled meanwhile, so a second tap while photos upload sent
   * them again: a second message (without the text, already cleared by the
   * first) and a second turn racing the first for the GPU. Refusing the
   * repeat makes the second send() stop before it appends anything.
   */
  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    if (this.sending.has(attachment.id)) {
      throw new Error(ALREADY_SENDING);
    }
    this.sending.add(attachment.id);
    try {
      return await super.send(attachment);
    } catch (error) {
      throw report(error, attachment.name);
    } finally {
      this.sending.delete(attachment.id);
    }
  }
}

function report(error: unknown, name: string): Error {
  const raw = error instanceof Error ? error.message : String(error);
  if (raw === ALREADY_SENDING) return new Error(raw);
  const message = raw.includes(UNSUPPORTED)
    ? `"${name}" can't be attached. Attach a text file (.txt, .md, .csv, .json…) ` +
      `or a PNG, JPEG, GIF or WebP photo.`
    : raw;
  showAttachmentNotice(message);
  return new Error(message);
}
