"""无密钥兜底 Provider。

当三档模型均未配置 API Key 时启用，保证「零网络也能完整演示」双产品线全流程。
生成内容以规则模板 + 已灌入的种子知识库要点填充，并由调用方（service 层）
依据 `is_mock` 标志改用本地模板生成——避免与真实模型输出混淆。
"""
import time
from typing import Optional

from app.ai.providers import LLMResult


class MockProvider:
    def __init__(self, tier: str) -> None:
        self.tier = tier

    async def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        task_type: Optional[str] = None,
    ) -> LLMResult:
        # 真实生成由 service 层在检测到 is_mock 后用本地模板 + 检索知识完成；
        # 此处返回空文本，调用方据此判断走本地生成分支。
        t0 = time.perf_counter()
        dur = int((time.perf_counter() - t0) * 1000)
        return LLMResult(
            text="",
            model=f"mock-{self.tier}",
            tier=self.tier,
            is_mock=True,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=0,
            duration_ms=dur,
        )
