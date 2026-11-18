"""Dashboard HTTP server for the RPent-style Live Agent Command & Status Monitor.

Supports:
1. RPent Live Monitor contract (`/api/commands`, `/api/session/config`, `/api/llm/check`,
   `/api/session/messages`, `/api/session/interrupt`, `/api/session/state`,
   `/api/session/primitives`, `/api/session/primitive`, `/api/session/transcript`,
   `/api/session/frame`, `/api/session/video`, `/api/session/action-video`,
   `/api/session/stream`).
2. Comm-Harness catalog and run routes (`/api/catalog`, `/api/check`, `/api/runs`,
   `/api/probe`, `/api/compare`, `/api/runs/{run_id}`, `/api/runs/{run_id}/monitor`,
   `/api/runs/{run_id}/stop`, `/api/runs/{run_id}/image`).
"""

import asyncio
from contextlib import asynccontextmanager
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from comm_harness.dashboard.interaction import (
    DashboardMessageConflictError,
    InteractionUnavailableError,
    UnknownDashboardMessageError,
)
from comm_harness.dashboard.state import (
    DashboardState,
    PrimitiveArgumentError,
    PrimitiveConfigError,
    redact,
)


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def create_app(
    state: DashboardState,
    *,
    language: str = "zh-cn",
    planner: Optional[str] = "api",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Any:
    from fastapi import Body, FastAPI, Request
    from fastapi.responses import (
        FileResponse,
        HTMLResponse,
        JSONResponse,
        Response,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, ConfigDict, Field

    class Selection(BaseModel):
        model_config = ConfigDict(extra="forbid")
        task: str = Field(min_length=1, max_length=150)
        profile: str = Field(min_length=1, max_length=150)

    @asynccontextmanager
    async def lifespan(app: Any):
        yield
        await asyncio.to_thread(state.close)

    # Infer default model from catalog if not explicitly provided
    resolved_model = model
    if not resolved_model and hasattr(state, "catalog"):
        try:
            cat = state.catalog()
            profiles = cat.get("profiles", [])
            if profiles:
                p0 = profiles[0]
                prov = p0.get("provider", "openai")
                mod = p0.get("model", "deepseek-flash")
                resolved_model = f"{prov}:{mod}" if prov and ":" not in str(mod) else str(mod)
        except Exception:
            resolved_model = "deepseek:deepseek-flash"

    planner_config: Dict[str, Optional[str]] = {
        "planner": planner or "api",
        "model": resolved_model,
    }
    llm_check_lock = threading.Lock()
    lang = "en" if language == "en" else "zh-cn"
    assets = Path(__file__).parent
    index_template = (assets / "index.html").read_text(encoding="utf-8")

    app = FastAPI(title="Comm Harness · Live Monitor", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin(request: Request, call_next: Any) -> Any:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"error": "不接受跨站操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def expected_error(request: Request, exc: Exception) -> JSONResponse:
        code = (
            404
            if isinstance(exc, FileNotFoundError)
            else 409
            if isinstance(exc, RuntimeError)
            else 400
        )
        return JSONResponse({"error": redact(str(exc))}, status_code=code)

    for error in (ValueError, FileNotFoundError, RuntimeError):
        app.add_exception_handler(error, expected_error)

    @app.get("/")
    def index() -> HTMLResponse:
        html = index_template.replace("__DASHBOARD_LANGUAGE__", lang)
        return HTMLResponse(html)

    @app.get("/healthz")
    def health() -> Dict[str, bool]:
        return {"ok": True}

    # ------------------------------------------------------------------
    # RPent Live Monitor Endpoints
    # ------------------------------------------------------------------

    @app.get("/api/commands")
    def api_commands() -> JSONResponse:
        return JSONResponse(state.dashboard_spec)

    @app.get("/api/session/config")
    def api_session_config() -> JSONResponse:
        return JSONResponse(planner_config)

    @app.post("/api/session/config")
    def api_update_session_config(
        payload: Dict[str, Any] = Body(default={}),
    ) -> JSONResponse:
        if planner_config.get("planner") == "flash":
            return JSONResponse(
                {"error": "Flash replay has no language model"}, status_code=422
            )
        if set(payload) != {"model"}:
            return JSONResponse(
                {"error": "Only model can be changed for the next task"},
                status_code=422,
            )
        try:
            state.set_next_model(payload["model"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        planner_config["model"] = str(payload["model"]).strip()
        return JSONResponse(planner_config)

    @app.post("/api/llm/check")
    def api_llm_check(payload: Dict[str, Any] = Body(default={})) -> JSONResponse:
        if not llm_check_lock.acquire(blocking=False):
            return JSONResponse(
                {"error": "a check is already running"}, status_code=409
            )
        try:
            from comm_harness.planner.check import LlmCheckRequest, check_llm
            from comm_harness.cli.main import read_key

            api_key = read_key(getattr(state, "env_file", None)) or None
            result = check_llm(
                LlmCheckRequest(
                    planner=str(
                        payload.get("planner") or planner_config.get("planner") or "api"
                    ),
                    model=(payload.get("model") or planner_config.get("model") or None),
                    base_url=base_url or None,
                    api_key=api_key,
                )
            )
        finally:
            llm_check_lock.release()
        return JSONResponse(result.as_dict())

    @app.post("/api/session/messages")
    def api_submit_message(
        payload: Dict[str, Any] = Body(default={}),
    ) -> JSONResponse:
        try:
            state.submit_input(
                payload.get("text"),
                operator_context=payload.get("operator_context"),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except InteractionUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"ok": True}, status_code=202)

    @app.delete("/api/session/messages/{message_id}")
    def api_withdraw_message(message_id: str) -> JSONResponse:
        try:
            state.withdraw_message(message_id)
        except UnknownDashboardMessageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (DashboardMessageConflictError, InteractionUnavailableError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"ok": True})

    @app.post("/api/session/interrupt")
    def api_interrupt() -> JSONResponse:
        try:
            result = state.request_interrupt()
        except InteractionUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        if result == "noop":
            return JSONResponse({"status": "noop", "interrupt_requested": False})
        return JSONResponse(
            {
                "status": "requested",
                "interrupt_requested": True,
                "deduplicated": result == "duplicate",
            },
            status_code=202,
        )

    @app.get("/api/session/state")
    def api_session_state(timeline_since: int = 0) -> JSONResponse:
        return JSONResponse(state.session_detail(timeline_since=timeline_since))

    @app.get("/api/session/primitives")
    def api_primitives() -> JSONResponse:
        try:
            primitives = state.primitive_specs()
        except InteractionUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"primitives": primitives})

    @app.post("/api/session/primitive")
    def api_execute_primitive(payload: Any = Body(default=None)) -> JSONResponse:
        if not isinstance(payload, dict):
            return JSONResponse(
                {"error": "request body must be a JSON object"}, status_code=422
            )
        name = payload.get("name")
        arguments = payload.get("arguments")
        if not isinstance(name, str) or not name.strip():
            return JSONResponse(
                {"error": "name must be a non-empty string"}, status_code=422
            )
        if not isinstance(arguments, dict):
            return JSONResponse(
                {"error": "arguments must be a JSON object"}, status_code=422
            )
        try:
            tool_result = state.execute_primitive(name, arguments)
        except InteractionUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except PrimitiveArgumentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except PrimitiveConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        result = tool_result.to_dict() if hasattr(tool_result, "to_dict") else tool_result
        if isinstance(result, dict) and result.get("error") is not None:
            error = " ".join(str(result["error"]).split())[:500]
            return JSONResponse(
                {"error": error or "primitive execution failed"}, status_code=422
            )
        return JSONResponse({"ok": True})

    @app.get("/api/session/transcript")
    def api_transcript(since: int = 0) -> JSONResponse:
        return JSONResponse({"events": state.events_since(since)})

    @app.get("/api/session/frame")
    def api_frame(kind: str) -> Response:
        frame = state.frame(kind)
        if frame is None:
            return Response(status_code=404)
        return Response(frame, media_type="image/png")

    @app.get("/api/session/video")
    def api_video() -> Response:
        if not state.has_video():
            return Response(status_code=404)
        return FileResponse(
            state.video_path,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/session/action-video")
    def api_action_video(step: int) -> Response:
        path = state.action_video_path(step)
        if path is None:
            return Response(status_code=404)
        return FileResponse(
            path,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/session/stream")
    def api_stream() -> StreamingResponse:
        async def gen():
            version = -1
            while True:
                version, snapshot = await asyncio.to_thread(
                    state.wait_for_snapshot,
                    version,
                    15.0,
                )
                if snapshot is None:
                    yield ": keepalive\n\n"
                else:
                    yield f"data: {json.dumps(snapshot)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # Existing Comm-Harness Catalog & Run Inspection Endpoints
    # ------------------------------------------------------------------

    @app.get("/api/catalog")
    def catalog():
        return state.catalog()

    @app.post("/api/check")
    def check(selection: Selection):
        return state.check(selection.task, selection.profile)

    @app.get("/api/runs")
    def runs():
        return {"runs": state.list_runs()}

    @app.post("/api/probe")
    def probe(selection: Selection):
        return state.probe(selection.task, selection.profile)

    @app.get("/api/compare")
    def compare(ids: str):
        return state.compare(ids.split(","))

    @app.get("/api/runs/{run_id}/monitor")
    def monitor(run_id: str):
        return state.monitor(run_id)

    @app.post("/api/runs", status_code=202)
    def start(selection: Selection):
        return state.start(selection.task, selection.profile)

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str):
        return state.detail(run_id)

    @app.post("/api/runs/{run_id}/stop", status_code=202)
    def stop(run_id: str):
        return state.stop(run_id)

    @app.get("/api/runs/{run_id}/image")
    def image(run_id: str, path: str):
        return FileResponse(state.image_path(run_id, path))

    app.mount("/static", StaticFiles(directory=assets / "static"), name="static")
    return app


class DashboardServer:
    """Threaded FastAPI server exposing one RPent-style Dashboard Session."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        language: str = "zh-cn",
        state: DashboardState,
        planner: Optional[str] = "api",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self._state = state
        self._app = create_app(
            state,
            language=language,
            planner=planner,
            model=model,
            base_url=base_url,
        )
        self._server: Optional[Any] = None

    def start(self) -> str:
        """Launch uvicorn in a daemon thread; return the base URL once serving."""
        import uvicorn

        port = self.port or _pick_free_port(self.host)
        config = uvicorn.Config(
            self._app, host=self.host, port=port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, daemon=True).start()

        t0 = time.time()
        while not self._server.started and time.time() - t0 < 10:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError(f"dashboard server did not start on {self.host}:{port}")
        self.port = port
        return f"http://{self.host}:{port}"


def serve(
    state: DashboardState,
    host: str = "127.0.0.1",
    port: int = 8091,
    *,
    language: str = "zh-cn",
    planner: Optional[str] = "api",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    try:
        import uvicorn

        app = create_app(
            state,
            language=language,
            planner=planner,
            model=model,
            base_url=base_url,
        )
    except ImportError as exc:
        raise SystemExit(
            "Web 界面需要安装 fastapi 和 uvicorn：pip install -e '.[ui]'"
        ) from exc
    print(f"Comm Harness: http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")
