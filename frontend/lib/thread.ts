/**
 * Name of the cookie that carries the LangGraph thread id.
 *
 * The chat proxy route reads it to decide which stored conversation a request
 * belongs to, and the "New chat" button clears it to start a fresh one. Both
 * sides import this so the name cannot drift.
 */
export const THREAD_COOKIE = "assistant_thread_id";
