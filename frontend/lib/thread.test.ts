import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearThreadCookie,
  newThreadId,
  readThreadCookie,
  setThreadCookie,
  THREAD_COOKIE,
} from "./thread";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  clearThreadCookie();
  vi.unstubAllGlobals();
});

describe("thread cookie", () => {
  it("round-trips a thread id", () => {
    setThreadCookie("thread-1");
    expect(readThreadCookie()).toBe("thread-1");
  });

  it("reads nothing when no thread is set", () => {
    expect(readThreadCookie()).toBeNull();
  });

  it("clearing removes it", () => {
    setThreadCookie("thread-1");
    clearThreadCookie();
    expect(readThreadCookie()).toBeNull();
  });

  it("is not confused by another cookie whose name ends the same way", () => {
    // A prefix match would read "decoy" here and switch the UI to a
    // conversation the user never opened.
    document.cookie = `other_${THREAD_COOKIE}=decoy; Path=/`;
    setThreadCookie("thread-1");

    expect(readThreadCookie()).toBe("thread-1");

    document.cookie = `other_${THREAD_COOKIE}=; Path=/; Max-Age=0`;
  });
});

describe("newThreadId", () => {
  it("uses crypto.randomUUID when it is available", () => {
    const randomUUID = vi.fn(() => "11111111-1111-4111-8111-111111111111");
    vi.stubGlobal("crypto", { ...globalThis.crypto, randomUUID });

    expect(newThreadId()).toBe("11111111-1111-4111-8111-111111111111");
    expect(randomUUID).toHaveBeenCalled();
  });

  it("still mints a valid v4 uuid where randomUUID is undefined", () => {
    // REGRESSION GUARD: crypto.randomUUID only exists in a secure context, and
    // this app is served over plain http on a LAN address. Calling it there
    // throws, which would break "New chat" on the actual deployment while
    // working perfectly on localhost.
    vi.stubGlobal("crypto", {
      getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto),
    });

    expect(newThreadId()).toMatch(UUID_V4);
  });

  it("does not repeat itself", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto),
    });

    const ids = new Set(Array.from({ length: 50 }, () => newThreadId()));
    expect(ids.size).toBe(50);
  });
});
