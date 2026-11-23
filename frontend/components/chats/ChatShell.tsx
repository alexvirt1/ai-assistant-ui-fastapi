"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AttachmentLimits } from "@/lib/attachments";
import {
  fetchChatDocuments,
  fetchChatMessages,
  type RestoredMessage,
} from "@/lib/chats";
import { setDocuments } from "@/lib/documentStore";
import { waitForSettledTurn } from "@/lib/recovery";
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
  // Bumped to remount the pane with a recovered transcript (same thread).
  const [paneVersion, setPaneVersion] = useState(0);
  const [recovering, setRecovering] = useState(false);
  // The chat list on narrow screens: a drawer over the chat, closed by
  // default. From md up it sits beside the chat and this is ignored.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const recovery = useRef<AbortController | null>(null);

  const stopRecovery = useCallback(() => {
    recovery.current?.abort();
    recovery.current = null;
    setRecovering(false);
  }, []);

  const startNewChat = useCallback(() => {
    stopRecovery();
    const id = newThreadId();
    setThreadCookie(id);
    // A new conversation has nothing attached. Without this, a document from
    // the previous chat would still be announced in this one's system prompt,
    // and the model told it can search something this conversation never saw.
    setDocuments([]);
    setInitialMessages([]);
    setThreadId(id);
    setError(null);
  }, [stopRecovery]);

  const openChat = useCallback(
    async (id: string) => {
      if (id === threadId) return;
      try {
        // Both fetched before anything is mutated, so a failed load leaves the
        // conversation you were reading exactly where it was. In parallel
        // because neither depends on the other.
        const [messages, documents] = await Promise.all([
          fetchChatMessages(id),
          fetchChatDocuments(id),
        ]);
        stopRecovery();
        setThreadCookie(id);
        setDocuments(documents);
        setInitialMessages(messages);
        setThreadId(id);
        setError(null);
      } catch {
        setError("Could not open that chat.");
      }
    },
    [threadId, stopRecovery],
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

    Promise.all([fetchChatMessages(existing), fetchChatDocuments(existing)])
      .then(([messages, documents]) => {
        if (cancelled) return;
        // How attachments survive a reload now: asked for, not read back out
        // of the browser.
        setDocuments(documents);
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

  // Deleting the chat on screen leaves nothing to continue: its transcript is
  // gone, and the next turn would quietly recreate it under the old id.
  const handleDeleted = useCallback(
    (ids: string[]) => {
      if (threadId && ids.includes(threadId)) startNewChat();
    },
    [threadId, startNewChat],
  );

  // A turn stopped on screen before it finished - usually its stream was cut
  // (seen with a browser extension). The backend finishes it anyway, so wait
  // for the stored transcript to settle and reload the pane from it.
  const handleTurnUnfinished = useCallback(() => {
    if (!threadId) return;
    recovery.current?.abort();
    const controller = new AbortController();
    recovery.current = controller;
    setRecovering(true);
    const id = threadId;
    void waitForSettledTurn(() => fetchChatMessages(id), controller.signal).then(
      (messages) => {
        if (controller.signal.aborted) return;
        recovery.current = null;
        setRecovering(false);
        if (messages) {
          setInitialMessages(messages);
          setPaneVersion((version) => version + 1);
        }
      },
    );
  }, [threadId]);

  useEffect(() => () => recovery.current?.abort(), []);

  const handleRunningChange = useCallback((running: boolean) => {
    // A new turn supersedes a recovery: reloading would wipe it.
    if (running) stopRecovery();
    setIsRunning(running);
    // Refetched on both edges. On start, so a brand-new chat appears in the
    // list while it is being answered; on end, to pick up the title the
    // backend derived and the new ordering. If the start refetch races the
    // backend's own registration and misses, the end refetch corrects it.
    setRefreshKey((key) => key + 1);
  }, [stopRecovery]);

  useEffect(() => {
    if (!sidebarOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSidebarOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  return (
    <div className="relative flex h-full">
      {sidebarOpen ? (
        <div
          aria-hidden
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
        />
      ) : null}
      {/* A drawer on phones, a column from md up. translate-x moves it off
          screen rather than unmounting it, so the list keeps its scroll
          position and search between openings. */}
      <div
        data-testid="sidebar-drawer"
        data-open={sidebarOpen}
        className={`fixed inset-y-0 left-0 z-40 w-[85vw] max-w-xs bg-aui-background shadow-xl transition-transform duration-200 md:static md:z-auto md:w-auto md:max-w-none md:translate-x-0 md:shadow-none ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <ChatSidebar
          activeId={threadId}
          refreshKey={refreshKey}
          disabled={isRunning}
          onSelect={(id) => {
            setSidebarOpen(false);
            void openChat(id);
          }}
          onNew={() => {
            setSidebarOpen(false);
            startNewChat();
          }}
          onDeleted={handleDeleted}
        />
      </div>
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
        {recovering ? (
          <p
            role="status"
            className="shrink-0 border-b border-gray-200 px-4 py-2 text-sm text-gray-600 dark:border-gray-800 dark:text-gray-300"
          >
            Connection to the answer was lost. Waiting for it to finish on the
            server, then reloading this chat…
          </p>
        ) : null}
        {threadId ? (
          <div className="min-h-0 flex-1">
          <MyAssistant
            // Remount on switch: initialMessages is read once, when the
            // runtime is created.
            key={`${threadId}:${paneVersion}`}
            threadId={threadId}
            initialMessages={initialMessages}
            attachmentLimits={attachmentLimits}
            onRunningChange={handleRunningChange}
            onTurnUnfinished={handleTurnUnfinished}
            onOpenSidebar={() => setSidebarOpen(true)}
          />
          </div>
        ) : null}
      </div>
    </div>
  );
}
