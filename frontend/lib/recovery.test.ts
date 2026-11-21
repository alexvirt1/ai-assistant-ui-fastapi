import { describe, expect, it, vi } from "vitest";

import type { RestoredMessage } from "./chats";
import { endedUnfinished, turnSettled, waitForSettledTurn } from "./recovery";

const tool = (result?: unknown) => ({ type: "tool-call", result });

describe("endedUnfinished", () => {
  it("flags a tool call left without a result", () => {
    expect(endedUnfinished({ role: "assistant", status: { type: "complete" }, content: [tool("{}"), tool()] })).toBe(true);
  });

  it("flags a run that failed", () => {
    expect(endedUnfinished({ role: "assistant", status: { type: "incomplete", reason: "error" }, content: [] })).toBe(true);
  });

  it("leaves finished and cancelled turns alone", () => {
    expect(endedUnfinished({ role: "assistant", status: { type: "complete" }, content: [tool("{}"), { type: "text" }] })).toBe(false);
    expect(endedUnfinished({ role: "assistant", status: { type: "incomplete", reason: "cancelled" }, content: [tool()] })).toBe(false);
    expect(endedUnfinished({ role: "user", content: [] })).toBe(false);
    expect(endedUnfinished(undefined)).toBe(false);
  });
});

const assistant = (content: RestoredMessage["content"]): RestoredMessage =>
  ({ role: "assistant", content }) as RestoredMessage;
const call = (result?: unknown) =>
  ({ type: "tool-call", toolCallId: "c", toolName: "generate_image", args: {}, ...(result === undefined ? {} : { result }) }) as const;

describe("turnSettled", () => {
  it("needs every result and the reply", () => {
    expect(turnSettled([assistant([call("{}"), { type: "text", text: "done" }])])).toBe(true);
    expect(turnSettled([assistant([call("{}")])])).toBe(false);
    expect(turnSettled([assistant([call(), { type: "text", text: "done" }])])).toBe(false);
    expect(turnSettled([{ role: "user", content: [{ type: "text", text: "hi" }] }])).toBe(false);
  });
});

describe("waitForSettledTurn", () => {
  it("polls until settled, riding out failed fetches", async () => {
    const settled = [assistant([call("{}"), { type: "text", text: "done" }])];
    const fetchMessages = vi
      .fn()
      .mockRejectedValueOnce(new Error("restarting"))
      .mockResolvedValueOnce([assistant([call()])])
      .mockResolvedValueOnce(settled);
    const result = await waitForSettledTurn(fetchMessages, new AbortController().signal, { pollMs: 1 });
    expect(result).toBe(settled);
    expect(fetchMessages).toHaveBeenCalledTimes(3);
  });

  it("gives up with the latest transcript", async () => {
    const pending = [assistant([call()])];
    const result = await waitForSettledTurn(async () => pending, new AbortController().signal, { pollMs: 1, timeoutMs: 0 });
    expect(result).toBe(pending);
  });

  it("returns null when aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    expect(await waitForSettledTurn(async () => [], controller.signal)).toBeNull();
  });
});
