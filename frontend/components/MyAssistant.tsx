"use client";

import { AssistantRuntimeProvider, useEdgeRuntime } from "@assistant-ui/react";
import { Thread } from "@assistant-ui/react";
import { makeMarkdownText } from "@assistant-ui/react-markdown";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import { remarkSections } from "@/lib/remarkSections";

import type { AttachmentLimits } from "@/lib/attachments";
import type { RestoredMessage } from "@/lib/chats";
import { getDocuments, hydrateDocuments, subscribe } from "@/lib/documentStore";

import { DocumentChips } from "./attachments/DocumentChips";
import { SectionCitation } from "./attachments/SectionCitation";
import { TextAttachmentAdapter } from "./attachments/TextAttachmentAdapter";
import { ThreadActivity } from "./chats/ThreadActivity";
import { ThemeToggle } from "./ThemeToggle";
import {
  ToolExecutionIndicators,
  ToolRunningFallback,
} from "./tools/ToolExecutionIndicators";

// remarkMath parses $...$ (inline) and $$...$$ (display) math; rehypeKatex
// renders it. KaTeX emits markup only, so katex.min.css is imported once in
// app/layout.tsx — without it the math renders unstyled.
const MarkdownText = makeMarkdownText({
  // remarkSections rewrites "[Section 148]" into a link with a section: URL,
  // which the `a` override below renders as an openable citation.
  remarkPlugins: [remarkMath, remarkSections],
  rehypePlugins: [rehypeKatex],
  components: { a: SectionCitation },
});

export function MyAssistant({
  threadId,
  initialMessages,
  attachmentLimits,
  onRunningChange,
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
}) {
  // Memoised rather than rebuilt per render: the runtime holds a reference to
  // the adapter, and churning it every render would be pointless work.
  const attachmentAdapter = useMemo(
    () =>
      new TextAttachmentAdapter({
        maxChars: attachmentLimits?.maxChars,
        maxBytes: attachmentLimits?.maxBytes,
      }),
    [attachmentLimits?.maxChars, attachmentLimits?.maxBytes],
  );

  // After mount, not during render: reading storage while rendering would make
  // the first client render disagree with the server HTML.
  useEffect(() => hydrateDocuments(), []);

  // Re-read on every change so a newly attached document is included from the
  // very next request onwards.
  const documents = useSyncExternalStore(subscribe, getDocuments, getDocuments);

  const runtime = useEdgeRuntime({
    api: "/api/chat",
    unstable_AISDKInterop: true,
    // Merged into every request body. The backend pins documents into the
    // system prompt, which is never trimmed - unlike the conversation - and
    // uses threadId to attribute the turn, in preference to the cookie.
    body: { documents, threadId },
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
      {onRunningChange ? (
        <ThreadActivity onRunningChange={onRunningChange} />
      ) : null}
      <div className="flex h-full flex-col">
        <header className="flex shrink-0 items-center justify-between gap-2 border-b border-gray-200 px-4 py-2 dark:border-gray-800">
          <DocumentChips />
          {/* ml-auto so the controls stay right-aligned when there are no
              chips and DocumentChips renders nothing. "New chat" lives in the
              sidebar now, next to the list it adds to. */}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <ThemeToggle />
          </div>
        </header>
        {/* min-h-0 so the Thread's own viewport scrolls instead of the flex
            item growing past the container. */}
        <div className="min-h-0 flex-1">
          <Thread
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
