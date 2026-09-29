"""证据接口越权读取探测（只读诊断，不修改任何生产代码）。

## 目的

`app/api/v1/cases.py` 里 `_case_or_404` 的注释记录了一个已修复的历史缺陷：

> 历史缺陷：`case_events` 仅按 `case_id` 查询事件表，未校验案件归属，
> 任意已登录用户遍历 `case_id` 即可读取他人案件的时间线、涉案描述等
> 敏感信息——对法律产品属致命数据泄露（违反《律师法》保密义务与 PIPL）。

该修复把 `cases.py` 的守卫统一收口为「租户 + 客户归属」双重校验。
本脚本用来验证 `app/api/v1/evidence.py` 里的同名守卫是否也已收口——
它的 `_case_or_404` 只接收 `tenant_id`，没有 `ctx`，看起来仍是旧口径。

## 断言设计

同一租户下两个客户 A、B，案件归 A。以 B 的身份请求：

| 端点                                   | 期望（收口后） | 收口前实际 |
|----------------------------------------|----------------|------------|
| GET /api/v1/cases/{id}                 | 404            | 404（对照组，已修复） |
| GET /api/v1/evidence/cases/{id}        | 404            | 200 + 他人证据列表 |
| GET /api/v1/evidence/cases/{id}/missing| 404            | 200 + 他人材料清单 |
| GET /api/v1/evidence/cases/{id}/timeline| 404           | 200 + 他人证据时间线 |

对照组是必要的：如果 `/cases/{id}` 也返回 200，说明探测脚本本身
（依赖覆盖 / 角色构造）有问题，结论不可信。

## 运行

    cd backend
    python ../evidence/authz_probe.py

退出码 0 = 未发现越权；1 = 发现越权（含逐条明细）。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

# ── 环境必须在 import app.* 之前设好：app.config 在导入时读取 env ──
WORK = BACKEND / "_tmp_verify"
WORK.mkdir(exist_ok=True)
DB_FILE = WORK / f"authz_probe_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_FILE}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB_FILE}"
os.environ["AUTH_COOKIE_ENABLED"] = "false"  # 纯 Bearer 场景，跳过 CSRF
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["MODERATION_CHECK_INPUT"] = "false"
os.environ["MODERATION_CHECK_OUTPUT"] = "false"

TENANT = "probe-tenant"

PROBES = [
    # (方法, 路径模板, 说明, 是否为对照组)
    ("GET", "/api/v1/cases/{cid}", "案件详情（对照组：已收口的守卫）", True),
    ("GET", "/api/v1/evidence/cases/{cid}", "案件证据列表", False),
    ("GET", "/api/v1/evidence/cases/{cid}/missing", "材料缺失清单", False),
    ("GET", "/api/v1/evidence/cases/{cid}/timeline", "证据时间线", False),
]


def main() -> int:
    import app.models  # noqa: F401  触发全部模型注册
    from app.core.deps import TenantContext, get_tenant_context
    from app.core.rbac import Role
    from app.main import create_app
    from app.models.base import Base
    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus, EvidenceCategory, EvidenceStatus
    from app.models.evidence import Evidence
    from app.models.identity import User
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def seed() -> tuple[int, int, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as db:
            a = User(
                tenant_id=TENANT,
                username="client_a",
                hashed_password="x",
                full_name="客户甲",
                role=Role.CLIENT,
            )
            b = User(
                tenant_id=TENANT,
                username="client_b",
                hashed_password="x",
                full_name="客户乙",
                role=Role.CLIENT,
            )
            db.add_all([a, b])
            await db.flush()

            case = Case(
                case_no=f"PROBE-{uuid.uuid4().hex[:6]}",
                tenant_id=TENANT,
                title="客户甲的案件（客户乙不应看到）",
                client_user_id=a.id,
                status=CaseStatus.INTAKE,
                grade=CaseGrade.B,
                dispute_type="劳动争议",
            )
            db.add(case)
            await db.flush()

            db.add(
                Evidence(
                    tenant_id=TENANT,
                    case_id=case.id,
                    uploaded_by=a.id,
                    name="客户甲的劳动合同.pdf",
                    file_path="probe/labor-contract.pdf",
                    file_type="application/pdf",
                    file_size=12345,
                    status=EvidenceStatus.UPLOADED,
                    category=EvidenceCategory.CONTRACT,
                )
            )
            await db.commit()
            return a.id, b.id, case.id

    a_id, b_id, case_id = asyncio.run(seed())

    app = create_app()
    # 以「客户乙」的身份注入上下文——不覆盖 get_db，让请求走真实会话工厂
    # （DATABASE_URL 已指向同一个探测库），这样验证的是真实查询路径。
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        tenant_id=TENANT, user_id=b_id, role=Role.CLIENT
    )

    # 不用 with：不触发 lifespan（避免 alembic 迁移），表已由 create_all 建好
    client = TestClient(app)

    print(f"探测库：{DB_FILE.name}")
    print(f"租户：{TENANT}    案件 #{case_id} 归属：客户甲(id={a_id})    请求身份：客户乙(id={b_id})")
    print()

    leaks: list[str] = []
    control_status: int | None = None

    for method, path, label, is_control in PROBES:
        url = path.format(cid=case_id)
        res = client.request(method, url)
        body = res.text[:200].replace("\n", " ")
        leaked = res.status_code == 200
        if is_control:
            control_status = res.status_code
        if leaked:
            leaks.append(f"{label}  {method} {url}  ->  {res.status_code}  泄露内容片段：{body}")
        flag = "泄露" if leaked else "拒绝"
        print(f"[{flag}] {label}")
        print(f"        {method} {url}")
        print(f"        HTTP {res.status_code}")
        if leaked:
            print(f"        body: {body}")
        print()

    print("─" * 78)
    if control_status != 404:
        print(f"⚠ 对照组异常：/cases/{{id}} 返回 {control_status}，期望 404。")
        print("  说明探测脚本本身的身份注入有问题（例如依赖覆盖未生效），")
        print("  本次结论不可信，请先修正脚本。")
        return 2

    print(f"✓ 对照组通过：/cases/{{id}} 对非归属客户返回 {control_status}（该守卫已收口）")

    if leaks:
        print(f"✗ 发现 {len(leaks)} 处越权读取：")
        for line in leaks:
            print(f"    - {line}")
        print()
        print("结论：evidence.py 的 _case_or_404 只校验 tenant_id，未校验客户归属，")
        print("      与 cases.py 已修复的守卫口径不一致。")
        return 1

    print("✓ 未发现越权读取。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
