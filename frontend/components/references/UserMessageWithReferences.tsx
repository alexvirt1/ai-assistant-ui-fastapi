"use client";

import {
  BranchPicker,
  MessagePrimitive,
  UserActionBar,
  UserMessage,
  type TextContentPartProps,
} from "@assistant-ui/react";

import { splitReferenceMarkers } from "@/lib/references";

import { ReferenceThumbs } from "./ReferenceThumbs";

/**
 * A user message's text, with reference markers shown as thumbnails.
 *
 * Live, a sent photo shows as the composer's attachment tile and the marker is
 * not in the text; after a reload the marker is all that is left of it (the
 * backend keeps the conversation as text), so this is where the photo shows.
 */
export function UserText({ text }: TextContentPartProps) {
  return (
    <div className="space-y-2">
      {splitReferenceMarkers(text).map((segment, index) =>
        segment.kind === "text" ? (
          <p key={index} className="aui-text whitespace-pre-wrap">
            {segment.text.trim()}
          </p>
        ) : (
          <ReferenceThumbs key={index} refs={segment.refs} />
        ),
      )}
    </div>
  );
}

/** assistant-ui's user message, with UserText in place of plain text. */
export function UserMessageWithReferences() {
  return (
    <UserMessage.Root>
      <UserMessage.Attachments />
      <MessagePrimitive.If hasContent>
        <UserActionBar />
        <UserMessage.Content components={{ Text: UserText }} />
      </MessagePrimitive.If>
      <BranchPicker />
    </UserMessage.Root>
  );
}
