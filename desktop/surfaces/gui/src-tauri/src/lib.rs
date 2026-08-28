//! Sourcecado desktop shell. It owns four native lifecycle jobs:
//!   1. pick a free loopback port
//!   2. start the Python sidecar as a child
//!   3. inject URL + one-time token before the UI loads
//!   4. tray: close hides (sidecar keeps running); Quit kills the sidecar

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use uuid::Uuid;

struct ServerProcess(Mutex<Option<Child>>);

fn free_port() -> u16 {
    // Prefer 8765 so Google OAuth can use a stable loopback redirect.
    if std::net::TcpListener::bind("127.0.0.1:8765").is_ok() {
        return 8765;
    }
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

fn launch_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

// Dev builds (`tauri dev`) run the sidecar straight out of the repo's dev
// venv. Release builds run the PyInstaller-frozen sidecar that `tauri build`
// bundles as a resource (see `desktop/packaging/`), so the packaged app never
// shells out to a repo checkout or `desktop/.venv`.
//
// The sidecar is bundled with PyInstaller `--onedir`, not `--onefile`: a
// onefile build's bootloader forks a second process to run the actual
// interpreter and hands back the bootloader's PID, so killing that PID
// orphans the real sidecar. onedir has no extraction step and no fork, so
// the PID we spawn is the PID that answers requests and the one `kill()`
// actually stops.
#[cfg(debug_assertions)]
fn desktop_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

#[cfg(debug_assertions)]
fn sidecar_command<R: tauri::Runtime>(_app: &tauri::App<R>, port: u16) -> Command {
    let mut cmd = Command::new(desktop_root().join(".venv/bin/python"));
    cmd.current_dir(desktop_root()).args([
        "-m",
        "coworker.run",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ]);
    cmd
}

#[cfg(not(debug_assertions))]
fn sidecar_command<R: tauri::Runtime>(app: &tauri::App<R>, port: u16) -> Command {
    use tauri::path::BaseDirectory;
    let exe = app
        .path()
        .resolve(
            "resources/sourcecado-sidecar/sourcecado-sidecar",
            BaseDirectory::Resource,
        )
        .expect("resolve bundled sourcecado-sidecar resource");
    let mut cmd = Command::new(exe);
    cmd.args(["--host", "127.0.0.1", "--port", &port.to_string()]);
    cmd
}

fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("CLUB_STATE_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".config").join("club")
}

fn server_log_file() -> Option<std::fs::File> {
    let dir = state_dir().join("logs");
    std::fs::create_dir_all(&dir).ok()?;
    let path = dir.join("sidecar.log");
    if path.exists() {
        let _ = std::fs::rename(&path, dir.join("sidecar.log.old"));
    }
    std::fs::File::create(&path).ok()
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

pub fn run() {
    let port = free_port();
    let api_token = launch_token();
    let http = format!("http://127.0.0.1:{port}");
    let inject = format!("window.__CLUB_HTTP__={http:?};window.__CLUB_API_TOKEN__={api_token:?};");

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app);
        }))
        .setup(move |app| {
            let mut server_cmd = sidecar_command(app, port);
            server_cmd
                .env("CLUB_API_TOKEN", &api_token)
                .env("CLUB_EXIT_WITH_PARENT", "1")
                .env("CLUB_PARENT_PID", std::process::id().to_string())
                .stdin(Stdio::null());
            match server_log_file() {
                Some(log) => {
                    if let Ok(err_clone) = log.try_clone() {
                        server_cmd
                            .stdout(Stdio::from(log))
                            .stderr(Stdio::from(err_clone));
                    } else {
                        server_cmd.stdout(Stdio::from(log)).stderr(Stdio::null());
                    }
                }
                None => {
                    server_cmd.stdout(Stdio::null()).stderr(Stdio::null());
                }
            }
            let child = match server_cmd.spawn() {
                Ok(child) => Some(child),
                Err(e) => {
                    eprintln!(
                        "[sourcecado] failed to start sidecar via {}: {e}",
                        server_cmd.get_program().to_string_lossy()
                    );
                    None
                }
            };
            app.manage(ServerProcess(Mutex::new(child)));

            let mut builder =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("Sourcecado")
                    .inner_size(960.0, 720.0)
                    .min_inner_size(640.0, 480.0)
                    .initialization_script(&inject);
            #[cfg(target_os = "macos")]
            {
                builder = builder
                    .title_bar_style(tauri::TitleBarStyle::Overlay)
                    .hidden_title(true);
            }
            let win = builder.build()?;

            let w = win.clone();
            win.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    let _ = w.hide();
                    api.prevent_close();
                }
            });

            let open_i = MenuItem::with_id(app, "open", "Open Sourcecado", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &quit_i])?;

            let mut tray = TrayIconBuilder::new()
                .tooltip("Sourcecado")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Sourcecado")
        .run(|app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                if let Some(state) = app.try_state::<ServerProcess>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
