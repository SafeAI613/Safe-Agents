//! Desktop shell: starts the Python agent manager as a sidecar and tells the UI where it is.
//!
//! The shell is deliberately thin. All platform logic (catalog, installs, agent servers,
//! secrets) lives in the manager; Rust only owns the process and the token.

mod manager;

use std::sync::Arc;

use tauri::{Manager, RunEvent};

use manager::{ManagerStatus, Shared};

#[tauri::command]
fn manager_status(state: tauri::State<'_, Arc<Shared>>) -> ManagerStatus {
    state.status()
}

pub fn run() {
    let shared = Arc::new(Shared::default());
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(shared.clone())
        .invoke_handler(tauri::generate_handler![manager_status])
        .setup({
            let shared = shared.clone();
            move |app| {
                if let Err(message) = manager::spawn(app.handle(), shared.clone()) {
                    shared.set_status(ManagerStatus::Failed { message });
                }
                Ok(())
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the Safe Agents app");

    app.run(move |handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Arc<Shared>>().shutdown();
        }
    });
}
