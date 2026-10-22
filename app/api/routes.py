"""HTTP 路由层。只负责协议转换：校验输入 → 调 llm 层 → 组织输出。"""

import json
import logging
import time
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import __version__
from ..llm import AllModelsFailed, complete, stream
from ..registry import Registry, UnknownModelError
from ..schema import ChatRequest, ChatResponse, Usage
from ..settings import get_settings

log = logging.getLogger("app.api")
router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲，否则流式会被攒成一坨
}


@lru_cache
def get_registry() -> Registry:
    return Registry(get_settings().config_path)


@router.get("/health", tags=["ops"])
async def health(registry: Registry = Depends(get_registry)) -> dict:
    available = [m["name"] for m in registry.list_models() if m["available"]]
    return {
        "status": "ok" if available else "degraded",
        "version": __version__,
        "models_available": available,
        "default_model": registry.effective_default(),
        "configured_default": registry.default,
    }


@router.get("/models", tags=["ops"])
async def models(registry: Registry = Depends(get_registry)) -> dict:
    return {"default": registry.default, "models": registry.list_models()}


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    req: ChatRequest, registry: Registry = Depends(get_registry)
) -> ChatResponse:
    started = time.perf_counter()
    try:
        content, usage, model_name, fallback = await complete(req, registry, get_settings())
    except UnknownModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AllModelsFailed as exc:
        log.error("all models failed", extra={"event": "chat.failed", "detail": str(exc)})
        raise HTTPException(status_code=503, detail=f"no model available: {exc}") from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "chat completed",
        extra={
            "event": "chat.completed",
            "model": model_name,
            "latency_ms": latency_ms,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    )
    return ChatResponse(
        model=model_name,
        content=content,
        usage=usage,
        latency_ms=latency_ms,
        fallback_used=fallback,
    )


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest, registry: Registry = Depends(get_registry)):
    settings = get_settings()

    async def event_source():
        started = time.perf_counter()
        model_used = "unknown"
        usage = Usage()
        try:
            async for event in stream(req, registry, settings):
                if event.get("type") == "meta":
                    model_used = event.get("model", model_used)
                elif event.get("type") == "usage":
                    usage = Usage(**event.get("usage", {}))
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except UnknownModelError as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
        except Exception as exc:  # 流已经开始，异常只能以事件形式下发
            log.exception("stream crashed")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
        finally:
            log.info(
                "stream finished",
                extra={
                    "event": "chat.stream_finished",
                    "model": model_used,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                },
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream", headers=SSE_HEADERS)
