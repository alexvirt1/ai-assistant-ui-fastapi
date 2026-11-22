import { beforeEach, describe, expect, it } from "vitest";

import { formatUsd, getHybrid, requestFields, resetHybrid, setHybrid, transcriptForClaude } from "./hybrid";

beforeEach(() => resetHybrid());

describe("requestFields", () => {
  it("is read when the request is built, not when the body was made", () => {
    // What MyAssistant passes to the runtime: the fields copied as getters.
    const body = Object.defineProperties(
      { threadId: "t1" },
      Object.getOwnPropertyDescriptors(requestFields),
    );
    setHybrid({ mode: "auto", escalateNext: true });
    // The edge runtime spreads the body into the request at send time.
    const sent = JSON.parse(JSON.stringify({ ...body }));
    expect(sent).toEqual({ threadId: "t1", mode: "auto", allowCloudDocuments: false, escalate: true });
  });

  it("resets per chat", () => {
    setHybrid({ mode: "cloud", allowDocuments: true, escalateNext: true });
    resetHybrid("auto");
    expect(getHybrid()).toEqual({ mode: "auto", allowDocuments: false, escalateNext: false });
  });
});

describe("transcriptForClaude", () => {
  it("keeps the text of both sides and skips tool parts", () => {
    const text = transcriptForClaude([
      { role: "user", content: [{ type: "text", text: "What is 2+2?" }] },
      { role: "assistant", content: [{ type: "tool-call" }, { type: "text", text: "4" }] },
    ]);
    expect(text).toContain("User: What is 2+2?");
    expect(text).toContain("Assistant (local model): 4");
    expect(text).not.toContain("tool-call");
  });
});

describe("formatUsd", () => {
  it("shows small amounts precisely", () => {
    expect(formatUsd(0.0062)).toBe("$0.0062");
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatUsd(0)).toBe("$0.00");
  });
});
