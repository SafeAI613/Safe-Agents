use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rand::RngCore;
use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// What the UI sees through the `manager_status` command.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum ManagerStatus {
    Starting,
    Ready { url: String, token: String },
    Failed { message: String },
}

#[derive(Deserialize)]
struct ReadyLine {
    event: String,
    port: u16,
}

pub struct Shared {
    status: Mutex<ManagerStatus>,
    child: Mutex<Option<CommandChild>>,
    exited: AtomicBool,
}

impl Default for Shared {
    fn default() -> Self {
        Self {
            status: Mutex::new(ManagerStatus::Starting),
            child: Mutex::new(None),
            exited: AtomicBool::new(false),
        }
    }
}

impl Shared {
    pub fn status(&self) -> ManagerStatus {
        self.status.lock().expect("status lock").clone()
    }

    pub fn set_status(&self, status: ManagerStatus) {
        *self.status.lock().expect("status lock") = status;
    }

    /// Ask the manager to stop all agents and exit; kill it if it does not within 10s.
    pub fn shutdown(&self) {
        let Some(mut child) = self.child.lock().expect("child lock").take() else {
            return;
        };
        if child.write(b"shutdown\n").is_ok() {
            let deadline = Instant::now() + Duration::from_secs(10);
            while !self.exited.load(Ordering::SeqCst) && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        if !self.exited.load(Ordering::SeqCst) {
            let _ = child.kill();
        }
    }
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Parses the manager's `{"event": "ready", "port": N}` line.
pub fn parse_ready(line: &str) -> Option<u16> {
    let ready: ReadyLine = serde_json::from_str(line.trim()).ok()?;
    (ready.event == "ready").then_some(ready.port)
}

pub fn spawn(app: &AppHandle, shared: Arc<Shared>) -> Result<(), String> {
    let token = random_token();
    let parent_pid = std::process::id().to_string();
    let args = [
        "serve",
        "--port",
        "0",
        "--exit-on-stdin-close",
        "--parent-pid",
        parent_pid.as_str(),
    ];

    // Development: run the manager from source, e.g.
    // SAFE_AGENTS_DEV_MANAGER="uv run --project ../manager safe-agents-manager"
    let command = match std::env::var("SAFE_AGENTS_DEV_MANAGER") {
        Ok(cmdline) => {
            let mut parts = cmdline.split_whitespace();
            let program = parts.next().ok_or("SAFE_AGENTS_DEV_MANAGER is empty")?;
            app.shell().command(program).args(parts)
        }
        Err(_) => app
            .shell()
            .sidecar("safe-agents-manager")
            .map_err(|e| format!("the bundled agent manager is missing: {e}"))?,
    };
    let mut command = command.args(args).env("SAFE_AGENTS_TOKEN", &token);
    if cfg!(debug_assertions) {
        // The dev webview is served by Vite instead of the tauri:// origin.
        command = command.env("SAFE_AGENTS_CORS_ORIGINS", "http://localhost:1420");
    }

    let (mut events, child) = command
        .spawn()
        .map_err(|e| format!("could not start the agent manager: {e}"))?;
    *shared.child.lock().expect("child lock") = Some(child);

    tauri::async_runtime::spawn(async move {
        let mut stderr_tail: Vec<String> = Vec::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    for line in String::from_utf8_lossy(&bytes).lines() {
                        if let Some(port) = parse_ready(line) {
                            shared.set_status(ManagerStatus::Ready {
                                url: format!("http://127.0.0.1:{port}"),
                                token: token.clone(),
                            });
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    let text = String::from_utf8_lossy(&bytes).to_string();
                    eprint!("[manager] {text}");
                    stderr_tail.push(text);
                    if stderr_tail.len() > 20 {
                        stderr_tail.remove(0);
                    }
                }
                CommandEvent::Terminated(payload) => {
                    shared.exited.store(true, Ordering::SeqCst);
                    shared.set_status(ManagerStatus::Failed {
                        message: format!(
                            "the agent manager stopped (exit code {:?}). {}",
                            payload.code,
                            stderr_tail.join("").trim()
                        ),
                    });
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_ready_line() {
        assert_eq!(
            parse_ready(r#"{"event": "ready", "port": 54321}"#),
            Some(54321)
        );
        assert_eq!(
            parse_ready(r#"{"event": "ready", "port": 1, "token": "x"}"#),
            Some(1)
        );
        assert_eq!(parse_ready(r#"{"event": "other", "port": 1}"#), None);
        assert_eq!(parse_ready("INFO starting"), None);
    }

    #[test]
    fn tokens_are_random_and_long() {
        let (a, b) = (random_token(), random_token());
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
    }

    #[test]
    fn status_serializes_for_the_ui() {
        let ready = ManagerStatus::Ready {
            url: "u".into(),
            token: "t".into(),
        };
        assert_eq!(
            serde_json::to_string(&ready).unwrap(),
            r#"{"state":"ready","url":"u","token":"t"}"#
        );
        assert_eq!(
            serde_json::to_string(&ManagerStatus::Starting).unwrap(),
            r#"{"state":"starting"}"#
        );
    }
}
