"""限流后端：进程内存（默认）与 Redis（多副本共享）。

抽象出 `RateLimitBackend` 是为了让「多副本部署」成为可选项而非陷阱：
- **内存后端**：零依赖，单副本下完全正确；多副本下每个实例独立计数，
  实际阈值 = 配置值 × 副本数（需在部署文档中明示）。
- **Redis 后端**：用 Lua 脚本保证「清理过期 + 计数 + 判定」三步原子执行，
  多副本共享同一计数窗口。

Redis 不可用时**自动降级**到内存后端并告警——限流是防护手段而非核心链路，
不应因为 Redis 抖动导致登录注册整体不可用。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Protocol, Tuple

from loguru import logger

#: Lua 脚本：滑动窗口计数（原子）
#: KEYS[1] = zset key, ARGV = now, window_start, max_requests, member
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

-- 清理滑出窗口的历史记录
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

local count = redis.call('ZCARD', key)
if count >= max_requests then
    -- 返回最早一条的时间戳，供计算 Retry-After
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest[2] then
        return {0, tonumber(oldest[2])}
    end
    return {0, now}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil((now - window_start) / 1000) + 1)
return {1, 0}
"""


class RateLimitBackend(Protocol):
    """限流后端协议。"""

    async def hit(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        """记录一次请求。

        返回 `(是否允许, retry_after_seconds)`；被拒时 retry_after 为建议等待秒数。
        """
        ...


class MemoryRateLimitBackend:
    """进程内滑动窗口计数。

    附带惰性清理：key 过多时清理已空窗口，避免长跑后内存无界增长
    （早期实现只清理单个 key 的过期记录，空 key 永不删除）。
    """

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_sweep = time.time()

    async def hit(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        now = time.time()
        window = self._hits[key]

        while window and window[0] <= now - window_seconds:
            window.popleft()

        if len(window) >= max_requests:
            retry_after = max(1, int(window_seconds - (now - window[0])) + 1)
            return False, retry_after

        window.append(now)
        self._sweep_if_needed()
        return True, 0

    def _sweep_if_needed(self) -> None:
        """每 60s 清理一次已完全过期的 key，防止内存泄漏。

        用**墙钟时间**而非业务时间判断巡检时机：窗口内新增请求不应推迟清理
        （否则长期高流量下 key 会无界增长）。
        """
        now = time.time()
        if now - self._last_sweep < 60:
            return
        self._last_sweep = now
        # 保留最近 2 个窗口内有活动的 key，其余视为过期
        horizon = now - 120
        expired = [k for k, dq in self._hits.items() if not dq or dq[-1] <= horizon]
        for k in expired:
            self._hits.pop(k, None)


class RedisRateLimitBackend:
    """Redis 滑动窗口（多副本共享计数）。

    Redis 不可用时自动降级到内存后端，并打印告警（只告警一次，避免日志刷屏）。
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._fallback = MemoryRateLimitBackend()
        self._redis = None
        self._degraded = False
        self._init_attempted = False

    async def _ensure_client(self):  # type: ignore[no-untyped-def]
        if self._redis is not None or self._degraded:
            return self._redis
        try:
            import redis.asyncio as aioredis  # 延迟导入：未装 redis 包时不影响启动

            self._redis = aioredis.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
            await self._redis.ping()
            logger.info("限流后端：Redis（多副本共享计数）")
        except Exception as exc:  # noqa: BLE001 降级而非崩溃
            self._redis = None
            self._degraded = True
            logger.warning(
                "Redis 限流后端不可用（{}），已降级为进程内计数；"
                "多副本部署下实际阈值将放大为配置值 × 副本数",
                exc,
            )
        return self._redis

    async def hit(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        client = await self._ensure_client()
        if client is None:
            return await self._fallback.hit(key, max_requests, window_seconds)

        now_ms = int(time.time() * 1000)
        window_start_ms = now_ms - window_seconds * 1000
        member = f"{now_ms}-{id(object())}"
        try:
            allowed, oldest_ms = await client.eval(
                _SLIDING_WINDOW_LUA,
                1,
                f"ratelimit:{key}",
                now_ms,
                window_start_ms,
                max_requests,
                member,
            )
        except Exception as exc:  # noqa: BLE001 运行期抖动同样降级
            logger.warning("Redis 限流执行失败，本次降级为内存计数: {}", exc)
            return await self._fallback.hit(key, max_requests, window_seconds)

        if int(allowed) == 1:
            return True, 0
        retry_after = max(1, int((int(oldest_ms) + window_seconds * 1000 - now_ms) / 1000) + 1)
        return False, retry_after


def build_backend(redis_url: Optional[str]) -> RateLimitBackend:
    """按配置构建限流后端。"""
    if redis_url:
        return RedisRateLimitBackend(redis_url)
    return MemoryRateLimitBackend()
