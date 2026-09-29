"""OpenAI 兼容 LLM Provider（Qwen / GLM / DeepSeek / 本地 Ollama）。

借鉴 zunicorn-agent `core/model_router.py` 的「多 Provider 抽象 + 档位映射」思路，
但 Provider 实现独立、零强制外部依赖：无 API Key 时由 MockProvider 兜底（见 router.py）。
"""
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings


@dataclass
class LLMResult:
    text: str
    model: str
    tier: str
    is_mock: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


class OpenAIProvider:
    """OpenAI Chat Completions 兼容提供商。"""

    def __init__(self, base_url: str, api_key: Optional[str], model: str, tier: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.tier = tier

    async def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        task_type: Optional[str] = None,  # 仅用于埋点/日志，与 MockProvider 保持接口一致
    ) -> LLMResult:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                self.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        dur = int((time.perf_counter() - t0) * 1000)
        return LLMResult(
            text=text,
            model=self.model,
            tier=self.tier,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=dur,
        )
