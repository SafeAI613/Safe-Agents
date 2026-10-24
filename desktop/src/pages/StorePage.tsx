import { useCallback, useState } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Dialog } from "../components/Dialog";
import { InstallRun } from "../components/InstallRun";
import { useApi } from "../lib/context";
import { FEATURE_LABELS, PERMISSION_LABELS } from "../lib/labels";
import type { CatalogAgent, ManifestPreview } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function StorePage({ onOpenAgents }: { onOpenAgents: () => void }) {
  const api = useApi();
  const [refresh, setRefresh] = useState(false);
  const catalog = useAsync(() => api.catalog(refresh), [api, refresh]);
  const [selected, setSelected] = useState<CatalogAgent | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);

  return (
    <section>
      <header className="page-header">
        <h1>חנות האייג'נטים</h1>
        <label className="file-pick">
          התקנה מקובץ ZIP
          <input
            type="file"
            accept=".zip"
            data-testid="install-zip"
            onChange={(event) => {
              setLocalFile(event.target.files?.[0] ?? null);
              event.target.value = "";
            }}
          />
        </label>
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
            <p dir="auto">{agent.description}</p>
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
      {localFile && (
        <LocalInstallDialog
          file={localFile}
          onClose={() => {
            setLocalFile(null);
            catalog.reload();
          }}
          onOpenAgents={onOpenAgents}
        />
      )}
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
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const start = useCallback(() => api.installFromCatalog(agent.id), [api, agent.id]);

  return (
    <Dialog
      title={`${agent.installed_version ? "עדכון" : "התקנת"} ${agent.name}`}
      onClose={onClose}
      closable={!busy}
    >
      {confirmed ? (
        <InstallRun
          start={start}
          onClose={onClose}
          onOpenAgents={onOpenAgents}
          onBusyChange={setBusy}
        />
      ) : (
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
              onClick={() => setConfirmed(true)}
            >
              אישור והתקנה
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function LocalInstallDialog({
  file,
  onClose,
  onOpenAgents,
}: {
  file: File;
  onClose: () => void;
  onOpenAgents: () => void;
}) {
  const api = useApi();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const start = useCallback(() => api.installUpload(file), [api, file]);

  return (
    <Dialog title={`התקנה מקובץ: ${file.name}`} onClose={onClose} closable={!busy}>
      {confirmed ? (
        <InstallRun
          start={start}
          onClose={onClose}
          onOpenAgents={onOpenAgents}
          onBusyChange={setBusy}
        />
      ) : (
        <>
          <p className="banner banner-warn">
            אייג'נט מקובץ לא עבר את בדיקות הקטלוג. התקינו רק קבצים ממקור שאתם סומכים עליו.
          </p>
          <div className="actions">
            <button type="button" onClick={onClose}>
              ביטול
            </button>
            <button type="button" className="primary" onClick={() => setConfirmed(true)}>
              התקנה
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function PreviewDetails({ preview }: { preview: ManifestPreview }) {
  const features = Object.entries(preview.features).filter(([, on]) => on);
  return (
    <div className="stack">
      {preview.description && <p dir="auto">{preview.description}</p>}
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
