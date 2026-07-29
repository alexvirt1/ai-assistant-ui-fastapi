"use client";

import { useAssistantRuntime, useThread } from "@assistant-ui/react";

import { THREAD_COOKIE } from "@/lib/thread";

/**
 * Starts a fresh conversation.
 *
 * Two things have to happen together. `switchToNewThread()` clears the
 * messages the UI is showing, and dropping the thread cookie makes the proxy
 * route mint a new LangGraph thread id on the next request. Without the cookie
 * step the new-looking thread would keep appending to the old conversation's
 * stored history, which is what lets stale tool results leak into answers.
 */
export function NewChatButton() {
  const runtime = useAssistantRuntime();
  const isRunning = useThread((t) => t.isRunning);

  const startNewChat = () => {
    document.cookie = `${THREAD_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
    runtime.switchToNewThread();
  };

  return (
    <button
      type="button"
      onClick={startNewChat}
      disabled={isRunning}
      title={
        isRunning
          ? "Wait for the current response to finish"
          : "Start a new conversation"
      }
      className="rounded-md border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
    >
      New chat
    </button>
  );
}
