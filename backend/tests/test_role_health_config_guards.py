"""`permissions_for` / `/api/health` / 审核后端配置校验 的判据（§3.22，2026-09-20）。

## 这三个为什么各成一条

本轮把 12 个「守卫类函数 + tests 零引用」的候选清完，其中：

| 候选 | 结论 |
|------|------|
| `_check_builtin` / `_check_external` | **假阳性**：`test_moderation.py` 通过 `ContentModerator(backend="builtin")` 已覆盖（`methodology.md` 73） |
| `_needs_check` | 真零判据 ⇒ 另开 `test_csrf_middleware_dispatch.py`（§3.21） |
| `permissions_for` | **未知角色必须落到空集**（最小权限），此前无人断言 |
| `health_check` | 被请求过 3 处，但**响应内容零断言**；生产脱敏分支从未执行 |
| `_validate_moderation_backend` | 启动期校验，此前完全没测；失败模式是「运行时才炸」 |

## 覆盖清单

| 编号 | 性质 |
|------|------|
| R1 | `permissions_for(未知角色)` ⇒ 空集（最小权限原则） |
| R2 | **结构**：每个 `Role` 成员都必须在 `ROLE_PERMISSIONS` 里有条目 |
| R3 | `has_permission(未知角色, 任意权限码)` ⇒ False |
| H1 | 生产：`/api/health` **不回传** `llm_providers`（不暴露档位细节） |
| H2 | 生产 + 未配 LLM ⇒ `status == "degraded"`（不能报 healthy） |
| H3 | **反向量**：非生产 ⇒ `llm_providers` 有明细（排查「为何走了 Mock」就靠它） |
| M1 | `MODERATION_BACKEND` 取值非法 ⇒ 启动期 `ValueError` |
| M2 | `MODERATION_BACKEND=external` 但无 Key ⇒ 启动期 `ValueError` |
| M3 | **反向量**：合法组合不误伤（`builtin` 无 Key 也应通过） |

## 关于 R2

`ROLE_PERMISSIONS` 是**字典**，`permissions_for` 用 `.get(role, frozenset())`。
新增角色时若忘了配权限，不会报错，只会让该角色**一无所能**——
表现是「新角色登录后每个按钮都 403」，排查方向完全跑偏。
⇒ 用**枚举**（遍历 `Role`）而不是写死清单，将来加角色自动纳入判定。
"""
from __future__ import annotations

import pytest

CSRF_UNRELATED = "csrf-unrelated"


# ═══════════════════════ R1–R3 角色权限矩阵 ═══════════════════════


def test_unknown_role_gets_no_permissions():
    """R1：未知角色 ⇒ **空集**（最小权限原则）。

    `Role` 是 `str`  Enum，所以从库里/令牌里读到的**任意字符串**都可能流到这里
    （例如旧数据、迁移期的脏值）。若改成 `ROLE_PERMISSIONS[role]` 会直接 KeyError，
    若改成「给个默认全集」则是提权 —— 空集是唯一安全的兜底。
    """
    from app.core.rbac import Role, permissions_for

    assert permissions_for("GHOST_ROLE") == frozenset(), (
        "未知角色必须落到空集，否则就是凭空提权"
    )
    # 反向对照：字符串形式的**合法**角色仍应命中（Role 是 str Enum）
    assert permissions_for("PLATFORM_ADMIN") == permissions_for(Role.PLATFORM_ADMIN)


def test_every_role_has_a_permission_entry():
    """R2（**结构**）：每个 `Role` 成员都必须在矩阵里。

    ⚠️ **枚举 `Role` 而不是写死清单**：写死只能防住今天这 7 个，
    将来加一个角色忘记配权限时判据照样全绿（与本轮 B2 同一个教训）。
    """
    from app.core.rbac import ROLE_PERMISSIONS, Role

    missing = [r.value for r in Role if r not in ROLE_PERMISSIONS]
    assert not missing, f"以下角色在 ROLE_PERMISSIONS 里没有条目 ⇒ 登录后一无所能：{missing}"

    empty = [r.value for r in Role if not ROLE_PERMISSIONS[r]]
    assert not empty, f"以下角色的权限集为空（除非是刻意的设计，否则等同不可用）：{empty}"


