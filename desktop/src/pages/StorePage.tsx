import { useState } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { InstallProgress } from "../components/InstallProgress";
import { useApi } from "../lib/context";
import { FEATURE_LABELS, PERMISSION_LABELS } from "../lib/labels";
import type { CatalogAgent, InstallEvent, ManifestPreview } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function StorePage({ onOpenAgents }: { onOpenAgents: () => void }) {
  const api = useApi();
  const [refresh, setRefresh] = useState(false);
  const catalog = useAsync(() => api.catalog(refresh), [api, refresh]);
  const [selected, setSelected] = useState<CatalogAgent | null>(null);

  return (
    <section>
      <header className="page-header">
        <h1>חנות האייג'נטים</h1>
        <button
          type="button"
          onClick={() => {
            setRefresh(true);
            catalog.reload();
          }}
          disabled={catalog.loading}
        >
          רענון
        </button>
      </header>
      <ErrorBanner error={catalog.error} onRetry={catalog.reload} />
      {catalog.data?.stale && (
        <p className="banner banner-warn">אין חיבור לאינטרנט. מוצג הקטלוג האחרון שנשמר.</p>
      )}
      {catalog.data && catalog.data.agents.length === 0 && <p>הקטלוג ריק כרגע.</p>}
      <div className="cards">
        {catalog.data?.agents.map((agent) => (
          <article key={agent.id} className="card" data-testid={`catalog-${agent.id}`}>
            <h2>{agent.name}</h2>
            <p>{agent.description}</p>
            <p className="muted">
              {agent.creator_name && <>מאת {agent.creator_name} · </>}
              גרסה <bdi>{agent.latest}</bdi>
            </p>
            <div className="actions">
              {agent.installed_version && !agent.update_available ? (
                <span className="badge">מותקן</span>
              ) : (
                <button type="button" className="primary" onClick={() => setSelected(agent)}>
                  {agent.update_available ? `עדכון ל-${agent.latest}` : "התקנה"}
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {selected && (
        <InstallDialog
          agent={selected}
          onClose={() => {
            setSelected(null);
            catalog.reload();
          }}
          onOpenAgents={onOpenAgents}
        />
      )}
    </section>
  );
}

function InstallDialog({
  agent,
  onClose,
  onOpenAgents,
}: {
  agent: CatalogAgent;
  onClose: () => void;
  onOpenAgents: () => void;
}) {
  const api = useApi();
  const preview = useAsync(() => api.manifestPreview(agent.id), [api, agent.id]);
  const [events, setEvents] = useState<InstallEvent[]>([]);
  const [phase, setPhase] = useState<"confirm" | "installing" | "done" | "failed">("confirm");
  const [error, setError] = useState<unknown>();

  async function install() {
    setPhase("installing");
    setEvents([]);
    setError(undefined);
    try {
      const job = await api.installFromCatalog(agent.id);
      const finished = await api.followJob(job.id, (event) =>
        setEvents((previous) => [...previous, event]),
      );
      setPhase(finished.status === "succeeded" ? "done" : "failed");
    } catch (reason) {
      setError(reason);
      setPhase("failed");
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="install-title">
        <h2 id="install-title">
          {agent.installed_version ? "עדכון" : "התקנת"} {agent.name}
        </h2>
        {phase === "confirm" && (
          <>
            <ErrorBanner error={preview.error} onRetry={preview.reload} />
            {preview.loading && <p>טוען פרטים...</p>}
            {preview.data && <PreviewDetails preview={preview.data} />}
            <div className="actions">
              <button type="button" onClick={onClose}>
                ביטול
              </button>
              <button
                type="button"
                className="primary"
                disabled={!preview.data}
                onClick={() => void install()}
              >
                אישור והתקנה
              </button>
            </div>
          </>
        )}
        {phase !== "confirm" && (
          <>
            <InstallProgress events={events} />
            <ErrorBanner error={error} />
            <div className="actions">
              {phase === "done" && (
                <button
                  type="button"
                  className="primary"
                  onClick={() => {
                    onClose();
                    onOpenAgents();
                  }}
                >
                  לאייג'נטים שלי
                </button>
              )}
              <button type="button" onClick={onClose} disabled={phase === "installing"}>
                סגירה
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PreviewDetails({ preview }: { preview: ManifestPreview }) {
  const features = Object.entries(preview.features).filter(([, on]) => on);
  return (
    <div className="stack">
      {preview.description && <p>{preview.description}</p>}
      <p className="muted">
        מפתח/ת: {preview.creator_name ?? "לא צוין"}
        {preview.contact_information && (
          <>
            {" "}
            · <bdi>{preview.contact_information}</bdi>
          </>
        )}
      </p>
      <h3>הרשאות שהאייג'נט מבקש</h3>
      {preview.permissions.length === 0 ? (
        <p>ללא הרשאות מיוחדות.</p>
      ) : (
        <ul data-testid="permissions">
          {preview.permissions.map((permission) => (
            <li key={permission}>{PERMISSION_LABELS[permission] ?? permission}</li>
          ))}
        </ul>
      )}
      {features.length > 0 && (
        <>
          <h3>יכולות</h3>
          <ul>
            {features.map(([feature]) => (
              <li key={feature}>{FEATURE_LABELS[feature] ?? feature}</li>
            ))}
          </ul>
        </>
      )}
      <p className="muted">
        האייג'נט הוא קוד שנכתב על ידי המפתח/ת. התקינו רק אייג'נטים שאתם סומכים עליהם.
      </p>
    </div>
  );
}
