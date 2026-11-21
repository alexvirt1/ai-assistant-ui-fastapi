"use client";

import { useThread } from "@assistant-ui/react";
import { useEffect, useRef } from "react";

import { endedUnfinished, type LiveMessage } from "@/lib/recovery";

/**
 * Tells the layout when a turn stopped running without finishing (see
 * lib/recovery.ts). Renders nothing; lives inside the runtime provider because
 * useThread does, like ThreadActivity.
 */
export function StreamRecovery({ onUnfinished }: { onUnfinished: () => void }) {
  const isRunning = useThread((t) => t.isRunning);
  const lastMessage = useThread((t) => t.messages[t.messages.length - 1]) as
    | LiveMessage
    | undefined;
  const wasRunning = useRef(isRunning);

  useEffect(() => {
    const ended = wasRunning.current && !isRunning;
    wasRunning.current = isRunning;
    if (ended && endedUnfinished(lastMessage)) onUnfinished();
  }, [isRunning, lastMessage, onUnfinished]);

  return null;
}
