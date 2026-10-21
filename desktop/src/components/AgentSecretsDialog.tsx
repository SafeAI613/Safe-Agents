import { useState, type FormEvent } from "react";

import { useApi } from "../lib/context";
import { useAsync } from "../lib/useAsync";
import { Dialog } from "./Dialog";
import { ErrorBanner } from "./ErrorBanner";

/** Keys the agent declared (env and MCP auth). Values are write-only: never shown back. */
export function AgentSecretsDialog({
  agentId,
  agentName,
  onClose,
}: {
  agentId: string;
  agentName: string;
  onClose: () => void;
}) {
  const api = useApi();
  const fields = useAsync(() => api.agentSecrets(agentId), [api, agentId]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>();
  const [saved, setSaved] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    try {
      await api.setAgentSecrets(agentId, values);
      setValues({});
      setSaved(true);
      fields.reload();
    } catch (reason) {
      setError(reason);
    }
  }

  const agentFields = fields.data?.filter((field) => field.scope === "agent") ?? [];
  const globalFields = fields.data?.filter((field) => field.scope === "global") ?? [];

  return (
    <Dialog title={`מפתחות וחיבורים – ${agentName}`} onClose={onClose}>
      <ErrorBanner error={fields.error} onRetry={fields.reload} />
      {fields.data && agentFields.length === 0 && <p>לאייג'נט הזה אין מפתחות משלו.</p>}
      <form className="stack" onSubmit={save}>
        {agentFields.map((field) => (
          <div key={field.key} className="field">
            <label htmlFor={`secret-${field.key}`}>
              {field.label}
              {field.source.startsWith("mcp:") && (
                <span className="muted"> · MCP: {field.source.slice(4)}</span>
              )}
              {field.required && <span className="badge">חובה</span>}
            </label>
            <div className="row">
              <input
                id={`secret-${field.key}`}
                type="password"
                dir="ltr"
                autoComplete="off"
                placeholder={field.set ? "•••••• (מוגדר)" : "לא מוגדר"}
                value={values[field.key] ?? ""}
                onChange={(event) => setValues({ ...values, [field.key]: event.target.value })}
              />
              {field.set && (
                <button
                  type="button"
                  className="link"
                  onClick={() => setValues({ ...values, [field.key]: "" })}
                  title="המחיקה תתבצע בשמירה"
                >
                  מחיקה
                </button>
              )}
            </div>
          </div>
        ))}
        {globalFields.length > 0 && (
          <p className="muted">
            {globalFields.map((field) => field.key).join(", ")} מוגדרים במסך ההגדרות הכללי.
          </p>
        )}
        <ErrorBanner error={error} />
        {saved && (
          <p className="banner banner-ok">נשמר. השינוי ייכנס לתוקף בהפעלה הבאה של האייג'נט.</p>
        )}
        {agentFields.length > 0 && (
          <button type="submit" className="primary" disabled={Object.keys(values).length === 0}>
            שמירה
          </button>
        )}
      </form>
    </Dialog>
  );
}
