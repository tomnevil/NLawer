"""模型路由：任务类型 -> 模型，统一网关 + 三档回退，生产禁止 Mock 降级。

借鉴 zunicorn-agent `ModelRouter` 分档 + YouTubeBoardcast `_resolve_zunicorn_src`
三级解析：未配置 API Key 时自动落到 MockProvider（**仅限非生产环境**），
保证零网络可完整演示。

安全约束：MockProvider 只返回占位文本，在真实法律业务中会产出看似合理
但无依据的内容。因此**生产环境缺失 Key 时直接拒绝服务**，而非静默降级
（见 `_provider`）。Mock 输出亦会被标记 `is_mock=True` 供审计追溯。
"""
import json
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


# 任务 -> 默认档位（用于 provider_status 与敏感判定）
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


# 法务任务 -> 默认模型（基于 CME tokenplan 网关可用模型，按任务性质挑选）。
# 选择依据：
#   - QA / COPILOT：高频短问答，优先低成本、低延迟、中文好 -> DeepSeek-V4-Flash
#   - DOCUMENT：常规文书，可能较长 -> qwen/qwen3.6-plus（长上下文，性价比高）
#   - EVIDENCE / COMPLIANCE / CALCULATION：高强度判定 / 推理 -> qwen/qwen3.7-max（旗舰推理）
#   - CONTRACT_REVIEW：合同长且需逐条法律风险判定 -> minimax/MiniMax-M3（1M 上下文、低延迟，
#       适合长合同；ZHIPU/GLM-5.1 实测 >60s 易超时，故不默认）
# 注：可用模型以 config.LLM_GATEWAY_MODELS 为准；如需微调，用 LLM_TASK_MODEL(JSON) 覆盖。
DEFAULT_TASK_MODEL: dict[TaskType, str] = {
    TaskType.QA: "DeepSeek-V4-Flash",
    TaskType.DOCUMENT: "qwen/qwen3.6-plus",
    TaskType.COPILOT: "DeepSeek-V4-Flash",
    TaskType.EVIDENCE: "qwen/qwen3.7-max",
    TaskType.COMPLIANCE: "qwen/qwen3.7-max",
    TaskType.CALCULATION: "qwen/qwen3.7-max",
    TaskType.CONTRACT_REVIEW: "minimax/MiniMax-M3",
}


_task_model_overrides: Optional[dict] = None


def _resolve_task_model(task: TaskType) -> str:
    """解析任务对应的模型名：LLM_TASK_MODEL(JSON) 覆盖 > 代码默认映射。"""
    global _task_model_overrides
    if _task_model_overrides is None:
        raw = (settings.LLM_TASK_MODEL or "").strip()
        if raw:
            try:
                _task_model_overrides = json.loads(raw)
            except (ValueError, TypeError):
                _task_model_overrides = {}
        else:
            _task_model_overrides = {}
    override = _task_model_overrides.get(task.value)
    return override if override else DEFAULT_TASK_MODEL[task]


def _provider(tier: ModelTier, model: Optional[str] = None) -> Optional[OpenAIProvider]:
    # 1) 优先统一网关（CME tokenplan）：一个 Key 覆盖全部可用模型
    if settings.LLM_GATEWAY_API_KEY:
        return OpenAIProvider(
            settings.LLM_GATEWAY_BASE_URL,
            settings.LLM_GATEWAY_API_KEY,
            model or settings.LLM_CHEAP_MODEL,
            tier.value,
        )
    # 2) 回退到三档独立配置（向后兼容历史部署）
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
    ModelTier.CHEAP: "LLM_GATEWAY_API_KEY / LLM_CHEAP_API_KEY",
    ModelTier.STRONG: "LLM_GATEWAY_API_KEY / LLM_STRONG_API_KEY",
    ModelTier.LOCAL: "LLM_GATEWAY_API_KEY / LLM_LOCAL_API_KEY",
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
        model = _resolve_task_model(task)
        provider = _provider(tier, model)

        if provider is None:
            # 生产环境：拒绝静默降级，避免用占位文本冒充法律分析
            if settings.ENVIRONMENT == "production":
                raise ConfigurationError(
                    f"生产环境未配置 {tier.value} 档模型（{_ENV_KEY_NAME[tier]} 为空），"
                    "已拒绝以 Mock 结果代替真实模型输出",
                    details={"tier": tier.value, "model": model},
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
