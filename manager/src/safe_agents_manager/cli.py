"""Command line interface: ``safe-agents-manager install|list|run|uninstall|serve``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import secrets
import signal
import socket
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .config import Settings
from .errors import ManagerError
from .installer import InstallEvent, InstallStep
from .secret_store import EnvSecretProvider
from .service import AgentManager


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safe-agents-manager", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home", type=Path, help="data folder (default: platform data dir)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed output")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="install an agent from a local zip archive")
    install.add_argument("archive", type=Path)
    install.add_argument("--sha256", help="expected sha256 of the archive")

    sub.add_parser("list", help="list installed agents")

    uninstall = sub.add_parser("uninstall", help="remove an installed agent")
    uninstall.add_argument("agent_id")

    run = sub.add_parser("run", help="start an agent server and keep it running until Ctrl+C")
    run.add_argument("agent_id")

    serve = sub.add_parser("serve", help="run the local HTTP API for the desktop shell")
    serve.add_argument("--port", type=int, default=0, help="port to bind (0 = pick a free one)")
    return parser


def _print_event(event: InstallEvent, verbose: bool) -> None:
    if event.detail and not verbose:
        return
    prefix = "  " if event.detail else f"[{event.step.value}] "
    stream = sys.stderr if event.step is InstallStep.FAILED else sys.stdout
    print(f"{prefix}{event.message}", file=stream, flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _serve(manager: AgentManager, port: int) -> None:
    import uvicorn

    from .api import create_app

    # The desktop shell normally generates the token and passes it in; print it otherwise.
    token = os.environ.get("SAFE_AGENTS_TOKEN")
    ready: dict[str, object] = {"event": "ready", "port": port or _free_port()}
    if not token:
        token = secrets.token_urlsafe(32)
        ready["token"] = token
    app = create_app(manager, token)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=int(str(ready["port"])), log_level="warning"
    )
    print(json.dumps(ready), flush=True)
    uvicorn.Server(config).run()


def _run_foreground(manager: AgentManager, agent_id: str) -> None:
    handle = manager.start(agent_id)
    print(
        f"{agent_id} is running at {handle.url} (graph '{handle.graph_id}'). Ctrl+C to stop.",
        flush=True,
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(1.0):
            if manager.supervisor.get(agent_id) is None:
                print(
                    f"{agent_id} stopped unexpectedly; see `logs/{agent_id}.log`", file=sys.stderr
                )
                return
    except KeyboardInterrupt:
        pass
    finally:
        manager.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if args.home:
        settings = dataclasses.replace(settings, home=args.home)

    try:
        manager = AgentManager(settings, EnvSecretProvider())
        if args.command == "install":
            agent = manager.install_archive(
                args.archive.resolve(),
                expected_sha256=args.sha256,
                on_event=lambda event: _print_event(event, args.verbose),
            )
            print(f"installed {agent.id} {agent.version} -> {agent.install_dir}")
        elif args.command == "list":
            agents = manager.list_agents()
            if not agents:
                print("no agents installed")
            for info in agents:
                print(f"{info['id']:<24} {info['version']:<10} {info['status']:<8} {info['name']}")
        elif args.command == "uninstall":
            manager.uninstall(args.agent_id)
            print(f"removed {args.agent_id}")
        elif args.command == "run":
            _run_foreground(manager, args.agent_id)
        elif args.command == "serve":
            _serve(manager, args.port)
    except ManagerError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
