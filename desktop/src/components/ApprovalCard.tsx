import { useState } from "react";

interface ApprovalRequest {
  type: "approval";
  action?: string;
  details?: unknown;
}

function isApproval(value: unknown): value is ApprovalRequest {
  return (
    typeof value === "object" && value !== null && (value as { type?: unknown }).type === "approval"
  );
}

/**
 * Human-in-the-loop: the agent paused with interrupt() and waits for the user's decision.
 * Agents following the contract send {type: "approval", action, details}; anything else is
 * shown as raw data with a free-text answer.
 */
export function ApprovalCard({
  value,
  onRespond,
}: {
  value: unknown;
  onRespond: (response: unknown) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState("");

  async function respond(response: unknown) {
    setBusy(true);
    try {
      await onRespond(response);
    } finally {
      setBusy(false);
    }
  }

  const approval = isApproval(value) ? value : null;
  return (
    <div className="approval" role="alertdialog" aria-label="האייג'נט מבקש אישור">
      <h3>האייג'נט מבקש אישור</h3>
      {approval?.action && (
        <p>
          פעולה: <code dir="ltr">{approval.action}</code>
        </p>
      )}
      <pre className="log" dir="auto">
        {JSON.stringify(approval ? approval.details : value, null, 2)}
      </pre>
      {approval ? (
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void respond({ approved: true })}
          >
            אישור
          </button>
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => void respond({ approved: false })}
          >
            דחייה
          </button>
        </div>
      ) : (
        <form
          className="row"
          onSubmit={(event) => {
            event.preventDefault();
            void respond(answer);
          }}
        >
          <input
            aria-label="תשובה לאייג'נט"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
          />
          <button type="submit" className="primary" disabled={busy}>
            שליחה
          </button>
        </form>
      )}
    </div>
  );
}
