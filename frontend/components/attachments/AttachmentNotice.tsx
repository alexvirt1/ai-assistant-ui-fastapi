"use client";

import { useEffect, useSyncExternalStore } from "react";

import {
  clearAttachmentNotice,
  getAttachmentNotice,
  subscribeAttachmentNotice,
} from "@/lib/attachmentNotice";

/** Shows why a file was refused, at the top of the chat, for 10 seconds (clear of the composer's photo tiles). */
export function AttachmentNotice() {
  const notice = useSyncExternalStore(
    subscribeAttachmentNotice,
    getAttachmentNotice,
    () => null,
  );

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(clearAttachmentNotice, 10_000);
    return () => clearTimeout(timer);
  }, [notice]);

  if (!notice) return null;
  return (
    <div
      role="alert"
      className="pointer-events-auto absolute inset-x-3 top-3 z-20 mx-auto flex max-w-xl items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 shadow-sm dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      <span className="min-w-0 flex-1">{notice}</span>
      <button
        type="button"
        onClick={clearAttachmentNotice}
        aria-label="Dismiss"
        className="shrink-0 rounded px-1 text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900"
      >
        ✕
      </button>
    </div>
  );
}
