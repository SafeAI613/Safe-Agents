import { useApi } from "../lib/context";
import { useAsync } from "../lib/useAsync";
import { Dialog } from "./Dialog";
import { ErrorBanner } from "./ErrorBanner";

/** An agent's log, or the manager's own log when no agent is given. */
export function LogsDialog({ agentId, onClose }: { agentId?: string; onClose: () => void }) {
  const api = useApi();
  const logs = useAsync(
    () => (agentId ? api.logs(agentId, 300) : api.managerLog(300)),
    [api, agentId],
  );
  return (
    <Dialog title={agentId ? `יומן – ${agentId}` : "יומן המערכת"} onClose={onClose}>
      <ErrorBanner error={logs.error} onRetry={logs.reload} />
      <pre className="log tall" dir="ltr">
        {logs.data?.lines.join("\n") || "היומן ריק."}
      </pre>
      <button type="button" onClick={logs.reload}>
        רענון
      </button>
    </Dialog>
  );
}
