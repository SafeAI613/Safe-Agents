export interface SecretField {
  key: string;
  label: string;
  required: boolean;
  source: string;
  scope?: "global" | "agent";
  set?: boolean;
}

export interface InstalledAgent {
  id: string;
  name: string;
  version: string;
  graph_id: string;
  description: string;
  installed_at: string;
  accepts_files: string[];
  hitl: boolean;
  status: "running" | "stopped";
  examples: string[];
  tasks: string[];
  permissions: string[];
  models: string[];
  default_model: string;
  mcp_servers: string[];
  missing_secrets: string[];
}

export interface CatalogAgent {
  id: string;
  name: string;
  description: string;
  creator_name: string | null;
  latest: string;
  versions: string[];
  installed_version: string | null;
  update_available: boolean;
}

export interface Catalog {
  agents: CatalogAgent[];
  fetched_at: string;
  stale: boolean;
}

export interface ManifestPreview {
  id: string;
  version: string;
  name: string;
  description: string | null;
  creator_name: string | null;
  contact_information: string | null;
  repository_url: string | null;
  examples: string[];
  tasks: string[];
  permissions: string[];
  features: Record<string, boolean>;
  models: string[];
  secrets: SecretField[];
}

export type InstallStep =
  | "downloading"
  | "verifying"
  | "extracting"
  | "validating"
  | "creating_env"
  | "generating_config"
  | "smoke_test"
  | "activating"
  | "installed"
  | "failed";

export interface InstallEvent {
  step: InstallStep;
  message: string;
  detail: boolean;
  timestamp: string;
}

export interface Job {
  id: string;
  kind: string;
  status: "pending" | "running" | "succeeded" | "failed";
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
}

export interface StartedAgent {
  agent_id: string;
  graph_id: string;
  /** The agent API behind the content guard, relative to the manager URL. */
  proxy_path: string;
  started_at: string;
}

export interface SettingsStatus {
  openrouter_key_set: boolean;
  langsmith_key_set: boolean;
  idle_timeout_s: number;
  exports_dir: string;
}

export interface ContentPolicy {
  classifier_model: string;
  blocked_topics: string[];
  source: "catalog" | "cache" | "bundled";
}

export interface About {
  version: string;
  data_dir: string;
  agents_dir: string;
  logs_dir: string;
  exports_dir: string;
  registry_url: string;
}

export interface KeyInfo {
  label: string | null;
  usage_usd: number | null;
  limit_usd: number | null;
  limit_remaining_usd: number | null;
  is_free_tier: boolean;
}

export interface UsageItem {
  message_id: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  unpriced_models: string[];
  by_model: {
    model: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    priced: boolean;
  }[];
}
