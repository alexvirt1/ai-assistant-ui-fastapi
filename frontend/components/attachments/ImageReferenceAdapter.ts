"use client";

import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";

import { attachmentId } from "@/lib/attachments";
import { finishUpload, startUpload } from "@/lib/documentStore";
import {
  REFERENCE_ACCEPT,
  formatReferenceMarker,
  referenceProblem,
  uploadReference,
} from "@/lib/references";

/**
 * Photos attached as references for image generation.
 *
 * The file stays in the browser until the message is sent (the composer shows
 * its thumbnail from the File), then goes to /api/references, which cleans it
 * (orientation, EXIF and GPS removed, at most 2048 px) and keeps it with the
 * chat. Uploading on send rather than on attach means a photo removed from the
 * composer never leaves the browser.
 *
 * What reaches the model is a text marker naming the stored photo, not the
 * picture: qwen3:8b can't see images, and the backend's message conversion
 * would otherwise hand it an image part. See lib/references.ts.
 */
export class ImageReferenceAdapter implements AttachmentAdapter {
  accept = REFERENCE_ACCEPT;

  /** Read at send time: a new chat gets its id before its first message. */
  constructor(private readonly threadId: () => string | undefined) {}

  async add({ file }: { file: File }): Promise<PendingAttachment> {
    const problem = referenceProblem(file);
    if (problem) throw new Error(problem);
    return {
      id: attachmentId(file.name),
      type: "image",
      name: file.name,
      contentType: file.type || "image/png",
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    const threadId = this.threadId();
    if (!threadId) throw new Error("The chat isn't ready yet; try again in a moment.");
    // The header's "uploading…" chip: a phone photo can take seconds to
    // upload, and with nothing on screen it looked as if Send did nothing.
    startUpload(attachment.id, attachment.name);
    let ref;
    try {
      ref = await uploadReference(attachment.file, threadId);
    } finally {
      finishUpload(attachment.id);
    }
    return {
      ...attachment,
      status: { type: "complete" },
      content: [{ type: "text", text: formatReferenceMarker(ref) }],
    };
  }

  async remove(): Promise<void> {
    // Nothing to clean up: the photo is uploaded only when the message is sent.
  }
}
