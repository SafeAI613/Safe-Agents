import { useApi } from "../lib/context";
import { useAsync } from "../lib/useAsync";
import { Dialog } from "./Dialog";
import { ErrorBanner } from "./ErrorBanner";

export function LogsDialog({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const api = useApi();
  const logs = useAsync(() => api.logs(agentId, 300), [api, agentId]);
  return (
    <Dialog title={`יומן – ${agentId}`} onClose={onClose}>
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
