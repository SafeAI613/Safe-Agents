import type { BaseMessage } from "@langchain/core/messages";

import type { UsageItem } from "./types";

export type Role = "human" | "ai" | "tool" | "system" | "other";

export function roleOf(message: BaseMessage): Role {
  switch (message.getType()) {
    case "human":
      return "human";
    case "ai":
      return "ai";
    case "tool":
      return "tool";
    case "system":
      return "system";
    default:
      return "other";
  }
}

/** Plain text of a message whose content may be a string or a list of content blocks. */
export function textOf(message: BaseMessage): string {
  const content: unknown = message.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block: unknown) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object" && "text" in block) {
          return String((block as { text: unknown }).text);
        }
        return "";
      })
      .join("");
  }
  return "";
}

interface UsageCarrier {
  type?: string;
  id?: string;
  usage_metadata?: { input_tokens?: number; output_tokens?: number } | null;
  response_metadata?: { model_name?: string; model?: string };
}

function isAi(message: unknown): boolean {
  if (!message || typeof message !== "object") return false;
  const withType = message as { getType?: () => string; type?: string };
  return (typeof withType.getType === "function" ? withType.getType() : withType.type) === "ai";
}

/**
 * Token usage of AI messages, from `BaseMessage` instances or stored message dicts. Needs the
 * model to report usage (with ChatOpenAI: `stream_usage=True`, see the agent contract).
 */
export function usageItems(messages: readonly unknown[], fallbackModel: string): UsageItem[] {
  const items: UsageItem[] = [];
  for (const message of messages) {
    if (!isAi(message)) continue;
    const carrier = message as UsageCarrier;
    const usage = carrier.usage_metadata;
    if (!carrier.id || !usage) continue;
    items.push({
      message_id: carrier.id,
      model:
        carrier.response_metadata?.model_name ?? carrier.response_metadata?.model ?? fallbackModel,
      input_tokens: usage.input_tokens ?? 0,
      output_tokens: usage.output_tokens ?? 0,
    });
  }
  return items;
}

const ROLE_TITLES: Record<Role, string> = {
  human: "משתמש",
  ai: "אייג'נט",
  tool: "כלי",
  system: "מערכת",
  other: "הודעה",
};

export function conversationMarkdown(
  agentName: string,
  messages: BaseMessage[],
  when = new Date(),
) {
  const lines = [`# שיחה עם ${agentName}`, "", `תאריך: ${when.toLocaleString("he-IL")}`, ""];
  for (const message of messages) {
    const text = textOf(message).trim();
    if (!text) continue;
    lines.push(`## ${ROLE_TITLES[roleOf(message)]}`, "", text, "");
  }
  return lines.join("\n");
}

export function formatUsd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(2)}`;
}
