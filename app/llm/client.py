"""LLM 调用层：重试、降级、流式解析。

设计原则：
- SDK 自带重试关掉（max_retries=0），重试只在一处发生，否则失败会被放大成 3x3 次。
- 降级按 fallbacks 链逐个尝试；缺 key 的供应商直接跳过，不算失败。
- 流式一旦开始吐字就不再切换模型，否则用户会看到两个回答粘在一起。
"""

import asyncio
import logging
from typing import AsyncIterator

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from ..registry import MissingCredentialError, ModelSpec, Registry
from ..schema import ChatRequest, Usage
from ..settings import Settings

log = logging.getLogger("app.llm")


class AllModelsFailed(RuntimeError):
    pass


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, RateLimitError, APIConnectionError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    return False


async def _with_retry(call, settings: Settings):
    """指数退避重试。只重试「值得重试」的错误，4xx 直接抛。"""
    for attempt in range(settings.max_retries + 1):
        try:
            return await call()
        except Exception as exc:
            if not _is_retryable(exc) or attempt == settings.max_retries:
                raise
            delay = settings.retry_base_delay * (2**attempt)
            log.warning(
                "llm retry",
                extra={
                    "event": "llm.retry",
                    "attempt": attempt + 1,
                    "delay_s": round(delay, 2),
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )
            await asyncio.sleep(delay)


def _payload(req: ChatRequest, spec: ModelSpec) -> dict:
    return {
        "model": spec.model,
        "messages": [m.model_dump() for m in req.messages],
        "temperature": spec.temperature if req.temperature is None else req.temperature,
        "max_tokens": spec.max_tokens if req.max_tokens is None else req.max_tokens,
    }


def _to_usage(raw) -> Usage:
    if raw is None:
        return Usage()
    return Usage(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw, "total_tokens", 0) or 0,
    )


# ---------------------------------------------------------------- 非流式
async def complete(
    req: ChatRequest, registry: Registry, settings: Settings
) -> tuple[str, Usage, str, bool]:
    """返回 (content, usage, 实际使用的模型名, 是否走了降级)。"""
    primary = registry.spec(req.model).name
    errors: list[str] = []

    for idx, name in enumerate(registry.chain(primary)):
        spec = registry.spec(name)
        try:
            client = registry.client(spec.provider)
        except MissingCredentialError as exc:
            errors.append(f"{name}: {exc}")
            continue

        try:
            resp = await _with_retry(
                lambda: client.chat.completions.create(**_payload(req, spec)), settings
            )
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning(
                "model failed, trying fallback",
                extra={"event": "llm.fallback", "model": name, "detail": errors[-1]},
            )
            continue

        content = resp.choices[0].message.content or ""
        usage = _to_usage(getattr(resp, "usage", None))
        return content, usage, name, idx > 0

    raise AllModelsFailed("; ".join(errors))


# ---------------------------------------------------------------- 流式
async def _open_stream(client, req: ChatRequest, spec: ModelSpec):
    payload = _payload(req, spec)
    try:
        return await client.chat.completions.create(
            **payload, stream=True, stream_options={"include_usage": True}
        )
    except (APIStatusError, TypeError):
        # 部分供应商不支持 stream_options，退回普通流式（拿不到 usage）
        return await client.chat.completions.create(**payload, stream=True)


def _parse_chunk(chunk) -> dict | None:
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        return {"type": "usage", "usage": _to_usage(usage).model_dump()}

    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return None
    content = getattr(choices[0].delta, "content", None)
    if not content:
        return None
    return {"type": "delta", "content": content}


async def stream(
    req: ChatRequest, registry: Registry, settings: Settings
) -> AsyncIterator[dict]:
    primary = registry.spec(req.model).name
    errors: list[str] = []

    for idx, name in enumerate(registry.chain(primary)):
        spec = registry.spec(name)
        try:
            client = registry.client(spec.provider)
            stream_obj = await _open_stream(client, req, spec)
        except MissingCredentialError as exc:
            errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning(
                "stream open failed",
                extra={"event": "llm.stream_open_failed", "model": name, "detail": errors[-1]},
            )
            continue

        yield {"type": "meta", "model": name, "fallback_used": idx > 0}

        try:
            async for chunk in stream_obj:
                event = _parse_chunk(chunk)
                if event:
                    yield event
        except Exception as exc:
            yield {"type": "error", "detail": f"{type(exc).__name__}: {exc}"}
            return
        return

    yield {"type": "error", "detail": "all models failed: " + "; ".join(errors)}
