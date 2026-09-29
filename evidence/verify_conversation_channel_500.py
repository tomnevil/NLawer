"""证据：`conversations.channel` 的枚举不一致会让**整个会话列表接口 500**。

## 发现经过（不是读代码推出来的）

本轮新增的运行时健康检查（`verify_runtime_health.py`）在 IM 端（3003）的
**对照组**（未注入任何故障的那一次导航）里抓到：

```
[失败请求] 500 Fetch http://localhost:8001/api/v1/conversations?page=1&page_size=50
```

随后用纯 HTTP 复现（4 个账号 × 3 个租户）得到清晰规律：

| 租户 | 结果 |
|---|---|
| `firm_hlw` | **全部 500**（8 条会话） |
| `platform` / `ent_acme` | 非 PLATFORM_ADMIN 500，`admin` 200（0 条会话） |

⇒ 判别变量不是角色，是**这个租户有没有会话行**。取数阶段抛：

```
LookupError: 'WEB' is not among the defined enum values.
             Enum name: imchannel. Possible values: WEB_SIM, WECOM, FEISHU
```

## ⚠️ 一条被实测推翻的推断（留在这里当反例）

看到 `ConversationCreate.channel: str = IMChannel.WEB_SIM.value`（默认值是小写
`"web_sim"`）而库里存的是大写 `"WEB_SIM"` 时，第一反应是
「**默认值自己就读不回来**，第一个用默认值建的会话就把列表打死」。
写成了断言之后，实测**没复现**：SQLAlchemy 的 `Enum` 在 **bind 阶段会把值解析成成员**，
`"web_sim"` 被规范化成 `"WEB_SIM"` 落库，读回正常。

**这正是「先实测、再解释」的价值**：ORM 对**合法**值会规范化，
所以问题不在默认值，而在**非法**值——非法值既不报错、也不规范化，原样落库，
然后在**读取**时把整个查询打炸。下面 `事实4` 就是这一对**对照**。

## 本脚本证明的四件事

1. **写入侧不校验**：`ConversationCreate.channel` 类型是 `str` 而不是 `IMChannel`
   ⇒ 任何字符串都能存进去（`WEB` 就是这么来的）。
2. **读取侧会炸**：库里只要有一条非法值，`select(Conversation)` 在
   **结果物化阶段**就抛 `LookupError` ⇒ 该租户的会话列表**永久 500**。
3. **对照组**：ORM 对**合法**枚举值会规范化（`"web_sim"` → `"WEB_SIM"`），可正常读回
   ⇒ 说明缺陷**只**由非法值触发，不是「这个字段整体坏了」。
4. **非法值走 ORM 也不报错**：`Conversation(channel="WEB")` 写入**静默成功**、
   原样落库、读回才炸 ⇒ 缺陷在写入侧无声、在读取侧爆发。

全程在**临时库**上跑（`atexit` 自删），不碰 `storage/nlawer.db`。

用法：python evidence/verify_conversation_channel_500.py
退出码：`0` 缺陷已复现（证据成立） / `1` 未能复现（说明结论不成立，需重查） / `2` 环境问题
"""
from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
BACKEND = HERE.parents[0] / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

# 临时库放在 backend/ 下（同卷，删除便宜），进程退出时自删。
_TMP = tempfile.NamedTemporaryFile(prefix="_verify_channel_", suffix=".db", dir=BACKEND, delete=False)
_TMP.close()
DB_PATH = pathlib.Path(_TMP.name)
DB_PATH.unlink()  # 交给 create_all 建

import atexit  # noqa: E402

atexit.register(lambda: DB_PATH.unlink(missing_ok=True))


def check_schema_accepts_invalid() -> tuple[bool, str]:
    """事实 1：写入 schema 是否接受非法 channel（无 DB，纯校验）。"""
    from app.schemas.conversation import ConversationCreate

    try:
        obj = ConversationCreate(external_user_id="probe", channel="WEB")
        return True, f"被接受，落库值将是 {obj.channel!r}"
    except Exception as e:  # noqa: BLE001
        return False, f"被拒绝：{type(e).__name__}"


def check_default_matches_orm_contract() -> tuple[bool, str]:
    """事实 3（对照组）：字段默认值与 SQLAlchemy 的存取口径是否一致。

    **预期「不一致」但无害**——ORM 会在 bind 阶段把「值」解析成「名」。
    这一条存在的意义是：把「默认值是坏的」这个**曾经的错误推断**钉死在这里。
    """
    from app.models.enums import IMChannel
    from app.schemas.conversation import ConversationCreate

    default = ConversationCreate(external_user_id="probe").channel
    names = sorted(m.name for m in IMChannel)
    mismatch = default not in set(names)
    return mismatch, (
        f"默认值 = {default!r}；枚举名 = {names} ⇒ 默认值是「值」不是「名」"
        f"（{'需要 ORM 规范化' if mismatch else '无需规范化'}；事实4 证明规范化确实发生）"
    )


async def _fresh_engine():
    from app.database import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _stored(tenant: str) -> list:
    con = sqlite3.connect(DB_PATH)
    try:
        return con.execute("select channel from conversations where tenant_id=?", (tenant,)).fetchall()
    finally:
        con.close()


