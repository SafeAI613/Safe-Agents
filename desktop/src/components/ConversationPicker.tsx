import type { Client } from "@langchain/langgraph-sdk";

import { conversationTitle } from "../lib/threads";
import { useAsync } from "../lib/useAsync";

/** Earlier conversations with this agent, newest first (stored by the agent server). */
export function ConversationPicker({
  client,
  threadId,
  refreshKey,
  onSelect,
}: {
  client: Client;
  threadId: string | null;
  refreshKey: number;
  onSelect: (threadId: string | null) => void;
}) {
  const threads = useAsync(
    () => client.threads.search({ limit: 30, sortBy: "updated_at", sortOrder: "desc" }),
    [client, refreshKey],
  );
  const options = (threads.data ?? [])
    .map((thread) => ({
      id: thread.thread_id,
      title: conversationTitle(thread.values),
      when: new Date(thread.updated_at).toLocaleString("he-IL", {
        dateStyle: "short",
        timeStyle: "short",
      }),
    }))
    .filter((option) => option.title !== null);

  return (
    <label>
      שיחות:{" "}
      <select
        aria-label="שיחות קודמות"
        value={threadId && options.some((o) => o.id === threadId) ? threadId : ""}
        onChange={(event) => onSelect(event.target.value || null)}
      >
        <option value="">שיחה חדשה</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.title} · {option.when}
          </option>
        ))}
      </select>
    </label>
  );
}
