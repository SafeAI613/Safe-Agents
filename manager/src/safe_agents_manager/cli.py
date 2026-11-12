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
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .config import Settings
from .errors import ManagerError
from .installer import InstallEvent, InstallStep
from .policy import ContentPolicy
from .secret_store import (
    EnvSecretProvider,
    KeyringBackend,
    MemoryBackend,
    SecretBackend,
    SecretProvider,
    SecretStore,
)
from .service import AgentManager


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safe-agents-manager", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home", type=Path, help="data folder (default: platform data dir)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed output")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install", help="install an agent from the catalog (by id) or from a local zip archive"
    )
    install.add_argument("source", help="catalog agent id, or path to a .zip archive")
    install.add_argument(
        "--version", dest="agent_version", help="catalog version (default: latest)"
    )
    install.add_argument("--sha256", help="expected sha256 of a local archive")

    sub.add_parser("list", help="list installed agents")

    catalog = sub.add_parser("catalog", help="list agents available in the catalog")
    catalog.add_argument("--refresh", action="store_true", help="skip the local cache")

    validate = sub.add_parser(
        "validate-registry", help="validate a registry.json change (used by the catalog CI)"
    )
    validate.add_argument("registry", type=Path)
    validate.add_argument("--base", type=Path, help="registry.json of the target branch")
    validate.add_argument("--deep", action="store_true", help="download and check new versions")
    validate.add_argument(
        "--install", action="store_true", help="also install new versions (implies --deep)"
    )
    validate.add_argument(
        "--scope",
        action="store_true",
        help="also check that new versions decline questions outside their task "
        "(implies --install; needs OPENROUTER_API_KEY; policy.json next to the registry)",
    )
    validate.add_argument("--report-dir", type=Path, help="write each scope check as CSV here")

    uninstall = sub.add_parser("uninstall", help="remove an installed agent")
    uninstall.add_argument("agent_id")

    run = sub.add_parser("run", help="start an agent server and keep it running until Ctrl+C")
    run.add_argument("agent_id")

    new = sub.add_parser("new-agent", help="create a new agent folder from the template")
    new.add_argument("directory", type=Path)
    new.add_argument("--id", dest="agent_id", required=True, help="snake_case agent id")
    new.add_argument("--name", required=True, help="display name")

    check = sub.add_parser("check", help="check an agent folder before publishing")
    check.add_argument("directory", type=Path)
    check.add_argument(
        "--install", action="store_true", help="also install it with uv and start it once"
    )
    check.add_argument(
        "--scope",
        action="store_true",
        help="also check that it declines questions outside its task, like the catalog does "
        "(implies --install; needs OPENROUTER_API_KEY)",
    )
    check.add_argument("--report", type=Path, help="write the scope check as CSV here")

    package = sub.add_parser("package", help="zip an agent for a release and print its entry")
    package.add_argument("directory", type=Path)
    package.add_argument("-o", "--output", type=Path, help="zip path (default: dist/<id>.zip)")

    serve = sub.add_parser("serve", help="run the local HTTP API for the desktop shell")
    serve.add_argument("--port", type=int, default=0, help="port to bind (0 = pick a free one)")
    serve.add_argument(
        "--exit-on-stdin-close",
        action="store_true",
        help="stop (and stop all agents) when stdin closes or reads 'shutdown'",
    )
    serve.add_argument("--parent-pid", type=int, help="stop when this process exits")
    return parser


def _print_event(event: InstallEvent, verbose: bool) -> None:
    if event.detail and not verbose:
        return
    prefix = "  " if event.detail else f"[{event.step.value}] "
    stream = sys.stderr if event.step is InstallStep.FAILED else sys.stdout
    print(f"{prefix}{event.message}", file=stream, flush=True)


def _install(manager: AgentManager, args: argparse.Namespace) -> None:
    def on_event(event: InstallEvent) -> None:
        _print_event(event, args.verbose)

    source = Path(args.source)
    if source.suffix.lower() == ".zip" or source.is_file():
        if args.agent_version:
            raise SystemExit("--version applies only to catalog installs")
        agent = manager.install_archive(
            source.resolve(), expected_sha256=args.sha256, on_event=on_event
        )
    else:
        if args.sha256:
            raise SystemExit("--sha256 applies only to local archives")
        agent = manager.install_from_registry(args.source, args.agent_version, on_event=on_event)
    print(f"installed {agent.id} {agent.version} -> {agent.install_dir}")


def _catalog(manager: AgentManager, refresh: bool) -> None:
    catalog, rows = manager.catalog(refresh=refresh)
    if catalog.stale:
        print("(offline: showing the last cached catalog)", file=sys.stderr)
    if not rows:
        print("the catalog is empty")
    for row in rows:
        installed = row["installed_version"] or "-"
        mark = " (update available)" if row["update_available"] else ""
        print(f"{row['id']:<24} {row['latest']:<10} installed: {installed:<10} {row['name']}{mark}")