async def _roundtrip(channel, tenant: str) -> tuple[bool, str]:
    """用 ORM 写入 → 读回。返回 (读回是否失败, 说明)。"""
    from app.models.conversation import Conversation
    from sqlalchemy import select

    engine, factory = await _fresh_engine()
    try:
        async with factory() as db:
            db.add(
                Conversation(
                    tenant_id=tenant, external_user_id="probe", channel=channel, status="BOT", context={}
                )
            )
            await db.commit()  # 关键：非法值在这一步**不报错**
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        return False, f"写入就报错了（{type(e).__name__}: {' '.join(str(e).split())[:90]}）——缺陷形态不同"
    stored = _stored(tenant)
    try:
        async with factory() as db:
            rows = (await db.execute(select(Conversation).where(Conversation.tenant_id == tenant))).scalars().all()
        await engine.dispose()
        return False, f"写入静默成功，落库 {stored}，**读回正常**（{len(rows)} 行）"
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        return True, f"写入静默成功，落库 {stored} ⇒ **读回时** {type(e).__name__}: {' '.join(str(e).split())[:100]}"


async def check_default_roundtrip() -> tuple[bool, str]:
    """事实 3 的实测面：默认值走一遍完整往返，**预期成功**（对照组）。"""
    from app.schemas.conversation import ConversationCreate

    default = ConversationCreate(external_user_id="probe").channel
    failed, detail = await _roundtrip(default, "firm_default")
    if failed:
        return True, f"❌ 默认值 {default!r} 读不回来 —— 缺陷成立。{detail}"
    return False, f"默认值 {default!r} 往返正常（对照组通过，符合预期）。{detail}"


async def check_invalid_roundtrip() -> tuple[bool, str]:
    """事实 4：**非法**值走同一条 ORM 路径 —— 写入静默成功，读回炸。"""
    failed, detail = await _roundtrip("WEB", "firm_invalid")
    return failed, detail


async def check_read_blows_up() -> tuple[bool, str]:
    """事实 2：库里有一条非法值，取数是否直接抛（绕开 ORM 直接插，复刻既成事实）。"""
    from app.models.conversation import Conversation
    from sqlalchemy import select

    engine, factory = await _fresh_engine()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "insert into conversations (tenant_id, external_user_id, channel, status, context, created_at, updated_at)"
        " values ('firm_raw','ext-1','WEB','BOT','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    con.commit()
    con.close()
    try:
        async with factory() as db:
            rows = (await db.execute(select(Conversation))).scalars().all()
        await engine.dispose()
        return False, f"竟然读出来了 {len(rows)} 行（缺陷不成立）"
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        return True, f"{type(e).__name__}: {' '.join(str(e).split())[:150]}"


async def main() -> int:
    print("── 复现 conversations.channel 枚举不一致 ──\n")
    # (名称, 是否触发, 说明, 期望是否触发)
    results: list[tuple[str, bool, str, bool]] = []

    # ⚠️ 事实1 的期望在 2026-09-20 **翻转过一次**，原因必须留在这里：
    #   原期望 `True`（「写入侧**接受**非法 channel」= 缺陷成立）。
    #   枚举域收口落地后，`ConversationCreate` 已经**拒绝**非法值 ⇒ 期望改为 `False`。
    #   不改的后果：**缺陷修好那天这条探针变红**，而且结论段还会继续打印
    #   「缺陷成立」——一个把「修好了」报成「没修好」的门禁，比没有门禁更糟
    #   （它会让人去"修"一个已经修好的东西）。
    ok1, d1 = check_schema_accepts_invalid()
    results.append(("事实1 写入侧拒绝非法 channel（schema 层 = 修复生效）", ok1, d1, False))

    ok2, d2 = await check_read_blows_up()
    results.append(("事实2 一条非法值 ⇒ 取数整体抛异常", ok2, d2, True))

    # 对照组：**期望不触发**。若它变成触发，说明缺陷性质变了（连合法值都读不回来）。
    ctrl_failed, d3 = await check_default_roundtrip()
    results.append(("事实3【对照组】合法默认值往返正常", ctrl_failed, d3, False))

    ok4, d4 = await check_invalid_roundtrip()
    results.append(("事实4 非法值走 ORM 写入静默成功、读回炸", ok4, d4, True))

    bad = 0
    for name, triggered, detail, expect in results:
        hit = triggered == expect
        bad += 0 if hit else 1
        tag = "触发" if triggered else "未触发"
        mark = "✅" if hit else "❌"
        print(f"  {mark} [{tag}] {name}（期望{'触发' if expect else '不触发'}）\n           {detail}")

    print("\n" + "=" * 72)
    if not bad:
        print("✅ 四条与预期全部一致（含对照组）⇒ 当前事实如下：")
        print("")
        print("   · **已修**：`ConversationCreate`（写入 schema）**拒绝**非法 channel ⇒")
        print("     走 API 的写入已被挡住（`evidence/verify_enum_domain_drift.py` 守这一层）。")
        print("   · **未修**：**模型层与原始 SQL 仍然没有校验**。绕过 schema 直接")
        print("     `Conversation(channel='WEB')` 或 raw insert，脏值照样落库；")
        print("     落库后 `GET /api/v1/conversations` 在结果物化阶段抛 `LookupError`")
        print("     ⇒ **该租户的会话列表永久 500**（清掉那条脏数据之前无法恢复）。")
        print("   · 对照组证明：合法值（含默认值）往返正常 ⇒ 500 **只**由非法值触发。")
        print("")
        print("   ⇒ 残留风险登记：缺 DB 层约束（原生 enum / CHECK），历史脏数据靠")
        print("     迁移归一化（`verify_migration_enum_defaults.py` 守），不是靠写入拦截。")
        return 0
    print(f"❌ 有 {bad} 条与预期不符 —— 结论必须按这些收窄，不要照抄上面的措辞。")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
