import { Client } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";

import { AgentSecretsDialog } from "../components/AgentSecretsDialog";
import { ApprovalCard } from "../components/ApprovalCard";
import { ConversationPicker } from "../components/ConversationPicker";
import { ErrorBanner } from "../components/ErrorBanner";
import { useApi } from "../lib/context";
import { ApiError } from "../lib/errors";
import { conversationMarkdown, formatUsd, roleOf, textOf, usageItems } from "../lib/messages";
import type { InstalledAgent, StartedAgent, UsageSummary } from "../lib/types";
import { runStatus } from "../lib/runStatus";
import { useAsync } from "../lib/useAsync";

const HEARTBEAT_MS = 60_000;

export function ChatPage({ agentId, onBack }: { agentId: string; onBack: () => void }) {
  const api = useApi();
  const agent = useAsync(() => api.getAgent(agentId), [api, agentId]);
  const started = useAsync(() => api.startAgent(agentId), [api, agentId]);
  const [showSecrets, setShowSecrets] = useState(false);

  useEffect(() => {
    // Keeps the manager from stopping the agent as idle while the chat is open.
    const timer = setInterval(
      () => void api.heartbeat(agentId).catch(() => undefined),
      HEARTBEAT_MS,
    );
    return () => clearInterval(timer);
  }, [api, agentId]);

  const missingSecret =
    started.error instanceof ApiError && started.error.code === "missing_secret";
  return (
    <section className="chat-page">
      <header className="page-header">
        <button type="button" className="link" onClick={onBack}>
          → חזרה
        </button>
        <h1>{agent.data?.name ?? agentId}</h1>
      </header>
      <ErrorBanner error={agent.error} />
      {started.loading && <p className="banner">מפעיל את האייג'נט...</p>}
      {started.error !== undefined && (
        <>
          <ErrorBanner error={started.error} onRetry={started.reload} />
          {missingSecret && (
            <button type="button" className="primary" onClick={() => setShowSecrets(true)}>
              הזנת מפתחות
            </button>
          )}
        </>
      )}
      {agent.data && started.data && !started.loading && (
        <ChatSession agent={agent.data} started={started.data} />
      )}
      {showSecrets && agent.data && (
        <AgentSecretsDialog
          agentId={agentId}
          agentName={agent.data.name}
          onClose={() => {
            setShowSecrets(false);
            started.reload();
          }}
        />
      )}
    </section>
  );
}

interface Attachment {
  name: string;
  path: string;
}

function ChatSession({ agent, started }: { agent: InstalledAgent; started: StartedAgent }) {
  const api = useApi();
  const [threadId, setThreadId] = useState<string | null>(null);
  const [model, setModel] = useState(agent.default_model);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [usage, setUsage] = useState<UsageSummary>();
  const [notice, setNotice] = useState<string>();
  const [localError, setLocalError] = useState<unknown>();
  const [showHelp, setShowHelp] = useState(false);
  const [dragging, setDragging] = useState(false);

  // Async callbacks check the current conversation through a ref before showing results.
  const latest = useRef({ threadId });

  const [historyVersion, setHistoryVersion] = useState(0);
  const client = useMemo(
    () => new Client(api.agentConnection(started.proxy_path)),
    [api, started.proxy_path],
  );
  const stream = useStream({
    client,
    assistantId: started.graph_id,
    threadId,
    onThreadId: setThreadId,
    onCompleted: () => setHistoryVersion((v) => v + 1),
  });
  latest.current = { threadId };

  // Report token usage once a run has ended and whenever more of it arrives afterwards: the
  // content guard can deliver a checked message after the run's "completed" event.
  const reported = useRef("");
  useEffect(() => {
    if (stream.isLoading || !threadId) return;
    const items = usageItems(stream.messages, model);
    const key = `${threadId}:${items.map((item) => item.message_id).join(",")}`;
    if (items.length === 0 || key === reported.current) return;
    reported.current = key;
    api
      .recordUsage(agent.id, threadId, items)
      .then((summary) => {
        if (latest.current.threadId === threadId) setUsage(summary);
      })
      .catch(() => undefined); // cost tracking must never break the conversation
  }, [api, agent.id, model, stream.isLoading, stream.messages, threadId]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text && attachments.length === 0) return;
    const files = attachments.map((file) => file.path);
    const content = files.length ? `${text}\n\nקבצים מצורפים:\n${files.join("\n")}` : text;
    setInput("");
    setAttachments([]);
    setLocalError(undefined);
    await stream.submit(
      { messages: [{ type: "human", content }] },
      { config: { configurable: { model } } },
    );
  }

  async function upload(files: FileList | File[]) {
    setLocalError(undefined);
    for (const file of Array.from(files)) {
      try {
        const saved = await api.uploadFile(agent.id, file);
        setAttachments((previous) => [...previous, { name: file.name, path: saved.path }]);
      } catch (reason) {
        setLocalError(reason);
      }
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (agent.accepts_files.length) void upload(event.dataTransfer.files);
  }

  async function exportConversation() {
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
    try {
      const saved = await api.exportFile(
        `${agent.name}-${stamp}.md`,
        conversationMarkdown(agent.name, stream.messages),
      );
      setNotice(`השיחה נשמרה: ${saved.path}`);
    } catch (reason) {
      setLocalError(reason);
    }
  }

  function openConversation(id: string | null) {
    setThreadId(id);
    setNotice(undefined);
    setUsage(undefined);
    if (id) {
      // Show what the earlier conversation cost so far.
      api
        .usage(agent.id, id)
        // Ignore a late answer if the user has already moved to another conversation.
        .then((summary) => {
          if (latest.current.threadId === id && summary.input_tokens > 0) setUsage(summary);
        })
        .catch(() => undefined);
    }
  }

  function newConversation() {
    setThreadId(null);
    setUsage(undefined);
    setNotice(undefined);
  }

  const interrupt = stream.interrupt;
  const last = stream.messages.at(-1);
  const status = runStatus({
    isLoading: stream.isLoading,
    hasInterrupt: interrupt !== undefined,
    lastRole: last ? roleOf(last) : undefined,
    lastHasText: last ? textOf(last).length > 0 : false,
    runningTools: stream.toolCalls.filter((call) => call.status === "running").map((c) => c.name),
  });
  return (
    <div
      className="chat"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="toolbar">
        <label>
          מודל:{" "}
          <select value={model} onChange={(event) => setModel(event.target.value)} dir="ltr">
            {agent.models.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => setShowHelp(!showHelp)} aria-expanded={showHelp}>
          עזרה
        </button>
        <button
          type="button"
          onClick={() => void exportConversation()}
          disabled={!stream.messages.length}
        >
          ייצוא שיחה
        </button>
        <button type="button" onClick={newConversation} disabled={stream.isLoading}>
          שיחה חדשה
        </button>
        <ConversationPicker
          client={client}
          threadId={threadId}
          refreshKey={historyVersion}
          disabled={stream.isLoading}
          onSelect={(id) => (id ? openConversation(id) : newConversation())}
        />
      </div>

      {showHelp && <HelpPanel agent={agent} onPick={(text) => setInput(text)} />}

      <div className="messages" data-testid="messages">
        {stream.messages.length === 0 && (
          <p className="muted">כתבו בקשה לאייג'נט. לחצו על "עזרה" לדוגמאות.</p>
        )}
        {stream.messages.map((message, index) => {
          const role = roleOf(message);
          const text = textOf(message);
          const toolCalls = (message as { tool_calls?: { name: string }[] }).tool_calls ?? [];
          if (role === "system" || (!text && toolCalls.length === 0)) return null;
          return (
            <div key={message.id ?? index} className={`message message-${role}`} dir="auto">
              {text}
              {toolCalls.map((call, i) => (
                <div key={i} className="muted">
                  קורא לכלי: <code dir="ltr">{call.name}</code>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {status.label && (
        <p className={`run-status run-${status.phase}`} role="status" data-testid="run-status">
          {status.label}
        </p>
      )}

      {interrupt !== undefined && (
        <ApprovalCard value={interrupt.value} onRespond={(response) => stream.respond(response)} />
      )}

      <ErrorBanner error={stream.error ?? localError} />
      {notice && <p className="banner banner-ok">{notice}</p>}

      <form
        className={dragging ? "composer dragging" : "composer"}
        onSubmit={(e) => void submit(e)}
      >
        {attachments.length > 0 && (
          <ul className="attachments">
            {attachments.map((file) => (
              <li key={file.path}>
                {file.name}{" "}
                <button
                  type="button"
                  className="link"
                  aria-label={`הסרת ${file.name}`}
                  onClick={() => setAttachments(attachments.filter((a) => a.path !== file.path))}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          aria-label="הודעה לאייג'נט"
          dir="auto"
          rows={3}
          value={input}
          disabled={interrupt !== undefined}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="row">
          {agent.accepts_files.length > 0 && (
            <label className="file-pick">
              צירוף קובץ (<bdi dir="ltr">{agent.accepts_files.join(", ")}</bdi>)
              <input
                type="file"
                multiple
                accept={agent.accepts_files.join(",")}
                onChange={(event) => event.target.files && void upload(event.target.files)}
              />
            </label>
          )}
          {stream.isLoading ? (
            <button type="button" onClick={() => void stream.stop()}>
              עצירה
            </button>
          ) : (
            <button type="submit" className="primary" disabled={interrupt !== undefined}>
              שליחה
            </button>
          )}
        </div>
      </form>

      {usage && (
        <p className="cost" data-testid="cost">
          עלות השיחה: {formatUsd(usage.cost_usd)} · {usage.input_tokens.toLocaleString("he-IL")}{" "}
          טוקנים בקלט · {usage.output_tokens.toLocaleString("he-IL")} בפלט
          {usage.unpriced_models.length > 0 && " · לחלק מהמודלים אין מחיר ידוע"}
        </p>
      )}
    </div>
  );
}

function HelpPanel({ agent, onPick }: { agent: InstalledAgent; onPick: (text: string) => void }) {
  const api = useApi();
  const readme = useAsync(() => api.readme(agent.id), [api, agent.id]);
  return (
    <aside className="help card" aria-label="עזרה">
      <p dir="auto">{agent.description}</p>
      {agent.tasks.length > 0 && (
        <>
          <h3>מה האייג'נט יודע לעשות</h3>
          <ul>
            {agent.tasks.map((task) => (
              <li key={task}>{task}</li>
            ))}
          </ul>
        </>
      )}
      {agent.examples.length > 0 && (
        <>
          <h3>דוגמאות (לחיצה מעתיקה לתיבת ההודעה)</h3>
          <ul>
            {agent.examples.map((example) => (
              <li key={example}>
                <button type="button" className="link" dir="auto" onClick={() => onPick(example)}>
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {readme.data?.readme && (
        <details>
          <summary>מדריך המפתח (README)</summary>
          <pre className="log" dir="auto">
            {readme.data.readme}
          </pre>
        </details>
      )}
    </aside>
  );
}
