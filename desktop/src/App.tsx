import { useEffect, useMemo, useState } from "react";

import { ErrorBanner } from "./components/ErrorBanner";
import { Onboarding } from "./components/Onboarding";
import { ManagerApi } from "./lib/api";
import { getManagerConnection } from "./lib/bridge";
import { ApiContext } from "./lib/context";
import { useAsync } from "./lib/useAsync";
import { ChatPage } from "./pages/ChatPage";
import { MyAgentsPage } from "./pages/MyAgentsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StorePage } from "./pages/StorePage";

export type Route =
  | { page: "store" }
  | { page: "agents" }
  | { page: "settings" }
  | { page: "chat"; agentId: string };

export function App() {
  const connection = useAsync(() => getManagerConnection(), []);
  const api = useMemo(
    () => (connection.data ? new ManagerApi(connection.data.url, connection.data.token) : null),
    [connection.data],
  );

  if (connection.error) {
    return (
      <main className="center">
        <h1>מנהל האייג'נטים לא עלה</h1>
        <ErrorBanner error={connection.error} onRetry={connection.reload} />
      </main>
    );
  }
  if (!api) return <main className="center">טוען...</main>;
  return (
    <ApiContext.Provider value={api}>
      <Shell api={api} />
    </ApiContext.Provider>
  );
}

function Shell({ api }: { api: ManagerApi }) {
  const settings = useAsync(() => api.settings(), [api]);
  const [route, setRoute] = useState<Route>({ page: "agents" });

  useEffect(() => {
    if (settings.data && !settings.data.openrouter_key_set) setRoute({ page: "agents" });
  }, [settings.data]);

  if (settings.error) return <ErrorBanner error={settings.error} onRetry={settings.reload} />;
  if (!settings.data) return <main className="center">טוען...</main>;
  if (!settings.data.openrouter_key_set) return <Onboarding onDone={settings.reload} />;

  const nav: { page: Route["page"]; label: string }[] = [
    { page: "agents", label: "האייג'נטים שלי" },
    { page: "store", label: "חנות" },
    { page: "settings", label: "הגדרות" },
  ];
  return (
    <div className="layout">
      <nav className="sidebar" aria-label="ניווט ראשי">
        <div className="brand">Safe Agents</div>
        {nav.map((item) => (
          <button
            key={item.page}
            type="button"
            className={route.page === item.page ? "nav active" : "nav"}
            onClick={() => setRoute({ page: item.page } as Route)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <main className="content">
        {route.page === "store" && <StorePage onOpenAgents={() => setRoute({ page: "agents" })} />}
        {route.page === "agents" && (
          <MyAgentsPage
            onOpenChat={(agentId) => setRoute({ page: "chat", agentId })}
            onOpenStore={() => setRoute({ page: "store" })}
          />
        )}
        {route.page === "settings" && <SettingsPage onKeyRemoved={settings.reload} />}
        {route.page === "chat" && (
          <ChatPage agentId={route.agentId} onBack={() => setRoute({ page: "agents" })} />
        )}
      </main>
    </div>
  );
}
