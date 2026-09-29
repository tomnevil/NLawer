"""WebSocket 连接注册表：把「通知落库」与「实时送达」解耦。

## 为什么必须有它

修复前全库**没有任何连接注册表**：`api/v1/ws.py` 是一个「逐会话回显循环」——
客户端发一句、服务端回一句。它能回复**发消息的那个人**，但**没有任何办法
找到「某个用户此刻挂在哪个 socket 上」**，因此**无法主动推送**。

这直接卡住门槛一的出口标准（真实企微中跑通「咨询→派单→接单→AI 摘要→律师确认」）：
该闭环的消息触达依赖通知推送，而**即使企微适配器接通，通知也送不出去**。

## 三个设计决定

### 1. 按 `user_id` 建索引，不按 `tenant_id`

与 P0-15 第一轮确立的隔离原则一致：通知是**用户级**资源，读路径一律
`tenant_id + user_id` 双条件。推送侧若按 tenant 建索引，同租户内律师甲
可以收到律师乙的通知——正是第一轮修掉的那类越权。

### 2. 单用户连接数**有上限**

一个用户开 20 个标签页、或前端重连逻辑有 bug 时，连接集合会无界增长。
上限之外的新连接被**拒绝注册**（由端点回 `1013 Try Again Later`），
而不是静默堆积——服务端的内存必须有一个明确的边界。

### 3. 发送在**锁外**进行，失败**只回收不抛出**

- **锁外发送**：`send_json` 是网络 IO。若持锁发送，一个慢客户端会阻塞
  所有其他用户的推送（锁被占用），把「某个用户网络差」放大成「全站推送卡住」。
  因此先在锁内取**快照**，再在锁外逐个发送。
- **失败只回收不抛出**：推送是**旁路**。连接已死、客户端已关、网络抖动
  都不该影响调用方的业务流程（`notify()` 的调用点分散在派单、复核、计费里，
  推送抛异常会把「接单」这类主流程带崩）。失败的连接就地标记、批量摘除。

## 多副本部署的已知限制

本实现是**进程内**的：连接挂在哪个进程，推送就只能由哪个进程发出。
多副本（uvicorn `--workers N` / K8s 多 Pod）时，若通知由 A 副本写入、
而用户连接挂在 B 副本上，则该条推送**丢失**（但**通知已在库中**，
用户下次轮询/重连补拉仍能看到，不会真的丢消息）。

要消除该限制需引入 Redis pub/sub 之类的跨进程广播。**本轮不做**，
理由：① 门槛一是「3 家律所灰度」，单副本足够；② 通知本就有轮询兜底，
丢失推送的后果是「晚 30 秒看到」而非「看不到」；③ 引入 Redis 会新增
运行期依赖与一个必须运维的组件。**该取舍已写入 PRD 的 Non-goals 与待确认项。**
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable, NamedTuple, Protocol, runtime_checkable

from loguru import logger

#: 单用户最大并发连接数。取值依据：正常用户 1~2 个（手机 + 电脑），
#: 5 已覆盖「多标签页 + 重连过渡期」的合理峰值；再高只会是异常或攻击。
DEFAULT_MAX_PER_USER = 5


class PushOutcome(NamedTuple):
    """一次推送尝试的结果。

    ## 为什么不是「返回送达数」这一个 int

    只返回送达数会丢掉一个**关键区分**：

    | 场景 | 送达数 | 真实含义 |
    |------|--------|----------|
    | 用户没在线 | 0 | 常态。通知已在库中，轮询会补上 |
    | 在线但每个连接都发送失败 | 0 | **事故**。连接已损坏，用户在收不到推送的同时还以为自己在线 |

    两者都是 `0`，但一个该被忽略、一个该被告警。若把它们合并，指标里
    「在线用户推送全失败」会被淹没在海量「用户不在线」中，告警永不触发
    ——这正是本项目反复出现的「静默失败」模式。因此这里同时给出
    `attempted`，让调用方能够区分。
    """

    #: 实际送达的连接数
    delivered: int
    #: 尝试发送的连接数（锁内快照的大小）
    attempted: int

    @property
    def failed(self) -> int:
        """尝试过但失败的连接数。

        每个未送达的尝试都对应一次异常（`send_to_user` 里失败即计入 `dead`），
        因此 `attempted - delivered` 恒等于失败数，不需要额外字段。
        """
        return self.attempted - self.delivered

    @property
    def had_connections(self) -> bool:
        """该用户当时是否有连接（用于区分「离线」与「推失败」）。"""
        return self.attempted > 0


@runtime_checkable
class SupportsSendJson(Protocol):
    """推送目标的最小接口。

    只声明本模块真正用到的两个方法，而非直接依赖 `starlette.WebSocket`：
    这样单元测试可以用一个几十行的假对象覆盖「多连接 / 死连接 / 超限 /
    发送失败」等分支，**不需要真的起一个 ASGI 服务**——那些分支用真实
    WebSocket 很难构造（尤其「发送到一半连接断掉」）。
    """

    async def send_json(self, data: Any) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class ConnectionManager:
    """用户级连接注册表（进程内单例，见模块末尾 `manager`）。"""

    def __init__(self, *, max_per_user: int = DEFAULT_MAX_PER_USER) -> None:
        self._max_per_user = max_per_user
        self._conns: dict[int, set[SupportsSendJson]] = {}
        # 注册表是跨协程共享的可变状态：`register`/`unregister` 都是
        # 「读-判断-写」，不加锁时两个协程可能同时通过 `len < max` 判断，
        # 双双写入，突破上限。
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ 只读视图

    @property
    def max_per_user(self) -> int:
        return self._max_per_user

    def online_users(self) -> int:
        """当前有连接的**用户数**（不是连接数）。"""
        return len(self._conns)

    def count(self, user_id: int) -> int:
        """某用户的活跃连接数。"""
        return len(self._conns.get(user_id, ()))

    def total_connections(self) -> int:
        return sum(len(s) for s in self._conns.values())

    # ------------------------------------------------------------ 注册 / 注销

    async def register(self, user_id: int, ws: SupportsSendJson) -> bool:
        """登记一个连接。返回 `False` 表示**超出单用户上限，已拒绝**。

        调用方（WS 端点）必须处理 `False`：关闭该连接并回 `1013`，
        而不是继续把它挂在注册表之外——那样它会成为一个「看起来连上了、
        但永远收不到推送」的半死连接，比明确拒绝更难排查。
        """
        if user_id <= 0:
            return False
        async with self._lock:
            conns = self._conns.setdefault(user_id, set())
            if len(conns) >= self._max_per_user:
                return False
            conns.add(ws)
            return True

    async def unregister(self, user_id: int, ws: SupportsSendJson) -> None:
        """注销一个连接。**幂等**：重复注销不报错。"""
        async with self._lock:
            conns = self._conns.get(user_id)
            if not conns:
                return
            conns.discard(ws)
            if not conns:
                # 不留空集合：否则 `online_users()` 会随「历史出现过的用户数」
                # 单调增长，指标失真且内存永不释放。
                self._conns.pop(user_id, None)

    # ------------------------------------------------------------ 推送

    async def send_to_user(self, user_id: int, message: dict) -> PushOutcome:
        """向某用户**全部**在线连接推送，返回 `PushOutcome`。

        返回**结构化结果**是刻意的：它让「推送是否真的发出去了」「是没人连
        还是连了但发不出去」都成为可断言的事实（端到端测试与指标都依赖它），
        而不是只能靠日志猜。
        """
        async with self._lock:
            targets = list(self._conns.get(user_id, ()))
        if not targets:
            return PushOutcome(delivered=0, attempted=0)

        delivered = 0
        dead: list[SupportsSendJson] = []
        for ws in targets:
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception as exc:  # noqa: BLE001  推送失败绝不能上抛
                dead.append(ws)
                logger.debug("推送失败（连接将被回收）：user_id={} err={}", user_id, exc)

        if dead:
            await self._prune(user_id, dead)
        return PushOutcome(delivered=delivered, attempted=len(targets))

    async def send_to_users(self, user_ids: Iterable[int], message: dict) -> PushOutcome:
        """向多个用户推送，返回**汇总**结果（各字段分别累加）。"""
        delivered = 0
        attempted = 0
        for uid in user_ids:
            outcome = await self.send_to_user(uid, message)
            delivered += outcome.delivered
            attempted += outcome.attempted
        return PushOutcome(delivered=delivered, attempted=attempted)

    async def _prune(self, user_id: int, dead: list[SupportsSendJson]) -> None:
        """摘除已失效的连接（锁内一次完成，避免逐个加锁）。"""
        async with self._lock:
            conns = self._conns.get(user_id)
            if not conns:
                return
            for ws in dead:
                conns.discard(ws)
            if not conns:
                self._conns.pop(user_id, None)

    # ------------------------------------------------------------ 停机

    async def close_all(self, code: int = 1001) -> int:
        """关闭全部连接（优雅停机用），返回关闭的连接数。

        `1001 = going away`：告诉客户端「服务端要下线了」，前端据此
        **主动重连**而不是当作错误反复重试。
        """
        async with self._lock:
            snapshot = [(uid, list(socks)) for uid, socks in self._conns.items()]
            self._conns.clear()

        closed = 0
        for _uid, socks in snapshot:
            for ws in socks:
                try:
                    await ws.close(code=code)
                    closed += 1
                except Exception:  # noqa: BLE001  停机阶段尽力而为
                    pass
        return closed

    def reset(self) -> None:
        """**仅供测试**：清空注册表而不触碰连接。"""
        self._conns.clear()


#: 进程内单例。WS 端点与 `notify()` 的推送桥都引用它。
manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    """FastAPI 依赖 / 测试替身注入点。"""
    return manager
