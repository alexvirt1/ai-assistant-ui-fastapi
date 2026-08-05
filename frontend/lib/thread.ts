/**
 * Name of the cookie that carries the LangGraph thread id.
 *
 * The chat proxy route reads it to decide which stored conversation a request
 * belongs to, and the sidebar rewrites it when you switch chats. Both sides
 * import this so the name cannot drift.
 *
 * Deliberately not httpOnly: the document store and the chat sidebar both read
 * it from the browser to tell which conversation is on screen.
 */
export const THREAD_COOKIE = "assistant_thread_id";

const ONE_YEAR_SECONDS = 31536000;

/** The thread id the browser is currently pointed at, or null before one exists. */
export function readThreadCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${THREAD_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]!) : null;
}

/**
 * Point the browser at a conversation.
 *
 * Written client-side when switching chats, rather than waiting for the proxy
 * route's Set-Cookie on the next request: the document store reads this cookie
 * to decide which conversation's attachments to restore, and it has to be
 * right *before* the switch finishes, not after the next message is sent.
 */
export function setThreadCookie(threadId: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${THREAD_COOKIE}=${encodeURIComponent(threadId)}; Path=/; SameSite=Lax; Max-Age=${ONE_YEAR_SECONDS}`;
}

export function clearThreadCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${THREAD_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/**
 * A fresh thread id, minted in the browser.
 *
 * Client-side rather than waiting for the server's Set-Cookie because the
 * sidebar needs the id immediately, to highlight the new chat and to send it
 * with the first message.
 *
 * crypto.randomUUID() is unavailable outside a secure context, and this app is
 * served over plain http on a LAN address - so on the deployment it is
 * actually used from, `crypto.randomUUID` is undefined and calling it would
 * throw on every "New chat". crypto.getRandomValues carries no such
 * restriction, so the fallback is still properly random rather than
 * Math.random.
 */
export function newThreadId(): string {
  const webcrypto = globalThis.crypto;

  if (typeof webcrypto?.randomUUID === "function") {
    return webcrypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  webcrypto.getRandomValues(bytes);
  // Version 4, variant 1, per RFC 4122.
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}
