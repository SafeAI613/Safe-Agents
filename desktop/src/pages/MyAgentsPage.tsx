import { useState } from "react";

import { AgentSecretsDialog } from "../components/AgentSecretsDialog";
import { Dialog } from "../components/Dialog";
import { ErrorBanner } from "../components/ErrorBanner";
import { LogsDialog } from "../components/LogsDialog";
import { useApi } from "../lib/context";
import type { InstalledAgent } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function MyAgentsPage({
  onOpenChat,
  onOpenStore,
}: {
  onOpenChat: (agentId: string) => void;
  onOpenStore: () => void;
}) {
  const api = useApi();
  const agents = useAsync(() => api.listAgents(), [api]);
  const [secretsFor, setSecretsFor] = useState<InstalledAgent | null>(null);
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [removing, setRemoving] = useState<InstalledAgent | null>(null);
  const [actionError, setActionError] = useState<unknown>();
  const [busy, setBusy] = useState<string | null>(null);

  async function run(agentId: string, action: () => Promise<unknown>) {
    setBusy(agentId);
    setActionError(undefined);
    try {
      await action();
      agents.reload();
    } catch (reason) {
      setActionError(reason);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <header className="page-header">
        <h1>האייג'נטים שלי</h1>
      </header>
      <ErrorBanner error={agents.error ?? actionError} onRetry={agents.reload} />
      {agents.data?.length === 0 && (
        <div className="empty">
          <p>עדיין לא הותקנו אייג'נטים.</p>
          <button type="button" className="primary" onClick={onOpenStore}>
            לחנות האייג'נטים
          </button>
        </div>
      )}
      <div className="cards">
        {agents.data?.map((agent) => (
          <article key={agent.id} className="card" data-testid={`agent-${agent.id}`}>
            <h2>
              {agent.name}{" "}
              <span className={agent.status === "running" ? "status on" : "status"}>
                {agent.status === "running" ? "פועל" : "כבוי"}
              </span>
            </h2>
            <p>{agent.description}</p>
            <p className="muted">
              גרסה <bdi>{agent.version}</bdi>
            </p>
            {agent.missing_secrets.length > 0 && (
              <p className="banner banner-warn">חסרים מפתחות: {agent.missing_secrets.join(", ")}</p>
            )}
            <div className="actions">
              <button
                type="button"
                className="primary"
                onClick={() => onOpenChat(agent.id)}
                disabled={busy === agent.id}
              >
                פתיחת שיחה
              </button>
              <button type="button" onClick={() => setSecretsFor(agent)}>
                מפתחות
              </button>
              <button type="button" onClick={() => setLogsFor(agent.id)}>
                יומן
              </button>
              {agent.status === "running" && (
                <button
                  type="button"
                  onClick={() => void run(agent.id, () => api.stopAgent(agent.id))}
                  disabled={busy === agent.id}
                >
                  עצירה
                </button>
              )}
              <button
                type="button"
                className="danger"
                disabled={busy === agent.id}
                onClick={() => setRemoving(agent)}
              >
                הסרה
              </button>
            </div>
          </article>
        ))}
      </div>
      {secretsFor && (
        <AgentSecretsDialog
          agentId={secretsFor.id}
          agentName={secretsFor.name}
          onClose={() => {
            setSecretsFor(null);
            agents.reload();
          }}
        />
      )}
      {logsFor && <LogsDialog agentId={logsFor} onClose={() => setLogsFor(null)} />}
      {removing && (
        <Dialog title={`הסרת ${removing.name}`} onClose={() => setRemoving(null)}>
          <p>האייג'נט, הקבצים שלו והמפתחות שהוגדרו עבורו יימחקו מהמחשב.</p>
          <div className="actions">
            <button type="button" onClick={() => setRemoving(null)}>
              ביטול
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => {
                const agentId = removing.id;
                setRemoving(null);
                void run(agentId, () => api.uninstall(agentId));
              }}
            >
              הסרה
            </button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
