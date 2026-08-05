"use client";

import { useCallback, useEffect, useState } from "react";

import type { AttachmentLimits } from "@/lib/attachments";
import { fetchChatMessages, type RestoredMessage } from "@/lib/chats";
import { switchDocumentsToThread } from "@/lib/documentStore";
import { newThreadId, readThreadCookie, setThreadCookie } from "@/lib/thread";

import { MyAssistant } from "../MyAssistant";
import { ChatSidebar } from "./ChatSidebar";

/**
 * Owns which conversation is on screen.
 *
 * The runtime this version of assistant-ui provides accepts `initialMessages`
 * only when it is created, so switching chats remounts it - hence `key`. That
 * is the whole mechanism: fetch the transcript, point the thread cookie at it,
 * re-key the pane.
 */
export function ChatShell({
  attachmentLimits,
}: {
  attachmentLimits?: AttachmentLimits;
}) {
  // Null until the mount effect has run. Reading the cookie during render
  // would make the first client render disagree with the server HTML.
  const [threadId, setThreadId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<RestoredMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const startNewChat = useCallback(() => {
    const id = newThreadId();
    setThreadCookie(id);
    // The cookie has to be set first: the document store reads it to decide
    // whose attachments these are.
    switchDocumentsToThread();
    setInitialMessages([]);
    setThreadId(id);
    setError(null);
  }, []);

  const openChat = useCallback(
    async (id: string) => {
      if (id === threadId) return;
      try {
        // Fetched before anything is mutated, so a failed load leaves the
        // conversation you were reading exactly where it was.
        const messages = await fetchChatMessages(id);
        setThreadCookie(id);
        switchDocumentsToThread();
        setInitialMessages(messages);
        setThreadId(id);
        setError(null);
      } catch {
        setError("Could not open that chat.");
      }
    },
    [threadId],
  );

  // Restore whatever the browser was last looking at, or start fresh.
  //
  // Intentional mount guard, same shape as ThemeToggle's: which conversation
  // to show is only knowable in the browser (it comes from a cookie), so the
  // first client render has to match the server's thread-less output and the
  // decision happens on the commit after mount.
  useEffect(() => {
    let cancelled = false;
    const existing = readThreadCookie();

    if (!existing) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      startNewChat();
      return;
    }

    fetchChatMessages(existing)
      .then((messages) => {
        if (cancelled) return;
        setInitialMessages(messages);
        setThreadId(existing);
      })
      .catch(() => {
        // The thread was deleted, or belongs to someone else. Landing on an
        // empty new chat is better than an error page you cannot leave.
        if (!cancelled) startNewChat();
      });

    return () => {
      cancelled = true;
    };
  }, [startNewChat]);

  const handleRunningChange = useCallback((running: boolean) => {
    setIsRunning(running);
    // Refetched on both edges. On start, so a brand-new chat appears in the
    // list while it is being answered; on end, to pick up the title the
    // backend derived and the new ordering. If the start refetch races the
    // backend's own registration and misses, the end refetch corrects it.
    setRefreshKey((key) => key + 1);
  }, []);

  return (
    <div className="flex h-full">
      <ChatSidebar
        activeId={threadId}
        refreshKey={refreshKey}
        disabled={isRunning}
        onSelect={openChat}
        onNew={startNewChat}
      />
      {/* flex-col + min-h-0 so the banner takes its own height and the thread
          below it scrolls, rather than the column growing past the viewport. */}
      <div className="flex min-w-0 flex-1 flex-col">
        {error ? (
          <p
            role="alert"
            className="shrink-0 border-b border-amber-200 px-4 py-2 text-sm text-amber-700 dark:border-amber-900 dark:text-amber-500"
          >
            {error}
          </p>
        ) : null}
        {threadId ? (
          <div className="min-h-0 flex-1">
          <MyAssistant
            // Remount on switch: initialMessages is read once, when the
            // runtime is created.
            key={threadId}
            threadId={threadId}
            initialMessages={initialMessages}
            attachmentLimits={attachmentLimits}
            onRunningChange={handleRunningChange}
          />
          </div>
        ) : null}
      </div>
    </div>
  );
}
