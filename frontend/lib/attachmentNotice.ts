/**
 * Why a file couldn't be attached, for the composer to show.
 *
 * assistant-ui calls the adapter's add() from the paperclip's file input and
 * drops a rejection on the floor: a refused file simply never appears, with no
 * reason given. Phones make that common - their pickers ignore the `accept`
 * filter, so any file can be chosen. The adapter reports here instead.
 */

type Listener = () => void;

let notice: string | null = null;
const listeners = new Set<Listener>();

export function getAttachmentNotice(): string | null {
  return notice;
}

export function showAttachmentNotice(message: string): void {
  notice = message;
  listeners.forEach((listener) => listener());
}

export function clearAttachmentNotice(): void {
  if (notice === null) return;
  notice = null;
  listeners.forEach((listener) => listener());
}

export function subscribeAttachmentNotice(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
