"""Application entrypoint: lifespan, middleware, router mounting."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .api.routes import get_registry, router
from .logging_setup import setup_logging, trace_id_var
from .settings import get_settings

log = logging.getLogger("app")


class TraceMiddleware(BaseHTTPMiddleware):
    """给每个请求分配 trace_id，写进日志和响应头。"""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex[:16]
        token = trace_id_var.set(trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled error",
                extra={
                    "event": "http.error",
                    "path": request.url.path,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            trace_id_var.reset(token)
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error", "trace_id": trace_id},
                headers={"x-trace-id": trace_id},
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "request",
            extra={
                "event": "http.request",
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        response.headers["x-trace-id"] = trace_id
        trace_id_var.reset(token)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    registry = get_registry()  # fail fast：配置写错就在这里炸，不要等到第一个请求
    available = [m["name"] for m in registry.list_models() if m["available"]]
    log.info(
        "service started",
        extra={
            "event": "app.startup",
            "detail": f"models_available={available or 'none (missing api keys)'}",
        },
    )
    if not available:
        log.warning(
            "no api key found, /chat will return 503",
            extra={"event": "app.no_credentials"},
        )
    yield
    log.info("service stopped", extra={"event": "app.shutdown"})


app = FastAPI(
    title="AI Service Starter",
    version=__version__,
    description="A minimal, production-shaped FastAPI skeleton for LLM applications.",
    lifespan=lifespan,
)

app.add_middleware(TraceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-trace-id"],
)

app.include_router(router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "ai-service-starter", "version": __version__, "docs": "/docs"}
