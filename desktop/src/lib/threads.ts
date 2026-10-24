interface StoredMessage {
  type?: string;
  content?: unknown;
}

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block: unknown) =>
        block && typeof block === "object" && "text" in block ? String(block.text) : "",
      )
      .join("");
  }
  return "";
}

/** A short title for a stored conversation: its first user message. */
export function conversationTitle(values: unknown, maxLength = 60): string | null {
  const messages = (values as { messages?: StoredMessage[] } | null)?.messages;
  const first = messages?.find((message) => message.type === "human");
  if (!first) return null;
  const text = contentText(first.content).replace(/\s+/g, " ").trim();
  if (!text) return null;
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}
