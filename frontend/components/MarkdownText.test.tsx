import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownText } from "./MarkdownText";

/**
 * Renders one finished assistant message through a real runtime, so the text
 * goes through the same plugins and component overrides as in the chat.
 */
function Answer({ text }: { text: string }) {
  const messages: ThreadMessageLike[] = [
    { role: "assistant", content: [{ type: "text", text }] },
  ];
  const runtime = useExternalStoreRuntime({
    messages,
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

const TABLE = [
  "Here is the comparison:",
  "",
  "| Model | Size | Tools |",
  "|:------|-----:|:-----:|",
  "| qwen3 | 8B | yes |",
  "| gemma4 | 12B | no |",
].join("\n");

describe("MarkdownText", () => {
  it("renders a pipe table as a table", async () => {
    render(<Answer text={TABLE} />);

    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((th) => th.textContent)).toEqual(["Model", "Size", "Tools"]);

    const rows = within(table).getAllByRole("row");
    // Header row plus two body rows.
    expect(rows).toHaveLength(3);
    expect(within(rows[2]!).getAllByRole("cell").map((td) => td.textContent)).toEqual([
      "gemma4",
      "12B",
      "no",
    ]);
    // No leftover pipes means nothing fell through as plain text.
    expect(screen.queryByText(/\|/)).toBeNull();
  });

  it("keeps the table styled and lets a wide one scroll", async () => {
    render(<Answer text={TABLE} />);

    const table = await screen.findByRole("table");
    expect(table).toHaveClass("aui-md-table");
    expect(table.parentElement).toHaveClass("overflow-x-auto");
    // Alignment from the delimiter row reaches the cells. react-markdown 9
    // sets it as an inline style rather than the old align attribute.
    expect(within(table).getAllByRole("columnheader")[1]).toHaveStyle({
      textAlign: "right",
    });
    expect(within(table).getAllByRole("cell")[2]).toHaveStyle({
      textAlign: "center",
    });
  });

  it("does not strike out text between two approximate numbers", async () => {
    render(<Answer text="A cold load takes ~6s for an 8B and ~19s for a 14B." />);

    await screen.findByText(/cold load/);
    expect(document.querySelector("del")).toBeNull();
  });

  it("still strikes out double-tilde text", async () => {
    render(<Answer text="This is ~~wrong~~ right." />);

    expect((await screen.findByText("wrong")).tagName).toBe("DEL");
  });

  it("still renders math and section citations alongside tables", async () => {
    render(<Answer text={"See [Section 148] where $x^2$ applies.\n\n" + TABLE} />);

    await screen.findByRole("table");
    expect(document.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText(/Section 148/)).toBeInTheDocument();
  });
});
