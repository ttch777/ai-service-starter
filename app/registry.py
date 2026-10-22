"""Model registry: config-driven provider and model resolution.

Adding a provider = one block in config/models.yaml. No code change.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from openai import AsyncOpenAI


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    model: str
    temperature: float = 0.3
    max_tokens: int = 2048


class UnknownModelError(KeyError):
    pass


class MissingCredentialError(RuntimeError):
    pass


class Registry:
    def __init__(self, config_path: Path) -> None:
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"config not found: {self.config_path} (cwd={Path.cwd()})"
            )
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

        self.providers: dict[str, dict] = raw.get("providers", {})
        self.models: dict[str, ModelSpec] = {
            name: ModelSpec(name=name, **spec) for name, spec in raw.get("models", {}).items()
        }
        self.fallbacks: dict[str, list[str]] = raw.get("fallbacks", {})
        self.default: str | None = raw.get("default")
        self._clients: dict[str, AsyncOpenAI] = {}

    # -- lookup ---------------------------------------------------------
    def _available(self, name: str) -> bool:
        spec = self.models.get(name)
        if spec is None:
            return False
        provider = self.providers.get(spec.provider)
        return bool(provider and os.environ.get(provider["api_key_env"]))

    def effective_default(self) -> str | None:
        """配置的 default 优先；它没有凭证时退到第一个可用模型。

        不这样做的话，只有一家 key 的时候每次请求都要先撞两个 4xx/缺凭证
        才降级到能用的那个 —— 白等两轮。
        """
        if self.default and self._available(self.default):
            return self.default
        for name in self.models:
            if self._available(name):
                return name
        return self.default if self.default in self.models else None

    def spec(self, name: str | None) -> ModelSpec:
        key = name or self.effective_default()
        if not key or key not in self.models:
            raise UnknownModelError(f"unknown model: {key!r}")
        return self.models[key]

    def chain(self, name: str) -> list[str]:
        """Primary model first, then its configured fallbacks."""
        seen = [name]
        for item in self.fallbacks.get(name, []):
            if item not in seen and item in self.models:
                seen.append(item)
        return seen

    def list_models(self) -> list[dict]:
        """Public view for /models. Never leaks credentials."""
        return [
            {
                "name": m.name,
                "provider": m.provider,
                "model": m.model,
                "available": bool(os.environ.get(self.providers[m.provider]["api_key_env"])),
                "fallbacks": self.fallbacks.get(m.name, []),
            }
            for m in self.models.values()
        ]

    # -- clients --------------------------------------------------------
    def client(self, provider: str) -> AsyncOpenAI:
        if provider in self._clients:
            return self._clients[provider]

        cfg = self.providers.get(provider)
        if cfg is None:
            raise MissingCredentialError(f"unknown provider: {provider}")

        key = os.environ.get(cfg["api_key_env"], "").strip()
        if not key:
            raise MissingCredentialError(f"missing env var {cfg['api_key_env']}")

        client = AsyncOpenAI(
            base_url=cfg["base_url"],
            api_key=key,
            timeout=cfg.get("timeout", 60),
            max_retries=0,  # 我们自己在外面做重试，避免双重放大
        )
        self._clients[provider] = client
        return client
