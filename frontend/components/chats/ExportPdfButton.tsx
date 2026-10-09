"use client";

import { useThread } from "@assistant-ui/react";
import { useEffect, useState } from "react";

import { fetchChatPdf } from "@/lib/chats";

/** How long a failure stays on the button before it can be retried quietly. */
const ERROR_VISIBLE_MS = 5000;

/** Hand a fetched file to the browser's own download. */
function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next tick: revoking synchronously can cancel the download
  // before the browser has started reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Downloads the open conversation as a PDF.
 *
 * Unavailable until there is something to export, and while an answer is
 * streaming: the PDF is built from the stored transcript, so mid-run it would
 * silently leave out the answer the user is watching being written.
 */
export function ExportPdfButton({ threadId }: { threadId: string }) {
  const isEmpty = useThread((t) => t.messages.length === 0);
  const isRunning = useThread((t) => t.isRunning);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), ERROR_VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [error]);

  async function exportPdf() {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await fetchChatPdf(threadId);
      save(blob, filename);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  const unavailable = isEmpty || isRunning;
  const title = isRunning
    ? "Wait for the current response to finish"
    : isEmpty
      ? "Nothing to export yet"
      : (error ?? "Download this conversation as a PDF");

  return (
    <button
      type="button"
      onClick={exportPdf}
      disabled={unavailable || busy}
      title={title}
      aria-live="polite"
      className={`flex h-8 shrink-0 items-center rounded-md border px-2.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        error
          ? "border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-500"
          : "border-gray-200 text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
      }`}
    >
      {busy ? "Exporting…" : error ? "Export failed" : "Export PDF"}
    </button>
  );
}
