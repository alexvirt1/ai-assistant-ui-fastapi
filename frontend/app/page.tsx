import { ChatShell } from "@/components/chats/ChatShell";
import { readAttachmentLimits } from "@/lib/attachments";

// Rendered dynamically so the attachment limits are read from the environment
// per request rather than frozen into a prerendered page at build time — the
// point of putting them in .env.local is to change them without rebuilding.
export const dynamic = "force-dynamic";

export default function Home() {
  const attachmentLimits = readAttachmentLimits();

  return (
    <main className="h-dvh">
      <ChatShell attachmentLimits={attachmentLimits} />
    </main>
  );
}
