"""Structured JSON logging with a per-request trace id.

Every log line carries trace_id, so one request can be reconstructed with:
    docker logs <container> | grep <trace_id>
"""

import json
import logging
import sys
import time
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

_EXTRA_FIELDS = (
    "event",
    "model",
    "provider",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "attempt",
    "path",
    "status",
    "detail",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "msg": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn 默认 handler 会打出非 JSON 的行，混在结构化日志里很难解析。
    # 撤掉它自己的 handler，让记录冒泡到 root 统一格式化。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # access log 由 TraceMiddleware 统一打，这里关掉避免重复
    logging.getLogger("uvicorn.access").disabled = True
