"use client";

import { AssistantRuntimeProvider, useEdgeRuntime } from "@assistant-ui/react";
import { Thread } from "@assistant-ui/react";
import { makeMarkdownText } from "@assistant-ui/react-markdown";
import { useMemo } from "react";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import type { AttachmentLimits } from "@/lib/attachments";

import { TextAttachmentAdapter } from "./attachments/TextAttachmentAdapter";
import { NewChatButton } from "./NewChatButton";
import { ThemeToggle } from "./ThemeToggle";
import {
  ToolExecutionIndicators,
  ToolRunningFallback,
} from "./tools/ToolExecutionIndicators";

// remarkMath parses $...$ (inline) and $$...$$ (display) math; rehypeKatex
// renders it. KaTeX emits markup only, so katex.min.css is imported once in
// app/layout.tsx — without it the math renders unstyled.
const MarkdownText = makeMarkdownText({
  remarkPlugins: [remarkMath],
  rehypePlugins: [rehypeKatex],
});

export function MyAssistant({
  attachmentLimits,
}: {
  attachmentLimits?: AttachmentLimits;
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

  const runtime = useEdgeRuntime({
    api: "/api/chat",
    unstable_AISDKInterop: true,
    // Configuring this is what makes the composer's "+" button appear: the
    // built-in ComposerAddAttachment opens an <input type="file"> filtered by
    // the adapter's `accept`, so no custom UI is needed.
    adapters: { attachments: attachmentAdapter },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ToolExecutionIndicators />
      <div className="flex h-full flex-col">
        <header className="flex shrink-0 items-center justify-end gap-2 border-b border-gray-200 px-4 py-2 dark:border-gray-800">
          <ThemeToggle />
          <NewChatButton />
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
