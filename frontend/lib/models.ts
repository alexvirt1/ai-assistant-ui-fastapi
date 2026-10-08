/**
 * Client for the chat-model selection: which model answers, what else the VM
 * can run, and switching between them.
 *
 * The selection is global on the backend - one model for every chat - because
 * the VM serves one model at a time. The agent can also switch it from inside
 * a conversation, so nothing here caches it.
 */

/** Mirrors ModelInfo in backend/app/models/selection.py. */
export type ModelInfo = {
  tag: string;
  /** Roles in models.yaml pointing at this tag, e.g. ["fast"]. */
  roles: string[];
  /** False when the model cannot call tools, which the agent requires. */
  tools: boolean;
  /** Held in memory on the VM, so switching costs no cold load. */
  resident: boolean;
  current: boolean;
  default: boolean;
};

export type CurrentModel = { current: string; default: string };

export type ModelCatalog = CurrentModel & {
  /** False when the VM could not be reached; models is then empty. */
  reachable: boolean;
  models: ModelInfo[];
};

async function json<T>(response: Response, what: string): Promise<T> {
  if (!response.ok) {
    // The backend explains a refused switch in `detail` ("cannot call tools"),
    // which is worth more to the user than a status code.
    let detail: string | undefined;
    try {
      detail = ((await response.json()) as { detail?: string }).detail;
    } catch {
      // Not JSON - fall through to the status.
    }
    throw new Error(detail ?? `${what} failed (${response.status})`);
  }
  return (await response.json()) as T;
}

/** The model in use. Cheap: the backend answers without asking the VM. */
export async function fetchCurrentModel(
  signal?: AbortSignal,
): Promise<CurrentModel> {
  const response = await fetch("/api/models/current", {
    signal,
    headers: { accept: "application/json" },
  });
  return json<CurrentModel>(response, "Loading the current model");
}

/** Chat-capable models on the VM. Asks the VM, so only fetched on demand. */
export async function listModels(signal?: AbortSignal): Promise<ModelCatalog> {
  const response = await fetch("/api/models", {
    signal,
    headers: { accept: "application/json" },
  });
  return json<ModelCatalog>(response, "Loading models");
}

/** Switch to a tag, a role name, or "default". */
export async function selectModel(model: string): Promise<CurrentModel> {
  const response = await fetch("/api/models/current", {
    method: "PUT",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ model }),
  });
  return json<CurrentModel>(response, "Switching model");
}
