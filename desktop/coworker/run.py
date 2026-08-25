"""Launch the slice-1 sidecar.

Copied shape from OpenWorker `coworker/server/run.py`: bind 127.0.0.1, mint a
launch token, persist it only for standalone/dev (Tauri later injects env and
never writes the token to disk).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from coworker.server import TOKEN_ENV, create_app, state_dir


def _exit_when_orphaned() -> None:
    """If Tauri spawned us (`CLUB_EXIT_WITH_PARENT=1`), die when the window process dies."""
    if os.environ.get("CLUB_EXIT_WITH_PARENT") != "1":
        return
    import threading
    import time

    try:
        parent = int(os.environ.get("CLUB_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    parent = parent or os.getppid()
    original_ppid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.5)
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass
            if os.getppid() != original_ppid:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _load_dotenv(path: Path) -> None:
    """Fill os.environ from a dotenv file. Existing vars win. Never prints values."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_token(port: int) -> tuple[str, Path | None]:
    existing = os.environ.get(TOKEN_ENV)
    if existing:
        return existing, None
    token = secrets.token_hex(32)
    os.environ[TOKEN_ENV] = token
    path = state_dir() / f"sidecar-{port}.token"
    _write_private(path, token + "\n")
    return token, path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="club-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        print("refusing to bind off loopback in slice 1", file=sys.stderr)
        sys.exit(2)

    _load_dotenv(state_dir() / ".env")
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token, token_path = ensure_token(args.port)
    app = create_app(token=token, public_url=f"http://{args.host}:{args.port}")
    if not app.state.store.list_schedule()["jobs"]:
        from coworker.automation.scheduler import next_monday_0900

        app.state.store.add_job(
            "0 9 * * 1",
            "weekly sourcing check-in",
            next_run_at=next_monday_0900(datetime.now()),
        )

    print(f"Club sidecar  http://{args.host}:{args.port}")
    print(f"token file    {token_path or '(from ' + TOKEN_ENV + ')'}")
    print("window talks with header X-Club-Token. chat is /ws/chat.")

    try:
        import threading
        import time

        import uvicorn

        def _ticks() -> None:
            while True:
                time.sleep(30)
                try:
                    app.state.scheduler.tick()
                except Exception:
                    import traceback

                    traceback.print_exc()

        threading.Thread(target=_ticks, daemon=True).start()
        _exit_when_orphaned()
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        if token_path is not None:
            token_path.unlink(missing_ok=True)
            os.environ.pop(TOKEN_ENV, None)


if __name__ == "__main__":
    # `python -m coworker.run` from desktop/ so coworker is importable.
    main()
