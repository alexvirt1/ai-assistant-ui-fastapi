"use client";

import { useEffect, useRef, useState } from "react";

import { listChats, type ChatSummary } from "@/lib/chats";

/** How long typing pauses before the list is refetched. */
const SEARCH_DEBOUNCE_MS = 200;

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString();
}

export function ChatSidebar({
  activeId,
  refreshKey,
  disabled = false,
  onSelect,
  onNew,
}: {
  activeId: string | null;
  /** Bumped by the caller when a turn finishes, to pick up a new title or order. */
  refreshKey: number;
  /** True while a response is streaming: switching mid-run would abandon it. */
  disabled?: boolean;
  onSelect: (threadId: string) => void;
  onNew: () => void;
}) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Distinguishes "still loading" from "genuinely no chats", which otherwise
  // both render as an empty list and make a slow first load look broken.
  const [loaded, setLoaded] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    // No debounce on the very first load, only on typing: waiting 200ms to
    // show a list that is already known would be a pointless flash of empty.
    const delay = query ? SEARCH_DEBOUNCE_MS : 0;

    const timer = setTimeout(() => {
      listChats(query, controller.signal)
        .then((result) => {
          setChats(result);
          setError(null);
        })
        .catch((cause: unknown) => {
          // An aborted request is this effect being superseded, not a failure.
          if (controller.signal.aborted) return;
          setError(cause instanceof Error ? cause.message : "Could not load chats");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoaded(true);
        });
    }, delay);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, refreshKey]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-gray-200 dark:border-gray-800">
      {/* shrink-0 so the controls stay put while the list below scrolls. */}
      <div className="shrink-0 space-y-2 border-b border-gray-200 p-3 dark:border-gray-800">
        <button
          type="button"
          onClick={onNew}
          disabled={disabled}
          title={
            disabled
              ? "Wait for the current response to finish"
              : "Start a new conversation"
          }
          className="w-full rounded-md border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
        >
          New chat
        </button>
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search chats"
          aria-label="Search chats"
          className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-1.5 text-sm text-gray-700 placeholder:text-gray-400 focus:border-gray-400 focus:outline-none dark:border-gray-700 dark:text-gray-200 dark:placeholder:text-gray-500 dark:focus:border-gray-500"
        />
      </div>

      {/* min-h-0 so this scrolls instead of pushing the panel past the viewport. */}
      <nav className="min-h-0 flex-1 overflow-y-auto p-2" aria-label="Chat history">
        {error ? (
          <p className="px-2 py-3 text-sm text-amber-700 dark:text-amber-500">{error}</p>
        ) : chats.length === 0 && loaded ? (
          <p className="px-2 py-3 text-sm text-gray-500 dark:text-gray-400">
            {query ? "No chats match." : "No chats yet."}
          </p>
        ) : (
          <ul className="space-y-1">
            {chats.map((chat) => {
              const isActive = chat.id === activeId;
              return (
                <li key={chat.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(chat.id)}
                    disabled={disabled || isActive}
                    aria-current={isActive ? "true" : undefined}
                    className={`w-full rounded-md px-2 py-1.5 text-left transition-colors disabled:cursor-default ${
                      isActive
                        ? "bg-gray-100 dark:bg-gray-800"
                        : "hover:bg-gray-50 disabled:opacity-50 dark:hover:bg-gray-900"
                    }`}
                  >
                    {/* truncate rather than wrap: a 60-character title would
                        otherwise take three lines and push the list around. */}
                    <span className="block truncate text-sm text-gray-700 dark:text-gray-200">
                      {chat.title || "Untitled chat"}
                    </span>
                    <span className="block text-xs text-gray-400 dark:text-gray-500">
                      {relativeTime(chat.updatedAt)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
    </aside>
  );
}
