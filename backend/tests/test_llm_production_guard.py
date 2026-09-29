"""P0-3（**LLM 生成侧**）生产环境 Mock 静默降级守卫回归测试。

## 为什么这个文件必须存在

P0-3 的原文是「生产环境 Mock LLM 静默降级，可能向真实用户输出假法律答案」。
修复收口在 `app/ai/router.py` 的 `ModelRouter.complete()`：

```python
provider = _provider(tier)
if provider is None:
    if settings.ENVIRONMENT == "production":
        raise ConfigurationError(f"生产环境未配置 {tier.value} 档模型（...）")
    provider = MockProvider(tier.value)
```

**但此前没有任何一条用例执行过这个分支**：

- `grep -rn "MockProvider" tests/` ⇒ **0 命中**；
- `test_vector_retrieval.py::test_production_without_embedding_key_fails_fast`
  只覆盖**向量侧**（`embeddings.build_embedding_client`），与 LLM 生成侧无关；
- `test_contract_review_llm.py:680` 里确实出现了 `ConfigurationError`，但那是
  **注入一个假的、会抛错的 router** 去测下游「不计费 / 不泄露」——
  它**假设守卫已经抛错**，并不验证守卫会抛错。两者差一跳，而那一跳正是 P0-3。

⇒ 与「注册越权」（`test_auth_register.py`）、「限流」（`test_rate_limit.py`）、
「复核越权」（`test_review_tenant_guard.py`）**同一族**：**「写了」≠「有判据」**。
把 `if settings.ENVIRONMENT == "production":` 这一行删掉，全量回归会全绿。

## 覆盖清单

- L1 **干净对照**：非生产 + 无 Key ⇒ 落 Mock（证明降级分支可达，
  否则 L2 的红可能只是「这函数本来就抛错」）
- L2 生产 + 无 Key ⇒ `ConfigurationError`，且消息**点名缺失的环境变量**
  （三档全覆盖：档位↔环境变量映射写错也必须被抓到）
- L3 生产 + **有** Key ⇒ 不抛错（守卫不能误伤真实调用）
"""
from __future__ import annotations

import pytest

from app.ai.providers import LLMResult, OpenAIProvider
from app.ai.router import ModelRouter, TaskType
from app.config import settings
from app.core.errors import ConfigurationError

ALL_TIERS = ("LLM_CHEAP_API_KEY", "LLM_STRONG_API_KEY", "LLM_LOCAL_API_KEY")


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture()
def no_keys(monkeypatch):
    """三档 Key 全部置空（不依赖运行机器上的实际配置）。"""
    for k in ALL_TIERS:
        monkeypatch.setattr(settings, k, None, raising=False)


@pytest.fixture()
def stub_http(monkeypatch):
    """把真实 HTTP 调用换成桩：`_provider` 与路由逻辑保持真实，只断网。

    不这么做的话，L3「生产 + 有 Key 不抛错」会真的去打外网。
    """

    async def _fake_complete(
        self, prompt, *, system=None, temperature=0.3, max_tokens=2000, task_type=None
    ):
        return LLMResult(text="stub", model="stub-model", tier="cheap", is_mock=False)

    monkeypatch.setattr(OpenAIProvider, "complete", _fake_complete)


def _err_text(exc: BaseException) -> str:
    """取异常的可读文本（`message` 字段 + `str()` 都算，避免只认一种）。"""
    return f"{getattr(exc, 'message', '')} {exc} {getattr(exc, 'details', '')}"


# ═══════════════════════ L1 干净对照 ═══════════════════════


async def test_non_production_falls_back_to_mock(monkeypatch, no_keys):
    """L1：非生产环境零配置 ⇒ 落 Mock（这是「零网络可演示」的设计前提）。

    它是 L2 的**干净对照**：如果这条也红，说明 `complete()` 整体坏了，
    那 L2 的红就不能归因于「生产守卫生效」。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)

    r = await ModelRouter().complete(TaskType.QA, "这是一个测试提示词")

    assert r.is_mock is True, "非生产无 Key 时应落到 MockProvider"
    assert r.tier == "cheap"


# ═══════════════════════ L2 生产守卫（三档全覆盖） ═══════════════════════


@pytest.mark.parametrize(
    ("task", "sensitive", "expected_env_key"),
    [
        (TaskType.QA, False, "LLM_CHEAP_API_KEY"),
        (TaskType.COMPLIANCE, False, "LLM_STRONG_API_KEY"),
        (TaskType.QA, True, "LLM_LOCAL_API_KEY"),  # sensitive ⇒ 强制 LOCAL 不出域
    ],
)
async def test_production_without_key_fails_fast(
    monkeypatch, no_keys, task, sensitive, expected_env_key
):
    """L2：生产缺 Key 必须**显式失败**，且错误信息点名该配哪一项。

    三档都测，而不是只测 cheap：
    `TIER_FOR_TASK` / `_ENV_KEY_NAME` 两张表写错（比如合同审查被错配到 CHEAP、
    或 LOCAL 档消息里写成 STRONG 的变量名）时，只测一档会完全漏掉。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)

    with pytest.raises(ConfigurationError) as e:
        await ModelRouter().complete(task, "这是一个测试提示词", sensitive=sensitive)

    assert expected_env_key in _err_text(e.value), (
        f"错误信息未点名缺失的环境变量 {expected_env_key}：{_err_text(e.value)}"
    )


# ═══════════════════════ L3 不误伤真实调用 ═══════════════════════


async def test_production_with_key_does_not_raise(monkeypatch, stub_http):
    """L3：生产环境配好 Key 时必须照常工作。

    没有这条，「生产一律抛错」这种过度收紧的实现也能让 L2 全绿——
    那等于把 P0-3 换成另一个 P0（线上完全不可用）。
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "LLM_CHEAP_API_KEY", "sk-test-not-real", raising=False)

    r = await ModelRouter().complete(TaskType.QA, "这是一个测试提示词")

    assert r.is_mock is False, "配置了 Key 却仍走了 MockProvider"
