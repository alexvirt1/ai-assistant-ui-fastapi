"use client";

import { useEffect, useRef, useState } from "react";

import { deleteChat, listChats, type ChatSummary } from "@/lib/chats";

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
  onDeleted,
}: {
  activeId: string | null;
  /** Bumped by the caller when a turn finishes, to pick up a new title or order. */
  refreshKey: number;
  /** True while a response is streaming: switching mid-run would abandon it. */
  disabled?: boolean;
  onSelect: (threadId: string) => void;
  onNew: () => void;
  /** Called with the ids actually deleted, so the caller can leave a chat that is gone. */
  onDeleted?: (threadIds: string[]) => void;
}) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Distinguishes "still loading" from "genuinely no chats", which otherwise
  // both render as an empty list and make a slow first load look broken.
  const [loaded, setLoaded] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  // Select mode turns a click on a row into a checkbox toggle instead of
  // opening the chat, so picking several to delete cannot switch conversations.
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  // Separate from `error`, which replaces the whole list: after a partial
  // failure the list is still valid and the user needs it to retry.
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Bumped after a delete. Refetching rather than only dropping the rows
  // locally, because the list is paginated and older chats move up to fill it.
  const [reloadKey, setReloadKey] = useState(0);

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
  }, [query, refreshKey, reloadKey]);

  // Only what is on screen counts as selected. A selection made before a
  // search narrowed the list must not delete chats the user can no longer see.
  const selectedChats = chats.filter((chat) => selected.has(chat.id));

  // The open chat cannot be deleted mid-response: its run would keep writing
  // to a thread that no longer exists.
  const isLocked = (id: string) => disabled && id === activeId;

  function exitSelecting() {
    setSelecting(false);
    setSelected(new Set());
    setDeleteError(null);
  }

  function toggle(id: string) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    const selectable = chats.filter((chat) => !isLocked(chat.id));
    const allSelected = selectable.every((chat) => selected.has(chat.id));
    setSelected(allSelected ? new Set() : new Set(selectable.map((chat) => chat.id)));
  }

  async function deleteSelected() {
    const ids = selectedChats.map((chat) => chat.id);
    if (ids.length === 0) return;
    const what = ids.length === 1 ? "this chat" : `these ${ids.length} chats`;
    if (!window.confirm(`Delete ${what}? This cannot be undone.`)) return;

    setDeleting(true);
    setDeleteError(null);
    // allSettled, not all: one failure must not hide which of the others went
    // through. Those stay selected so a retry is one click.
    const results = await Promise.allSettled(ids.map((id) => deleteChat(id)));
    const deleted = ids.filter((_, i) => results[i].status === "fulfilled");
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setDeleting(false);

    if (deleted.length > 0) onDeleted?.(deleted);
    setReloadKey((key) => key + 1);
    if (failed.length > 0) {
      setSelected(new Set(failed));
      setDeleteError(
        `Could not delete ${failed.length} of ${ids.length} chat${ids.length === 1 ? "" : "s"}.`,
      );
    } else {
      exitSelecting();
    }
  }

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-gray-200 dark:border-gray-800">
      {/* shrink-0 so the controls stay put while the list below scrolls. */}
      <div className="shrink-0 space-y-2 border-b border-gray-200 p-3 dark:border-gray-800">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onNew}
            disabled={disabled}
            title={
              disabled
                ? "Wait for the current response to finish"
                : "Start a new conversation"
            }
            className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            New chat
          </button>
          <button
            type="button"
            onClick={() => (selecting ? exitSelecting() : setSelecting(true))}
            disabled={deleting || (!selecting && chats.length === 0)}
            aria-pressed={selecting}
            title={selecting ? "Stop selecting" : "Select chats to delete"}
            className="shrink-0 rounded-md border border-gray-200 px-3 py-1.5 text-sm text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            {selecting ? "Done" : "Select"}
          </button>
        </div>
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
              if (selecting) {
                const locked = isLocked(chat.id);
                return (
                  <li key={chat.id}>
                    <label
                      title={locked ? "Wait for the current response to finish" : undefined}
                      className={`flex items-start gap-2 rounded-md px-2 py-1.5 transition-colors ${
                        locked
                          ? "cursor-not-allowed opacity-50"
                          : "cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-900"
                      } ${isActive ? "bg-gray-100 dark:bg-gray-800" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(chat.id)}
                        onChange={() => toggle(chat.id)}
                        disabled={locked || deleting}
                        className="mt-1 shrink-0"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-gray-700 dark:text-gray-200">
                          {chat.title || "Untitled chat"}
                        </span>
                        <span className="block text-xs text-gray-400 dark:text-gray-500">
                          {relativeTime(chat.updatedAt)}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              }
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

      {selecting ? (
        <div
          role="toolbar"
          aria-label="Selected chats"
          className="shrink-0 space-y-2 border-t border-gray-200 p-3 dark:border-gray-800"
        >
          {deleteError ? (
            <p role="alert" className="text-xs text-amber-700 dark:text-amber-500">
              {deleteError}
            </p>
          ) : null}
          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>{selectedChats.length} selected</span>
            <button
              type="button"
              onClick={toggleAll}
              disabled={deleting || chats.length === 0}
              className="underline-offset-2 hover:underline disabled:opacity-50"
            >
              {chats.length > 0 &&
              chats.every((chat) => isLocked(chat.id) || selected.has(chat.id))
                ? "Select none"
                : "Select all"}
            </button>
          </div>
          <button
            type="button"
            onClick={deleteSelected}
            disabled={deleting || selectedChats.length === 0}
            className="w-full rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
          >
            {deleting
              ? "Deleting…"
              : selectedChats.length > 0
                ? `Delete ${selectedChats.length}`
                : "Delete"}
          </button>
        </div>
      ) : null}
    </aside>
  );
}
