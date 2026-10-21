"""PyInstaller entry point for the desktop sidecar (the package itself uses relative imports)."""

from safe_agents_manager.cli import main

raise SystemExit(main())