def _validate_registry(settings: Settings, args: argparse.Namespace) -> int:
    import httpx

    from .errors import RegistryError
    from .policy import bundled_policy, parse_policy
    from .registry_validate import RegistryValidator, ScopeOptions, real_install_check

    # The catalog's policy sits next to its registry.json; a broken one fails the check.
    policy = None
    policy_path = args.registry.resolve().parent / "policy.json"
    if policy_path.is_file():
        try:
            policy = parse_policy(json.loads(policy_path.read_text("utf-8")), "catalog")
        except (ValueError, RegistryError) as exc:
            print(f"error: {policy_path.name}: {exc}", file=sys.stderr)
            return 1

    with httpx.Client(timeout=settings.http_timeout_s) as client:
        scope = None
        if args.scope:
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                print("error: --scope needs OPENROUTER_API_KEY", file=sys.stderr)
                return 2
            if policy is None:
                print("warning: no policy.json next to the registry; using the default")
                policy = bundled_policy()
            scope = ScopeOptions(api_key, policy, args.report_dir)
        install = args.install or args.scope
        validator = RegistryValidator(
            settings, client, real_install_check(settings, scope) if install else None
        )
        findings = validator.validate(args.registry, base=args.base, deep=args.deep or install)
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    for finding in findings:
        # GitHub Actions turns ::error lines into annotations on the PR.
        print(f"::error title=registry::{finding}" if github else f"error: {finding}")
    if findings:
        return 1
    print("registry is valid")
    return 0


def _developer_command(settings: Settings, args: argparse.Namespace) -> int:
    from .devtools import check_agent, install_check, new_agent, package_agent, registry_entry

    try:
        if args.command == "new-agent":
            created = new_agent(args.directory, args.agent_id, args.name)
            print(f"created {created}. Next: edit manifest.json, then `safe-agents-manager check`.")
            return 0
        manifest, findings = check_agent(args.directory)
        for finding in findings:
            print(finding, file=sys.stderr if finding.level == "error" else sys.stdout)
        if manifest is None or any(f.level == "error" for f in findings):
            return 1
        if args.command == "check":
            scope = None
            if args.scope:
                api_key = os.environ.get("OPENROUTER_API_KEY")
                if not api_key:
                    print("error: --scope needs OPENROUTER_API_KEY", file=sys.stderr)
                    return 2
                scope = (api_key, _current_policy(settings))
            if args.install or scope:
                print("installing in a temporary folder (uv + smoke test)...", flush=True)
                report = install_check(args.directory.resolve(), settings, scope)
                if report is not None:
                    print(report.summary())
                    if args.report:
                        report.write_csv(args.report)
                    if report.problem:
                        print(f"error: {report.problem}", file=sys.stderr)
                        return 1
            print(f"{manifest.agent_id} {manifest.version} looks good")
            return 0
        archive, digest = package_agent(args.directory, args.output)
        print(f"package: {archive}\nsha256:  {digest}\n")
        print(f"1. Upload {archive.name} to the GitHub Release v{manifest.version}")
        print("2. Add this entry to registry.json in the catalog (PR):")
        print(json.dumps(registry_entry(manifest, digest), indent=2, ensure_ascii=False))
        return 0
    except (ValueError, ManagerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _current_policy(settings: Settings) -> ContentPolicy:
    import httpx

    from .policy import PolicyClient, policy_url_for

    with httpx.Client(timeout=settings.http_timeout_s) as client:
        url = settings.policy_url or policy_url_for(settings.registry_url)
        return PolicyClient(
            url, settings.registry_cache_dir, client, settings.allowed_hosts, 0
        ).get()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _desktop_secrets() -> SecretStore:
    """OS credential store; ``SAFE_AGENTS_SECRETS=memory`` keeps them in RAM (tests, CI)."""
    backend: SecretBackend
    if os.environ.get("SAFE_AGENTS_SECRETS") == "memory":
        backend = MemoryBackend()
    else:
        backend = KeyringBackend()
    return SecretStore(backend, fallback=EnvSecretProvider())


def _log_to_file(settings: Settings) -> None:
    """Desktop mode has no console: keep a rotating log next to the agents' logs."""
    from logging.handlers import RotatingFileHandler

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.logs_dir / "manager.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _serve(manager: AgentManager, args: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_app
    from .lifecycle import watch_parent, watch_stdin

    # The desktop shell normally generates the token and passes it in; print it otherwise.
    token = os.environ.get("SAFE_AGENTS_TOKEN")
    ready: dict[str, object] = {"event": "ready", "port": args.port or _free_port()}
    if not token:
        token = secrets.token_urlsafe(32)
        ready["token"] = token
    app = create_app(manager, token)
    _log_to_file(manager.settings)
    # log_config=None: uvicorn's own messages (including unhandled errors) go to our handlers.
    config = uvicorn.Config(
        app, host="127.0.0.1", port=int(str(ready["port"])), log_level="info", log_config=None
    )
    server = uvicorn.Server(config)

    def request_exit() -> None:
        server.should_exit = True  # uvicorn then runs the lifespan shutdown (stops agents)

    if args.exit_on_stdin_close:
        watch_stdin(request_exit)
    if args.parent_pid:
        watch_parent(args.parent_pid, request_exit)

    def announce_when_listening() -> None:
        # The shell connects as soon as it reads this line, so only print it once bound.
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            print(json.dumps(ready), flush=True)

    manager.start_background()
    threading.Thread(target=announce_when_listening, name="ready", daemon=True).start()
    server.run()


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
        manager.supervisor.stop_all()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if args.home:
        settings = dataclasses.replace(settings, home=args.home)

    if args.command == "validate-registry":
        return _validate_registry(settings, args)
    if args.command in ("new-agent", "check", "package"):
        return _developer_command(settings, args)

    manager: AgentManager | None = None
    try:
        provider: SecretProvider = (
            _desktop_secrets() if args.command == "serve" else EnvSecretProvider()
        )
        manager = AgentManager(settings, provider)
        if args.command == "install":
            _install(manager, args)
        elif args.command == "catalog":
            _catalog(manager, args.refresh)
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
            _serve(manager, args)
    except ManagerError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if manager is not None:
            manager.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
