import { ApiError } from "./errors";
import { readSse } from "./sse";
import type {
  Catalog,
  InstallEvent,
  InstalledAgent,
  Job,
  KeyInfo,
  ManifestPreview,
  SecretField,
  SettingsStatus,
  StartedAgent,
  UsageItem,
  UsageSummary,
} from "./types";

type Method = "GET" | "POST" | "PUT" | "DELETE";

/** Typed client for the local manager API (see manager/README.md). */
export class ManagerApi {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly fetchImpl: typeof fetch = (...args) => fetch(...args),
  ) {}

  private async request<T>(method: Method, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}` };
    let payload: BodyInit | undefined;
    if (body instanceof FormData) {
      payload = body;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, { method, headers, body: payload });
    } catch (error) {
      throw new ApiError("network", error instanceof Error ? error.message : String(error), 0);
    }
    if (response.status === 204) return undefined as T;
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) throw toApiError(response.status, data);
    return data as T;
  }

  // agents
  listAgents = () => this.request<InstalledAgent[]>("GET", "/agents");
  getAgent = (id: string) => this.request<InstalledAgent>("GET", `/agents/${id}`);
  startAgent = (id: string) => this.request<StartedAgent>("POST", `/agents/${id}/start`);
  stopAgent = (id: string) => this.request<{ stopped: boolean }>("POST", `/agents/${id}/stop`);
  uninstall = (id: string) => this.request<undefined>("DELETE", `/agents/${id}`);
  heartbeat = (id: string) => this.request<{ running: boolean }>("POST", `/agents/${id}/heartbeat`);
  logs = (id: string, tail = 200) =>
    this.request<{ lines: string[] }>("GET", `/agents/${id}/logs?tail=${tail}`);
  readme = (id: string) => this.request<{ readme: string | null }>("GET", `/agents/${id}/readme`);
  agentSecrets = (id: string) => this.request<SecretField[]>("GET", `/agents/${id}/secrets`);
  setAgentSecrets = (id: string, values: Record<string, string>) =>
    this.request<undefined>("PUT", `/agents/${id}/secrets`, { values });

  uploadFile(id: string, file: File) {
    const form = new FormData();
    form.append("file", file, file.name);
    return this.request<{ path: string; name: string; size: number }>(
      "POST",
      `/agents/${id}/files`,
      form,
    );
  }

  // catalog
  catalog = (refresh = false) => this.request<Catalog>("GET", `/registry?refresh=${refresh}`);
  manifestPreview = (id: string) =>
    this.request<ManifestPreview>("GET", `/registry/${id}/manifest`);
  installFromCatalog = (id: string, version?: string) =>
    this.request<Job>("POST", `/registry/${id}/install`, version ? { version } : {});
  getJob = (id: string) => this.request<Job>("GET", `/jobs/${id}`);

  /** Stream a job's progress; resolves with the finished job. */
  async followJob(jobId: string, onEvent: (event: InstallEvent) => void): Promise<Job> {
    const response = await this.fetchImpl(`${this.baseUrl}/jobs/${jobId}/events`, {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!response.ok || !response.body) {
      throw toApiError(response.status, await response.json().catch(() => null));
    }
    for await (const message of readSse(response.body)) {
      if (message.event === "progress") onEvent(JSON.parse(message.data) as InstallEvent);
      if (message.event === "done") return JSON.parse(message.data) as Job;
    }
    return this.getJob(jobId); // stream ended early: ask for the final state
  }

  // settings
  settings = () => this.request<SettingsStatus>("GET", "/settings");
  setOpenRouterKey = (value: string) =>
    this.request<KeyInfo>("PUT", "/settings/openrouter-key", { value });
  openRouterStatus = () => this.request<KeyInfo>("GET", "/settings/openrouter-key/status");
  deleteOpenRouterKey = () => this.request<undefined>("DELETE", "/settings/openrouter-key");
  setLangSmithKey = (value: string) =>
    this.request<undefined>("PUT", "/settings/langsmith-key", { value });
  deleteLangSmithKey = () => this.request<undefined>("DELETE", "/settings/langsmith-key");

  // usage and export
  recordUsage = (agentId: string, threadId: string, items: UsageItem[]) =>
    this.request<UsageSummary & { recorded: number }>("POST", "/usage", {
      agent_id: agentId,
      thread_id: threadId,
      items,
    });
  usage = (agentId?: string, threadId?: string) => {
    const query = new URLSearchParams();
    if (agentId) query.set("agent_id", agentId);
    if (threadId) query.set("thread_id", threadId);
    return this.request<UsageSummary>("GET", `/usage?${query.toString()}`);
  };
  exportFile = (filename: string, content: string) =>
    this.request<{ path: string }>("POST", "/exports", { filename, content });
}

function toApiError(status: number, data: unknown): ApiError {
  const error = (data as { error?: { code?: string; message?: string } } | null)?.error;
  if (error?.code) return new ApiError(error.code, error.message ?? error.code, status);
  const detail = (data as { detail?: unknown } | null)?.detail;
  return new ApiError(
    status === 401 ? "unauthorized" : "http_error",
    typeof detail === "string" ? detail : `HTTP ${status}`,
    status,
  );
}
