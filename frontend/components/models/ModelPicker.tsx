"use client";

import { useThread } from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchCurrentModel,
  listModels,
  selectModel,
  type ModelCatalog,
} from "@/lib/models";

/**
 * Shows which model is answering, and switches it.
 *
 * The button carries only the current model, which the backend answers without
 * touching the VM. The list is fetched when the menu opens: building it asks
 * the VM about every tag it holds, and is stale the moment a model is pulled.
 *
 * The agent can switch models itself when asked in chat, so the current model
 * is refetched whenever a run ends - otherwise the header would keep naming
 * the model it switched away from.
 */
export function ModelPicker() {
  const isRunning = useThread((t) => t.isRunning);
  const [current, setCurrent] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const refreshCurrent = useCallback((signal?: AbortSignal) => {
    fetchCurrentModel(signal)
      .then((model) => setCurrent(model.current))
      .catch(() => {
        // Leave whatever was shown: a failed refresh is not a reason to claim
        // the model is unknown.
      });
  }, []);

  // On mount and at the end of every run. Not at the start: nothing can have
  // switched it yet.
  useEffect(() => {
    if (isRunning) return;
    const controller = new AbortController();
    refreshCurrent(controller.signal);
    return () => controller.abort();
  }, [isRunning, refreshCurrent]);

  // Fetched per opening rather than kept, so a model pulled since the last
  // look appears, and the "loaded" marks reflect what the VM holds now.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    listModels(controller.signal)
      .then((result) => {
        setCatalog(result);
        setCurrent(result.current);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Loading models failed");
      });
    return () => controller.abort();
  }, [open]);

  // Close on a click elsewhere or Escape, as a menu is expected to.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const toggle = () => {
    if (!open) {
      setCatalog(null);
      setError(null);
    }
    setOpen(!open);
  };

  const choose = async (tag: string) => {
    setSwitching(true);
    setError(null);
    try {
      const model = await selectModel(tag);
      setCurrent(model.current);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Switching model failed");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={toggle}
        // Switching mid-run would hand the rest of the answer to another model
        // after a cold load, so wait for the run to end.
        disabled={isRunning}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={current ? `Model: ${current}` : "Choose model"}
        title={isRunning ? "Model can be changed once the answer finishes" : undefined}
        className="flex h-8 max-w-[16rem] items-center gap-1.5 rounded-md border border-gray-200 px-2.5 text-xs text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
      >
        <span className="min-w-0 truncate font-medium">{current ?? "Model"}</span>
        <span aria-hidden className="text-gray-400">
          ▾
        </span>
      </button>

      {open ? (
        <div className="absolute right-0 z-20 mt-1 max-h-[24rem] w-80 overflow-y-auto rounded-md border border-gray-200 bg-white p-1 shadow-lg dark:border-gray-700 dark:bg-gray-900">
          {error ? (
            <p role="alert" className="px-2 py-2 text-xs text-amber-700 dark:text-amber-500">
              {error}
            </p>
          ) : null}
          {!catalog && !error ? (
            <p className="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
              Loading models…
            </p>
          ) : null}
          {catalog && !catalog.reachable ? (
            <p className="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
              The Ollama VM is unreachable.
            </p>
          ) : null}
          {catalog?.reachable ? (
            <ul role="listbox" aria-label="Available models" className="space-y-0.5">
              {catalog.models.map((model) => {
                const isCurrent = model.tag === current;
                return (
                  <li key={model.tag} role="option" aria-selected={isCurrent}>
                    <button
                      type="button"
                      onClick={() => choose(model.tag)}
                      disabled={switching || isCurrent || !model.tools}
                      title={model.tools ? undefined : "Cannot call tools, which this assistant needs"}
                      className={`w-full rounded px-2 py-1.5 text-left transition-colors disabled:cursor-default ${
                        isCurrent
                          ? "bg-gray-100 dark:bg-gray-800"
                          : "hover:bg-gray-100 disabled:opacity-50 disabled:hover:bg-transparent dark:hover:bg-gray-800"
                      }`}
                    >
                      <span className="flex items-center gap-1.5">
                        <span
                          aria-hidden
                          className={`size-1.5 shrink-0 rounded-full ${
                            model.resident ? "bg-emerald-500" : "bg-transparent"
                          }`}
                        />
                        <span className="min-w-0 truncate text-xs font-medium text-gray-700 dark:text-gray-200">
                          {model.tag}
                        </span>
                        {isCurrent ? (
                          <span className="ml-auto shrink-0 text-xs text-gray-500">✓</span>
                        ) : null}
                      </span>
                      <span className="block pl-3 text-[11px] text-gray-400 dark:text-gray-500">
                        {describe(model)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function describe(model: ModelCatalog["models"][number]): string {
  const notes: string[] = [];
  if (model.roles.length) notes.push(model.roles.join(", "));
  if (model.default) notes.push("default");
  if (model.resident) notes.push("loaded");
  if (!model.tools) notes.push("no tool support");
  return notes.join(" · ") || " ";
}
