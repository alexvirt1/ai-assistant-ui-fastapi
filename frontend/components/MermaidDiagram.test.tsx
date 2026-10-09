import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mermaid needs a real layout engine to measure text, which jsdom does not
// have. Stubbed: these tests are about when and how the component asks for a
// diagram, not about Mermaid's drawing.
const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  parse: vi.fn<(code: string, options?: unknown) => Promise<boolean>>(async () => true),
  render: vi.fn<(id: string, code: string) => Promise<{ svg: string }>>(async () => ({
    svg: '<svg data-testid="diagram"><text>drawn</text></svg>',
  })),
}));
vi.mock("mermaid", () => ({ default: mermaid }));

let theme = "light";
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: theme }) }));

import { MarkdownText } from "./MarkdownText";
import { mermaidSources } from "@/lib/mermaid";

import { fenceClosed } from "./MermaidDiagram";

const FLOW = "graph TD\n  A[Start] --> B{Ok?}\n  B -->|yes| C[Done]\n";

function Answer({ text, running = false }: { text: string; running?: boolean }) {
  const messages: ThreadMessageLike[] = [
    {
      role: "assistant",
      content: [{ type: "text", text }],
      status: running ? { type: "running" } : { type: "complete", reason: "stop" },
    },
  ];
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: running,
    convertMessage: (message: ThreadMessageLike) => message,
    onNew: async () => {},
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Messages
        components={{
          UserMessage: () => null,
          AssistantMessage: () => (
            <MessagePrimitive.Content components={{ Text: MarkdownText }} />
          ),
        }}
      />
    </AssistantRuntimeProvider>
  );
}

beforeEach(() => {
  theme = "light";
  mermaid.parse.mockResolvedValue(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("MermaidDiagram", () => {
  it("draws a finished mermaid block as a diagram", async () => {
    render(<Answer text={"Here:\n\n```mermaid\n" + FLOW + "```\n\nDone."} />);

    expect(await screen.findByTestId("diagram")).toBeInTheDocument();
    expect(mermaid.render).toHaveBeenCalledWith(expect.stringMatching(/^mermaid-/), FLOW);
    // Strict: the diagram is model output and must not carry script.
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ securityLevel: "strict", startOnLoad: false }),
    );
    // Its source is not shown alongside.
    expect(screen.queryByText(/A\[Start\]/)).toBeNull();
  });

  it("waits for the closing fence while the answer streams", async () => {
    // Half a diagram: Mermaid would fail on it, or draw the wrong thing.
    render(<Answer running text={"```mermaid\ngraph TD\n  A[Start] -->"} />);

    expect(await screen.findByText(/A\[Start\] -->/)).toBeInTheDocument();
    await act(async () => {});
    expect(mermaid.render).not.toHaveBeenCalled();
  });

  it("draws as soon as the fence closes, before the answer ends", async () => {
    render(<Answer running text={"```mermaid\n" + FLOW + "```\n\nAnd then"} />);

    expect(await screen.findByTestId("diagram")).toBeInTheDocument();
  });

  it("shows the source and says why when the diagram is invalid", async () => {
    mermaid.parse.mockResolvedValue(false);
    render(<Answer text={"```mermaid\ngraph TD\n  A --> \n```"} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't draw this diagram: The diagram has a syntax error.",
    );
    expect(screen.getByText(/graph TD/)).toBeInTheDocument();
    // render() on bad input leaves an error graphic on <body>; never called.
    expect(mermaid.render).not.toHaveBeenCalled();
  });

  it("switches between the diagram and its source", async () => {
    render(<Answer text={"```mermaid\n" + FLOW + "```"} />);
    await screen.findByTestId("diagram");

    await act(async () => screen.getByRole("button", { name: "Show code" }).click());
    expect(screen.getByText(/A\[Start\]/)).toBeInTheDocument();
    expect(screen.queryByTestId("diagram")).toBeNull();

    await act(async () => screen.getByRole("button", { name: "Show diagram" }).click());
    expect(screen.getByTestId("diagram")).toBeInTheDocument();
  });

  it("copies the source", async () => {
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Answer text={"```mermaid\n" + FLOW + "```"} />);
    await screen.findByTestId("diagram");

    await act(async () => screen.getByRole("button", { name: "Copy" }).click());
    expect(writeText).toHaveBeenCalledWith(FLOW);
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("enlarges the diagram on click and closes on Escape or a click outside", async () => {
    render(<Answer text={"```mermaid\n" + FLOW + "```"} />);
    await screen.findByTestId("diagram");

    await act(async () => screen.getByRole("button", { name: "Enlarge diagram" }).click());
    const dialog = screen.getByRole("dialog", { name: "Enlarged diagram" });
    expect(dialog).toContainElement(screen.getAllByTestId("diagram")[1]!);

    // A click on the diagram itself keeps it open.
    await act(async () => screen.getByRole("img", { name: "Diagram" }).click());
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();

    await act(async () => screen.getByRole("button", { name: "Enlarge diagram" }).click());
    await act(async () => screen.getByRole("dialog").click());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("follows dark mode", async () => {
    theme = "dark";
    render(<Answer text={"```mermaid\n" + FLOW + "```"} />);

    await screen.findByTestId("diagram");
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ theme: "dark" }),
    );
  });

  it("leaves other code blocks as code", async () => {
    render(<Answer text={"```python\nprint('graph TD')\n```"} />);

    expect(await screen.findByText(/print\('graph TD'\)/)).toBeInTheDocument();
    await act(async () => {});
    expect(mermaid.render).not.toHaveBeenCalled();
    expect(screen.queryByText("Diagram")).toBeNull();
  });

  it("draws several diagrams in one answer, one at a time", async () => {
    let active = 0;
    let overlapped = false;
    mermaid.render.mockImplementation(async () => {
      active += 1;
      if (active > 1) overlapped = true;
      await new Promise((resolve) => setTimeout(resolve, 5));
      active -= 1;
      return { svg: '<svg data-testid="diagram"></svg>' };
    });
    render(
      <Answer
        text={"```mermaid\n" + FLOW + "```\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```"}
      />,
    );

    await waitFor(() => expect(screen.getAllByTestId("diagram")).toHaveLength(2));
    // initialize() is global; overlapping renders could swap themes.
    expect(overlapped).toBe(false);
  });
});

describe("fenceClosed", () => {
  it("is false while the block is still open", () => {
    expect(fenceClosed("```mermaid\ngraph TD\n  A -->", "graph TD\n  A -->")).toBe(false);
  });

  it("is true once the closing fence follows the code", () => {
    expect(fenceClosed("```mermaid\ngraph TD\n```\nmore", "graph TD\n")).toBe(true);
  });

  it("is false when the code is not in the text", () => {
    expect(fenceClosed("something else", "graph TD\n")).toBe(false);
  });
});

describe("mermaidSources", () => {
  it("finds each finished mermaid block, trimmed, and nothing else", () => {
    const text =
      "Intro\n\n```mermaid\n" +
      FLOW +
      "```\n\n```python\nprint(1)\n```\n\n````mermaid\nsequenceDiagram\n  A->>B: hi\n````\n";
    expect(mermaidSources(text)).toEqual([FLOW.trim(), "sequenceDiagram\n  A->>B: hi"]);
  });

  it("skips a block that never closes", () => {
    expect(mermaidSources("```mermaid\ngraph TD\n  A -->")).toEqual([]);
  });
});
