"""Local HTTP API used by the desktop shell. Binds to 127.0.0.1 and requires a bearer token.

No ``from __future__ import annotations`` here: FastAPI must evaluate the dependency
annotations at runtime, and they refer to objects local to ``create_app``.
"""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__
from .errors import ManagerError
from .service import AgentManager

AGENT_ID_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
AgentId = Annotated[str, Path(pattern=AGENT_ID_PATTERN)]
SSE_POLL_S = 0.2
SSE_KEEPALIVE_S = 15.0


class InstallRequest(BaseModel):
    archive_path: str = Field(description="Absolute path of a local agent zip archive")
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class RegistryInstallRequest(BaseModel):
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(manager: AgentManager, token: str) -> FastAPI:
    bearer = HTTPBearer(auto_error=False)

    def require_token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        manager.shutdown()  # never leave agent servers running after the manager exits

    app = FastAPI(title="Safe-Agents Manager", version=__version__, lifespan=lifespan)

    @app.exception_handler(ManagerError)
    async def handle_manager_error(_request: Request, exc: ManagerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    router = APIRouter(dependencies=[Depends(require_token)])

    @router.get("/agents")
    def list_agents() -> list[dict[str, Any]]:
        return manager.list_agents()

    @router.get("/agents/{agent_id}")
    def get_agent(agent_id: AgentId) -> dict[str, Any]:
        return manager.describe(manager.get_agent(agent_id))

    @router.post("/agents/install", status_code=status.HTTP_202_ACCEPTED)
    def install(body: InstallRequest) -> dict[str, Any]:
        job = manager.submit_install(FsPath(body.archive_path), expected_sha256=body.sha256)
        return job.to_dict()

    @router.get("/registry")
    def registry(refresh: bool = False) -> dict[str, Any]:
        catalog, rows = manager.catalog(refresh=refresh)
        return {"agents": rows, "fetched_at": catalog.fetched_at, "stale": catalog.stale}

    @router.post("/registry/{agent_id}/install", status_code=status.HTTP_202_ACCEPTED)
    def install_from_registry(
        agent_id: AgentId, body: RegistryInstallRequest | None = None
    ) -> dict[str, Any]:
        version = body.version if body else None
        return manager.submit_registry_install(agent_id, version).to_dict()

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @router.get("/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def stream() -> AsyncIterator[str]:
            sent = 0
            idle = 0.0
            while True:
                done = job.done  # read before draining so no final event is missed
                events = job.events_since(sent)
                for event in events:
                    yield _sse("progress", event.to_dict())
                sent += len(events)
                if done and not job.events_since(sent):
                    yield _sse("done", job.to_dict())
                    return
                idle = 0.0 if events else idle + SSE_POLL_S
                if idle >= SSE_KEEPALIVE_S:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(SSE_POLL_S)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/agents/{agent_id}/start")
    def start(agent_id: AgentId) -> dict[str, Any]:
        handle = manager.start(agent_id)
        return {
            "agent_id": agent_id,
            "graph_id": handle.graph_id,
            "url": handle.url,
            "port": handle.port,
            "started_at": handle.started_at,
        }

    @router.post("/agents/{agent_id}/stop")
    def stop(agent_id: AgentId) -> dict[str, Any]:
        return {"agent_id": agent_id, "stopped": manager.stop(agent_id)}

    @router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
    def uninstall(agent_id: AgentId) -> None:
        manager.uninstall(agent_id)

    @router.get("/agents/{agent_id}/logs")
    def logs(agent_id: AgentId, tail: Annotated[int, Query(ge=1, le=2000)] = 200) -> dict[str, Any]:
        return {"agent_id": agent_id, "lines": manager.logs(agent_id, tail)}

    app.include_router(router)
    return app
