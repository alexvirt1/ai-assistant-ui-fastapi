"use client";

import { makeAssistantToolUI, useThread, useThreadRuntime } from "@assistant-ui/react";
import { useState } from "react";

import { formatUsd, setHybrid } from "@/lib/hybrid";

function parse<T>(result: unknown): T | null {
  try {
    return (typeof result === "string" ? JSON.parse(result) : result) as T;
  } catch {
    return null;
  }
}

export const ASK_CLOUD_TEXT = "Answer my last question with the cloud model.";
export const KEEP_LOCAL_TEXT = "Please answer my last question yourself, as well as you can.";

/**
 * Auto mode: the local model proposed handing the question to the cloud
 * model. Nothing is sent until the user clicks; "Ask" sends a follow-up turn
 * flagged to go to the cloud, "Keep local" asks the local model to try.
 */
export function EscalationCard({
  reason,
  model,
  isLatest,
}: {
  reason: string;
  model: string;
  isLatest: boolean;
}) {
  const thread = useThreadRuntime();
  const isRunning = useThread((t) => t.isRunning);
  const [choice, setChoice] = useState<"ask" | "local" | null>(null);

  function ask() {
    setChoice("ask");
    setHybrid({ escalateNext: true });
    thread.append(ASK_CLOUD_TEXT);
  }

  function keepLocal() {
    setChoice("local");
    thread.append(KEEP_LOCAL_TEXT);
  }

  const done = choice !== null || !isLatest;
  const button =
    "rounded-md border px-2.5 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="my-2 max-w-lg rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm dark:border-sky-900 dark:bg-sky-950/40">
      <p className="text-gray-700 dark:text-gray-200">
        ☁ The local model suggests asking <strong>{model}</strong>
        {reason ? <span className="text-gray-500 dark:text-gray-400">: {reason}</span> : null}
      </p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={ask}
          disabled={done || isRunning}
          className={`${button} border-sky-300 bg-white text-sky-800 hover:bg-sky-100 dark:border-sky-800 dark:bg-gray-900 dark:text-sky-300`}
        >
          {choice === "ask" ? `Sent to ${model}` : `Ask ${model}`}
        </button>
        <button
          type="button"
          onClick={keepLocal}
          disabled={done || isRunning}
          className={`${button} border-gray-200 text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800`}
        >
          {choice === "local" ? "Kept local" : "Keep local"}
        </button>
      </div>
    </div>
  );
}

export const EscalationUI = makeAssistantToolUI<{ reason?: string }, string>({
  toolName: "escalate",
  render: function Render({ args, result, status }) {
    const isLatest = useThread((t) => {
      const last = t.messages[t.messages.length - 1];
      return last?.role === "assistant" && last.content.some(
        (part) => part.type === "tool-call" && part.toolName === "escalate" && part.result === result,
      );
    });
    if (status.type === "running" || result === undefined) return null;
    const proposal = parse<{ reason?: string; model?: string }>(result);
    return (
      <EscalationCard
        reason={proposal?.reason ?? args?.reason ?? ""}
        model={proposal?.model ?? "the cloud model"}
        isLatest={isLatest}
      />
    );
  },
});

/** Under an answer written by a cloud model: which one, and what it cost. */
export function CloudBadge({
  model,
  inputTokens,
  outputTokens,
  cost,
}: {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cost: number;
}) {
  const tokens = inputTokens + outputTokens;
  const shown = tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
  return (
    <p
      className="mt-1 text-xs text-gray-500 dark:text-gray-400"
      title={`${inputTokens} tokens in, ${outputTokens} out`}
    >
      ☁ {model} · {shown} tokens · {formatUsd(cost)}
    </p>
  );
}

export const CloudBadgeUI = makeAssistantToolUI<Record<string, never>, string>({
  toolName: "cloud_answer",
  render: ({ result }) => {
    const usage = parse<{ model: string; input_tokens: number; output_tokens: number; cost_usd: number }>(
      result,
    );
    if (!usage) return null;
    return (
      <CloudBadge
        model={usage.model}
        inputTokens={usage.input_tokens}
        outputTokens={usage.output_tokens}
        cost={usage.cost_usd}
      />
    );
  },
});
