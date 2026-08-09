"use client";

import { useThread } from "@assistant-ui/react";
import { useEffect, useRef } from "react";

/**
 * Reports run start/stop out of the runtime to whoever owns the layout.
 *
 * Renders nothing. It exists because `useThread` only works inside
 * AssistantRuntimeProvider, while the sidebar - which needs to know a run is
 * in progress, and to refetch when one ends - lives outside it.
 */
export function ThreadActivity({
  onRunningChange,
}: {
  onRunningChange: (isRunning: boolean) => void;
}) {
  const isRunning = useThread((t) => t.isRunning);
  const previous = useRef(isRunning);

  useEffect(() => {
    // Guarded on an actual transition, not just an effect run: the callback
    // identity changes on every parent render, and firing "a run ended" again
    // on each of those would refetch the chat list continuously.
    if (isRunning === previous.current) return;
    previous.current = isRunning;
    onRunningChange(isRunning);
  }, [isRunning, onRunningChange]);

  return null;
}
