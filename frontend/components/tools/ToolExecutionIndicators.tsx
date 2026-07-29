"use client";

import { makeAssistantToolUI, useMessage } from "@assistant-ui/react";

/**
 * While a backend tool call is streaming (status "running"), render a small
 * icon + description of what the agent is doing. Once the tool completes
 * (result arrives) or fails, render nothing so the indicator disappears.
 */

const RunningIndicator = ({
  icon,
  description,
}: {
  icon: string;
  description: string;
}) => (
  <div className="my-1.5 flex w-fit items-center gap-2 rounded-full border border-gray-200 bg-gray-100 px-3 py-1.5 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
    <span className="animate-pulse" aria-hidden>
      {icon}
    </span>
    <span>{description}</span>
  </div>
);

const ThinkingDot = () => (
  <div
    className="my-1.5 flex w-fit items-center"
    role="status"
    aria-label="Waiting for the assistant's response"
  >
    <span className="size-2.5 animate-pulse rounded-full bg-gray-400 dark:bg-gray-500" />
  </div>
);

/**
 * Bridges the gap between a tool finishing and the answer starting to stream —
 * often a second or more with a local model. By then the message already has
 * content parts, so the Thread's own empty-message loading indicator no longer
 * applies and the UI would otherwise sit blank.
 *
 * Renders only for the message's last part, so a run with several tool calls
 * shows one dot rather than one per completed call. As soon as the first text
 * token arrives a text part is appended, this is no longer the last part, and
 * the dot disappears.
 */
const WaitingForAnswer = ({ toolCallId }: { toolCallId: string }) => {
  const waiting = useMessage((m) => {
    if (m.role !== "assistant" || m.status.type !== "running") return false;
    const last = m.content[m.content.length - 1];
    if (!last || last.type !== "tool-call") return false;
    return last.toolCallId === toolCallId;
  });

  return waiting ? <ThinkingDot /> : null;
};

const makeIndicator = (
  toolName: string,
  icon: string,
  describe: (args: Record<string, unknown>) => string,
) =>
  makeAssistantToolUI({
    toolName,
    render: ({ args, status, toolCallId }) => {
      if (status.type === "running") {
        return (
          <RunningIndicator icon={icon} description={describe(args ?? {})} />
        );
      }
      return <WaitingForAnswer toolCallId={toolCallId} />;
    },
  });

const CurrentTimeIndicator = makeIndicator(
  "current_time",
  "🕐",
  () => "Checking the current server time…",
);

const WebSearchIndicator = makeIndicator("web_search", "🌐", (args) =>
  typeof args.query === "string" && args.query
    ? `Searching the web for “${args.query}”…`
    : "Searching the web…",
);

const FetchPageIndicator = makeIndicator("fetch_page", "🌐", (args) =>
  typeof args.url === "string" && args.url
    ? `Reading ${args.url}…`
    : "Reading a web page…",
);

const AskOpenAiIndicator = makeIndicator(
  "ask_openai",
  "🧠",
  () => "Consulting OpenAI (GPT)…",
);

const AskClaudeIndicator = makeIndicator(
  "ask_claude",
  "🧠",
  () => "Consulting Anthropic Claude…",
);

/**
 * Fallback for tools without a dedicated indicator (MCP servers, declarative
 * REST tools): generic wrench icon plus the tool's name while it runs.
 * Passed as assistantMessage.components.ToolFallback on the Thread.
 */
export const ToolRunningFallback = ({
  toolName,
  status,
  toolCallId,
}: {
  toolName: string;
  status: { type: string };
  toolCallId: string;
}) => {
  if (status.type === "running") {
    return (
      <RunningIndicator
        icon="🔧"
        description={`Running ${toolName.replace(/_/g, " ")}…`}
      />
    );
  }
  return <WaitingForAnswer toolCallId={toolCallId} />;
};

export function ToolExecutionIndicators() {
  return (
    <>
      <CurrentTimeIndicator />
      <WebSearchIndicator />
      <FetchPageIndicator />
      <AskOpenAiIndicator />
      <AskClaudeIndicator />
    </>
  );
}
