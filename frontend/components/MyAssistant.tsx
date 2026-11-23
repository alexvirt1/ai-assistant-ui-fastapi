"use client";

import { AssistantRuntimeProvider, Thread, useEdgeRuntime } from "@assistant-ui/react";
import { useMemo, useSyncExternalStore } from "react";

import type { AttachmentLimits } from "@/lib/attachments";
import type { RestoredMessage } from "@/lib/chats";
import { getDocuments, subscribe } from "@/lib/documentStore";

import { AttachmentNotice } from "./attachments/AttachmentNotice";
import { ChatAttachmentAdapter } from "./attachments/ChatAttachmentAdapter";
import { DocumentChips } from "./attachments/DocumentChips";
import { ImageReferenceAdapter } from "./attachments/ImageReferenceAdapter";
import { TextAttachmentAdapter } from "./attachments/TextAttachmentAdapter";
import { ExportPdfButton } from "./chats/ExportPdfButton";
import { StreamRecovery } from "./chats/StreamRecovery";
import { ThreadActivity } from "./chats/ThreadActivity";
import { MarkdownText } from "./MarkdownText";
import { UserMessageWithReferences } from "./references/UserMessageWithReferences";
import { ModelPicker } from "./models/ModelPicker";
import { ThemeToggle } from "./ThemeToggle";
import {
  ToolExecutionIndicators,
  ToolRunningFallback,
} from "./tools/ToolExecutionIndicators";
import { GeneratedImageUI } from "./tools/GeneratedImage";
import { CloudBadgeUI, EscalationUI } from "./cloud/CloudCards";
import { HybridControls, OpenInClaudeButton } from "./cloud/HybridControls";
import { requestFields } from "@/lib/hybrid";

export function MyAssistant({
  threadId,
  initialMessages,
  attachmentLimits,
  onRunningChange,
  onTurnUnfinished,
  onOpenSidebar,
}: {
  /** Which stored conversation this pane is. Sent with every request so the
   *  backend attributes the turn to the right thread rather than falling back
   *  to the cookie. */
  threadId?: string;
  /** Read once, when the runtime is created - ChatShell remounts this
   *  component to switch conversations. */
  initialMessages?: RestoredMessage[];
  attachmentLimits?: AttachmentLimits;
  onRunningChange?: (isRunning: boolean) => void;
  /** Called when a turn stops running without finishing - see lib/recovery. */
  onTurnUnfinished?: () => void;
  /** Shows the chat list on narrow screens, where it is a drawer. */
  onOpenSidebar?: () => void;
}) {
  // Memoised rather than rebuilt per render: the runtime holds a reference to
  // the adapter, and churning it every render would be pointless work.
  // Text files and reference photos share the paperclip: the composite picks
  // the adapter whose `accept` matches the file, and says why when none does.
  const attachmentAdapter = useMemo(
    () =>
      new ChatAttachmentAdapter([
        new TextAttachmentAdapter({
          maxChars: attachmentLimits?.maxChars,
          maxBytes: attachmentLimits?.maxBytes,
        }),
        // Photos are uploaded to this chat. ChatShell remounts this pane to
        // switch chats, so threadId changes only when a new chat gets its id.
        new ImageReferenceAdapter(() => threadId),
      ]),
    [attachmentLimits?.maxChars, attachmentLimits?.maxBytes, threadId],
  );

  // Re-read on every change so a newly attached document is included from the
  // very next request onwards.
  const documents = useSyncExternalStore(subscribe, getDocuments, getDocuments);

  const runtime = useEdgeRuntime({
    api: "/api/chat",
    unstable_AISDKInterop: true,
    // Merged into every request body. The backend pins documents into the
    // system prompt, which is never trimmed - unlike the conversation - and
    // uses threadId to attribute the turn, in preference to the cookie.
    //
    // The hybrid-mode fields (lib/hybrid.ts) are copied in as getters, so they
    // are read when a request is built: the runtime applies new options only
    // after a render, and the one-turn escalate flag must not go out late.
    body: Object.defineProperties(
      { documents, threadId },
      Object.getOwnPropertyDescriptors(requestFields),
    ),
    // The transcript of a conversation opened from the sidebar. Restored
    // rather than replayed through the model: it comes from the checkpoint
    // that produced it.
    initialMessages,
    // Configuring this is what makes the composer's "+" button appear: the
    // built-in ComposerAddAttachment opens an <input type="file"> filtered by
    // the adapter's `accept`, so no custom UI is needed.
    adapters: { attachments: attachmentAdapter },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ToolExecutionIndicators />
      <GeneratedImageUI />
      <EscalationUI />
      <CloudBadgeUI />
      {onRunningChange ? (
        <ThreadActivity onRunningChange={onRunningChange} />
      ) : null}
      {onTurnUnfinished ? <StreamRecovery onUnfinished={onTurnUnfinished} /> : null}
      <div className="flex h-full flex-col">
        {/* Wraps on a phone: the controls do not fit one 390px row, and a
            row that does not wrap makes the whole page scroll sideways. */}
        <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-gray-200 px-3 py-2 sm:px-4 dark:border-gray-800">
          {onOpenSidebar ? (
            <button
              type="button"
              onClick={onOpenSidebar}
              aria-label="Show chats"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-gray-200 text-gray-700 hover:bg-gray-100 md:hidden dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <path d="M3 5h14M3 10h14M3 15h14" />
              </svg>
            </button>
          ) : null}
          {/* On a phone the chips take a row of their own under the controls;
              empty:hidden drops the wrapper when there are none. */}
          <div className="order-last w-full empty:hidden sm:order-none sm:w-auto">
            <DocumentChips />
          </div>
          {/* flex-1 so the controls share the menu button's row and wrap
              inside it. "New chat" lives in the sidebar, next to the list it
              adds to. */}
          <div className="ml-auto flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <HybridControls threadId={threadId} />
            <OpenInClaudeButton />
            {threadId ? <ExportPdfButton threadId={threadId} /> : null}
            <ModelPicker />
            <ThemeToggle />
          </div>
        </header>
        {/* min-h-0 so the Thread's own viewport scrolls instead of the flex
            item growing past the container. */}
        <div className="relative min-h-0 flex-1">
          <AttachmentNotice />
          <Thread
            // Reference photos come back from a reload as markers in the
            // text; this user message shows them as thumbnails.
            components={{ UserMessage: UserMessageWithReferences }}
            assistantMessage={{
              components: {
                Text: MarkdownText,
                ToolFallback: ToolRunningFallback,
              },
            }}
          />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}
