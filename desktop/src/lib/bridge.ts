import { invoke } from "@tauri-apps/api/core";

export interface ManagerConnection {
  url: string;
  token: string;
}

type ShellStatus =
  | { state: "starting" }
  | { state: "ready"; url: string; token: string }
  | { state: "failed"; message: string };

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Where the manager API is and which token it expects.
 *
 * In the desktop app the Rust shell starts the manager and knows both. When the UI runs in
 * a plain browser (development and end-to-end tests) they come from Vite env variables.
 */
export async function getManagerConnection(timeoutMs = 60_000): Promise<ManagerConnection> {
  if (!isTauri()) {
    const url = import.meta.env.VITE_MANAGER_URL as string | undefined;
    const token = import.meta.env.VITE_MANAGER_TOKEN as string | undefined;
    if (!url || !token) {
      throw new Error(
        "VITE_MANAGER_URL and VITE_MANAGER_TOKEN must be set outside the desktop app",
      );
    }
    return { url, token };
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await invoke<ShellStatus>("manager_status");
    if (status.state === "ready") return { url: status.url, token: status.token };
    if (status.state === "failed") throw new Error(status.message);
    await sleep(250);
  }
  throw new Error("the agent manager did not start in time");
}
