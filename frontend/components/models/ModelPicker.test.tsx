import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelCatalog } from "@/lib/models";

let isRunning = false;

// useThread only works inside a runtime provider. Stubbed so these tests are
// about the picker, with the run state driven directly.
vi.mock("@assistant-ui/react", () => ({
  useThread: (select: (t: { isRunning: boolean }) => boolean) =>
    select({ isRunning }),
}));

import { ModelPicker } from "./ModelPicker";

const model = (tag: string, overrides: Partial<ModelCatalog["models"][number]> = {}) => ({
  tag,
  roles: [],
  tools: true,
  resident: false,
  current: false,
  default: false,
  ...overrides,
});

let current = "qwen3:8b";
let selectStatus = 200;

function mockBackend() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/models/current" && init?.method === "PUT") {
      if (selectStatus !== 200) {
        return {
          ok: false,
          status: selectStatus,
          json: async () => ({ detail: "qwen2.5vl:7b cannot call tools" }),
        };
      }
      current = JSON.parse(String(init.body)).model;
      return { ok: true, status: 200, json: async () => ({ current, default: "qwen3:8b" }) };
    }
    if (url === "/api/models/current") {
      return { ok: true, status: 200, json: async () => ({ current, default: "qwen3:8b" }) };
    }
    const catalog: ModelCatalog = {
      current,
      default: "qwen3:8b",
      reachable: true,
      models: [
        model("gemma4:26b"),
        model("qwen2.5vl:7b", { tools: false }),
        model("qwen3:8b", { roles: ["fast"], default: true, resident: true }),
      ],
    };
    return { ok: true, status: 200, json: async () => catalog };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  isRunning = false;
  current = "qwen3:8b";
  selectStatus = 200;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelPicker", () => {
  it("shows the current model without asking for the list", async () => {
    const fetchMock = mockBackend();
    render(<ModelPicker />);

    expect(await screen.findByRole("button", { name: "Model: qwen3:8b" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/api/models/current"]);
  });

  it("lists the models when opened", async () => {
    mockBackend();
    render(<ModelPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Model: qwen3:8b" }));

    expect(await screen.findByText("gemma4:26b")).toBeInTheDocument();
    expect(screen.getByText("fast · default · loaded")).toBeInTheDocument();
  });

  it("switches to the chosen model", async () => {
    mockBackend();
    render(<ModelPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Model: qwen3:8b" }));
    fireEvent.click(await screen.findByText("gemma4:26b"));

    expect(await screen.findByRole("button", { name: "Model: gemma4:26b" })).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("will not offer a model that cannot call tools", async () => {
    mockBackend();
    render(<ModelPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Model: qwen3:8b" }));

    const option = (await screen.findByText("qwen2.5vl:7b")).closest("button");
    expect(option).toBeDisabled();
  });

  it("shows why the backend refused a switch", async () => {
    mockBackend();
    selectStatus = 400;
    render(<ModelPicker />);
    fireEvent.click(await screen.findByRole("button", { name: "Model: qwen3:8b" }));
    fireEvent.click(await screen.findByText("gemma4:26b"));

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot call tools");
  });

  it("is disabled while an answer is streaming", async () => {
    mockBackend();
    isRunning = true;
    render(<ModelPicker />);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("picks up a switch the agent made during a run", async () => {
    mockBackend();
    isRunning = true;
    const { rerender } = render(<ModelPicker />);

    // The agent called switch_model while answering.
    current = "gemma4:26b";
    isRunning = false;
    await act(async () => rerender(<ModelPicker />));

    expect(await screen.findByRole("button", { name: "Model: gemma4:26b" })).toBeInTheDocument();
  });
});
