"use client";

import { useThread } from "@assistant-ui/react";
import { useEffect, useState, useSyncExternalStore } from "react";

import {
  CLAUDE_NEW_CHAT_URL,
  fetchChatHybrid,
  fetchCloudStatus,
  formatUsd,
  getHybrid,
  resetHybrid,
  saveChatHybrid,
  setHybrid,
  subscribeHybrid,
  transcriptForClaude,
  type CloudStatus,
  type HybridMode,
} from "@/lib/hybrid";

const control =
  "h-8 rounded-md border border-gray-200 bg-transparent px-2 text-xs text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200";

/**
 * "Open in Claude": continue the conversation on the user's claude.ai
 * subscription. Copies the conversation as text and opens a new Claude chat to
 * paste it into - no API key, nothing sent by this app.
 */
export function OpenInClaudeButton() {
  const messages = useThread((t) => t.messages);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  async function open() {
    const text = transcriptForClaude(messages as never);
    try {
      await navigator.clipboard.writeText(text);
      setCopied("copied");
    } catch {
      // Clipboard access is refused on plain http from another device.
      setCopied("failed");
    }
    window.open(CLAUDE_NEW_CHAT_URL, "_blank", "noopener");
    setTimeout(() => setCopied("idle"), 4000);
  }

  return (
    <button
      type="button"
      onClick={open}
      disabled={messages.length === 0}
      title="Copy this conversation and open a new chat on claude.ai (uses your Claude subscription)"
      className={`${control} font-medium hover:bg-gray-100 dark:hover:bg-gray-800`}
    >
      {copied === "copied" ? "Copied – paste in Claude" : copied === "failed" ? "Copy failed" : "Open in Claude"}
    </button>
  );
}

/**
 * Which model answers this chat: local, auto (the local model may propose
 * Claude; the user approves each time) or Claude for every turn. Shown only
 * when a cloud model is configured on the backend.
 */
export function HybridControls({ threadId }: { threadId?: string }) {
  const hybrid = useSyncExternalStore(subscribeHybrid, getHybrid, getHybrid);
  const isRunning = useThread((t) => t.isRunning);
  const [status, setStatus] = useState<CloudStatus | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchCloudStatus(controller.signal).then(setStatus);
    return () => controller.abort();
  }, [isRunning]);

  // Load the chat's stored settings when it is opened or started.
  useEffect(() => {
    let cancelled = false;
    resetHybrid();
    if (threadId) {
      void fetchChatHybrid(threadId).then((settings) => {
        if (!cancelled && settings) resetHybrid(settings.mode, settings.allowDocuments);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  // The escalate flag covers one turn only: clear it once that turn starts.
  useEffect(() => {
    if (isRunning && getHybrid().escalateNext) setHybrid({ escalateNext: false });
  }, [isRunning]);

  if (!status?.configured || !status.model) return null;
  const label = status.model.label;

  function change(update: { mode?: HybridMode; allowDocuments?: boolean }) {
    setHybrid(update);
    if (threadId) void saveChatHybrid(threadId, update);
  }

  const spent =
    status.month_spent_usd === null
      ? ""
      : `${formatUsd(status.month_spent_usd)} of ${formatUsd(status.budget_usd)} this month`;

  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="Who answers in this chat"
        value={hybrid.mode}
        disabled={isRunning}
        onChange={(event) => change({ mode: event.target.value as HybridMode })}
        title={spent ? `Cloud spend: ${spent}` : undefined}
        className={control}
      >
        <option value="local">Local only</option>
        <option value="auto">Auto (ask before {label})</option>
        <option value="cloud">{label}</option>
      </select>
      {hybrid.mode !== "local" ? (
        <label
          className="flex items-center gap-1 text-xs text-gray-600 dark:text-gray-300"
          title={`Let ${label} read this chat's attached documents`}
        >
          <input
            type="checkbox"
            checked={hybrid.allowDocuments}
            disabled={isRunning}
            onChange={(event) => change({ allowDocuments: event.target.checked })}
          />
          Docs
        </label>
      ) : null}
    </div>
  );
}
