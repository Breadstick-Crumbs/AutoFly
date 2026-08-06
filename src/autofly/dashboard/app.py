from __future__ import annotations

import hmac
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from autofly.config import WatchConfig
from autofly.errors import AutoFlyError

from .airports import AIRPORT_SUGGESTIONS
from .service import DashboardService

ASSET_DIR = Path(__file__).with_name("assets")
TEMPLATE_DIR = Path(__file__).with_name("templates")


class WatchMutation(BaseModel):
    watch: WatchConfig
    original_id: str | None = None


class ToggleMutation(BaseModel):
    enabled: bool


class CheckMutation(BaseModel):
    watch_id: str | None = None


def create_app(config_path: Path, *, password: str | None = None) -> FastAPI:
    dashboard = DashboardService(config_path)
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    security = HTTPBasic(auto_error=False)
    application = FastAPI(
        title="AutoFly Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_auth(
        credentials: HTTPBasicCredentials | None = Depends(security),  # noqa: B008
    ) -> None:
        if password is None:
            return
        valid = bool(
            credentials
            and hmac.compare_digest(credentials.username, "autofly")
            and hmac.compare_digest(credentials.password, password)
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="AutoFly"'},
            )

    def require_mutation_guard(request: Request) -> None:
        if request.headers.get("x-autofly-request") != "dashboard":
            raise HTTPException(status_code=403, detail="Missing dashboard request header")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get(
                "host"
            ):
                raise HTTPException(status_code=403, detail="Cross-origin request rejected")

    router = APIRouter(dependencies=[Depends(require_auth)])

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @router.get("/api/state")
    def state_payload() -> dict[str, object]:
        return dashboard.state()

    @router.get("/api/airports")
    def airport_payload() -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in AIRPORT_SUGGESTIONS]

    @router.get("/api/history")
    def history_payload(
        watch_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        return dashboard.history(watch_id, limit)

    mutation_dependencies = [Depends(require_mutation_guard)]

    @router.post("/api/watches", dependencies=mutation_dependencies, status_code=201)
    def create_watch(payload: WatchMutation) -> dict[str, object]:
        config = dashboard.save_watch(payload.watch, None)
        return {"saved": payload.watch.id, "watch_count": len(config.watches)}

    @router.put("/api/watches/{watch_id}", dependencies=mutation_dependencies)
    def update_watch(watch_id: str, payload: WatchMutation) -> dict[str, object]:
        config = dashboard.save_watch(payload.watch, watch_id)
        return {"saved": payload.watch.id, "watch_count": len(config.watches)}

    @router.post("/api/watches/{watch_id}/enabled", dependencies=mutation_dependencies)
    def toggle_watch(watch_id: str, payload: ToggleMutation) -> dict[str, object]:
        dashboard.set_enabled(watch_id, payload.enabled)
        return {"watch_id": watch_id, "enabled": payload.enabled}

    @router.post("/api/checks", dependencies=mutation_dependencies, status_code=202)
    def start_check(payload: CheckMutation) -> dict[str, object]:
        return asdict(dashboard.start_check(payload.watch_id))

    @router.get("/api/checks/{job_id}")
    def check_status(job_id: str) -> dict[str, object]:
        return asdict(dashboard.job(job_id))

    @application.exception_handler(AutoFlyError)
    async def autofly_error(_: Request, exc: AutoFlyError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    application.include_router(router)
    application.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")

    @application.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    return application