def test_unknown_role_fails_every_permission_check():
    """R3：`has_permission(未知角色, ...)` ⇒ False，包括通配场景。

    单独成条的理由：`has_permission` 里有 `_ALL in perms` 的短路。
    若哪天把「未知角色」错误地实现成「给 `*`」，R1（空集）会红但 R3 也会红——
    两条抓的是**同一次改动的两种写法**，分开才定位得快。
    """
    from app.core.rbac import has_permission

    assert has_permission("GHOST_ROLE", "case:read") is False
    assert has_permission("GHOST_ROLE", "*") is False


# ═══════════════════════ H1–H3 `/api/health` ═══════════════════════


@pytest.fixture
def health_client(monkeypatch):
    """真实 `create_app()`（关限流，避免撞桶干扰），并暴露一个切环境的开关。"""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False, raising=False)
    application = create_app()

    def _set_env(value: str) -> None:
        monkeypatch.setattr(settings, "ENVIRONMENT", value, raising=False)

    # 不用 with：不触发 lifespan
    return TestClient(application), _set_env


def test_production_health_hides_provider_details(health_client):
    """H1：生产环境 `/api/health` **不回传** `llm_providers`。

    非生产回传档位明细是为了本地排查「为何走了 Mock」；生产回传则等于
    对外暴露内部模型编排（用了哪几档、哪一档没配）。这条判的是**信息泄露**，
    与「健康检查能不能用」是两件事。
    """
    client, set_env = health_client
    set_env("production")

    body = client.get("/api/health").json()["data"]
    assert body["llm_providers"] is None, f"生产环境泄露了档位明细：{body['llm_providers']}"


def test_production_health_reports_degraded_when_llm_unconfigured(health_client):
    """H2：生产 + LLM 未配齐 ⇒ `status == "degraded"`。

    报成 `healthy` 的后果是**监控永远绿**：负载均衡照常把流量打上来，
    用户侧才是 500（见 `ai/router.py` 的 `ConfigurationError` 守卫）。
    这是「健康检查说谎」的典型形态。
    """
    client, set_env = health_client
    set_env("production")

    body = client.get("/api/health").json()["data"]
    assert body["status"] == "degraded", (
        f"LLM 未配置却报 {body['status']!r} ⇒ 监控会一直绿：{body}"
    )


def test_non_production_health_exposes_provider_details(health_client):
    """H3（**反向量**）：非生产必须回传明细。

    没有这条，把 `llm_providers` 一律置 None 上面两条依然全绿，
    而本地排查「为何走了 Mock」的能力就没了。
    """
    client, set_env = health_client
    set_env("development")

    body = client.get("/api/health").json()["data"]
    providers = body.get("llm_providers")
    assert isinstance(providers, dict) and providers, (
        f"非生产应回传档位明细，实际 {providers!r}"
    )


# ═══════════════════════ M1–M3 审核后端配置校验 ═══════════════════════


def _settings(**overrides):
    """构造一个**不读 `.env`** 的 Settings（否则本地 `.env` 会覆盖掉注入值）。"""
    from app.config import Settings

    base = {"_env_file": None, "ENVIRONMENT": "development"}
    base.update(overrides)
    return Settings(**base)


def test_invalid_moderation_backend_rejected_at_startup():
    """M1：`MODERATION_BACKEND` 取值非法 ⇒ **启动期** ValueError。

    为什么必须在启动期：这是 pydantic 的 `model_validator`，
    一旦被人误删/写错条件，非法值会一路带到运行时的 `if backend == ...`
    分支里 ⇒ 表现为「审核静默不生效」而不是「启动失败」。
    """
    with pytest.raises(ValueError, match="MODERATION_BACKEND"):
        _settings(MODERATION_BACKEND="bogus-backend")


def test_external_backend_requires_api_key():
    """M2：`external` 但无 Key ⇒ 启动期 ValueError（无法调用外部审核）。

    与 M1 分开：M1 判的是**取值域**，M2 判的是**跨字段依赖**。
    """
    with pytest.raises(ValueError, match="MODERATION_API_KEY"):
        _settings(MODERATION_BACKEND="external", MODERATION_API_KEY="")


def test_valid_combinations_are_accepted():
    """M3（**反向量**）：合法组合不误伤。

    `builtin` 允许无 Key（内置最小词库不需要外部凭证）；
    `none` 在非生产也应放行（生产的禁止由另一个校验器负责）。
    """
    assert _settings(MODERATION_BACKEND="builtin", MODERATION_API_KEY="").MODERATION_BACKEND == "builtin"
    assert _settings(MODERATION_BACKEND="none").MODERATION_BACKEND == "none"
    assert _settings(MODERATION_BACKEND="external", MODERATION_API_KEY="k").MODERATION_BACKEND == "external"
