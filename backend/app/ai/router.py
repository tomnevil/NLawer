"""模型路由：任务类型 -> 模型档位，三级降级 env -> config -> Mock。

借鉴 zunicorn-agent `ModelRouter` 分档 + YouTubeBoardcast `_resolve_zunicorn_src`
三级解析：未配置 API Key 时自动落到 MockProvider（**仅限非生产环境**），
保证零网络可完整演示。

安全约束：MockProvider 只返回占位文本，在真实法律业务中会产出看似合理
但无依据的内容。因此**生产环境缺失 Key 时直接拒绝服务**，而非静默降级
（见 `_provider`）。Mock 输出亦会被标记 `is_mock=True` 供审计追溯。
"""
from enum import Enum
from typing import Optional

from app.ai.mock_provider import MockProvider
from app.ai.providers import LLMResult, OpenAIProvider
from app.config import settings
from app.core.errors import ConfigurationError


class TaskType(str, Enum):
    QA = "qa"
    DOCUMENT = "document"
    COPILOT = "copilot"
    EVIDENCE = "evidence"
    COMPLIANCE = "compliance"
    CALCULATION = "calculation"
    #: 合同审查：需要逐条通读并判定法律风险，属高精度判定任务
    CONTRACT_REVIEW = "contract_review"


class ModelTier(str, Enum):
    CHEAP = "cheap"  # 通用问答 / 常规文书
    STRONG = "strong"  # 赔偿计算 / 合规判定 / 证据分析
    LOCAL = "local"  # 敏感数据不出域


# 任务 -> 默认档位
TIER_FOR_TASK: dict[TaskType, ModelTier] = {
    TaskType.QA: ModelTier.CHEAP,
    TaskType.DOCUMENT: ModelTier.CHEAP,
    TaskType.COPILOT: ModelTier.CHEAP,
    TaskType.EVIDENCE: ModelTier.STRONG,
    TaskType.COMPLIANCE: ModelTier.STRONG,
    TaskType.CALCULATION: ModelTier.STRONG,
    # 合同审查按次 99 元交付，且结论会被直接用于签署决策——与合规判定同级。
    # 用 CHEAP 档省钱在这里是**负收益**：一次误判的代价远超 token 差价。
    TaskType.CONTRACT_REVIEW: ModelTier.STRONG,
}


def _provider(tier: ModelTier) -> Optional[OpenAIProvider]:
    if tier == ModelTier.CHEAP and settings.LLM_CHEAP_API_KEY:
        return OpenAIProvider(
            settings.LLM_CHEAP_BASE_URL, settings.LLM_CHEAP_API_KEY, settings.LLM_CHEAP_MODEL, "cheap"
        )
    if tier == ModelTier.STRONG and settings.LLM_STRONG_API_KEY:
        return OpenAIProvider(
            settings.LLM_STRONG_BASE_URL, settings.LLM_STRONG_API_KEY, settings.LLM_STRONG_MODEL, "strong"
        )
    if tier == ModelTier.LOCAL and settings.LLM_LOCAL_API_KEY:
        return OpenAIProvider(
            settings.LLM_LOCAL_BASE_URL, settings.LLM_LOCAL_API_KEY, settings.LLM_LOCAL_MODEL, "local"
        )
    return None


# 环境变量名映射，便于错误信息直接告诉运维该配哪一项
_ENV_KEY_NAME: dict[ModelTier, str] = {
    ModelTier.CHEAP: "LLM_CHEAP_API_KEY",
    ModelTier.STRONG: "LLM_STRONG_API_KEY",
    ModelTier.LOCAL: "LLM_LOCAL_API_KEY",
}


def provider_status() -> dict[str, bool]:
    """各档位是否已配置真实 Provider（供 /health 暴露，便于运维自查）。"""
    return {tier.value: _provider(tier) is not None for tier in ModelTier}


class ModelRouter:
    """模型路由 + 三级降级（生产禁止 Mock 降级）。"""

    async def complete(
        self,
        task: TaskType,
        prompt: str,
        *,
        system: Optional[str] = None,
        sensitive: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> LLMResult:
        tier = ModelTier.LOCAL if sensitive else TIER_FOR_TASK[task]
        provider = _provider(tier)

        if provider is None:
            # 生产环境：拒绝静默降级，避免用占位文本冒充法律分析
            if settings.ENVIRONMENT == "production":
                raise ConfigurationError(
                    f"生产环境未配置 {tier.value} 档模型（{_ENV_KEY_NAME[tier]} 为空），"
                    "已拒绝以 Mock 结果代替真实模型输出",
                    details={"tier": tier.value, "env_key": _ENV_KEY_NAME[tier]},
                )
            provider = MockProvider(tier.value)

        return await provider.complete(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            task_type=task.value,
        )


# 单例
router = ModelRouter()
